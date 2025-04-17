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


# Add a resource that provides information
@mcp.resource("info://about")
def get_info() -> str:
    """Information about this server"""
    return "This is a simple MCP server that demonstrates basic tools and resources."


if __name__ == "__main__":
    mcp.run()
