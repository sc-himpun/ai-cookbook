# -*- coding: utf-8 -*-
import os
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
PORT = int(os.getenv("SLACK_MCP_PORT", "8003"))  # Port for the FastMCP server
REDIRECT_URI = os.getenv(
    "SLACK_REDIRECT_URI", f"https://localhost:{PORT}/slack/oauth/callback"
)
SLACK_SCOPES = os.getenv(
    "SLACK_SCOPES",
    "users:read,users:read.email,channels:read,groups:read,im:read,mpim:read,chat:write,channels:history",
)
SLACK_USER_SCOPES = os.getenv(
    "SLACK_USER_SCOPES",
    "users:read,users:read.email,channels:read,groups:read,im:read,mpim:read,chat:write,channels:history,search:read",
)

user_tokens: Dict[str, str] = {}
mcp = FastMCP("SlackToolkit")

# ─── Tools ─────────────────────────────────────────────────────────────────


def format_slack_response(success: bool, action: str, message: str, data=None) -> dict:
    """
    Standardizes Slack MCP tool responses.

    Args:
        success (bool): Whether the action succeeded.
        action (str): The action performed, e.g. "list_channels", "post_message".
        message (str): Human-readable message summarizing the result.
        data (dict | list, optional): Structured payload data.

    Returns:
        dict: Standardized response object.
    """
    return {
        "success": success,
        "action": action,
        "message": message,
        "data": data or {},
    }


def get_slack_client(metadata: Dict) -> Optional[WebClient]:
    """
    Create a Slack WebClient using an access token extracted from provided metadata.

    Parameters:
        metadata (Dict): A dictionary carrying authentication credentials. The
            function expects either:
              - metadata["slack"] -> a dict containing an "access_token" key, or
              - metadata itself to directly contain an "access_token" key.

            Example valid shapes:
                {"slack": {"access_token": "xoxb-..."}}
                {"access_token": "xoxb-..."}

    Returns:
        Optional[WebClient]: An instance of `slack_sdk.WebClient` configured
            with the provided token, or `None` if `metadata` is falsy.

    Raises:
        ValueError: If an access token cannot be found in the provided metadata.

    Notes:
        - This helper centralizes token lookup so other tools can call it with
          different metadata shapes (MCP metadata vs direct slack dict).
        - The returned client will authenticate requests with the token; make
          sure to never log or expose the token to untrusted sinks.

    Example:
        client = get_slack_client({"slack": {"access_token": "xoxb-..."}})
        response = client.conversations_list()
    """
    if not metadata:
        return None

    creds = metadata.get("slack", metadata)

    token = creds.get("access_token")
    if not token:
        raise ValueError("Slack access_token missing in metadata")

    return WebClient(token=token)


def get_slack_workspace_url(client) -> str:
    """
    Fetch the Slack workspace base URL for constructing links to channels and
    messages.

    Parameters:
        client: A `slack_sdk.WebClient` (or compatible object) with a valid
            access token configured. The function will call `auth_test` on the
            client to discover the workspace URL.

    Returns:
        str: The workspace URL returned by Slack (e.g. "https://acme.slack.com").
            If the API call fails for any reason, a sensible default
            ("https://slack.com") is returned to avoid crashing callers.

    Behavior and error handling:
        - Calls `client.auth_test()` and returns the `url` field when present.
        - Catches any exception and returns the global Slack domain as a
          fallback. This keeps downstream code that builds message links from
          failing when the token lacks `auth.test` permission.

    Security:
        - Do not use this function to validate token scopes; it is a convenience
          helper. Use Slack's APIs or your own permission checks when scope
          validation is required.
    """
    try:
        resp = client.auth_test()
        return resp.get("url", "https://slack.com")
    except Exception:
        return "https://slack.com"


