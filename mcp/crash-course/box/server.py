# -*- coding: utf-8 -*-
import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
import json
import os
import requests
import tempfile
import json
from typing import Dict
import io
import fitz  # PyMuPDF

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("BOX_CLIENT_ID")
CLIENT_SECRET = os.getenv("BOX_CLIENT_SECRET")
SCOPES = os.getenv("BOX_SCOPES", "root_readonly").split()
PORT = int(os.getenv("BOX_MCP_PORT", "8029"))
REDIRECT_URI = os.getenv("BOX_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")

user_tokens: Dict[str, Dict] = {}
MAX_PDF_SIZE = 550 * 1024  # 550 KB
# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("box-mcp")
print("Scopes -> ",SCOPES)

# ─── Helpers ─────────────────────────────────────────────────────────────────
def is_access_token_valid(access_token: str) -> bool:
    """Validate Box token by calling /users/me."""
    resp = requests.get(
        "https://api.box.com/2.0/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    return resp.status_code == 200

def refresh_box_token(refresh_token: str) -> Optional[Dict]:
    """Refresh Box access token."""
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    resp = requests.post("https://api.box.com/oauth2/token", data=data)
    if resp.status_code == 200:
        return resp.json()
    else:
        print(f"[refresh_box_token] Failed: {resp.text}")
        return None

def get_box_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """Extract and refresh Box credentials from metadata."""
    if not metadata:
        return None
    creds = metadata.get("box", metadata)

    email = creds.get("email")
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")

    if not email or not access_token:
        return None

    if is_access_token_valid(access_token):
        return {"email": email, "access_token": access_token, "refresh_token": refresh_token}

    if refresh_token:
        print("🔁 Refreshing Box token…")
        new_tokens = refresh_box_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            return {
                "email": email,
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens.get("refresh_token", refresh_token),
            }
    return None

# ─── Tools ───────────────────────────────────────────────────────────────────
@mcp.tool(name="box_list_files")
def list_files(metadata: Dict, folder_id: str = "0"):
    """
    List files in a Box folder. Default is root ("0").
    """
    creds = get_box_creds(metadata)
    if not creds:
        return "❌ Box credentials missing or invalid."
    access_token = creds["access_token"]

    url = f"https://api.box.com/2.0/folders/{folder_id}/items"
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        return f"❌ Failed: {resp.text}"

    items = resp.json().get("entries", [])
    return "\n".join(f"{i['type']} - {i['name']} (id: {i['id']})" for i in items)

@mcp.tool(name="box_get_file_info")
def get_file_info(metadata: Dict, file_id: str):
    """Get metadata about a Box file."""
    creds = get_box_creds(metadata)
    if not creds:
        return "❌ Box credentials missing or invalid."
    access_token = creds["access_token"]

    url = f"https://api.box.com/2.0/files/{file_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    return resp.json() if resp.status_code == 200 else f"❌ Failed: {resp.text}"

@mcp.tool(name="box_download_file")
def download_file(metadata: Dict, file_id: str):
    """Download a Box file (returns metadata + download URL)."""
    creds = get_box_creds(metadata)
    if not creds:
        return "❌ Box credentials missing or invalid."
    access_token = creds["access_token"]

    url = f"https://api.box.com/2.0/files/{file_id}/content"
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, allow_redirects=False)

    if resp.status_code in (302, 303):  # Redirect to actual download URL
        return {"download_url": resp.headers.get("Location")}
    return f"❌ Failed: {resp.text}"


@mcp.tool(name="box_search_files")
def box_search_files(
    keyword: str,
    metadata: Dict = {},
    search_type: str = "both",   # "filename", "content", or "both"
    folder_id: str = "0",        # "0" = root
    limit: int = 25
) -> str:
    """
    Search files in Box by filename or content.
    
    Args:
        keyword: Keyword to search for (case-insensitive).
        metadata: OAuth-authenticated Box credentials.
        search_type: "filename", "content", or "both".
        folder_id: Box folder ID to restrict search (default "0" = root).
        limit: Number of results (default 25).
    
    Returns:
        JSON list of matching files (name + ID), or message if none found.
    """
    creds = get_box_creds(metadata)
    if not creds:
        return "❌ Box credentials missing or invalid."
    access_token = creds["access_token"]

    # Map search_type → Box content_types
    if search_type == "filename":
        content_types = "name,description"
    elif search_type == "content":
        content_types = "file_content"
    else:  # both
        content_types = "name,description,file_content"

    params = {
        "query": keyword,
        "type": "file",
        "limit": limit,
        "content_types": content_types,
    }
    if folder_id and folder_id != "0":
        params["ancestor_folder_ids"] = folder_id

    url = "https://api.box.com/2.0/search"
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
    if resp.status_code != 200:
        return f"❌ Search failed: {resp.text}"

    items = resp.json().get("entries", [])
    if not items:
        return "🔍 No matching files found."

    results = [{"name": i["name"], "id": i["id"], "type": i["type"]} for i in items]
    return json.dumps(results, indent=2)


