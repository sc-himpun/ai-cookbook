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
import io
import fitz  # PyMuPDF

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("BOX_CLIENT_ID")
CLIENT_SECRET = os.getenv("BOX_CLIENT_SECRET")
SCOPES = os.getenv("BOX_SCOPES", "root_readonly root_readwrite").split()
PORT = int(os.getenv("BOX_MCP_PORT", "8029"))
REDIRECT_URI = os.getenv(
    "BOX_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback"
)

user_tokens: Dict[str, Dict] = {}
MAX_PDF_SIZE = 1024 * 1024  # 1024 KB or 1 MB
# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("box-mcp")
print("Scopes -> ", SCOPES)


# ─── Helpers ─────────────────────────────────────────────────────────────────
def is_access_token_valid(access_token: str) -> bool:
    """
    Validate a Box access token by making a request to the Box API.

    Args:
        access_token (str): The Box OAuth2 access token to validate.

    Returns:
        bool: True if the token is valid (API returns 200), False otherwise.
    """
    resp = requests.get(
        "https://api.box.com/2.0/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return resp.status_code == 200


def refresh_box_token(refresh_token: str) -> Optional[Dict]:
    """
    Refresh a Box access token using a refresh token.

    Args:
        refresh_token (str): The Box OAuth2 refresh token.

    Returns:
        Optional[Dict]: The new token response as a dictionary if successful, None otherwise.
    """
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
    """
    Extract Box credentials from metadata and refresh the access token if needed.

    Args:
        metadata (Optional[Dict]): Metadata containing Box credentials (may be nested under 'box').

    Returns:
        Optional[Dict]: Dictionary with 'email', 'access_token', and 'refresh_token' if valid, else None.
    """
    if not metadata:
        return None
    creds = metadata.get("box", metadata)

    email = creds.get("email")
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")

    if not email or not access_token:
        return None

    if is_access_token_valid(access_token):
        return {
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }

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


def make_response(success: bool, action: str, message: str, data=None) -> Dict:
    """
    Standardize tool responses in MCP server.

    Args:
        success (bool): Indicates if the tool operation was successful.
        action (str): Name of the tool or action performed.
        message (str): Human-readable status or error message.
        data (Any, optional): Structured payload returned by the tool. Defaults to empty dict if None.

    Returns:
        dict: Standardized response with the following keys:
            - "success" (bool): Success status.
            - "action" (str): Tool/action name.
            - "message" (str): Status message.
            - "data" (dict | list | Any): Tool-specific payload. Defaults to `{}` if None.

    Notes:
        - All MCP tools should use this format to ensure consistent responses.
        - The `data` field can be a dictionary, list, or any serializable object.
        - For sensitive information (e.g., internal IDs), include them only if required for tool chaining,
          but avoid exposing them directly to the UI.
    """
    return {
        "success": success,
        "action": action,
        "message": message,
        "data": data if data is not None else {},
    }


# ─── Tools ───────────────────────────────────────────────────────────────────
@mcp.tool(name="box_list_files")
def list_files(metadata: Dict, folder_id: str = "0") -> Dict:
    """
    List files and folders in a Box folder, including size and owner details.

    This tool retrieves the items inside a given Box folder and returns them in a
    standardized response format. By default, it lists the root folder (`"0"`).

    Parameters
    ----------
    metadata : Dict
        Dictionary containing Box OAuth credentials.
    folder_id : str, optional
        The Box folder ID to list. Defaults to "0" (root).

    Returns
    -------
    dict
        Standardized response from `make_response` with:
        - "success" (bool): Whether the operation was successful.
        - "action" (str): Tool name (`"box_list_files"`).
        - "message" (str): Status or error message.
        - "data" (list): List of file/folder metadata objects, each with:
            - "name" (str): File or folder name (safe to show in UI).
            - "id" (str): Box internal ID (for tool chaining only, **not to be shown to user/UI**).
            - "type" (str): `"file"` or `"folder"`.
            - "file_type" (str, optional): File extension (e.g., `"pdf"`, `"txt"`) if applicable.
            - "url" (str): Human-friendly Box web URL to open the item.
            - "size" (int, optional): File size in bytes (folders do not have size, not to be shown to user/UI unless asked).

    Notes
    -----
    - The "id" field is included for tool-to-tool usage by the LLM and should not
      be displayed in the user-facing UI.
    - The "url" field provides a clickable link for the user.
    - File sizes are in bytes, which can be formatted on the UI side (e.g., MB, GB).
    - Owner info comes from the `owned_by` field in Box API.
    """
    creds = get_box_creds(metadata)
    if not creds:
        return make_response(
            False, "box_list_files", "❌ Box credentials missing or invalid."
        )

    access_token = creds["access_token"]
    url = f"https://api.box.com/2.0/folders/{folder_id}/items"
    params = {"fields": "id,name,type,size,owned_by"}  # request extra fields
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {access_token}"}, params=params
    )

    if resp.status_code != 200:
        return make_response(False, "box_list_files", f"❌ Failed: {resp.text}")

    items = resp.json().get("entries", [])
    results = []
    for i in items:
        # derive file extension if type=file
        file_ext = (
            os.path.splitext(i["name"])[1][1:].lower() if i["type"] == "file" else None
        )
        results.append(
            {
                "name": i["name"],
                "id": i["id"],  # for internal tool chaining, not for UI
                "type": i["type"],  # file or folder
                "file_type": file_ext,  # pdf, txt, docx, etc. (None for folders)
                "url": f"https://app.box.com/{'file' if i['type']=='file' else 'folder'}/{i['id']}",
                "size": i.get("size"),  # file size in bytes (None for folders)
                # "owner": i.get("owned_by", {}).get("name"),  # owner display name
            }
        )

    return make_response(
        True, "box_list_files", "✅ Files listed successfully.", results
    )


