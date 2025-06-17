import asyncio
import nest_asyncio
from mcp import ClientSession
from mcp.client.sse import sse_client
import openai
from openai import AsyncOpenAI
from fastmcp.client import Client
from langchain.agents import initialize_agent, tool
from langchain_community.chat_models import ChatOpenAI
import os
from typing import Any, Dict, List, Optional
import json
from contextlib import AsyncExitStack
from dotenv import load_dotenv
import time


load_dotenv("")


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

        # Dict of name → session
        self.sessions: Dict[str, ClientSession] = {}
        self.tool_map: Dict[str, str] = {}  # tool_name → session_key

    async def connect_to_servers(self):
        servers = {
            "s3": "http://localhost:8050/sse",
            "gdrive": "http://localhost:8051/sse",
            "github": "http://localhost:8052/sse"
        }
        for key, url in servers.items():
            transport = await self.exit_stack.enter_async_context(sse_client(url))
            reader, writer = transport
            session = await self.exit_stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
            self.sessions[key] = session

            tools = await session.list_tools()
            for tool in tools.tools:
                self.tool_map[tool.name] = key  # map tool name to session
                print(f"  - {key}: {tool.name}: {tool.description} ")


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



    async def process_query(self, query: str, max_rounds: int = 25, timeout_seconds: int = 300) -> str:
        tools = await self.get_mcp_tools()
        messages = [{"role": "user", "content": query}]
        start_time = time.time()

        for round_num in range(max_rounds):
            # Check for timeout
            if time.time() - start_time > timeout_seconds:
                return f"Stopped due to timeout after {timeout_seconds} seconds."

            response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

            assistant_message = response.choices[0].message
            messages.append(assistant_message)
            print(f"Round {round_num + 1} - Assistant Message: ", assistant_message)

            if not assistant_message.tool_calls:
                return assistant_message.content or "No tool call needed."

            for tool_call in assistant_message.tool_calls:
                session_key = self.tool_map.get(tool_call.function.name)
                if session_key is None:
                    continue  # or raise error

                result = await self.sessions[session_key].call_tool(
                    tool_call.function.name,
                    arguments=json.loads(tool_call.function.arguments),
                )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result.content[0].text,
                })
                print(f"Executed tool '{tool_call.function.name}': {result.content[0].text}")

        return f"Stopped after reaching max of {max_rounds} tool call rounds without finishing."


    async def cleanup(self):
        """Clean up resources."""
        await self.exit_stack.aclose()



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
# query = "create a new branch update and create test commit on sc-himpun/testmcp repo in github with some random description on test.txt file"
# query = "create a pull request in the repo sc-himpun/testmcp from update branch to main branch"
# query = "list all open pull requests in the repo sc-himpun/testmcp"
# query = "close open pull request from update branch in the repo sc-himpun/testmcp and delete update branch"


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
