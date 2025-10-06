# -*- coding: utf-8 -*-
import os
import json
from typing import Dict, Optional
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
REDIRECT_URI = os.getenv(
    "SLACK_REDIRECT_URI", f"https://localhost:{PORT}/slack/oauth/callback"
)
SLACK_SCOPES = "channels:read,channels:history,chat:write"

print(REDIRECT_URI)
user_tokens: Dict[str, str] = {}  # email -> access token

mcp = FastMCP("SlackToolkit-OAuth")

# ─── Tools ─────────────────────────────────────────────────────────────────


def get_slack_client(metadata: Dict) -> Optional[WebClient]:
    """
    Create a Slack WebClient using access_token from metadata.
    """
    if not metadata:
        return None

    creds = metadata.get("slack", metadata)

    token = creds.get("access_token")
    if not token:
        raise ValueError("Slack access_token missing in metadata")

    return WebClient(token=token)


@mcp.tool(name="slack_list_channels")
def slack_list_channels(metadata: Dict) -> dict:
    """
    List channels the authenticated Slack user has access to.
    """
    client = get_slack_client(metadata)
    try:
        response = client.conversations_list(types="public_channel,private_channel")
        return {"channels": response["channels"]}
    except SlackApiError as e:
        return {"error": str(e)}


@mcp.tool(name="slack_post_message")
def slack_post_message(metadata: Dict, channel: str, text: str) -> dict:
    """
    Post a message to a specific Slack channel.

    Args:
        metadata (Dict): Must contain slack.access_token
        channel (str): Channel ID (e.g. C12345678)
        text (str): Message text
    """
    client = get_slack_client(metadata)
    try:
        response = client.chat_postMessage(channel=channel, text=text)
        return {"ok": response["ok"], "ts": response["ts"]}
    except SlackApiError as e:
        return {"error": str(e)}


@mcp.tool(name="slack_get_current_user")
def slack_get_current_user(metadata: Dict) -> dict:
    """
    Fetch the current authenticated Slack user’s profile info.
    Useful to resolve email or user ID linked to the access_token.
    """
    client = get_slack_client(metadata)
    try:
        auth_test = client.auth_test()
        user_id = auth_test["user_id"]
        team_id = auth_test["team_id"]

        user_info = client.users_info(user=user_id)
        profile = user_info.get("user", {}).get("profile", {})

        return {
            "user_id": user_id,
            "team_id": team_id,
            "real_name": profile.get("real_name"),
            "display_name": profile.get("display_name"),
            "email": profile.get("email"),
        }
    except SlackApiError as e:
        return {"error": str(e)}


@mcp.tool(name="slack_search_messages")
def slack_search_messages(metadata: Dict, query: str, limit: int = 10) -> dict:
    """
    Search Slack messages containing the given query string.

    Args:
        metadata (Dict): Must contain slack.access_token
        query (str): The search query
        limit (int): Max number of results
    """
    client = get_slack_client(metadata)
    try:
        result = client.search_messages(query=query, count=limit)
        messages = [
            {"channel": m["channel"]["name"], "text": m["text"], "ts": m["ts"]}
            for m in result["messages"]["matches"]
        ]
        return {"messages": messages}
    except SlackApiError as e:
        return {"error": f"Error searching messages: {e.response['error']}"}


@mcp.tool(name="slack_list_channel_messages")
def slack_list_channel_messages(
    metadata: Dict, channel_name: str, limit: int = 10
) -> dict:
    """
    List the most recent messages from a public Slack channel.

    Args:
        metadata (Dict): Must contain slack.access_token
        channel_name (str): The name of the channel (not ID)
        limit (int): Max number of messages to fetch
    """
    client = get_slack_client(metadata)
    try:
        # find channel by name
        channels = client.conversations_list(types="public_channel,private_channel")[
            "channels"
        ]
        channel = next((c for c in channels if c["name"] == channel_name), None)
        if not channel:
            return {"error": f"Channel '{channel_name}' not found."}

        channel_id = channel["id"]

        # join if not a member
        if not channel.get("is_member", False):
            client.conversations_join(channel=channel_id)

        response = client.conversations_history(channel=channel_id, limit=limit)
        messages = [
            {"text": m.get("text", ""), "ts": m["ts"]} for m in response["messages"]
        ]
        return {"messages": messages}
    except SlackApiError as e:
        return {"error": f"Error retrieving messages: {e.response['error']}"}


@mcp.tool(name="slack_get_authorization_url")
def slack_get_authorization_url() -> str:
    """Returns the Slack OAuth authorization URL."""
    params = {
        "client_id": CLIENT_ID,
        "scope": SLACK_SCOPES,
        "redirect_uri": REDIRECT_URI,
    }
    return f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"


# @mcp.tool(name="slack_list_authorized_users")
# def list_users() -> str:
#     if not user_tokens:
#         return "No users authorized."
#     return "\n".join(user_tokens.keys())


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
        "redirect_uri": REDIRECT_URI,
    }
    token_resp = requests.post(
        "https://slack.com/api/oauth.v2.access", data=data
    ).json()
    if not token_resp.get("ok"):
        return JSONResponse(
            {"error": "OAuth failed", "details": token_resp}, status_code=400
        )

    access_token = token_resp["access_token"]
    # refresh_token = token_resp["refresh_token"]
    team_name = token_resp.get("team", {}).get("name", "unknown")
    user_id = token_resp.get("authed_user", {}).get("id", "unknown")

    user_tokens[user_id] = access_token  # Store using Slack user ID

    # return JSONResponse({"message": f"Authenticated for team '{team_name}' as user {user_id}"})
    return JSONResponse(
        {
            # "message": f"Authenticated as {email}",
            # "email": email,
            "access_token": access_token,
            # "refresh_token": refresh_token
        }
    )


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
        # ssl_keyfile="localhost-key.pem",
        # ssl_certfile="localhost.pem"
    )