@mcp.tool(name="box_search_files")
def box_search_files(
    keyword: str,
    metadata: Dict = {},
    search_type: str = "both",  # "filename", "content", or "both"
    folder_id: str = "0",  # "0" = root
    limit: int = 25,
) -> Dict:
    """
    Search for files in a Box workspace by filename, description, or content.

    This tool queries the Box Search API and returns a structured list of matching files.
    Each file entry contains both its name and Box ID for internal tool chaining, while
    keeping IDs hidden from the end-user UI. The response also includes the Box object type
    (e.g., "file"), the file extension (e.g., "pdf", "docx"), and a direct Box URL.

    Parameters
    ----------
    keyword : str
        The search query string to look for in filenames, descriptions, or file content.
    metadata : Dict, optional
        Authentication metadata required to obtain Box access tokens.
        Must include user credentials for Box API.
    search_type : str, default "both"
        Defines the scope of the search:
        - "filename": Matches against file names and descriptions.
        - "content": Matches inside file content.
        - "both": Searches both filenames/descriptions and file contents.
        Note: Some box accounts like personal and business basic don't support content search.
    folder_id : str, default "0"
        ID of the parent folder to restrict the search within. "0" refers to the root folder.
    limit : int, default 25
        Maximum number of search results to return.

    Returns
    -------
    dict
        A structured dictionary containing:
        - "files": A list of matching files, where each file includes:
            - "name" (str): File name shown to the user.
            - "id" (str): Box file ID (hidden from user-facing UI, but used for tool chaining).
            - "type" (str): Box object type (e.g., "file").
            - "file_type" (str): File extension/type (e.g., "pdf", "docx", "jpg").
            - "url" (str): Direct link to open the file in Box.
        - "message" (str, optional): Informational message if no matches are found.

    Example
    -------
    >>> box_search_files(keyword="report", search_type="filename")
    {
        "files": [
            {
                "name": "Annual Report.pdf",
                "id": "123456",
                "type": "file",
                "file_type": "pdf",
                "url": "https://app.box.com/file/123456"
            }
        ]
    }
    """
    creds = get_box_creds(metadata)
    if not creds:
        return make_response(
            False, "box_search_files", "❌ Box credentials missing or invalid."
        )

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
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {access_token}"}, params=params
    )
    if resp.status_code != 200:
        return make_response(
            False, "box_search_files", f"❌ Search failed: {resp.text}"
        )

    items = resp.json().get("entries", [])
    message = ""
    if search_type in ("content", "both") and not items:
        message = "Content search may not be supported on this Box account or files are not yet indexed."

    results = []
    for i in items:
        file_type = None
        if i.get("type") == "file" and "name" in i and "." in i["name"]:
            file_type = i["name"].split(".")[-1].lower()
        results.append(
            {
                "name": i["name"],
                "id": i["id"],
                "type": i["type"],  # Box object type (usually "file")
                "file_type": file_type,  # Actual file extension
                "url": f"https://app.box.com/file/{i['id']}",  # Direct Box URL
            }
        )

    return make_response(True, "search_files", message or "Search completed.", results)


