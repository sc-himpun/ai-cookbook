# MCP Crash Course for Python Developers

The Model Context Protocol (MCP) is a powerful framework that enables developers to build AI applications with large language models (LLMs) by providing a standardized way to connect models with external data sources and tools. This crash course will guide you through the fundamentals of MCP, from understanding its core concepts to implementing servers and clients that leverage prompts, resources, and tools. You'll learn how to set up your development environment, create simple servers, integrate with OpenAI models, understand the differences between MCP and traditional function calling, and manage the lifecycle of your MCP applications. Whether you're building chatbots, data analysis tools, or complex AI workflows, this course will give you the practical skills needed to start building with MCP today.

## Table of Contents

1. [Introduction and Context](./1-introduction-and-context/README.md)
   - Understanding the MCP ecosystem
   - Key concepts and terminology

2. [Understanding MCP](./2-understanding-mcp/README.md)
   - Core architecture
   - Communication protocols
   - Server and client roles

3. [Simple Server Setup](./3-simple-server-setup/README.md)
   - Creating a basic MCP server
   - Implementing tools
   - Connecting with clients (SSE and stdio)

4. [OpenAI Integration](./4-openai-integration/README.md)
   - Connecting MCP with OpenAI models
   - Implementing a client for OpenAI
   - Handling tool calls and responses

5. [MCP vs Function Calling](./5-mcp-vs-function-calling/README.md)
   - Comparing MCP with traditional function calling
   - Advantages and use cases
   - Implementation differences

6. [Lifecycle Management](./6-lifecycle-management/README.md)
   - Managing server lifecycles
   - Error handling and recovery
   - Best practices for production

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
