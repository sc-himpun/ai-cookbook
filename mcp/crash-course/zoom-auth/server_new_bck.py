# -*- coding: utf-8 -*-
import base64
import json
import os
from datetime import datetime, timedelta
from typing import Dict, Optional
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Mount, Route

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
PORT = int(os.getenv("ZOOM_MCP_PORT", "8019"))
REDIRECT_URI = os.getenv(
    "ZOOM_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback"
)
SCOPES = os.getenv(
    "ZOOM_SCOPES",
    (
        "user:read meeting:read meeting:write "
        "chat_message:write:admin chat_channel:read:admin"
    ),
).split()
print(
    f"🔑 Using CLIENT_ID={CLIENT_ID}, REDIRECT_URI={REDIRECT_URI}, "
    f"SCOPES={SCOPES}"
)
mcp = FastMCP("zoom-mcp")


# ─── Token Helpers ───────────────────────────────────────────────────────────
def make_response(success: bool, action: str, message: str, data=None):
    """
    Standardized response format for all MCP tools.

    Args:
        success (bool): True if the operation succeeded.
        action (str): Name of the action/tool executed.
        message (str): Short status message.
        data (Any, optional): Additional response data.

    Returns:
        dict: Structured response dictionary.
    """
    return {
        "success": success,
        "action": action,
        "message": message,
        "data": data if data is not None else {},
    }


def safe_json(resp: requests.Response) -> Dict:
    """
    Safely parse a JSON response.

    Args:
        resp (requests.Response): The HTTP response object to parse.

    Returns:
        Dict: Parsed JSON dictionary if valid, otherwise an empty dict.

    This helper only tries to parse JSON when:
        - Status code is not 204
        - Response body is non-empty
        - Content-Type header indicates JSON

    Otherwise it returns an empty dict.
    """
    if resp.status_code == 204:
        return {}
    text = resp.text or ""
    if not text.strip():
        return {}
    content_type = resp.headers.get("Content-Type", "")
    if "json" not in content_type.lower():
        return {}
    try:
        return resp.json()
    except (requests.exceptions.JSONDecodeError, ValueError):
        return {}


