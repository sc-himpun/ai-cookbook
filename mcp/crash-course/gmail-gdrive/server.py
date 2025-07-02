import os
import json
import base64
import requests
from typing import Dict

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from fastmcp import FastMCP
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from starlette.applications import Starlette

# ─── Load .env ───────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "email",
    "profile",
]


# ─── Token Store ─────────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

PORT=8000  # Port for the FastMCP server
REDIRECT_URI = f"http://localhost:{PORT}/oauth2callback"

# ─── MCP Setup ───────────────────────────────────────────────────────────────
mcp = FastMCP("gmail-mcp")


@mcp.tool(name="gmail_search_emails")
def search_emails(query: str, email: str):
    """Search for emails in the user's Gmail account."""
    if email not in user_tokens:
        return "❌ Email not authorized. Please login first."

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('gmail', 'v1', credentials=creds)
    results = service.users().messages().list(userId='me', q=query, maxResults=5).execute()
    messages = results.get('messages', [])
    if not messages:
        return "No messages found."

    summary = []
    for msg in messages:
        data = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
        snippet = data.get("snippet", "")
        summary.append(f"ID: {msg['id']}\nSnippet: {snippet}")
    return "\n---\n".join(summary)

@mcp.tool(name="gmail_fetch_email")
def fetch_email(message_id: str, email: str):
    """Fetch a specific email by its ID."""
    if email not in user_tokens:
        return "❌ Email not authorized. Please login first."

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('gmail', 'v1', credentials=creds)
    full_msg = service.users().messages().get(userId='me', id=message_id).execute()
    payload = full_msg.get('payload', {})

    subject = "(no subject)"
    sender = "(unknown sender)"
    for header in payload.get("headers", []):
        if header['name'].lower() == "subject":
            subject = header['value']
        if header['name'].lower() == "from":
            sender = header['value']

    body = ""
    parts = payload.get("parts", [])
    if parts:
        for part in parts:
            data = part.get('body', {}).get('data')
            if data:
                try:
                    text = base64.urlsafe_b64decode(data.encode()).decode('utf-8', errors='ignore')
                    body += text + "\n"
                except Exception:
                    pass

    return f"From: {sender}\nSubject: {subject}\n\n{body.strip()}"


@mcp.tool(name="gmail_get_authorization_url")
def get_authorization_url() -> str:
    """Return the URL the user must visit to authorize Gmail access."""
    url = (
        f"http://localhost:{PORT}/authorize"
    )
    return url


@mcp.tool(name="gmail_list_authorized_accounts")
def list_authorized_accounts() -> str:
    """List all Gmail accounts that have been authorized."""
    if not user_tokens:
        return "No accounts have been authorized yet."
    return "\n".join(user_tokens.keys())



@mcp.tool(name="gdrive_search_files")
def gdrive_search_files(keyword: str, email: str, search_type: str = "both") -> str:
    """Search for files in the user's Google Drive by name or content."""
    if email not in user_tokens:
        return "❌ Email not authorized. Please login first."

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('drive', 'v3', credentials=creds)

    results = []
    page_token = None
    query = f"name contains '{keyword}' and trashed = false"
    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()
        for file in response.get('files', []):
            match = False
            if search_type in ["filename", "both"]:
                if keyword.lower() in file["name"].lower():
                    match = True

            if not match and search_type in ["content", "both"]:
                try:
                    text = gdrive_download_file_content(service, file["id"])
                    if keyword.lower() in text.lower():
                        match = True
                except Exception:
                    continue  # unreadable file, skip

            if match:
                results.append(f"{file['name']} (ID: {file['id']})")
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if not results:
        return "No matching files found."
    return "\n".join(results)


@mcp.tool(name="gdrive_fetch_file")
def gdrive_fetch_file(file_id: str, email: str) -> str:
    """Fetch content of a file by its ID from Google Drive."""
    if email not in user_tokens:
        return "❌ Email not authorized. Please login first."

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('drive', 'v3', credentials=creds)
    try:
        return gdrive_download_file_content(service, file_id)
    except Exception as e:
        return f"Error downloading file: {str(e)}"


def gdrive_download_file_content(service, file_id: str) -> str:
    """Download file content as text from Google Drive."""
    from googleapiclient.http import MediaIoBaseDownload
    import io

    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read().decode("utf-8", errors="ignore")


@mcp.tool(name="gdrive_list_all_files")
def gdrive_list_all_files(email: str, folder_id: str = "root") -> str:
    """Recursively list all files in the user's Google Drive under the given folder."""
    if email not in user_tokens:
        return "❌ Email not authorized. Please login first."

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('drive', 'v3', credentials=creds)

    all_files = _gdrive_recursive_list(service, folder_id)
    if not all_files:
        return "No files found."

    return "\n".join(f"{f['name']} (ID: {f['id']})" for f in all_files)


def _gdrive_recursive_list(service, folder_id: str) -> list:
    """Helper to recursively list all files and subfolders."""
    all_files = []
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()
        for file in response.get("files", []):
            if file["mimeType"] == "application/vnd.google-apps.folder":
                all_files.extend(_gdrive_recursive_list(service, file["id"]))
            else:
                all_files.append(file)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return all_files


# ─── Auth Endpoints ──────────────────────────────────────────────────────────

async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    resp = requests.post("https://oauth2.googleapis.com/token", data=data)
    tokens = resp.json()

    if "access_token" not in tokens:
        return JSONResponse({"error": "Token exchange failed", "details": tokens}, status_code=400)

    userinfo = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ).json()

    email = userinfo.get("email")
    if not email:
        return JSONResponse({"error": "Could not fetch email from userinfo"}, status_code=400)

    user_tokens[email] = tokens
    return JSONResponse({"message": f"Authenticated as {email}"})


async def authorize(request: Request):
    scope = " ".join(SCOPES)
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return RedirectResponse(url)


async def status(request: Request):
    email = request.query_params.get("email")
    if email in user_tokens:
        return JSONResponse({"status": "authenticated"})
    return JSONResponse({"status": "pending"})

# ─── Starlette App Setup ─────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")

routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
    Route("/status", status),
]

app = Starlette(
    routes=routes,
    lifespan=mcp_app.lifespan,
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
