import os
import json
import requests
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from dotenv import load_dotenv
import base64
from datetime import datetime, timedelta

load_dotenv()

CLIENT_ID = os.getenv("ZOOM_CLIENT_ID")
CLIENT_SECRET = os.getenv("ZOOM_CLIENT_SECRET")
PORT = 8004
REDIRECT_URI = f"http://localhost:{PORT}/oauth2callback"
SCOPES = "user:read meeting:read meeting:write chat_message:write:admin chat_channel:read:admin team_chat:read:list_user_channels team_chat:write:admin"

user_tokens = {}

mcp = FastMCP("zoom-mcp")

# ─────────────────────────────── Tools ─────────────────────────────────────────────── #

@mcp.tool(name="zoom_list_meetings")
def list_meetings(email: str) -> str:
    """List all upcoming and past Zoom meetings for the authenticated user."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get("https://api.zoom.us/v2/users/me/meetings", headers=headers)
    return json.dumps(resp.json())


@mcp.tool(name="zoom_list_chat_channels")
def list_chat_channels(email: str) -> str:
    """List all Zoom chat channels for the user."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}"}
    url = "https://api.zoom.us/v2/chat/users/me/channels"
    r = requests.get(url, headers=headers)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_send_chat_message")
def send_chat_message(email: str, to_channel: str, message: str) -> str:
    """Send a message to a Zoom chat channel."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.zoom.us/v2/chat/users/me/messages"
    payload = {"message": message, "to_channel": to_channel}
    r = requests.post(url, headers=headers, json=payload)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_get_current_time")
def get_current_utc_time() -> str:
    """Get the current UTC time and time 1 hour from now in ISO 8601 format."""
    now = datetime.utcnow()
    one_hour_later = now + timedelta(hours=1)
    return json.dumps({
        "current_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "one_hour_later": one_hour_later.strftime("%Y-%m-%dT%H:%M:%SZ")
    })


@mcp.tool(name="zoom_get_meeting_transcript")
def get_meeting_transcript(email: str, meeting_id: str) -> str:
    """Get transcript download URLs for a recorded Zoom meeting."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    r = requests.get(url, headers=headers)
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
def create_meeting(email: str, topic: str, start_time: str, duration: int) -> str:
    """Create a Zoom meeting for the authenticated user."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = f"https://api.zoom.us/v2/users/me/meetings"
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


@mcp.tool(name="zoom_get_meeting_recordings")
def get_meeting_recordings(email: str, meeting_id: str) -> str:
    """Get recording files and chat for a past Zoom meeting."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
    r = requests.get(url, headers=headers)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_list_recent_meetings")
def list_recent_meetings(email: str) -> str:
    """List recent (scheduled/live/recently ended) meetings for a user."""
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."
    headers = {"Authorization": f"Bearer {token}"}
    url = f"https://api.zoom.us/v2/users/me/meetings"
    params = {"type": "scheduled", "page_size": 30}
    r = requests.get(url, headers=headers, params=params)
    return r.text if r.ok else f"Error: {r.status_code} {r.text}"


@mcp.tool(name="zoom_get_auth_url")
def get_auth_url() -> str:
    """Get the Zoom OAuth URL for the user to authorize access."""
    return f"https://zoom.us/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"


@mcp.tool(name="zoom_list_users")
def list_users() -> str:
    """List all currently authorized Zoom users."""
    return "\n".join(user_tokens.keys()) or "No users authorized yet."


# ─────────────────────────────── Auth Handlers ───────────────────────────────────────── #

def build_auth_url() -> str:
    return f"https://zoom.us/oauth/authorize?response_type=code&client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}"


async def authorize(request: Request):
    return RedirectResponse(build_auth_url())


async def oauth2callback(request: Request):
    code = request.query_params.get("code")

    headers = {
        "Authorization": f"Basic {base64.b64encode(f'{CLIENT_ID}:{CLIENT_SECRET}'.encode()).decode()}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }

    token_resp = requests.post("https://zoom.us/oauth/token", headers=headers, data=data).json()

    if not token_resp.get("access_token"):
        return JSONResponse({"error": "OAuth failed", "details": token_resp}, status_code=400)

    access_token = token_resp["access_token"]
    userinfo = requests.get(
        "https://api.zoom.us/v2/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    email = userinfo.get("email")
    if not email:
        return JSONResponse({"error": "Failed to fetch user info"}, status_code=400)

    user_tokens[email] = access_token
    return JSONResponse({"message": f"✅ Authenticated as {email}"})


# ─────────────────────────────── App Entrypoint ───────────────────────────────────────── #

mcp_app = mcp.http_app(transport="sse")

routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=PORT)
