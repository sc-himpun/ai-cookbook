# -*- coding: utf-8 -*-
import os
import json
import base64
import requests
from typing import Dict, Optional, List
import fitz  # PyMuPDF
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from fastmcp import FastMCP
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from starlette.applications import Starlette
from googleapiclient.http import MediaIoBaseDownload
import io


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
MAX_PDF_SIZE = 400 * 1024  # 400 KB

# ─── Token Store ─────────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

PORT =  int(os.getenv("GOOGLE_MCP_PORT", "8000"))  # Port for the FastMCP server
REDIRECT_URI = os.getenv("GOOGLE_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")
# PORT=8000  # Port for the FastMCP server
# REDIRECT_URI = f"http://localhost:{PORT}/oauth2callback"

# ─── MCP Setup ───────────────────────────────────────────────────────────────
mcp = FastMCP("gmail-mcp")


from google.auth.transport.requests import Request as GoogleRequest

def get_google_creds(metadata: Optional[Dict]) -> Optional[Credentials]:
    """
    Extract and refresh Google credentials from metadata.

    Args:
        metadata (Optional[Dict]): Dict with 'access_token', 'refresh_token', etc.

    Returns:
        google.oauth2.credentials.Credentials or None
    """
    if not metadata:
        return None

    access_token = metadata.get("access_token")
    refresh_token = metadata.get("refresh_token")
    token_uri = "https://oauth2.googleapis.com/token"

    if not access_token:
        return None

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token if refresh_token else None,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        token_uri=token_uri
    )

    # Refresh if expired and refresh_token is available
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
        except Exception as e:
            print(f"🔁 Failed to refresh token: {e}")
            return None

    return creds


@mcp.tool(name="gmail_search_emails")
def search_emails(query: str, metadata: Dict = {}):
    """Search for emails in the user's Gmail account."""
    creds = get_google_creds(metadata)
    if not creds:
        return "❌ Email not authorized. Please login first."

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
def fetch_email(message_id: str, metadata: Dict = {}):
    """Fetch a specific email by its ID."""
    creds = get_google_creds(metadata)
    if not creds:
        return "❌ Email not authorized. Please login first."

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
def list_authorized_accounts(metadata: Dict = {}) -> str:
    global user_tokens

    # Auto-register user from metadata
    creds = get_google_creds(metadata)
    if creds and "email" in metadata:
        email = metadata["email"]
        user_tokens[email] = {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
        }

    if not user_tokens:
        return "No accounts have been authorized yet."

    return "\n".join(user_tokens.keys())


def get_drive_service(metadata: Dict):
    """
    Create a Google Drive API service using credentials from metadata.
    """
    creds = get_google_creds(metadata)
    if not creds:
        raise ValueError("Could not get valid Google credentials from metadata.")
    return build("drive", "v3", credentials=creds)


@mcp.tool(name="gdrive_get_file_download_url")
def gdrive_get_file_download_url(file_id: str, metadata: Dict = {}) -> str:
    """Return a direct download URL for the given file."""
    creds = get_google_creds(metadata)
    if not creds:
        return "❌ Email not authorized."

    # Get download URL (via Drive API or export for Google Docs)
    service =  get_drive_service(metadata)
    file = service.files().get(fileId=file_id, fields="id, name, mimeType, webContentLink").execute()

    # Google Docs require export
    if file["mimeType"].startswith("application/vnd.google-apps"):
        # Export to plain text (fallback)
        export_mime = "text/plain"
        export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export?mimeType={export_mime}"
    else:
        export_url = f"https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"

    return f"{export_url}&access_token={creds.token}"


