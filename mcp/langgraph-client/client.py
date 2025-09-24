# -*- coding: utf-8 -*-
import asyncio
import json
from typing import List, Dict, Any, Literal

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    HumanMessage, AIMessage, ToolMessage, SystemMessage, BaseMessage
)
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from langchain_mcp_adapters.client import MultiServerMCPClient

# --- CONFIG ---
MCP_SERVERS = {
    "confluence": {"url": "http://localhost:8071/mcp-server/sse"},
    "zoom": {"url": "http://localhost:8019/mcp-server/sse"},
    "s3": {"url": "http://localhost:8050/sse"},
}

SYSTEM_MESSAGE = "You are an AI assistant with access to multiple tools via MCP servers."


# --- GRAPH STATE ---
class State(Dict):
    messages: List[BaseMessage]


# --- Agent Class ---
class MCPAgent:
    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
        self.client = MultiServerMCPClient(MCP_SERVERS)

    async def get_tools(self):
        tools = []
        for server in self.client.connections:
            try:
                tools.extend(await self.client.get_tools(server))
            except Exception as e:
                print(f"[WARN] Failed to fetch tools from {server}: {e}")
        return tools

    async def agent_node(self, state: State) -> Dict[str, Any]:
        messages = [SystemMessage(content=SYSTEM_MESSAGE)] + state["messages"]

        # Ask the model
        ai_msg: AIMessage = await self.llm.ainvoke(messages)
        new_history = state["messages"] + [ai_msg]

        if getattr(ai_msg, "tool_calls", None):
            tool_results = []
            for call in ai_msg.tool_calls:
                tool_name = call["name"]
                args = call.get("args", {}) or {}
                server = self.client.tool_map.get(tool_name)

                try:
                    result = await self.client.call_tool(server, tool_name, args)
                    result_str = json.dumps(result, ensure_ascii=False)[:4000]
                except Exception as e:
                    result_str = f"[ERROR] {e}"

                tool_results.append(ToolMessage(content=result_str, tool_call_id=call["id"]))

            followup_ai = await self.llm.ainvoke(messages + tool_results)
            new_history.extend(tool_results + [followup_ai])
            return {"messages": new_history}

        return {"messages": new_history}

    async def should_continue(self, state: State) -> Literal["agent", "end"]:
        last_msg = state["messages"][-1]
        if isinstance(last_msg, AIMessage) and getattr(last_msg, "tool_calls", None):
            return "agent"
        return "end"

    async def build_graph(self):
        tools = await self.get_tools()
        self.llm = self.llm.bind_tools(tools)

        builder = StateGraph(State)
        builder.add_node("agent", self.agent_node)
        builder.add_conditional_edges("agent", self.should_continue, {"agent": "agent", "end": END})
        builder.add_edge(START, "agent")

        memory = MemorySaver()
        return builder.compile(checkpointer=memory)


# --- CLI Runner ---
async def main():
    agent = MCPAgent()
    graph = await agent.build_graph()

    print("🚀 MCP Agent Ready. Type your queries (or 'exit' to quit).")

    state = {"messages": []}
    while True:
        query = input("\n> ")
        if query.strip().lower() in ("exit", "quit"):
            break

        state["messages"].append(HumanMessage(content=query))
        result = await graph.ainvoke(state)
        state = result  # maintain history

        last_msg = state["messages"][-1]
        print(f"\n🤖 {last_msg.content}")


if __name__ == "__main__":
    asyncio.run(main())