def refresh_zoom_token(refresh_token: str) -> Optional[Dict]:
    """
    Refreshes the Zoom OAuth access token using the provided refresh token.
    Args:
        refresh_token (str): The refresh token to use for obtaining a new access token.
    Returns:
        Optional[Dict]: The new token dictionary if successful, None otherwise.
    """
    headers = {
        "Authorization": (
            "Basic "
            + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        ),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    resp = requests.post("https://zoom.us/oauth/token", headers=headers, data=data)
    if resp.status_code == 200:
        return resp.json()
    print(f"[refresh_zoom_token] Failed: {resp.text}")
    return None


def get_zoom_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Extracts Zoom credentials from the provided metadata and refreshes
    the access token if expired.
    Args:
        metadata (Optional[Dict]): Metadata containing Zoom credentials.
    Returns:
    Optional[Dict]: Dictionary with email, access_token, and refresh_token
    if valid, None otherwise.
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
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if resp.status_code == 200:
        return {
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

    if refresh_token:
        print("🔁 Access token expired, refreshing...")
        new_tokens = refresh_zoom_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            return {
                "email": email,
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens.get(
                    "refresh_token", refresh_token
                ),
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
        "Content-Type": "application/json",
    }


# ─── Tools ───────────────────────────────────────────────────────────────────
@mcp.tool(name="zoom_list_chat_channels")
def list_chat_channels(metadata: Dict) -> Dict:
    """
    List chat channels for the authenticated Zoom user.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.

    Returns:
    dict: Standardized response containing the list of chat channels
    or error details.
    """
    url = "https://api.zoom.us/v2/chat/users/me/channels"
    r = requests.get(url, headers=get_auth_headers(metadata))
    if not r.ok:
        return make_response(
            False, "zoom_list_chat_channels", f"❌ {r.status_code} {r.text}"
        )
    return make_response(
        True, "zoom_list_chat_channels", "✅ Retrieved chat channels.", r.json()
    )


@mcp.tool(name="zoom_send_chat_message")
def send_chat_message(metadata: Dict, to_channel: str, message: str) -> Dict:
    """
    Send a chat message to a Zoom channel.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        to_channel (str): Channel ID to send the message to.
        message (str): Message content.

    Returns:
        dict: Standardized response with the API result or error information.
    """
    url = "https://api.zoom.us/v2/chat/users/me/messages"
    payload = {"message": message, "to_channel": to_channel}
    r = requests.post(url, headers=get_auth_headers(metadata), json=payload)
    if not r.ok:
        return make_response(
            False, "zoom_send_chat_message", f"❌ {r.status_code} {r.text}"
        )
    return make_response(
        True,
        "zoom_send_chat_message",
        "✅ Message sent successfully.",
        r.json(),
    )


@mcp.tool(name="zoom_list_meetings")
def list_meetings(metadata: Dict, user_id: str = "me") -> Dict:
    """
    List meetings for a Zoom user.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        user_id (str, optional): Zoom user ID. Defaults to "me".

    Returns:
        dict: Standardized response containing meeting details or error info.
    """
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    r = requests.get(url, headers=get_auth_headers(metadata))
    if not r.ok:
        return make_response(
            False, "zoom_list_meetings", f"❌ {r.status_code} {r.text}"
        )
    data = r.json()
    meetings = data.get("meetings", [])

    def to_utc(start_time: str, tz: str | None) -> str:
        """Convert a Zoom meeting start_time + timezone to UTC ISO string.

        Adds a Z suffix.
        """
        if not start_time:
            return ""
        # Already UTC if ends with Z
        if start_time.endswith("Z"):
            return start_time
        try:
            dt = datetime.fromisoformat(start_time)
            if tz:
                try:
                    dt = dt.replace(tzinfo=ZoneInfo(tz))
                except Exception:
                    # Fallback: treat as UTC if timezone invalid
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(ZoneInfo("UTC")).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except Exception:
            return start_time  # return original if parsing fails

    for m in meetings:
        original = m.get("start_time")
        tz = m.get("timezone") or data.get("timezone")
        m["start_time_original"] = original
        m["start_time_utc"] = to_utc(original, tz)

    data["meetings"] = meetings
    return make_response(
        True,
        "zoom_list_meetings",
        "✅ Meetings list with UTC conversion (start_time_utc added).",
        data,
    )


@mcp.tool(name="zoom_get_current_time")
def get_current_utc_time(metadata: Dict = {}) -> Dict:
    """
    Get the current UTC time and one hour later.

    Args:
        metadata (Dict, optional): Metadata (not used).

    Returns:
    dict: Standardized response containing the current UTC time and
    one hour later.
    """
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)
    data = {
        "current_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "one_hour_later": one_hour_later.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    return make_response(
        True,
        "zoom_get_current_time",
        "✅ Fetched current UTC and one hour later.",
        data,
    )


@mcp.tool(name="zoom_get_meeting_transcript")
def get_meeting_transcript(metadata: Dict, meeting_id: str) -> Dict:
    """
    Get transcript files for a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.

    Returns:
    dict: Standardized response with transcript URLs or a message if
    none found.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    r = requests.get(url, headers=get_auth_headers(metadata))
    if not r.ok:
        return make_response(
            False,
            "zoom_get_meeting_transcript",
            f"Error: {r.status_code} {r.text}",
        )
    data = r.json()
    transcript_files = [
        {"file_type": f["file_type"], "download_url": f["download_url"]}
        for f in data.get("recording_files", [])
        if f["file_type"] in ("TRANSCRIPT", "TRANSCRIPT_VTT")
    ]
    if not transcript_files:
        return make_response(
            True,
            "zoom_get_meeting_transcript",
            "ℹ️ No transcript files found.",
            [],
        )
    return make_response(
        True,
        "zoom_get_meeting_transcript",
        "✅ Retrieved transcript files.",
        transcript_files,
    )


@mcp.tool(name="zoom_create_meeting")
def create_meeting(
    metadata: Dict, user_id: str, topic: str, start_time: str, duration: int
) -> Dict:
    """
    Create a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        user_id (str): Zoom user ID.
        topic (str): Meeting topic.
        start_time (str): Meeting start time (ISO format).
        duration (int): Meeting duration in minutes.

    Returns:
    dict: Standardized response containing the created meeting details
    or error info.
    """
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    payload = {
        "topic": topic,
        "type": 2,
        "start_time": start_time,
        "duration": duration,
        "timezone": "UTC",
        "settings": {"join_before_host": True, "waiting_room": False},
    }
    r = requests.post(url, headers=get_auth_headers(metadata), json=payload)
    if not r.ok:
        return make_response(
            False, "zoom_create_meeting", f"❌ {r.status_code} {r.text}"
        )
    return make_response(
        True,
        "zoom_create_meeting",
        "✅ Meeting created successfully.",
        r.json(),
    )


@mcp.tool(name="zoom_get_meeting_recordings")
def get_meeting_recordings(metadata: Dict, meeting_id: str) -> Dict:
    """
    Get recordings for a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.

    Returns:
    dict: Standardized response containing recording metadata or
    error info.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    r = requests.get(url, headers=get_auth_headers(metadata))
    if not r.ok:
        return make_response(
            False, "zoom_get_meeting_recordings", f"❌ {r.status_code} {r.text}"
        )
    return make_response(
        True,
        "zoom_get_meeting_recordings",
        "✅ Retrieved meeting recordings.",
        r.json(),
    )


@mcp.tool(name="zoom_reschedule_meeting")
def reschedule_meeting(
    metadata: Dict, meeting_id: str, new_start_time: str, new_duration: int
) -> Dict:
    """
    Reschedule a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.
        new_start_time (str): New start time (ISO format).
        new_duration (int): New duration in minutes.

    Returns:
        dict: Standardized response with updated meeting details or error info.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    payload = {
        "start_time": new_start_time,
        "duration": new_duration,
        "timezone": "UTC",
    }
    r = requests.patch(url, headers=get_auth_headers(metadata), json=payload)
    if not r.ok:
        return make_response(
            False, "zoom_reschedule_meeting", f"❌ {r.status_code} {r.text}"
        )
    data = safe_json(r)
    return make_response(
        True,
        "zoom_reschedule_meeting",
        "✅ Meeting rescheduled successfully.",
        data,
    )


@mcp.tool(name="zoom_cancel_meeting")
def cancel_meeting(metadata: Dict, meeting_id: str) -> Dict:
    """
    Cancel a Zoom meeting.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        meeting_id (str): Zoom meeting ID.

    Returns:
        dict: Standardized response confirming cancellation or error message.
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    r = requests.delete(url, headers=get_auth_headers(metadata))
    if r.status_code == 204:
        return make_response(
            True, "zoom_cancel_meeting", f"✅ Meeting {meeting_id} cancelled."
        )
    return make_response(False, "zoom_cancel_meeting", f"❌ {r.status_code} {r.text}")


@mcp.tool(name="zoom_list_recent_meetings")
def list_recent_meetings(metadata: Dict, user_id: str = "me") -> Dict:
    """
    List recent scheduled meetings for a Zoom user.

    Args:
        metadata (Dict): Metadata containing Zoom credentials.
        user_id (str, optional): Zoom user ID. Defaults to "me".

    Returns:
        dict: Standardized response containing recent meetings or error info.
    """
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    params = {"type": "scheduled", "page_size": 30}
    r = requests.get(url, headers=get_auth_headers(metadata), params=params)
    if not r.ok:
        return make_response(
            False, "zoom_list_recent_meetings", f"❌ {r.status_code} {r.text}"
        )
    data = r.json()
    meetings = data.get("meetings", [])

    def to_utc(start_time: str, tz: str | None) -> str:
        if not start_time:
            return ""
        if start_time.endswith("Z"):
            return start_time
        try:
            dt = datetime.fromisoformat(start_time)
            if tz:
                try:
                    dt = dt.replace(tzinfo=ZoneInfo(tz))
                except Exception:
                    dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            elif dt.tzinfo is None:
                dt = dt.replace(tzinfo=ZoneInfo("UTC"))
            return dt.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            return start_time

    for m in meetings:
        original = m.get("start_time")
        tz = m.get("timezone") or data.get("timezone")
        m["start_time_original"] = original
        m["start_time_utc"] = to_utc(original, tz)

    data["meetings"] = meetings
    return make_response(
        True,
        "zoom_list_recent_meetings",
        "✅ Retrieved recent scheduled meetings with UTC conversion (start_time_utc added).",
        data,
    )


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
    print()
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
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    token_resp = requests.post(
        "https://zoom.us/oauth/token", headers=headers, data=data
    ).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")
    if not access_token:
        return JSONResponse(
            {"error": "OAuth failed", "details": token_resp}, status_code=400
        )

    userinfo = requests.get(
        "https://api.zoom.us/v2/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    email = userinfo.get("email")
    return JSONResponse(
        {
            "message": f"✅ Authenticated as {email}",
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }
    )


# ─── App Setup ───────────────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")
routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