@mcp.tool(name="gdrive_search_files")
def gdrive_search_files(
    keyword: str,
    metadata: Dict = {},
    search_type: str = "both",  # "filename", "content", or "both"
    file_type: str = "text",    # "text" or "all"
    path: str = "root",         # Folder name or "root"
    depth: int = 2              # Folder traversal depth
) -> str:
    """
    Hybrid search in Google Drive combining:
    - Filename search using Drive API query,
    - Google Docs content search via fullText,
    - Client-side content search for text-based files.

    Args:
        keyword: Keyword to search for (case-insensitive).
        email: OAuth-authenticated Gmail address.
        search_type: "filename", "content", or "both".
        file_type: "text" for readable formats or "all" for any file type.
        path: Folder name (case-insensitive) or "root" to search from the top level.
        depth: Max folder depth to search in (default is 2).

    Returns:
        A JSON list of matching files (name + ID), or a message if no matches found.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return "❌ Email not authorized. Please login first."

    import io
    from googleapiclient.http import MediaIoBaseDownload

    service = build('drive', 'v3', credentials=creds)

    # Resolve folder path to folder ID
    try:
        folder_id = resolve_drive_id_by_name(service, path, is_folder=True) if path != "root" else "root"
        # folder_id = resolve_folder_id_by_name(service, path) if path != "root" else "root"
    except FileNotFoundError as e:
        return str(e)

    # Traverse target folder to get all files within depth
    all_files = _gdrive_recursive_list(
        service,
        folder_id=folder_id,
        current_depth=0,
        max_depth=depth,
        file_type=file_type
    )

    results = []
    seen_ids = set()

    # Step 1: Match by filename
    if search_type in ["filename", "both"]:
        for file in all_files:
            if keyword.lower() in file["name"].lower():
                results.append({"name": file["name"], "id": file["id"]})
                seen_ids.add(file["id"])

    # Step 2: Google Docs content search (via Drive query API)
    if search_type in ["content", "both"]:
        for file in all_files:
            if file["id"] in seen_ids:
                continue
            if file["mimeType"] == "application/vnd.google-apps.document":
                # Let Google Drive search inside Google Docs using fullText
                try:
                    query = f"fullText contains '{keyword}' and trashed = false and mimeType = 'application/vnd.google-apps.document'"
                    doc_match = service.files().list(
                        q=query,
                        fields="files(id)",
                        pageSize=100
                    ).execute()
                    doc_ids = {f["id"] for f in doc_match.get("files", [])}
                    if file["id"] in doc_ids:
                        results.append({"name": file["name"], "id": file["id"]})
                        seen_ids.add(file["id"])
                except Exception:
                    continue

    # Step 3: Client-side scan for text content
    if search_type in ["content", "both"]:
        for file in all_files:
            if file["id"] in seen_ids:
                continue
            try:
                content = gdrive_download_file_content(service, file["id"])
                if keyword.lower() in content.lower():
                    results.append({"name": file["name"], "id": file["id"]})
                    seen_ids.add(file["id"])
            except Exception:
                continue  # unreadable

    return json.dumps(results) if results else "No matching files found."




def resolve_drive_id_by_name(
    service,
    name: str,
    is_folder: bool = False,
    parent_id: Optional[str] = None,
    match_mode: str = "exact"  # "exact", "startswith", or "contains"
) -> str:
    """
    Resolve a file or folder name to its Drive ID.

    Args:
        service: Google Drive service object.
        name (str): Name of the file or folder.
        is_folder (bool): Whether to look for folders only.
        parent_id (Optional[str]): Scope the search within a parent folder.
        match_mode (str): "exact", "startswith", or "contains".

    Returns:
        str: The ID of the first matching item.

    Raises:
        FileNotFoundError: If no match is found.
    """
    mime_filter = "mimeType = 'application/vnd.google-apps.folder'" if is_folder else "mimeType != 'application/vnd.google-apps.folder'"
    query_parts = [mime_filter, "trashed = false"]

    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")

    query = " and ".join(query_parts)

    response = service.files().list(
        q=query,
        spaces='drive',
        fields="files(id, name)",
        pageSize=1000
    ).execute()

    items = response.get("files", [])
    name_lower = name.lower()

    for item in items:
        item_name = item["name"].lower()
        if (
            (match_mode == "exact" and item_name == name_lower) or
            (match_mode == "startswith" and item_name.startswith(name_lower)) or
            (match_mode == "contains" and name_lower in item_name)
        ):
            return item["id"]

    raise FileNotFoundError(f"❌ {'Folder' if is_folder else 'File'} '{name}' not found with match mode '{match_mode}'.")


@mcp.tool(name="gdrive_get_folder_id")
def gdrive_get_folder_id(name: str, metadata: Dict) -> str:
    """Get the Google Drive folder ID by name."""
    creds = get_google_creds(metadata)
    if not creds:
        return "❌ Email not authorized. Please login first."

    service = build('drive', 'v3', credentials=creds)
    try:
        folder_id = resolve_drive_id_by_name(service, name, is_folder=True)
        return f"Folder ID for '{name}': {folder_id}"
    except FileNotFoundError as e:
        return str(e)


@mcp.tool(name="gdrive_fetch_file")
def gdrive_fetch_file(file_id: str, metadata: Dict = {}) -> str:
    """Fetch content of a file by its ID from Google Drive.
       - For large pdf files, delegate to vector_search.
       - Supports text files and PDFs.
       - PDFs larger than 400KB are rejected to save memory."""
    creds = get_google_creds(metadata)           
    if not creds:
        return "❌ Email not authorized. Please login first."    
    service = build('drive', 'v3', credentials=creds)

    try:
        return gdrive_download_file_content(service, file_id)
    except Exception as e:
        return f"Error downloading file: {str(e)}"



def gdrive_download_file_content(service, file_id: str) -> str:
    """Download file content as text from Google Drive.
       Uses PyMuPDF for PDFs (≤400KB), direct decode for text files."""
    
    # Step 1: Check metadata for file size & type
    file_meta = service.files().get(fileId=file_id, fields="name, mimeType, size").execute()
    mime_type = file_meta.get("mimeType", "")
    file_size = int(file_meta.get("size", 0) or 0)

    # Step 2: Download raw bytes
    request = service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)

    # Step 3: Process based on type
    if mime_type == "application/pdf":
        if file_size > MAX_PDF_SIZE:
            # return f"⚠️ PDF too large ({file_size/1024:.1f} KB). Limit is {MAX_PDF_SIZE/1024} KB."
            return json.dumps({
                    "delegate": "vector_ingest_gdrive",
                    "key": file_meta.get("name", "unknown"),
                    "reason": f"File size {file_size} exceeds threshold {file_size/1024:.1f} KB. Use 'vector_ingest_gdrive' for ingesting the chunks as embeddings and searching."
                })
        
        # Memory-efficient PyMuPDF extraction
        text_parts = []
        with fitz.open(stream=fh.read(), filetype="pdf") as doc:
            for page in doc:
                text = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE) # type: ignore
                if text.strip():
                    text_parts.append(text)
        return "\n".join(text_parts) if text_parts else "⚠️ No extractable text found in PDF."
    
    else:
        # Assume text-like file (CSV, TXT, JSON, etc.)
        return fh.read().decode("utf-8", errors="ignore")



@mcp.tool(name="gdrive_list_drive_folders")
def list_drive_folders(metadata: Dict) -> List[Dict]:
    """
    Lists all non-trashed folders in the user's Google Drive.
    """
    service = get_drive_service(metadata)
    results = service.files().list(
        q="mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id, name)"
    ).execute()
    return results.get("files", [])


@mcp.tool(name="gdrive_list_drive_folder_contents")
def list_drive_folder_contents(folder_id: str, metadata: Dict) -> List[Dict]:
    """
    List files/folders inside a given folder.
    """
    service = get_drive_service(metadata)
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType)"
    ).execute()
    return results.get("files", [])


@mcp.tool(name="gdrive_list_all_files")
def gdrive_list_all_files(
    folder_id: str,
    metadata: Dict = {},
    depth: int = 2,
    file_type: str = "text"  # "text" or "all"
) -> str:
    """
    Recursively list all files in the user's Google Drive under the given folder,
    filtered by file type and limited by depth.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return "❌ Email not authorized. Please login first."

    service = build('drive', 'v3', credentials=creds)

    all_files = _gdrive_recursive_list(
        service, folder_id, current_depth=0, max_depth=depth, file_type=file_type
    )
    if not all_files:
        return "No matching files found."

    return "\n".join(f"{f['name']} (ID: {f['id']})" for f in all_files)



