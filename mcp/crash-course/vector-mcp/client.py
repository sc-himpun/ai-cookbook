# -*- coding: utf-8 -*-
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
    """Client for interacting with OpenAI models using MCP tools."""

    def __init__(self, model: str = "gpt-4o-mini"):
    # def __init__(self, model: str = "meta-llama-3-8b-instruct"):
        """Initialize the OpenAI MCP client.

        Args:
            model: The OpenAI model to use.
        """
        # Initialize session and client objects
        # openai.api_base = os.environ["OPENAI_API_BASE"]
        openai.api_key = os.environ["OPENAI_API_KEY"]
        self.session: Optional[ClientSession] = None
        self.exit_stack = AsyncExitStack()
        # self.openai_client = AsyncOpenAI(base_url=os.environ["OPENAI_API_BASE"],
        #                                  api_key=os.environ["OPENAI_API_KEY"])
        self.openai_client = AsyncOpenAI()
        self.model = model
        # self.stdio: Optional[Any] = None
        # self.write: Optional[Any] = None
        self.reader: Optional[Any] = None
        self.writer: Optional[Any] = None

    async def connect_to_server(self, base_url: str = "http://localhost:8050/sse"):
        """Connect to an MCP SSE server."""
        transport = await self.exit_stack.enter_async_context(
            sse_client(base_url)
        )
        self.reader, self.writer = transport

        self.session = await self.exit_stack.enter_async_context(
            ClientSession(self.reader, self.writer)
        )

        await self.session.initialize()
        tools_result = await self.session.list_tools()
        print("\nConnected to server with tools:")
        for tool in tools_result.tools:
            print(f"  - {tool.name}: {tool.description}")

    async def get_mcp_tools(self) -> List[Dict[str, Any]]:
        """Get available tools from the MCP server in OpenAI format.

        Returns:
            A list of tools in OpenAI format.
        """
        tools_result = await self.session.list_tools()
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }
            for tool in tools_result.tools
        ]

    async def process_query(self, query: str) -> str:
        tools = await self.get_mcp_tools()
        messages = [{"role": "user", "content": query}]

        # First OpenAI call (tool_choice="auto")
        response = await self.openai_client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )

        assistant_message = response.choices[0].message
        messages.append(assistant_message)

        # Handle first round of tool calls (e.g. search_files)
        if assistant_message.tool_calls:
            for tool_call in assistant_message.tool_calls:
                result = await self.session.call_tool(
                    tool_call.function.name,
                    arguments=json.loads(tool_call.function.arguments),
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result.content[0].text,
                })

            # Let OpenAI decide next step (e.g. call fetch_file)
            second_response = await self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
            )

            second_message = second_response.choices[0].message
            messages.append(second_message)
            print("second_message:", second_message)
            # Handle second round of tool calls (e.g. fetch_file)
            if second_message.tool_calls:
                for tool_call in second_message.tool_calls:
                    result = await self.session.call_tool(
                        tool_call.function.name,
                        arguments=json.loads(tool_call.function.arguments),
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result.content[0].text,
                    })

                # Final response (summary etc.)
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
        await client.connect_to_server()
        query = "Find a file related to sherlock and summarize it?"
        # query = "Find a file related to sherlock, does it contain anything related to Blue Carbuncle?"
        # query = "which filename in s3 mentions anything about Bohemia, show the full file content and details"
        # query = "which filename contains anything related to Elephants Can Remember, give context"
        print(f"\nQuery: {query}")
        response = await client.process_query(query)
        print(f"\nResponse: {response}")
    finally:
        await client.cleanup() 


if __name__ == "__main__":
    asyncio.run(main())
