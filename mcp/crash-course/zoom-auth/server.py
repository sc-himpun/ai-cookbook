# -*- coding: utf-8 -*-
import os
import json
import base64
import requests
from datetime import datetime, timedelta
from typing import Dict, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request


# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
PORT = int(os.getenv("ZOOM_MCP_PORT", "8019"))
REDIRECT_URI = os.getenv(
    "ZOOM_MCP_REDIRECT_URI",
    f"http://localhost:{PORT}/oauth2callback"
)
SCOPES = os.getenv(
    "ZOOM_SCOPES",
    "user:read meeting:read meeting:write chat_message:write:admin chat_channel:read:admin"
).split()
print(
    f"🔑 Using CLIENT_ID={CLIENT_ID}, REDIRECT_URI={REDIRECT_URI}, SCOPES={SCOPES}"
)
mcp = FastMCP("zoom-mcp")


# ─── Token Helpers ───────────────────────────────────────────────────────────
def refresh_zoom_token(refresh_token: str) -> Optional[Dict]:
    """
    Refreshes the Zoom OAuth access token using the provided refresh token.
    Args:
        refresh_token (str): The refresh token to use for obtaining a new access token.
    Returns:
        Optional[Dict]: The new token dictionary if successful, None otherwise.
    """
    headers = {
        "Authorization": f"Basic {base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token
    }
    resp = requests.post("https://zoom.us/oauth/token",
                         headers=headers, data=data)
    if resp.status_code == 200:
        return resp.json()
    print(f"[refresh_zoom_token] Failed: {resp.text}")
    return None


def get_zoom_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Extracts Zoom credentials from the provided metadata and refreshes the access token if expired.
    Args:
        metadata (Optional[Dict]): Metadata containing Zoom credentials.
    Returns:
        Optional[Dict]: Dictionary with email, access_token, and refresh_token if valid, None otherwise.
    """
    if not metadata:
        return None
    creds = metadata.get("zoom", metadata)
    email = creds.get("email")
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")

    if not email or not access_token:
        return None

    # Check validity by hitting /users/me
    resp = requests.get(
        "https://api.zoom.us/v2/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if resp.status_code == 200:
        return {"email": email, "access_token": access_token, "refresh_token": refresh_token}

    if refresh_token:
        print("🔁 Access token expired, refreshing...")
        new_tokens = refresh_zoom_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            return {
                "email": email,
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens.get("refresh_token", refresh_token)
            }
    return None


def get_auth_headers(metadata: Dict) -> Dict[str, str]:
    """
    Get authorization headers for Zoom API requests.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.

    Returns:
        Dict[str, str]: Headers for Zoom API requests.
    """
    creds = get_zoom_creds(metadata)
    if not creds:
        raise Exception("❌ Zoom credentials not found or invalid in metadata")
    return {
        "Authorization": f"Bearer {creds['access_token']}",
        "Content-Type": "application/json"
    }

# ─── Tools ───────────────────────────────────────────────────────────────────


@mcp.tool(name="zoom_list_chat_channels")
def list_chat_channels(metadata: Dict) -> str:
    """
    List chat channels for the authenticated Zoom user.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.

    Returns:
        str: JSON response or error message.
    """
    url = "https://api.zoom.us/v2/chat/users/me/channels"
    r = requests.get(url, headers=get_auth_headers(metadata))
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_send_chat_message")
def send_chat_message(metadata: Dict, to_channel: str, message: str) -> str:
    """
    Send a chat message to a Zoom channel.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        to_channel (str): Channel ID to send the message to.
        message (str): Message content.

    Returns:
        str: JSON response or error message.
    """
    url = "https://api.zoom.us/v2/chat/users/me/messages"
    payload = {"message": message, "to_channel": to_channel}
    r = requests.post(url, headers=get_auth_headers(metadata), json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_list_meetings")
def list_meetings(metadata: Dict, user_id: str = "me") -> str:
    """
    List meetings for a Zoom user.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        user_id (str, optional): Zoom user ID. Defaults to "me".

    Returns:
        str: JSON response or error message.
    """
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    r = requests.get(url, headers=get_auth_headers(metadata))
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_get_current_time")
def get_current_utc_time(metadata: Dict = {}) -> str:
    """
    Get the current UTC time and one hour later.

    Args:
        metadata (Dict, optional): Metadata (not used).

    Returns:
        str: JSON with current UTC and one hour later.
    """
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)
    return json.dumps({
        "current_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "one_hour_later": one_hour_later.strftime("%Y-%m-%dT%H:%M:%SZ")
    })


@mcp.tool(name="zoom_get_meeting_transcript")
def get_meeting_transcript(metadata: Dict, meeting_id: str) -> str:
    """
    Get transcript files for a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.

    Returns:
        str: JSON with transcript files or error message.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    r = requests.get(url, headers=get_auth_headers(metadata))
    if not r.ok:
        return f"Error: {r.status_code} {r.text}"
    data = r.json()
    transcript_files = [
        {"file_type": f["file_type"], "download_url": f["download_url"]}
        for f in data.get("recording_files", [])
        if f["file_type"] in ("TRANSCRIPT", "TRANSCRIPT_VTT")
    ]
    return json.dumps(transcript_files or "No transcript files found.")


