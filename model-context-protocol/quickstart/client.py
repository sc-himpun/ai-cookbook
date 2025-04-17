import asyncio
import nest_asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

nest_asyncio.apply()


async def main():
    # Define server parameters
    server_params = StdioServerParameters(
        command="python",  # The command to run your server
        args=["server.py"],  # Arguments to the command
        env=None,  # Optional environment variables
    )

    # Connect to the server
    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize the connection
            await session.initialize()

            # List available tools
            tools_result = await session.list_tools()
            print("Available tools:")
            for tool in tools_result.tools:
                print(f"  - {tool.name}: {tool.description}")

            # Call the say_hello tool
            hello_result = await session.call_tool("say_hello", {"name": "Developer"})
            print("\nCalling say_hello:")
            for content in hello_result.content:
                if content.type == "text":
                    print(f"  Result: {content.text}")


if __name__ == "__main__":
    asyncio.run(main())