@mcp.tool(name="slack_list_channels")
def slack_list_channels(metadata: Dict) -> dict:
    """
    List all channels accessible to the authenticated Slack user or bot.

    This tool retrieves the list of public and private channels that the
    authenticated entity (bot or user) has access to. It also includes
    direct URLs to each channel within the Slack workspace for convenience.

    Args:
        metadata (Dict):
            Metadata containing authentication information.
            Must include:
              - `slack.access_token` (str): OAuth access token for the bot or user.

    Returns:
        dict: A standardized response object with the following structure:
            - **success** (`bool`): Whether the operation succeeded.
            - **action** (`str`): Always `"list_channels"`.
            - **message** (`str`): A human-readable summary of the result.
            - **data** (`list`): A list of channel objects. Each includes:
                - `id` (`str`): The unique Slack channel ID (e.g., `C092ABCD`).
                  Don't show this to end-users (Not UI Safe).
                - `name` (`str`): The name of the channel (e.g., `general`). UI Safe
                - `is_private` (`bool`): Whether the channel is private.
                  Don't show this to end-users (Not UI Safe)
                - `num_members` (`int`): The number of members in the channel.UI Safe
                - `url` (`str`): Direct link to the channel in the Slack UI. UI Safe

    Example:
        ```python
        result = slack_list_channels(
            metadata={"slack": {"access_token": "xoxb-123..."}}
        )
        ```

        Example successful output:
        ```json
        {
            "success": true,
            "action": "list_channels",
            "message": "Fetched 5 channels.",
            "data": [
                {
                    "id": "C092YKCN6NB",
                    "name": "testmcp",
                    "is_private": false,
                    "num_members": 10,
                    "url": "https://scryaiworkspace.slack.com/archives/C092YKCN6NB"
                }
            ]
        }
        ```

    Notes:
        - The bot or user must have `channels:read` and/or `groups:read` scopes
          to list public and private channels respectively.
        - Private channels will only appear if the bot or user is a member.

    Raises:
        SlackApiError: If the Slack API call fails or authentication is invalid.
    """
    client = get_slack_client(metadata)
    action = "list_channels"

    try:
        response = client.conversations_list(types="public_channel,private_channel")
        channels = response.get("channels", [])

        # Include workspace URLs for direct access
        workspace_url = get_slack_workspace_url(client)
        for c in channels:
            c["url"] = f"{workspace_url}/archives/{c['id']}"

        return format_slack_response(
            success=True,
            action=action,
            message=f"Fetched {len(channels)} channels.",
            data=channels,
        )

    except SlackApiError as e:
        return format_slack_response(
            success=False,
            action=action,
            message=f"Slack API error: {str(e)}",
            data={},
        )


