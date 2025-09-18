# -*- coding: utf-8 -*-
import os
import json
import requests
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import base64
from datetime import datetime, timedelta
import time

# ─── Load Environment ────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
ACCOUNT_ID = os.getenv("ZOOM_ACCOUNT_ID")
TOKEN_FILE = "zoom_token.json"

# ─── Token Management ────────────────────────────────────────────────────────
def get_zoom_token():
    now = int(time.time())

    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            data = json.load(f)
            if "access_token" in data and "expires_at" in data:
                if data["expires_at"] > now + 30:  # add buffer of 30 seconds
                    return data["access_token"]

    # Request new token
    url = "https://zoom.us/oauth/token"
    auth_str = f"{CLIENT_ID}:{CLIENT_SECRET}"
    b64_auth = base64.b64encode(auth_str.encode()).decode()

    headers = {
        "Authorization": f"Basic {b64_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "account_credentials",
        "account_id": ACCOUNT_ID
    }

    response = requests.post(url, headers=headers, data=data)
    if not response.ok:
        raise Exception(f"Failed to get token: {response.status_code} - {response.text}")

    token_data = response.json()
    token_data["expires_at"] = now + token_data.get("expires_in", 3600)

    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)

    return token_data["access_token"]




def get_auth_headers():
    token = get_zoom_token()
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

# ─── MCP Server ──────────────────────────────────────────────────────────────
mcp = FastMCP(name="ZoomToolkit", host="0.0.0.0", port=8055)

# ─── Tool: List Chat Channels ────────────────────────────────────────────────
@mcp.tool(name="zoom_list_chat_channels")
def list_chat_channels() -> str:
    """List all Zoom chat channels."""
    url = "https://api.zoom.us/v2/chat/users/me/channels"
    headers = get_auth_headers()
    r = requests.get(url, headers=headers)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"

# ─── Tool: Send Chat Message ─────────────────────────────────────────────────
@mcp.tool(name="zoom_send_chat_message")
def send_chat_message(to_channel: str, message: str) -> str:
    """Send a message to a Zoom chat channel (channel ID required)."""
    url = "https://api.zoom.us/v2/chat/users/me/messages"
    headers = get_auth_headers()
    payload = {
        "message": message,
        "to_channel": to_channel
    }
    r = requests.post(url, headers=headers, json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"

# ─── Tool: List Meetings ─────────────────────────────────────────────────────
@mcp.tool(name="zoom_list_meetings")
def list_meetings(user_id: str = "me") -> str:
    """List upcoming and past Zoom meetings for a user (default is 'me')."""
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    headers = get_auth_headers()
    r = requests.get(url, headers=headers)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_get_current_time")
def get_current_utc_time() -> str:
    """Get the current UTC time and time 1 hour from now in Zoom-compatible format."""
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)

    return json.dumps({
        "current_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "one_hour_later": one_hour_later.strftime("%Y-%m-%dT%H:%M:%SZ")
    })


@mcp.tool(name="zoom_get_meeting_transcript")
def get_meeting_transcript(meeting_id: str) -> str:
    """Get download URLs for transcript files of a recorded Zoom meeting. Note: Meeting must be recorded to cloud with transcripts enabled."""
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    headers = get_auth_headers()
    r = requests.get(url, headers=headers)
    
    if not r.ok:
        return f"Error: {r.status_code} {r.text}"

    data = r.json()
    transcript_files = [
        {
            "file_type": f["file_type"],
            "download_url": f["download_url"]
        }
        for f in data.get("recording_files", [])
        if f["file_type"] in ("TRANSCRIPT", "TRANSCRIPT_VTT")
    ]

    return json.dumps(transcript_files or "No transcript files found.")

# ─── Tool: Create a Meeting ──────────────────────────────────────────────────
@mcp.tool(name="zoom_create_meeting")
def create_meeting(user_id: str, topic: str, start_time: str, duration: int) -> str:
    """Create a Zoom meeting for a user. Format for start_time: YYYY-MM-DDTHH:MM:SSZ (UTC).Example: 2025-06-20T15:00:00Z"""
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    headers = get_auth_headers()
    payload = {
        "topic": topic,
        "type": 2,
        "start_time": start_time,
        "duration": duration,
        "timezone": "UTC",
        "settings": {
            "join_before_host": True,
            "waiting_room": False
        }
    }
    r = requests.post(url, headers=headers, json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"

# ─── Tool: Get Meeting Recordings ────────────────────────────────────────────
@mcp.tool(name="zoom_get_meeting_recordings")
def get_meeting_recordings(meeting_id: str) -> str:
    """Get recording files and chat for a past Zoom meeting."""
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    headers = get_auth_headers()
    r = requests.get(url, headers=headers)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


# ─── Tool: Reschedule a Meeting ──────────────────────────────────────────────
@mcp.tool(name="zoom_reschedule_meeting")
def reschedule_meeting(meeting_id: str, new_start_time: str, new_duration: int) -> str:
    """Reschedule an existing Zoom meeting by updating its start time and duration.
    Format for new_start_time: YYYY-MM-DDTHH:MM:SSZ (UTC). Example: 2025-06-20T15:00:00Z
    """
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    headers = get_auth_headers()
    payload = {
        "start_time": new_start_time,
        "duration": new_duration,
        "timezone": "UTC"
    }
    r = requests.patch(url, headers=headers, json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


# ─── Tool: Cancel a Meeting ──────────────────────────────────────────────────
@mcp.tool(name="zoom_cancel_meeting")
def cancel_meeting(meeting_id: str) -> str:
    """Cancel (delete) an existing Zoom meeting by meeting ID."""
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}"
    headers = get_auth_headers()
    r = requests.delete(url, headers=headers)
    # Zoom returns 204 No Content on success
    if r.status_code == 204:
        return json.dumps({"status": "success", "message": f"Meeting {meeting_id} cancelled."})
    return f"Error: {r.status_code} {r.text}"



@mcp.tool(name="zoom_list_recent_meetings")
def list_recent_meetings(user_id: str = "me") -> str:
    """List recent (scheduled, live, or recently ended) meetings for a user."""
    url = f"https://api.zoom.us/v2/users/{user_id}/meetings"
    headers = get_auth_headers()
    params = {
        "type": "scheduled",  # could try "live" or "upcoming" too
        "page_size": 30
    }
    r = requests.get(url, headers=headers, params=params)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"



# ─── Run MCP Server ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
