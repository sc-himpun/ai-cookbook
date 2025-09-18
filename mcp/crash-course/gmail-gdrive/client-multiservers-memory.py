# -*- coding: utf-8 -*-
import asyncio
import nest_asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client
import openai
from openai import AsyncOpenAI
from fastmcp.client import Client
import os
from typing import Any, Dict, List, Optional
import json
from contextlib import AsyncExitStack
from dotenv import load_dotenv
import time


load_dotenv()


# os.environ["OPENAI_API_KEY"] = "lm-studio"
# os.environ["OPENAI_API_BASE"] = "http://192.168.29.53:1234/v1"  # Adjust if needed
# os.environ["OPENAI_API_KEY"] = ""

nest_asyncio.apply()  # Needed to run interactive python



class MCPOpenAIClient:
    def __init__(self, model: str = "gpt-4o-mini"):
        openai.api_key = os.environ["OPENAI_API_KEY"]
        self.openai_client = AsyncOpenAI()
        self.model = model
        self.exit_stack = AsyncExitStack()
        self.message_history: List[List[Dict[str, Any]]] = []  
        self.max_memory_turns = 25  
        # Dict of name → session
        self.sessions: Dict[str, ClientSession] = {}
        self.tool_map: Dict[str, str] = {}  # tool_name → session_key

    async def connect_to_servers(self):
        servers = {
            "s3": "http://localhost:8050/sse",
            # "gdrive": "http://localhost:8051/sse",
            # "github": "http://localhost:8052/sse",
            # "youtrack": "http://localhost:8053/sse",
            # "slack": "http://localhost:8054/sse",
            # "zoom": "http://localhost:8055/sse",
            # "azure": "http://localhost:5008/sse",
            # "onedrive": "http://localhost:8056/sse",
            # "onedrive-business_sharepoint": "http://localhost:8057/sse",
            "gmail-gdrive": "http://localhost:8000/mcp-server/sse/",
            # "jira"  : "http://localhost:8002/mcp-server/sse/",
            #  "onedrive-business_sharepoint": "http://localhost:8007/mcp-server/sse/",
        }

        for key, url in servers.items():
            try:
                print(f"Connecting to {key} at {url}...")
                transport = await self.exit_stack.enter_async_context(sse_client(url))
                reader, writer = transport
                session = await self.exit_stack.enter_async_context(ClientSession(reader, writer))
                await session.initialize()
                self.sessions[key] = session

                tools = await session.list_tools()
                for tool in tools.tools:
                    self.tool_map[tool.name] = key
                    print(f"  - {key}: {tool.name}: {tool.description} ")

            except Exception as e:
                print(f"[WARNING] Skipping {key} ({url}) - Could not connect: {e}")


    async def get_mcp_tools(self) -> List[Dict[str, Any]]:
        tools_openai_format = []
        for key, session in self.sessions.items():
            tools = await session.list_tools()
            for tool in tools.tools:
                self.tool_map[tool.name] = key
                tools_openai_format.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema,
                    },
                })
        return tools_openai_format



    async def process_query(self, query: str, max_rounds: int = 10, timeout_seconds: int = 300) -> str:
        tools = await self.get_mcp_tools()

        current_turn: List[Dict[str, Any]] = [{"role": "user", "content": query}]
        start_time = time.time()
        tool_call_occurred = False  # Track whether any tool call happened

        for round_num in range(max_rounds):
            if time.time() - start_time > timeout_seconds:
                return f"⏱️ Stopped after {timeout_seconds} seconds (timeout)."

            # Flatten full memory + current turn
            flat_messages = [m for turn in self.message_history for m in turn] + current_turn

            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=flat_messages,
                tools=tools,
                tool_choice="auto",
            )

            assistant_msg_obj = response.choices[0].message
            assistant_msg = {
                "role": "assistant",
                "content": assistant_msg_obj.content or "",
            }

            if assistant_msg_obj.tool_calls:
                assistant_msg["tool_calls"] = [tc.model_dump() for tc in assistant_msg_obj.tool_calls]
                tool_call_occurred = True  # At least one tool call occurred

            current_turn.append(assistant_msg)
            print(f"Round {round_num + 1} - Assistant Message: ", assistant_msg_obj)

            if not assistant_msg_obj.tool_calls:
                # No tool calls — finalize turn
                self.message_history.append(current_turn)
                self.trim_history()
                return assistant_msg_obj.content or "No tool call needed."

            for tool_call in assistant_msg_obj.tool_calls:
                session_key = self.tool_map.get(tool_call.function.name)
                if session_key is None:
                    continue

                result = await self.sessions[session_key].call_tool(
                    tool_call.function.name,
                    arguments=json.loads(tool_call.function.arguments),
                )

                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result.content[0].text,
                }

                current_turn.append(tool_msg)
                print(f"Executed tool '{tool_call.function.name}': {result.content[0].text}")

            self.message_history.append(current_turn)
            self.trim_history()
            current_turn = []

        # Final fallback if tool calls happened but never led to a full answer
        if tool_call_occurred:
            flat_messages = [m for turn in self.message_history for m in turn]
            flat_messages.append({
                "role": "system",
                "content": (
                    "Some tools responded, but the full query could not be resolved. "
                    "Based on all previous conversation and tool outputs, summarize what part of query could be answered "
                    "and what could not be answered/retrieved. Provide any reasonable conclusion."
                )
            })

            final_response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=flat_messages,
                tools=[],  # Force no tools
            )

            final_msg = final_response.choices[0].message
            return f"📤 Final LLM reasoning after max tool rounds:\n{final_msg.content}"

        return f"⛔ Stopped after {max_rounds} tool call rounds without finishing."





    async def cleanup(self):
        """Clean up resources."""
        await self.exit_stack.aclose()
    
    def trim_history(self):
        """Keep memory within max_memory_turns limit."""
        if len(self.message_history) > self.max_memory_turns:
            self.message_history = self.message_history[-self.max_memory_turns:]