@mcp.tool(name="zoom_create_meeting")
def create_meeting(metadata: Dict, user_id: str, topic: str, start_time: str, duration: int) -> str:
    """
    Create a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        user_id (str): Zoom user ID.
        topic (str): Meeting topic.
        start_time (str): Meeting start time (ISO format).
        duration (int): Meeting duration in minutes.

    Returns:
        str: JSON response or error message.
    """
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    payload = {
        "topic": topic,
        "type": 2,
        "start_time": start_time,
        "duration": duration,
        "timezone": "UTC",
        "settings": {"join_before_host": True, "waiting_room": False}
    }
    r = requests.post(url, headers=get_auth_headers(metadata), json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_get_meeting_recordings")
def get_meeting_recordings(metadata: Dict, meeting_id: str) -> str:
    """
    Get recordings for a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.

    Returns:
        str: JSON response or error message.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    r = requests.get(url, headers=get_auth_headers(metadata))
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_reschedule_meeting")
def reschedule_meeting(metadata: Dict, meeting_id: str, new_start_time: str, new_duration: int) -> str:
    """
    Reschedule a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.
        new_start_time (str): New start time (ISO format).
        new_duration (int): New duration in minutes.

    Returns:
        str: JSON response or error message.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    payload = {"start_time": new_start_time,
               "duration": new_duration, "timezone": "UTC"}
    r = requests.patch(url, headers=get_auth_headers(metadata), json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_cancel_meeting")
def cancel_meeting(metadata: Dict, meeting_id: str) -> str:
    """
    Cancel a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.

    Returns:
        str: Success message or error message.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    r = requests.delete(url, headers=get_auth_headers(metadata))
    if r.status_code == 204:
        return json.dumps({"status": "success", "message": f"Meeting {meeting_id} cancelled."})
    return f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_list_recent_meetings")
def list_recent_meetings(metadata: Dict, user_id: str = "me") -> str:
    """
    List recent scheduled meetings for a Zoom user.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        user_id (str, optional): Zoom user ID. Defaults to "me".

    Returns:
        str: JSON response or error message.
    """
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    params = {"type": "scheduled", "page_size": 30}
    r = requests.get(url, headers=get_auth_headers(metadata), params=params)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


def build_auth_url() -> str:
    """
    Build the Zoom OAuth authorization URL.

    Returns:
        str: OAuth authorization URL.
    """
    return (
        f"https://zoom.us/oauth/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
    )


# ─── OAuth Handlers ──────────────────────────────────────────────────────────
async def authorize(request: Request):
    """
    Redirect user to Zoom OAuth authorization page.

    Args:
        request (Request): Starlette request object.

    Returns:
        RedirectResponse: Redirect to Zoom OAuth page.
    """
    url = build_auth_url()
    return RedirectResponse(url)


async def oauth2callback(request: Request):
    """
    Handle OAuth2 callback from Zoom and exchange code for tokens.

    Args:
        request (Request): Starlette request object.

    Returns:
        JSONResponse: Authenticated user info or error.
    """
    code = request.query_params.get("code")
    headers = {
        "Authorization": f"Basic {base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "authorization_code",
            "code": code, "redirect_uri": REDIRECT_URI}
    token_resp = requests.post(
        "https://zoom.us/oauth/token", headers=headers, data=data).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")
    if not access_token:
        return JSONResponse({"error": "OAuth failed", "details": token_resp}, status_code=400)

    userinfo = requests.get("https://api.zoom.us/v2/users/me",
                            headers={"Authorization": f"Bearer {access_token}"}).json()
    email = userinfo.get("email")
    return JSONResponse({"message": f"✅ Authenticated as {email}", "email": email,
                         "access_token": access_token, "refresh_token": refresh_token})

# ─── App Setup ───────────────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")
routes = [Mount("/mcp-server", app=mcp_app), Route("/authorize",
                                                   authorize), Route("/oauth2callback", oauth2callback)]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
