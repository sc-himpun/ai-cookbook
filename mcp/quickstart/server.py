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


if __name__ == "__main__":
    mcp.run()