@mcp.tool(name="box_list_folders")
def box_list_folders(
    metadata: Dict,
    parent_id: str = "0",  # "0" = root
    limit: int = 100,
) -> Dict:
    """
    List all subfolders inside a given Box folder.

    This tool queries the Box API to fetch the list of child folders under a given parent
    folder ID. Each returned folder includes its name, ID (for tool chaining), and a direct
    Box URL (safe to display in the UI).

    Parameters
    ----------
    metadata : Dict
        OAuth-authenticated Box credentials, required to authorize API requests.
    parent_id : str, default "0"
        The ID of the parent folder to list subfolders from. "0" represents the root folder.
    limit : int, default 100
        Maximum number of items to return.

    Returns
    -------
    dict
        A structured dictionary containing:
        - "folders": A list of subfolders, where each folder includes:
            - "name" (str): Folder name shown to the user.
            - "id" (str): Box folder ID (used internally for tool chaining).
            - "url" (str): Direct link to open the folder in Box (safe for UI).
        - "message" (str, optional): Informational message if no subfolders are found.

    Example
    -------
    >>> box_list_folders(metadata, parent_id="0")
    {
        "folders": [
            {"name": "Projects", "id": "12345", "url": "https://app.box.com/folder/12345"},
            {"name": "Reports", "id": "67890", "url": "https://app.box.com/folder/67890"}
        ]
    }
    """
    creds = get_box_creds(metadata)
    if not creds:
        return {"error": "❌ Box credentials missing or invalid."}
    access_token = creds["access_token"]

    url = f"https://api.box.com/2.0/folders/{parent_id}/items"
    params = {"limit": limit, "fields": "id,name,type"}
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {access_token}"}, params=params
    )

    if resp.status_code != 200:
        return {"error": f"❌ Folder listing failed: {resp.text}"}

    items = resp.json().get("entries", [])
    folders = [
        {
            "name": i["name"],
            "id": i["id"],
            "url": f"https://app.box.com/folder/{i['id']}",
        }
        for i in items
        if i["type"] == "folder"
    ]

    if not folders:
        return {
            "folders": [],
            "message": f"📂 No subfolders found in folder {parent_id}.",
        }

    return {"folders": folders}


