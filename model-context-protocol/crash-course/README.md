# MCP Crash Course for Python Developers

## Setting Up Your Development Environment

Let's start by setting up our environment. The MCP Python SDK provides everything we need to build both servers and clients.

```bash
# Using uv (recommended)
uv pip install -r requirements.txt

# Or using pip
pip install -r requirements.txt
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

## Resources and Next Steps

Key resources for deepening your MCP knowledge:

- [Model Context Protocol documentation](https://modelcontextprotocol.io)
- [Model Context Protocol specification](https://spec.modelcontextprotocol.io)
- [Python SDK GitHub repository](https://github.com/modelcontextprotocol/python-sdk)
- [Officially supported servers](https://github.com/modelcontextprotocol/servers)
- [MCP Core Architecture](https://modelcontextprotocol.io/docs/concepts/architecture)
