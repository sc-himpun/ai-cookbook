## Part 3: Practical Implementation with Python SDK

### Setting Up Your Development Environment

Let's start by setting up our environment. The MCP Python SDK provides everything we need to build both servers and clients.

```bash
# Using uv (recommended)
uv init mcp-project
cd mcp-project
uv add "mcp[cli]"

# Or using pip
pip install "mcp[cli]"
```

The MCP CLI tools provide helpful utilities for development and testing:

```bash
# Test a server with the MCP Inspector
mcp dev server.py

# Install a server in Claude Desktop
mcp install server.py

# Run a server directly
mcp run server.py
```

### Building Your First MCP Server

Let's create a simple server with a calculator tool:


```python
# server.py
from mcp.server.fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP("Calculator")

# Add a simple calculator tool
@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two numbers together"""
    return a + b

# Run the server
if __name__ == "__main__":
    mcp.run()
```

This minimal example demonstrates the core pattern for creating MCP servers:

1. Initialize a FastMCP instance
2. Register tools using decorators
3. Run the server

For more complex servers, you'll likely need to manage lifecycle and state:


```python
from mcp.server.fastmcp import FastMCP, Context
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass

# Create a typed context for our server
@dataclass
class AppContext:
    db_client: DatabaseClient  # Your database client type

# Define the server lifecycle
@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Manage application lifecycle with type-safe context"""
    # Initialize on startup
    db = await DatabaseClient.connect()
    try:
        yield AppContext(db_client=db)
    finally:
        # Cleanup on shutdown
        await db.disconnect()

# Initialize server with lifecycle management
mcp = FastMCP("DatabaseTools", lifespan=app_lifespan)

# Access context in tools
@mcp.tool()
def query_data(ctx: Context, query: str) -> str:
    """Run a query against the database"""
    db = ctx.request_context.lifespan_context.db_client
    return db.query(query)
```

This pattern ensures proper resource management and provides type-safe access to shared resources across tools.

### Client-Side Implementation

Now, let's see how to create a client that uses our server:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Create server parameters for stdio connection
server_params = StdioServerParameters(
    command="python",  # Executable
    args=["server.py"],  # Our server script
)

async def run():
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # Initialize the connection
            await session.initialize()
            
            # List available tools
            tools = await session.list_tools()
            print(f"Available tools: {[tool.name for tool in tools]}")
            
            # Call our calculator tool
            result = await session.call_tool("add", arguments={"a": 2, "b": 3})
            print(f"2 + 3 = {result.content[0].text}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(run())
```

This client:

1. Creates a connection to our server via stdio
2. Establishes an MCP session
3. Lists available tools
4. Calls the `add` tool with arguments

For integration with LLMs, you'd typically wrap these tool calls in a format suitable for your LLM provider:

```python
import openai

# Get tools from MCP server
async def get_mcp_tools():
    # ... connection code from above ...
    tools = await session.list_tools()
    # Convert to OpenAI format
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        } for tool in tools
    ]

# Call LLM with MCP tools
async def chat_with_tools():
    tools = await get_mcp_tools()
    response = await openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "Calculate 25 + 17"}],
        tools=tools
    )
    
    # Handle tool calls
    if response.choices[0].message.tool_calls:
        tool_call = response.choices[0].message.tool_calls[0]
        tool_name = tool_call.function.name
        tool_args = json.loads(tool_call.function.arguments)
        
        # Execute through MCP
        result = await session.call_tool(tool_name, arguments=tool_args)
        
        # Send result back to model
        final_response = await openai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "user", "content": "Calculate 25 + 17"},
                response.choices[0].message,
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": result.content[0].text
                }
            ]
        )
        return final_response.choices[0].message.content
```