@mcp.tool(name="box_fetch_file")
def box_fetch_file(metadata: Dict, file_id: str, download: bool = False) -> Dict:
    """
    Fetch metadata and optional preview content of a Box file.

    This tool retrieves detailed information about a file in Box given its file ID.
    Optionally, it can also download and return a text preview of the file (for PDFs
    and text-based formats). For binary files, a placeholder message is included.

    Parameters
    ----------
    metadata : Dict
        OAuth-authenticated Box credentials.
    file_id : str
        The Box file ID to fetch details for.
    download : bool, default False
        If True, download the file and return a preview of its content (PDF/text only).
        If False, return metadata only.

    Returns
    -------
    dict
        Standardized response from `make_response` with:
        - "success" (bool): Whether the operation was successful.
        - "action" (str): Tool name (`"box_fetch_file"`).
        - "message" (str): Status or error message.
        - "data" (dict): File details including:
            - "id" (str): File ID.
            - "name" (str): File name.
            - "type" (str): Always `"file"`.
            - "file_type" (str): File extension (e.g., `"pdf"`, `"txt"`).
            - "size" (int): File size in bytes.
            - "owner" (str): File owner’s display name.
            - "url" (str): Human-friendly Box web URL to open the file.
            - "preview" (str, optional): Text preview if `download=True`.

    Notes
    -----
    - File previews are truncated (first ~5000 chars) to avoid excessive output.
    - Large PDFs over `MAX_PDF_SIZE` return a delegation hint instead of raw text.
    - Non-text/binary files will return `"preview": "[Binary file — no preview available]"`.
    """
    creds = get_box_creds(metadata)
    if not creds:
        return make_response(
            False, "box_fetch_file", "❌ Box credentials missing or invalid."
        )

    access_token = creds["access_token"]

    # 1. Get file metadata
    url = f"https://api.box.com/2.0/files/{file_id}"
    params = {"fields": "id,name,type,size,owned_by"}
    resp = requests.get(
        url, headers={"Authorization": f"Bearer {access_token}"}, params=params
    )
    if resp.status_code != 200:
        return make_response(
            False, "box_fetch_file", f"❌ Metadata fetch failed: {resp.text}"
        )

    file_info = resp.json()
    name = file_info.get("name", "unknown")
    size = int(file_info.get("size", 0) or 0)

    result = {
        "id": file_info.get("id"),
        "name": name,
        "type": file_info.get("type"),
        "file_type": os.path.splitext(name)[1][1:].lower() if "." in name else None,
        "size": size,
        "owner": file_info.get("owned_by", {}).get("name"),
        "url": f"https://app.box.com/file/{file_info.get('id')}",
    }

    if not download:
        return make_response(
            True, "box_fetch_file", "✅ File metadata fetched.", result
        )

    # 2. Download file bytes
    content_url = f"https://api.box.com/2.0/files/{file_id}/content"
    resp = requests.get(
        content_url, headers={"Authorization": f"Bearer {access_token}"}
    )
    if resp.status_code != 200:
        return make_response(
            False, "box_fetch_file", f"❌ File download failed: {resp.text}"
        )

    file_bytes = io.BytesIO(resp.content)

    # 3. Extract preview
    if name.lower().endswith(".pdf"):
        if size > MAX_PDF_SIZE:
            result["message"] = {
                "delegate": "vector_ingest_box",
                "key": name,
                "reason": f"File size {size/1024:.1f} KB exceeds threshold ({MAX_PDF_SIZE/1024} KB). Use 'vector_ingest_box' tool.",
            }

        else:
            text_parts = []
            with fitz.open(stream=file_bytes.read(), filetype="pdf") as doc:
                for page in doc:
                    text = page.get_text("text", flags=fitz.TEXT_PRESERVE_WHITESPACE)  # type: ignore
                    if text.strip():
                        text_parts.append(text)
            result["preview"] = (
                "\n".join(text_parts) if text_parts else "⚠️ No extractable text in PDF."
            )
    else:
        try:
            result["preview"] = file_bytes.read().decode("utf-8", errors="ignore")[
                :5000
            ]
        except Exception:
            result["preview"] = "[Binary file — no preview available]"

    return make_response(
        True, "box_fetch_file", "✅ File details and preview fetched.", result
    )


# ─── OAuth Endpoints ─────────────────────────────────────────────────────────
async def authorize(request: Request):
    """
    Initiate Box OAuth2 authorization flow by redirecting to Box login/consent page.

    Args:
        request (Request): Starlette request object.

    Returns:
        RedirectResponse: Redirects user to Box OAuth2 authorization URL.
    """
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
    """
    Handle OAuth2 callback, exchange code for tokens, and fetch user profile.

    Args:
        request (Request): Starlette request object containing 'code' query param.

    Returns:
        JSONResponse: Contains authentication result, tokens, and user email.
    """
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
        return JSONResponse(
            {"error": "Token exchange failed", "details": token_resp}, status_code=400
        )

    # Fetch user profile
    userinfo = requests.get(
        "https://api.box.com/2.0/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    email = userinfo.get("login")

    return JSONResponse(
        {
            "message": f"Authenticated as {email}",
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
