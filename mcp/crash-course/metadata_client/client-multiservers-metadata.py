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


load_dotenv("")


# os.environ["OPENAI_API_KEY"] = "lm-studio"
# os.environ["OPENAI_API_BASE"] = "http://192.168.29.53:1234/v1"  # Adjust if needed

with open("./mcp/crash-course/metadata_client/key.json", "r") as fl:
    os.environ["OPENAI_API_KEY"] = json.load(fl).get("key")

nest_asyncio.apply()  # Needed to run interactive python
# print(os.environ["OPENAI_API_KEY"])


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
        self.metadata = None

    async def connect_to_servers(self):
        servers = {
            # "s3": "http://localhost:8050/sse",
            # "s3": "http://localhost:8050/sse",
            # "vector": "http://localhost:8060/mcp-server/sse",
          
            # "onedrive-business_sharepoint": "http://localhost:8007/mcp-server/sse/",
            # "gmail-gdrive": "http://localhost:8000/mcp-server/sse/",
            # "jira": "http://localhost:8002/mcp-server/sse/",
            #"gitlab": "http://localhost:8017/mcp-server/sse/",
            #"github": "http://localhost:8010/mcp-server/sse/",
            # "s3": "http://localhost:8050/sse/",
            # "odata": "http://localhost:8016/mcp-server/sse/",
            #"asana": "http://localhost:8011/mcp-server/sse/",
            #"asana": "https://mcp.asana.com/sse",
            # "youtrack": "http://localhost:8089/mcp-server/sse",
            #"salesforce": "http://localhost:8091/mcp-server/sse",
            # "confluence": "http://localhost:8071/mcp-server/sse",
            # "zoom": "http://localhost:8019/mcp-server/sse",
            # "box": "http://localhost:8029/mcp-server/sse",
            # "slack": "http://localhost:8003/mcp-server/sse"
            ################################################,

            # "gmail-gdrive": "http://10.1.1.22:8000/mcp-server/sse",
            # "jira": "http://10.1.1.22:8002/mcp-server/sse",
            # "onedrive-business_sharepoint": "http://10.1.1.22:8007/mcp-server/sse",
            # "gitlab": "http://10.1.1.22:8017/mcp-server/sse",
            "github": "http://10.1.1.22:8010/mcp-server/sse",
            # "youtrack": "http://10.1.1.22:8053/mcp-server/sse",
            # "s3": "http://10.1.1.22:8050/sse",
            # "odata": "http://10.1.1.22:8016/mcp-server/sse",
            # "asana": "http://10.1.1.22:8011/mcp-server/sse",
            # "salesforce": "http://10.1.1.22:8091/mcp-server/sse",
            # "confluence": "http://10.1.1.22:8071/mcp-server/sse",
            # "zoom": "http://10.1.1.22:8019/mcp-server/sse",
            # "box": "http://10.1.1.22:8029/mcp-server/sse",
            # "zendesk": "http://10.1.1.22:9150/mcp-server/sse/",      
            # "slack": "http://10.1.1.22:8003/mcp-server/sse",
            # "vector": "http://10.1.1.22:8060/sse"            
            }

        total_tools = 0
        for key, url in servers.items():
            
            try:
                print(f"Connecting to {key} at {url}...")
                transport = await self.exit_stack.enter_async_context(sse_client(url))
                reader, writer = transport
                session = await self.exit_stack.enter_async_context(ClientSession(reader, writer))
                await session.initialize()
                self.sessions[key] = session
                tools = await session.list_tools()
                tool_count = len(tools.tools)
                total_tools += tool_count
                # print(f"{key} has {tool_count} tools")
                for tool in tools.tools:
                    self.tool_map[tool.name] = key
                    # print(f"  - {key}: {tool.name}: {tool.description} ")
                    print(f"  - {key}: {tool.name}")

            except Exception as e:
                print(f"[WARNING] Skipping {key} ({url}) - Could not connect: {e}")

        print("Total tools connected -", total_tools)

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



    # async def process_query(
    # self,
    # query: str,
    # metadata: dict,
    # max_rounds: int = 10,
    # timeout_seconds: int = 300
    # ) -> str:
    #     self.metadata = metadata
    #     tools = await self.get_mcp_tools()
    #     self.tool_to_platform = {
    #         tool["function"]["name"]: tool["function"]["name"].split("_", 1)[0]
    #         for tool in tools
    #     }
    #     current_turn: List[Dict[str, Any]] = [{"role": "user", "content": query}]
    #     start_time = time.time()
    #     tool_call_occurred = False

    #     for round_num in range(max_rounds):
    #         if time.time() - start_time > timeout_seconds:
    #             return f"⏱️ Stopped after {timeout_seconds} seconds (timeout)."

    #         flat_messages = [m for turn in self.message_history for m in turn] + current_turn

    #         response = await self.openai_client.chat.completions.create(
    #             model=self.model,
    #             messages=flat_messages,
    #             tools=tools,
    #             tool_choice="auto",
    #         )

    #         assistant_msg_obj = response.choices[0].message
    #         assistant_msg = {
    #             "role": "assistant",
    #             "content": assistant_msg_obj.content or "",
    #         }

    #         if assistant_msg_obj.tool_calls:
    #             assistant_msg["tool_calls"] = [tc.model_dump() for tc in assistant_msg_obj.tool_calls]
    #             tool_call_occurred = True

    #         current_turn.append(assistant_msg)
    #         print(f"Round {round_num + 1} - Assistant Message: ", assistant_msg_obj)

    #         if not assistant_msg_obj.tool_calls:
    #             self.message_history.append(current_turn)
    #             self.trim_history()
    #             return assistant_msg_obj.content or "No tool call needed."

    #         for tool_call in assistant_msg_obj.tool_calls:
    #             session_key = self.tool_map.get(tool_call.function.name)
    #             if session_key is None:
    #                 continue

    #             args = json.loads(tool_call.function.arguments)

    #             platform = self.tool_to_platform.get(tool_call.function.name)
    #             platform_metadata = self.metadata.get(platform, {})

    #             for k, v in platform_metadata.items():
    #                 if k not in args:
    #                     args[k] = v

    #             namespace = tool_call.function.name.split("_")[0]
    #             platform_meta = metadata.get(namespace)

    #             if platform_meta is None:
    #                 # Send fallback tool response to inform LLM of missing auth
    #                 not_auth_msg = {
    #                     "role": "tool",
    #                     "tool_call_id": tool_call.id,
    #                     "content": f"🔒 Not authenticated for '{namespace}'. Please authenticate to proceed.",
    #                 }
    #                 current_turn.append(not_auth_msg)
    #                 print(f"Skipped tool '{tool_call.function.name}' — not authenticated.")
    #                 continue

    #             # Inject metadata
    #             for k, v in platform_meta.items():
    #                 if k not in args:
    #                     args[k] = v

    #             # result = await self.sessions[session_key].call_tool(
    #             #     tool_call.function.name,
    #             #     arguments=args,
    #             # )
    #             tool_args = json.loads(tool_call.function.arguments)

    #             # Inject metadata if required
    #             platform = tool_call.function.name.split("_", 1)[0]
    #             if platform in self.metadata:
    #                 tool_args["metadata"] = self.metadata[platform]

    #             result = await self.sessions[session_key].call_tool(
    #                 tool_call.function.name,
    #                 arguments=tool_args,
    #             )

    #             tool_msg = {
    #                 "role": "tool",
    #                 "tool_call_id": tool_call.id,
    #                 "content": result.content[0].text,
    #             }

    #             current_turn.append(tool_msg)
    #             print(f"Executed tool '{tool_call.function.name}': {result.content[0].text}")

    #         self.message_history.append(current_turn)
    #         self.trim_history()
    #         current_turn = []

    #     if tool_call_occurred:
    #         flat_messages = [m for turn in self.message_history for m in turn]
    #         flat_messages.append({
    #             "role": "system",
    #             "content": (
    #                 "Some tools responded, but the full query could not be resolved. "
    #                 "Based on all previous conversation and tool outputs, summarize what part of query could be answered "
    #                 "and what could not be answered/retrieved. Provide any reasonable conclusion."
    #             )
    #         })

    #         final_response = await self.openai_client.chat.completions.create(
    #             model=self.model,
    #             messages=flat_messages,
    #             tools=[],
    #         )

    #         final_msg = final_response.choices[0].message
    #         return f"📤 Final LLM reasoning after max tool rounds:\n{final_msg.content}"

    #     return f"⛔ Stopped after {max_rounds} tool call rounds without finishing."

    async def process_query(
    self,
    query: str,
    metadata: dict,
    max_rounds: int = 10,
    timeout_seconds: int = 300
    ) -> str:
        self.metadata = metadata
        tools = await self.get_mcp_tools()
        self.tool_to_platform = {
            tool["function"]["name"]: tool["function"]["name"].split("_", 1)[0]
            for tool in tools
        }
        current_turn: List[Dict[str, Any]] = [{"role": "user", "content": query}]
        start_time = time.time()
        tool_call_occurred = False

        for round_num in range(max_rounds):
            if time.time() - start_time > timeout_seconds:
                return f"⏱️ Stopped after {timeout_seconds} seconds (timeout)."

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
                tool_call_occurred = True

            current_turn.append(assistant_msg)
            print(f"Round {round_num + 1} - Assistant Message: ", assistant_msg_obj)

            if not assistant_msg_obj.tool_calls:
                self.message_history.append(current_turn)
                self.trim_history()
                return assistant_msg_obj.content or "NOTFOUND"

            for tool_call in assistant_msg_obj.tool_calls:
                session_key = self.tool_map.get(tool_call.function.name)
                if session_key is None:
                    continue

                args = json.loads(tool_call.function.arguments)

                platform = self.tool_to_platform.get(tool_call.function.name)
                platform_metadata = self.metadata.get(platform, {})

                for k, v in platform_metadata.items():
                    if k not in args:
                        args[k] = v

                namespace = tool_call.function.name.split("_")[0]
                platform_meta = metadata.get(namespace)

                if platform_meta is None:
                    # Send fallback tool response to inform LLM of missing auth
                    not_auth_msg = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": f"🔒 Not authenticated for '{namespace}'. Please authenticate to proceed.",
                    }
                    current_turn.append(not_auth_msg)
                    print(f"Skipped tool '{tool_call.function.name}' — not authenticated.")
                    continue

                # Inject metadata
                for k, v in platform_meta.items():
                    if k not in args:
                        args[k] = v

                # result = await self.sessions[session_key].call_tool(
                #     tool_call.function.name,
                #     arguments=args,
                # )
                tool_args = json.loads(tool_call.function.arguments)

                # Inject metadata if required
                platform = tool_call.function.name.split("_", 1)[0]
                if platform in self.metadata:
                    tool_args["metadata"] = self.metadata[platform]

                result = await self.sessions[session_key].call_tool(
                    tool_call.function.name,
                    arguments=tool_args,
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

        if tool_call_occurred:
            flat_messages = [m for turn in self.message_history for m in turn]
            flat_messages.append({
                "role": "system",
                "content": (
                    "Some tools responded, but the full query could not be resolved. "
                    "If query cannot be responded or response is not relevent to query, return response as NOTFOUND"
                    # "Based on all previous conversation and tool outputs, summarize what part of query could be answered "
                    # "and what could not be answered/retrieved. Provide any reasonable conclusion."
                )
            })

            final_response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=flat_messages,
                tools=[],
            )

            final_msg = final_response.choices[0].message
            final_text = final_msg.content.strip() if final_msg and final_msg.content else ""
            return final_text if final_text else "NOTFOUND"

        return "NOTFOUND"

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
# query = "list my jira issues"
# query = "add comment on SCRUM-12 that testing is started for MCP now"
# query = "change the state of SCRUM-12 to In-progress"
# query = "change the state of SCRUM-12 to To-Do"
# query = "show comments on SCRUM-12"

