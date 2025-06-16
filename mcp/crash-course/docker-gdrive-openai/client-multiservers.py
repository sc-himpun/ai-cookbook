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

load_dotenv("../.env")


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
            "gdrive": "http://localhost:8051/sse"
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



    async def process_query(self, query: str) -> str:
        tools = await self.get_mcp_tools()
        messages = [{"role": "user", "content": query}]

        response = await self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        assistant_message = response.choices[0].message
        messages.append(assistant_message)
        print("first_call_msg: ", assistant_message)

        if assistant_message.tool_calls:
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
                print("first_call_res: ", result)

            # Second pass (just like before)
            second_response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )
            second_message = second_response.choices[0].message
            messages.append(second_message)
            print("second_call_msg: ", second_message)

            if second_message.tool_calls:
                for tool_call in second_message.tool_calls:
                    session_key = self.tool_map.get(tool_call.function.name)
                    result = await self.sessions[session_key].call_tool(
                        tool_call.function.name,
                        arguments=json.loads(tool_call.function.arguments),
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result.content[0].text,
                    })
                    print("second_call_res: ", result)

                final_response = await self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                )
                return final_response.choices[0].message.content

            return second_message.content or "No further tools called."

        return assistant_message.content or "No tool call needed."


    async def cleanup(self):
        """Clean up resources."""
        await self.exit_stack.aclose()


async def main():
    client = MCPOpenAIClient()
    try:
        await client.connect_to_servers()
        # query = "Find a file related to sherlock and summarize it?"
        # query = "Find a file related to sherlock, does it contain anything related to Blue Carbuncle?"
        # query = "which filename in s3 mentions anything about Bohemia, show the full file content and details"
        # query = "which filename contains anything related to Elephants Can Remember, give context"
        # query = "which file in s3 mentions about The Black Tower, give full file details and context."
        # query = "Any file contains anything about Bellona Club? , if yes, show details of the file"
        query = "Any file mentions Hammer of God? , if yes, show details of the file"
        print(f"\nQuery: {query}")
        response = await client.process_query(query)
        print(f"\nResponse: {response}")
    finally:
        await client.cleanup() 



if __name__ == "__main__":
    asyncio.run(main())
