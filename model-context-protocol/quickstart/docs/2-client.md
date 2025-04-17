# Creating a Custom MCP Client in Python

As a developer wanting to integrate MCP with your own Python backend, you have a couple of options for creating client applications that connect to your MCP server. I'll guide you through building a simple client that can connect to your server.

## Option 1: Connect via stdio Transport

If you're running your server and client within the same Python application or as separate processes that you control, you can use the stdio transport, which is simpler and doesn't require HTTP.

### Step 1: Create your server (server.py)

```python
from mcp.server.fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP("SimpleDemoServer")

@mcp.tool()
def say_hello(name: str) -> str:
    """Say hello to someone"""
    return f"Hello, {name}! Nice to meet you."


if __name__ == "__main__":
    mcp.run()
```

### Step 2: Create a client (client.py)

Here's a simple client that connects to your server via stdio:

```python
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

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
            for tool in tools_result:
                print(f"  - {tool.name}: {tool.description}")
            
            # Call the say_hello tool
            hello_result = await session.call_tool("say_hello", {"name": "Developer"})
            print("\nCalling say_hello:")
            for content in hello_result.content:
                if content.type == "text":
                    print(f"  Result: {content.text}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Running the stdio client

Run the client with:

```bash
python client.py
```

## Option 2: Connect via HTTP Transport

If you want to run your server and client as separate services or potentially on different machines, using the HTTP transport makes more sense.

### Step 1: Create your server with HTTP support (server_http.py)

```python
from mcp.server.fastmcp import FastMCP

# Create an MCP server
mcp = FastMCP("SimpleDemoServer")

@mcp.tool()
def say_hello(name: str) -> str:
    """Say hello to someone"""
    return f"Hello, {name}! Nice to meet you."

if __name__ == "__main__":
    # Run with HTTP/SSE transport
    import uvicorn
    app = mcp.sse_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

### Step 2: Create an HTTP client (client_http.py)

```python
import asyncio
from mcp.client.ssehttp import ssehttp_client
from mcp import ClientSession

async def main():
    # Connect to the server via HTTP
    server_url = "http://127.0.0.1:8000"
    
    # Connect to the server
    async with ssehttp_client(server_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize the connection
            await session.initialize()
            
            # List available tools
            tools_result = await session.list_tools()
            print("Available tools:")
            for tool in tools_result:
                print(f"  - {tool.name}: {tool.description}")
            
            # Call the say_hello tool
            hello_result = await session.call_tool("say_hello", {"name": "Developer"})
            print("\nCalling say_hello:")
            for content in hello_result.content:
                if content.type == "text":
                    print(f"  Result: {content.text}")
            
            # Call the calculate tool
            calc_result = await session.call_tool(
                "calculate", 
                {"operation": "add", "a": 5, "b": 3}
            )
            print("\nCalling calculate:")
            for content in calc_result.content:
                if content.type == "text":
                    print(f"  Result: {content.text}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Running the HTTP client

First, start your server:

```bash
python server_http.py
```

Then in another terminal, run your client:

```bash
python client_http.py
```

## Integration with Your Existing Backend

For integrating with an existing Flask or Django application, you have a few approaches:

### 1. Run MCP server as a separate process and connect via HTTP

This is the most decoupled approach. Your main application can make HTTP requests to the MCP server whenever needed.

### 2. Embed the MCP server in your application

You can create an MCP server instance within your existing application and mount its ASGI app:

```python
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

from mcp import ClientSession
from mcp.client.ssehttp import ssehttp_client

app = FastAPI(title="MCP Client API")

# Define the models for our API
class ToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]

class ToolResponse(BaseModel):
    result: str
    success: bool

# Create a client session pool to reuse connections
class MCPClientManager:
    def __init__(self, server_url: str):
        self.server_url = server_url
        self._session = Nones
        self._lock = asyncio.Lock()
    
    async def get_session(self):
        async with self._lock:
            if self._session is None:
                read_stream, write_stream = await ssehttp_client(self.server_url)
                self._session = ClientSession(read_stream, write_stream)
                await self._session.initialize()
        return self._session
    
    async def close(self):
        if self._session:
            await self._session.close()
            self._session = None
    
    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        session = await self.get_session()
        try:
            result = await session.call_tool(tool_name, arguments)
            # Extract text content from result
            response_text = ""
            for content in result.content:
                if hasattr(content, "text") and content.text:
                    response_text += content.text + "\n"
            return response_text.strip()
        except Exception as e:
            # Handle potential errors, maybe reconnect
            await self.close()  # Close the broken session
            raise Exception(f"Error calling MCP tool: {str(e)}")
    
    async def list_tools(self):
        session = await self.get_session()
        try:
            return await session.list_tools()
        except Exception as e:
            await self.close()
            raise Exception(f"Error listing tools: {str(e)}")

# Initialize our client manager
client_manager = MCPClientManager("http://localhost:8000")

# Set up shutdown event to close the client connection
@app.on_event("shutdown")
async def shutdown_event():
    await client_manager.close()

@app.post("/tools/call", response_model=ToolResponse)
async def call_tool(request: ToolRequest):
    """Call an MCP tool with the provided arguments"""
    try:
        result = await client_manager.call_tool(request.tool_name, request.arguments)
        return {"result": result, "success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/tools")
async def list_tools():
    """List all available tools from the MCP server"""
    try:
        tools = await client_manager.list_tools()
        return {"tools": [{"name": tool.name, "description": tool.description} for tool in tools]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Add more endpoints for other MCP functionality as needed

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
```

### 3. Create a client service module in your application

You can create a service module that provides a higher-level API to your MCP server:

```python
# mcp_service.py
import asyncio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

class MCPService:
    async def _call_tool(self, tool_name, arguments):
        server_params = StdioServerParameters(
            command="python",
            args=["server.py"],
            env=None,
        )
        
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)
                
                # Extract text content
                responses = []
                for content in result.content:
                    if content.type == "text":
                        responses.append(content.text)
                
                return responses
    
    def say_hello(self, name):
        return asyncio.run(self._call_tool("say_hello", {"name": name}))
    
    def calculate(self, operation, a, b):
        return asyncio.run(self._call_tool("calculate", {
            "operation": operation, 
            "a": a, 
            "b": b
        }))

# Then use it in your application:
# mcp_service = MCPService()
# result = mcp_service.say_hello("Developer")
```

## Which Approach Should You Choose?

- **Use stdio** if your client and server will be running in the same process or if you're starting the server process directly from your client.
- **Use HTTP** if your server will be running separately from your client, possibly on different machines or in different containers.

For most production backend integrations, the HTTP approach offers better separation and scalability, while the stdio approach might be simpler for development or tightly coupled systems.