@mcp.tool(name="slack_post_message")
def slack_post_message(metadata: Dict, channel: str, text: str) -> dict:
    """
    Post a message to a specific Slack channel using the provided bot or user token.

    This tool allows authenticated Slack integrations to send text messages
    to any public or private channel the app has access to. It returns a standardized
    response with message metadata and a direct Slack URL to the posted message.

    Args:
        metadata (Dict):
            Metadata dictionary containing authentication and workspace information.
            Must include:
              - `slack.access_token` (str): OAuth token (usually `xoxb-...` for bot).
        channel (str):
            The ID of the channel where the message should be posted (e.g., `"C12345678"`).
            Use `slack_list_channels` to discover available channel IDs.
        text (str):
            The message text to send to the specified channel.

    Returns:
        dict: A standardized JSON-like structure with the following keys:
            - **success** (`bool`): Whether the operation succeeded.
            - **action** (`str`): The action name (`"post_message"`).
            - **message** (`str`): A human-readable message describing the result.
            - **data** (`dict`): A structured payload containing:
                - `ok` (`bool`): Slack API success flag.
                - `channel` (`str`): The target channel ID.
                - `ts` (`str`): Slack message timestamp.
                - `message_url` (`str`): Direct Slack link to the posted message.

    Example:
        ```python
        result = slack_post_message(
            metadata={"slack": {"access_token": "xoxb-123..."}},
            channel="C092YKCN6NB",
            text="Hello from MCP!"
        )
        ```

        Successful response:
        ```json
        {
            "success": true,
            "action": "post_message",
            "message": "Message posted successfully to channel C092YKCN6NB.",
            "data": {
                "ok": true,
                "channel": "C092YKCN6NB",
                "ts": "1738912345.000800",
                "message_url": "https://scryaiworkspace.slack.com/archives/C092YKCN6NB/p1738912345000800"
            }
        }
        ```

    Raises:
        SlackApiError: If the Slack API call fails (e.g., invalid auth, missing scope, not in channel).
    """
    client = get_slack_client(metadata)
    action = "post_message"

    try:
        response = client.chat_postMessage(channel=channel, text=text)

        ts = response.get("ts")
        workspace_url = get_slack_workspace_url(client)
        message_url = (
            f"{workspace_url}/archives/{channel}/p{ts.replace('.', '')}" if ts else None
        )

        data = {
            "ok": response.get("ok", False),
            "channel": channel,
            "ts": ts,
            "message_url": message_url,
        }

        return format_slack_response(
            success=True,
            action=action,
            message=f"Message posted successfully to channel {channel}.",
            data=data,
        )

    except SlackApiError as e:
        return format_slack_response(
            success=False,
            action=action,
            message=f"Slack API error: {str(e)}",
            data={},
        )


@mcp.tool(name="slack_search_messages")
def slack_search_messages(metadata: Dict, query: str, limit: int = 10) -> dict:
    """
    Search Slack messages that contain the given query string.

    This tool searches across all public channels (and private ones if the bot
    is a member) for messages matching the provided query. It returns structured
    information including message text, channel name, timestamp, and a direct
    Slack link for each match.

    Args:
        metadata (Dict):
            Metadata dictionary containing authentication details.
            Must include:
              - `slack.access_token` (str): OAuth token (typically `xoxp-...` for user or `xoxb-...` for bot).
        query (str):
            The text string to search for within messages.
        limit (int, optional):
            Maximum number of messages to return. Defaults to `10`.

    Returns:
        dict: A standardized response with the following fields:
            - **success** (`bool`): Whether the operation succeeded.
            - **action** (`str`): Always `"search_messages"`.
            - **message** (`str`): Human-readable status message.
            - **data** (`list`): A list of message objects, each containing:
                - `channel` (`str`): The Slack channel name.
                - `text` (`str`): The message text snippet.
                - `ts` (`str`): Slack message timestamp.
                - `message_url` (`str`): Direct URL to open the message in Slack.

    Example:
        ```python
        result = slack_search_messages(
            metadata={"slack": {"access_token": "xoxp-123..."}},
            query="project update",
            limit=5
        )
        ```

        Example successful output:
        ```json
        {
            "success": true,
            "action": "search_messages",
            "message": "Found 3 matching messages for query 'project update'.",
            "data": [
                {
                    "channel": "testmcp",
                    "text": "Project update: all MCP servers deployed.",
                    "ts": "1738912345.000800",
                    "message_url": "https://scryaiworkspace.slack.com/archives/C091XYZ/p1738912345000800"
                }
            ]
        }
        ```

    Raises:
        SlackApiError: If the API request fails due to missing scopes or invalid tokens.
    """
    client = get_slack_client(metadata)
    action = "search_messages"

    try:
        result = client.search_messages(query=query, count=limit)

        matches = result.get("messages", {}).get("matches", [])
        workspace_url = get_slack_workspace_url(client)

        messages = []
        for m in matches:
            channel_id = m["channel"]["id"]
            ts = m["ts"]
            message_url = (
                f"{workspace_url}/archives/{channel_id}/p{ts.replace('.', '')}"
            )
            messages.append(
                {
                    "channel": m["channel"]["name"],
                    "text": m["text"],
                    "ts": ts,
                    "message_url": message_url,
                }
            )

        return format_slack_response(
            success=True,
            action=action,
            message=f"Found {len(messages)} matching messages for query '{query}'.",
            data=messages,
        )

    except SlackApiError as e:
        return format_slack_response(
            success=False,
            action=action,
            message=f"Slack API error: {e.response.get('error', str(e))}",
            data={},
        )