# salesforce related queries
# query = "show recently created accounts in past 1 day in salesforce"
"""
SELECT Id, Name, CreatedDate
FROM Account
WHERE CreatedDate = LAST_N_DAYS:1
ORDER BY CreatedDate DESC

"""

# what contacts are in account with id 001KY00000EUMeDYAX
"""
SELECT Id, FirstName, LastName, Email
FROM Contact
WHERE AccountId = '001KY00000EUMeDYAX'

"""
# update status of task Project Submit to Not Started
# update status of task Project Submit to In progress 
# update the priority of task Project Submit to High
# update the priority of task Project Submit to Normal
#  Update description of the task Project Submit mentioning testing is ongoing, currently its in progress
# change the priority of case 00001026 to high
#  change the priority of case number 00001026 to medium


# what does conclusion section of MCP.pdf in s3 concludes?
# what are the three parts of MCP arhictecuture as per MCP.pdf in s3
# what are the three parts of MCP arhictecuture as per MCP.pdf file in google drive in MCPDatatest folder
# suggest some MCP server collections based on MCP.pdf in google drive in MCPDatatest folder

# what are my in-progress issues in youtrack

async def main():
    client = MCPOpenAIClient()
    try:
        await client.connect_to_servers()
        print("✅ Connected to MCP servers. Enter a query (or type 'exit' to quit).")

        import os
        metadata_path = os.path.join(os.path.dirname(__file__), "metadata.json")


        while True:
            try:
                query = input("\n🔎 Query: ").strip()
                if not query:
                    continue
                if query.lower() in {"exit", "quit"}:
                    print("👋 Exiting...")
                    break

                print(f"\n⏳ Running: {query}")
                # response = await client.process_query(query)

                """ metadata structure example:
                    metadata = {
                            "gdrive": {
                                "email": "...",
                                "access_token": "...",
                                "refresh_token": "..."
                            },
                            "gmail": {
                                "email": "...",
                                "access_token": "...",
                                "refresh_token": "..."
                            },
                            "jira": {
                                "email": "...",
                                "access_token": "...",
                                "refresh_token": "...",
                                "cloud_id": "..."
                            },
                            "onedrive": {
                                "email": "...",
                                "access_token": "...",
                                "refresh_token": "...",
                            },
                            "sharepoint": {
                                "email": "...",
                                "access_token": "...",
                                "refresh_token": "...",
                            },
                        }

                """
                with open(metadata_path, "r") as f:
                    metadata = json.load(f)

                response = await client.process_query(query, metadata=metadata)

                print(f"\n📤 Response:\n{response}")

            except KeyboardInterrupt:
                print("\n🛑 Interrupted. Exiting...")
                break
            except Exception as e:
                print(f"❌ Error: {e}")
                import traceback
                traceback.print_exc()

    finally:
        await client.cleanup()
        print("🧹 Cleaned up.")



if __name__ == "__main__":
    asyncio.run(main())