@mcp.tool(name="box_list_folders")
def box_list_folders(
    metadata: Dict,
    parent_id: str = "0",   # "0" = root
    limit: int = 100
) -> str:
    """
    List folders inside a given Box folder.

    Args:
        metadata: OAuth-authenticated Box credentials.
        parent_id: The parent folder ID (default "0" = root).
        limit: Max number of items to return (default 100).

    Returns:
        JSON list of folders with name + ID.
    """
    creds = get_box_creds(metadata)
    if not creds:
        return "❌ Box credentials missing or invalid."
    access_token = creds["access_token"]

    url = f"https://api.box.com/2.0/folders/{parent_id}/items"
    params = {"limit": limit, "fields": "id,name,type"}
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)

    if resp.status_code != 200:
        return f"❌ Folder listing failed: {resp.text}"

    items = resp.json().get("entries", [])
    folders = [{"name": i["name"], "id": i["id"]} for i in items if i["type"] == "folder"]

    if not folders:
        return f"📂 No subfolders found in folder {parent_id}."

    return json.dumps(folders, indent=2)



@mcp.tool(name="box_fetch_file")
def box_fetch_file(metadata: Dict, file_id: str, download: bool = False) -> str:
    """
    Fetch details of a Box file by ID.
    - If download=True: also return extracted text (for PDFs/text files).
    """
    creds = get_box_creds(metadata)
    if not creds:
        return "❌ Box credentials missing or invalid."
    access_token = creds["access_token"]

    # 1 Get file metadata
    url = f"https://api.box.com/2.0/files/{file_id}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        return f"❌ Metadata fetch failed: {resp.text}"

    file_info = resp.json()
    result = {
        "id": file_info.get("id"),
        "name": file_info.get("name"),
        "size": file_info.get("size"),
        "type": file_info.get("type"),
    }

    if not download:
        return json.dumps(result, indent=2)

    # 2 Download file bytes
    content_url = f"https://api.box.com/2.0/files/{file_id}/content"
    resp = requests.get(content_url, headers={"Authorization": f"Bearer {access_token}"})
    if resp.status_code != 200:
        return f"❌ File download failed: {resp.text}"

    file_bytes = io.BytesIO(resp.content)
    size = int(file_info.get("size", 0) or 0)
    name = file_info.get("name", "unknown")

    # 3 Extract content
    if name.lower().endswith(".pdf"):
        if size > MAX_PDF_SIZE:
            result["preview"] = json.dumps({
                "delegate": "vector_ingest_box",
                "key": name,
                "reason": f"File size {size/1024:.1f} KB exceeds threshold ({MAX_PDF_SIZE/1024} KB). Use 'vector_ingest_box'."
            })
        else:
            text_parts = []
            with fitz.open(stream=file_bytes.read(), filetype="pdf") as doc:
                for page in doc:
                    text = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)  # type: ignore
                    if text.strip():
                        text_parts.append(text)
            result["preview"] = "\n".join(text_parts) if text_parts else "⚠️ No extractable text in PDF."
    else:
        try:
            result["preview"] = file_bytes.read().decode("utf-8", errors="ignore")[:5000]
        except Exception:
            result["preview"] = "[Binary file — no preview available]"

    return json.dumps(result, indent=2)





# ─── OAuth Endpoints ─────────────────────────────────────────────────────────
async def authorize(request: Request):
    scope = " ".join(SCOPES)
    url = (
        f"https://account.box.com/api/oauth2/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope}"
    )
    return RedirectResponse(url)

async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
    }
    token_resp = requests.post("https://api.box.com/oauth2/token", data=data).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")

    if not access_token:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    # Fetch user profile
    userinfo = requests.get(
        "https://api.box.com/2.0/users/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()
    email = userinfo.get("login")

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
    })

async def status(request: Request):
    email = request.query_params.get("email")
    if email in user_tokens:
        return JSONResponse({"status": "authenticated"})
    return JSONResponse({"status": "pending"})

# ─── App Setup ───────────────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")

routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
    Route("/status", status),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