def _gdrive_recursive_list(
    service,
    folder_id: str,
    current_depth: int,
    max_depth: int,
    file_type: str = "all"
) -> list:
    """Helper to recursively list all files and subfolders up to max_depth, with optional file_type filter."""
    all_files = []

    # Allowed filters
    allowed_extensions = {".txt", ".md", ".csv", ".json"}
    allowed_mime_types = {
        "text/plain",
        "text/markdown",
        "text/csv",
        "application/json",
        "application/vnd.google-apps.document",  # Google Docs
    }


    if current_depth > max_depth:
        return all_files

    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        response = service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()
        for file in response.get("files", []):
            mime = file["mimeType"]
            name = file["name"]

            if mime == "application/vnd.google-apps.folder":
                all_files.extend(
                    _gdrive_recursive_list(service, file["id"], current_depth + 1, max_depth, file_type)
                )
            else:
                if file_type == "all":
                    all_files.append(file)
                elif (
                    mime in allowed_mime_types
                    or any(name.lower().endswith(ext) for ext in allowed_extensions)
                ):
                    all_files.append(file)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return all_files





# ─── Auth Endpoints ──────────────────────────────────────────────────────────


async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    token_resp = requests.post("https://oauth2.googleapis.com/token", data=data).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")

    if not access_token:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    # Get user info using the access_token
    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    email = user_info.get("email")

    if not email:
        return JSONResponse({"error": "Failed to get user email", "user_info": user_info}, status_code=400)

    # user_tokens[email] = {
    #     "access_token": access_token,
    #     "refresh_token": refresh_token,
    # }

    print(f"✅ Authenticated: {email}")
    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token
    })




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