# Example queries:
# query = "Find a file related to sherlock and summarize it?"
# query = "Find a file related to sherlock, does it contain anything related to Blue Carbuncle?"
# query = "which filename in s3 mentions anything about Bohemia, show the full file content and details"
# query = "which filename contains anything related to Elephants Can Remember, give context"
# query = "which file in s3 mentions about The Black Tower, give full file details and context."
# query = "Any file contains anything about Bellona Club? , if yes, show details of the file"
# query = "Any file mentions Hammer of God? , if yes, show details of the file"
# query = "which file(s) mention about The Blue Cross and about Shroud for a Nightingale , give the filenames, locations, search both in s3 and gdrive"
# query = "which file mentions about The Redeemer , is it present in sc-himpun/testmcp? give the file details"
# query = "create a new branch update and create test commit on sc-himpun/testmcp repo in github with some random description on new test.txt file"
# query = "create a pull request in the repo sc-himpun/testmcp from update branch to main branch"
# query = "list all open pull requests in the repo sc-himpun/testmcp"
# query = "close open pull request from update branch in the repo sc-himpun/testmcp and delete update branch"
# query = "Does any youtrack issue mentions about MCP? , if yes, summarize it"
# query = "Add comment on Issue VC-1288 -  The github MCP server is created and tested"
# query = "show slack channels"
# query = "post a hello message to testmcp channel on slack"
# query = "show messages of testmcp channel on slack"
# query = "create a meeting for 5 PM IST on 18th June 2025 , include just me, meeting will be of 30mins duration, subject will be Meet for testing MCP"
# query = "create a meeting after 10 mins for 30 mins duration , include just me, subject will be Meet for MCP"
# query = "list chat channels on zoom"
# query = "send message to testzoommcp channel on zoom - Testing MCP chat"
# query = "which file mentions about yellow dog in bookstypes folder of onedrive"
# query = "is any file tana_french.txt present in onedrive in bookstypes folder?"
# query = "list files in bookstypes folder on onedrive"
# query = "which file contains anything related to Elephants Can Remember in MCPDatatest folder of gdrive?"


async def main():
    client = MCPOpenAIClient()
    try:
        await client.connect_to_servers()
        print("✅ Connected to MCP servers. Enter a query (or type 'exit' to quit).")

        while True:
            try:
                query = input("\n🔎 Query: ").strip()
                if not query:
                    continue
                if query.lower() in {"exit", "quit"}:
                    print("👋 Exiting...")
                    break

                print(f"\n⏳ Running: {query}")
                response = await client.process_query(query)
                print(f"\n📤 Response:\n{response}")

            except KeyboardInterrupt:
                print("\n🛑 Interrupted. Exiting...")
                break
            except Exception as e:
                print(f"❌ Error: {e}")

    finally:
        await client.cleanup()
        print("🧹 Cleaned up.")



if __name__ == "__main__":
    asyncio.run(main())