@mcp.tool(name="slack_list_channel_messages")
def slack_list_channel_messages(
    metadata: Dict, channel_name: str, limit: int = 10
) -> dict:
    """
    List the most recent messages from a specified Slack channel.

    This tool fetches recent messages from a Slack channel (public or private,
    if the bot is a member). It also generates direct Slack URLs for each
    message to enable easy access in the Slack UI.

    Args:
        metadata (Dict):
            Metadata containing authentication information.
            Must include:
              - `slack.access_token` (str): OAuth token for Slack bot or user.
        channel_name (str):
            The name of the Slack channel (e.g., "general" or "testmcp").
        limit (int, optional):
            Maximum number of messages to fetch. Defaults to `10`.

    Returns:
        dict: A standardized response with the following fields:
            - **success** (`bool`): Whether the operation succeeded.
            - **action** (`str`): Always `"list_channel_messages"`.
            - **message** (`str`): Human-readable description of the result.
            - **data** (`list`): List of message objects, each containing:
                - `text` (`str`): Message text content.
                - `ts` (`str`): Slack timestamp identifier.
                - `message_url` (`str`): Direct link to the message in Slack.

    Example:
        ```python
        result = slack_list_channel_messages(
            metadata={"slack": {"access_token": "xoxb-123..."}},
            channel_name="testmcp",
            limit=5
        )
        ```

        Example successful output:
        ```json
        {
            "success": true,
            "action": "list_channel_messages",
            "message": "Fetched 5 most recent messages from channel 'testmcp'.",
            "data": [
                {
                    "text": "Hello, world!",
                    "ts": "1738912345.000800",
                    "message_url": "https://scryaiworkspace.slack.com/archives/C091XYZ/p1738912345000800"
                }
            ]
        }
        ```

    Raises:
        SlackApiError: If the Slack API call fails or permissions are missing.
    """
    action = "list_channel_messages"
    client = get_slack_client(metadata)

    try:
        # Step 1: Find channel by name
        channels = client.conversations_list(types="public_channel,private_channel")[
            "channels"
        ]
        channel = next((c for c in channels if c["name"] == channel_name), None)
        if not channel:
            return format_slack_response(
                success=False,
                action=action,
                message=f"Channel '{channel_name}' not found.",
                data={},
            )

        channel_id = channel["id"]

        # Step 2: Join if not a member
        if not channel.get("is_member", False):
            client.conversations_join(channel=channel_id)

        # Step 3: Retrieve recent messages
        response = client.conversations_history(channel=channel_id, limit=limit)
        workspace_url = get_slack_workspace_url(client)

        messages = []
        for m in response["messages"]:
            ts = m["ts"]
            message_url = (
                f"{workspace_url}/archives/{channel_id}/p{ts.replace('.', '')}"
            )
            messages.append(
                {"text": m.get("text", ""), "ts": ts, "message_url": message_url}
            )

        return format_slack_response(
            success=True,
            action=action,
            message=f"Fetched {len(messages)} most recent messages from channel '{channel_name}'.",
            data=messages,
        )

    except SlackApiError as e:
        return format_slack_response(
            success=False,
            action=action,
            message=f"Slack API error: {e.response.get('error', str(e))}",
            data={},
        )


