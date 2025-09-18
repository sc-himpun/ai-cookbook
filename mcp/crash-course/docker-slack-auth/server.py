# -*- coding: utf-8 -*-
import os
import json
from typing import Dict
from dotenv import load_dotenv
import requests

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from urllib.parse import urlencode
from fastmcp import FastMCP
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from starlette.applications import Starlette

load_dotenv()

CLIENT_ID = os.getenv("SLACK_CLIENT_ID")
CLIENT_SECRET = os.getenv("SLACK_CLIENT_SECRET")
PORT = 8003  # Port for the FastMCP server
REDIRECT_URI = os.getenv("SLACK_REDIRECT_URI", f"https://localhost:{PORT}/slack/oauth/callback")
SLACK_SCOPES = "channels:read,channels:history,chat:write"

print(REDIRECT_URI)
user_tokens: Dict[str, str] = {}  # email -> access token

mcp = FastMCP("SlackToolkit-OAuth")

# ─── Tools ─────────────────────────────────────────────────────────────────

@mcp.tool(name="slack_post_message")
def post_message(channel_name: str, text: str, user_email: str) -> str:
    """Post a message to a specific Slack channel."""
    if user_email not in user_tokens:
        return "❌ Not authorized. Please log in first."

    client = WebClient(token=user_tokens[user_email])
    try:
        channels = client.conversations_list(types="public_channel")["channels"]
        channel_id = next((c["id"] for c in channels if c["name"] == channel_name), None)
        if not channel_id:
            return f"Channel {channel_name} not found."

        client.chat_postMessage(channel=channel_id, text=text)
        return f"✅ Message posted to #{channel_name}"
    except SlackApiError as e:
        return f"❌ Error posting message: {e.response['error']}"

@mcp.tool(name="slack_list_channels")
def list_channels(user_email: str) -> str:
    """List all public Slack channels."""
    if user_email not in user_tokens:
        return "❌ Not authorized. Please log in first."
    client = WebClient(token=user_tokens[user_email])
    try:
        result = client.conversations_list(types="public_channel")
        channels = [{"name": c["name"], "id": c["id"]} for c in result["channels"]]
        return json.dumps(channels)
    except SlackApiError as e:
        return f"❌ Error listing channels: {e.response['error']}"

@mcp.tool(name="slack_search_messages")
def search_messages(query: str, limit: int, user_email: str) -> str:
    """Search Slack messages containing the given query string."""
    if user_email not in user_tokens:
        return "❌ Not authorized. Please log in first."
    client = WebClient(token=user_tokens[user_email])
    try:
        result = client.search_messages(query=query, count=limit)
        messages = [
            {
                "channel": m["channel"]["name"],
                "text": m["text"],
                "ts": m["ts"]
            } for m in result["messages"]["matches"]
        ]
        return json.dumps(messages)
    except SlackApiError as e:
        return f"❌ Error searching messages: {e.response['error']}"

@mcp.tool(name="slack_list_channel_messages")
def list_channel_messages(channel_name: str, limit: int, user_email: str) -> str:
    """List the most recent messages from a public Slack channel."""
    if user_email not in user_tokens:
        return "❌ Not authorized. Please log in first."
    client = WebClient(token=user_tokens[user_email])
    try:
        channels = client.conversations_list(types="public_channel")["channels"]
        channel = next((c for c in channels if c["name"] == channel_name), None)
        if not channel:
            return f"Channel '{channel_name}' not found."

        channel_id = channel["id"]
        if not channel.get("is_member", False):
            client.conversations_join(channel=channel_id)

        response = client.conversations_history(channel=channel_id, limit=limit)
        messages = [{"text": m.get("text", ""), "ts": m["ts"]} for m in response["messages"]]
        return json.dumps(messages)
    except SlackApiError as e:
        return f"❌ Error retrieving messages: {e.response['error']}"

@mcp.tool(name="slack_get_authorization_url")
def slack_get_authorization_url() -> str:
    """Returns the Slack OAuth authorization URL."""
    params = {
        "client_id": CLIENT_ID,
        "scope": SLACK_SCOPES,
        "redirect_uri": REDIRECT_URI,
    }
    return f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"


@mcp.tool(name="slack_list_authorized_users")
def list_users() -> str:
    if not user_tokens:
        return "No users authorized."
    return "\n".join(user_tokens.keys())


async def slack_authorize(request: Request):
    """Redirect to Slack OAuth authorization page."""
    params = {
        "client_id": CLIENT_ID,
        "scope": SLACK_SCOPES,
        "redirect_uri": REDIRECT_URI,
    }
    url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    return RedirectResponse(url)


# ─── OAuth Callback ─────────────────────────────────────────────────────────
async def oauth_callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    token_resp = requests.post("https://slack.com/api/oauth.v2.access", data=data).json()
    if not token_resp.get("ok"):
        return JSONResponse({"error": "OAuth failed", "details": token_resp}, status_code=400)

    access_token = token_resp["access_token"]
    # refresh_token = token_resp["refresh_token"]
    team_name = token_resp.get("team", {}).get("name", "unknown")
    user_id = token_resp.get("authed_user", {}).get("id", "unknown")

    user_tokens[user_id] = access_token  # Store using Slack user ID

    # return JSONResponse({"message": f"Authenticated for team '{team_name}' as user {user_id}"})
    return JSONResponse({
            # "message": f"Authenticated as {email}",
            # "email": email,
            "access_token": access_token,
            # "refresh_token": refresh_token
        })


# ─── Starlette App ─────────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")
routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", slack_authorize),
    Route("/slack/oauth/callback", oauth_callback),
]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

# ─── Run the Server ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        ssl_keyfile="localhost-key.pem",
        ssl_certfile="localhost.pem"
    )
