# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import json
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# ─── Load Environment ────────────────────────────────────────────────────────
load_dotenv()

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
client = WebClient(token=SLACK_BOT_TOKEN)

# ─── MCP Server Setup ────────────────────────────────────────────────────────
mcp = FastMCP(name="SlackToolkit", host="0.0.0.0", port=8054)

# ─── Tool: List Channels ─────────────────────────────────────────────────────
@mcp.tool(name="slack_list_channels")
def list_channels() -> str:
    """List all public Slack channels."""
    try:
        result = client.conversations_list(types="public_channel")
        channels = [{"name": c["name"], "id": c["id"]} for c in result["channels"]]
        return json.dumps(channels)
    except SlackApiError as e:
        return f"Error listing channels: {e.response['error']}"

# ─── Tool: Search Messages ───────────────────────────────────────────────────
@mcp.tool(name="slack_search_messages")
def search_messages(query: str, limit: int = 5) -> str:
    """Search Slack messages containing the given query string."""
    try:
        result = client.search_messages(query=query, count=limit)
        messages = []
        for match in result["messages"]["matches"]:
            messages.append({
                "channel": match["channel"]["name"],
                "text": match["text"],
                "ts": match["ts"]
            })
        return json.dumps(messages)
    except SlackApiError as e:
        return f"Error searching messages: {e.response['error']}"

# ─── Tool: Post Message ──────────────────────────────────────────────────────
@mcp.tool(name="slack_post_message")
def post_message(channel_name: str, text: str) -> str:
    """Post a message to a specific Slack channel."""
    try:
        channels = client.conversations_list(types="public_channel")["channels"]
        channel_id = next((c["id"] for c in channels if c["name"] == channel_name), None)
        if not channel_id:
            return f"Channel {channel_name} not found."

        client.chat_postMessage(channel=channel_id, text=text)
        return f"Message posted to #{channel_name}"
    except SlackApiError as e:
        return f"Error posting message: {e.response['error']}"


@mcp.tool(name="slack_list_channel_messages")
def list_channel_messages(channel_name: str, limit: int = 10) -> str:
    """List the most recent messages from a public Slack channel. Automatically joins the channel if bot isn't in it."""
    try:
        # Get channel ID
        channels = client.conversations_list(types="public_channel")["channels"]
        channel = next((c for c in channels if c["name"] == channel_name), None)

        if not channel:
            return f"Channel '{channel_name}' not found."

        channel_id = channel["id"]

        # Try joining the channel if not a member
        if not channel.get("is_member", False):
            client.conversations_join(channel=channel_id)

        # Get messages
        response = client.conversations_history(channel=channel_id, limit=limit)
        messages = [{"text": msg.get("text", ""), "ts": msg["ts"]} for msg in response["messages"]]
        return json.dumps(messages)

    except SlackApiError as e:
        return f"Error retrieving messages: {e.response['error']}"


# ─── MCP Run ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