async def slack_authorize(request: Request):
    """Redirect to Slack OAuth authorization page."""
    """
    Initiate the OAuth 2.0 authorization flow for Slack by redirecting the
    user-agent to Slack's authorization endpoint.

    The function constructs an authorization URL using the configured
    `CLIENT_ID`, `REDIRECT_URI`, and requested scopes and then returns a
    `starlette.responses.RedirectResponse` that instructs the browser to
    navigate to Slack's consent screen.

    Parameters:
        request (Request): The incoming Starlette request object. The handler
            does not currently inspect the request body or headers, but the
            request is included to match Starlette route handler signatures.

    Returns:
        RedirectResponse: A 307/302 redirect that sends the client to the
            Slack OAuth consent page.

    Notes:
        - `SLACK_SCOPES` may include both bot and user scopes. Slack expects
          `scope` for bot scopes and `user_scope` for user token scopes when
          using `oauth.v2` endpoints.
        - The function uses `REDIRECT_URI` to ensure Slack returns the
          authorization code to the correct callback route. Make sure the
          redirect URI is registered in your Slack app settings.

    Security and best practices:
        - Use PKCE or state parameters in production to prevent CSRF and
          authorization code interception attacks. This example keeps the
          flow simple for local demos but is not production-hardened.
    """
    params = {
        "client_id": CLIENT_ID,
        "scope": SLACK_SCOPES,  # Bot scopes
        "user_scope": SLACK_USER_SCOPES,  # User scopes
        "redirect_uri": REDIRECT_URI,
    }
    url = f"https://slack.com/oauth/v2/authorize?{urlencode(params)}"
    return RedirectResponse(url)


# ─── OAuth Callback ─────────────────────────────────────────────────────────
async def oauth_callback(request: Request):
    """
    OAuth callback endpoint to exchange Slack authorization code for tokens.

    This handler is intended to be registered as the `redirect_uri` for the
    Slack OAuth flow. After the user approves the app in Slack's consent
    screen, Slack calls this route with a `code` query parameter. This
    function exchanges that code for an access token using Slack's
    `oauth.v2.access` endpoint and returns a JSON summary containing the
    obtained tokens and team/user metadata.

    Parameters:
        request (Request): Starlette request containing query parameters from
            Slack. Expected query parameter:
              - `code` (str): The authorization code to exchange.

    Returns:
        JSONResponse: On success, returns a JSON object with keys:
            - `bot_access_token` (str): The bot token (xoxb-...)
            - `user_access_token` (str|None): The authed user's token (xoxp-...)
            - `team` (dict): Team information returned by Slack
            - `authed_user` (dict): Additional authed user metadata

        On failure, returns a 400 JSONResponse with an `error` field and the
        raw Slack response in `details` to aid debugging.

    Raises:
        None explicitly: network errors from `requests.post` will raise an
        exception which Starlette/ASGI server will surface as a 500 error.

    Security and deployment notes:
        - In production, validate the `state` parameter (and use PKCE where
          applicable) to protect against CSRF and code injection attacks.
        - Do not return raw tokens to end-users in production. Persist tokens
          securely (e.g., encrypted vault or database) and redirect the
          browser to a friendly success page instead of dumping JSON.
        - Ensure `CLIENT_SECRET` is never committed to source control; keep
          it in environment variables or a secrets manager.

    Example (successful JSON shape):
        {
            "bot_access_token": "xoxb-...",
            "user_access_token": "xoxp-...",
            "team": {"id": "T123", "name": "acme"},
            "authed_user": {"id": "U123", ...}
        }
    """

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

    bot_access_token = token_resp["access_token"]
    authed_user_info = token_resp.get("authed_user", {})
    user_access_token = authed_user_info.get("access_token")

    return JSONResponse(
        {
            "bot_access_token": bot_access_token,
            "user_access_token": user_access_token,
            "team": token_resp.get("team", {}),
            "authed_user": authed_user_info,
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
        # ssl_keyfile="localhost-key.pem",  #For authentication, it requires a valid certificate and key
        # ssl_certfile="localhost.pem"
    )
