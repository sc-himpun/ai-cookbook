## Creating a Simple MCP Server

First, you need to install the MCP Python SDK. The recommended way is using `uv`, but you can also use `pip`:

```bash
# Using uv (recommended)
uv init my-first-mcp
cd my-first-mcp
uv add "mcp[cli]"

# Or using pip
pip install "mcp[cli]"
```

Now, let's create a simple server. Create a file named `server.py`:

```python
from mcp.server.fastmcp import FastMCP

# Create an MCP server with a name
mcp = FastMCP("SimpleDemoServer")

# Add a tool that says hello
@mcp.tool()
def say_hello(name: str) -> str:
    """Say hello to someone
    
    Args:
        name: The person's name to greet
    """
    return f"Hello, {name}! Nice to meet you."

# Add a simple calculator tool
@mcp.tool()
def calculate(operation: str, a: float, b: float) -> str:
    """Perform a basic calculation
    
    Args:
        operation: One of 'add', 'subtract', 'multiply', or 'divide'
        a: First number
        b: Second number
    """
    if operation == "add":
        return f"{a} + {b} = {a + b}"
    elif operation == "subtract":
        return f"{a} - {b} = {a - b}"
    elif operation == "multiply":
        return f"{a} * {b} = {a * b}"
    elif operation == "divide":
        if b == 0:
            return "Error: Division by zero"
        return f"{a} / {b} = {a / b}"
    else:
        return f"Unknown operation: {operation}"

# Add a resource that provides information
@mcp.resource("info://about")
def get_info() -> str:
    """Information about this server"""
    return "This is a simple MCP server that demonstrates basic tools and resources."
```

## Running the Server

There are several ways to run your MCP server:

### 1. Development Mode with MCP Inspector

The easiest way to test your server is using the MCP Inspector:

```bash
mcp dev server.py
```

This runs your server locally and connects it to the MCP Inspector, a web-based tool that lets you interact with your server's tools and resources directly. This is great for testing.

### 2. Claude Desktop Integration

If you have Claude Desktop installed, you can install your server to use with Claude:

```bash
mcp install server.py
```

This will add your server to Claude Desktop's configuration, making it available to Claude.

### 3. Direct Execution

You can also run the server directly:

```bash
# Method 1: Using mcp CLI
mcp run server.py

# Method 2: Running as a Python script
python server.py
```

For the second method, you'll need to add this to your server.py file:

```python
if __name__ == "__main__":
    mcp.run()
```

## What Happens When You Run an MCP Server?

When you run an MCP server:

1. The server initializes with the capabilities you've defined (tools, resources, etc.)
2. It starts listening for connections on a specific transport

By default, MCP servers don't use a traditional web server port. Instead, they use either:

- **stdio transport**: The server communicates through standard input and output (the default for `mcp run` and integration with Claude Desktop)
- **SSE transport**: For HTTP-based communication (used when explicitly configured)

If you want to expose your server over HTTP with a specific port, you need to modify your server to use the SSE transport:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("MyServer")

# Add your tools and resources here...

if __name__ == "__main__":
    # Run with SSE transport on port 8000
    import uvicorn
    app = mcp.sse_app()
    uvicorn.run(app, host="127.0.0.1", port=8000)
```

Then you can run it with:

```bash
python server.py
```

This will start your server at `http://127.0.0.1:8000`.

## Understanding MCP Server Communication

MCP servers don't operate like traditional web servers. They're specifically designed to be connected to by MCP clients (like Claude Desktop), which know how to speak the MCP protocol.

When a client connects to your server:

1. **Initialization**: The client and server exchange capabilities
2. **Operation**: The client can:
   - List available tools, resources, and prompts
   - Call tools with arguments
   - Read resources
   - Get prompts
3. **Termination**: The connection is closed when done

You don't typically interact with an MCP server through a web browser or API client directly. Instead, you connect to it through an MCP client like:

- Claude Desktop
- The MCP Inspector tool
- A custom application using the MCP client SDK

## Testing Your Server

To verify your server is working correctly:

1. Run in developer mode: `mcp dev server.py`
2. The MCP Inspector will open in your browser
3. Click on the "Tools" tab to see your tools
4. Try calling one of your tools with some arguments
5. Check the "Resources" tab to see your resources

If everything works correctly, you can then integrate it with Claude Desktop or another MCP client.