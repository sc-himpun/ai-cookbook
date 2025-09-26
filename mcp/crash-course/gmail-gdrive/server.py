# -*- coding: utf-8 -*-
from google.auth.transport.requests import Request as GoogleRequest
import os
import json
import base64
import requests
from typing import Dict, Optional
import fitz  # PyMuPDF
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from fastmcp import FastMCP
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from starlette.applications import Starlette
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

PORT = int(os.getenv("GOOGLE_MCP_PORT", "8000"))  # Port for the FastMCP server
REDIRECT_URI = os.getenv(
    "GOOGLE_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback"
)

# ─── MCP Setup ───────────────────────────────────────────────────────────────
mcp = FastMCP("gmail-mcp")


def make_response(success: bool, action: str, message: str, data=None):
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
        token_uri=token_uri,
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
    """
    Search Gmail messages matching a query string and return snippet, subject, and sender.

    Args:
        query (str): Gmail search query (e.g., "from:someone@example.com", "subject:report").
        metadata (Dict, optional): Dictionary containing OAuth credentials:
            - access_token (str): OAuth access token.
            - refresh_token (str, optional): OAuth refresh token.
            - email (str, optional): User email associated with credentials.

    Returns:
        dict: Standard MCP tool response with the following structure:
            {
                "success": bool,        # True if search succeeded, False otherwise
                "action": str,          # "search_emails"
                "message": str,         # Human-readable status message
                "data": list            # List of matched messages (max 5), each containing:
                    [
                        {
                            "id": str,       # Message ID (for tool chaining, not for UI)
                            "snippet": str,  # Snippet or preview of the message
                            "subject": str,  # Email subject
                            "from": str      # Sender of the email
                        },
                        ...
                    ]
            }

    Notes:
        - If credentials are missing or invalid, the response will indicate authorization failure.
        - The 'id' field is retained for internal tool chaining; the UI should not display it directly.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return make_response(
            False, "search_emails", "❌ Email not authorized. Please login first."
        )

    try:
        service = build("gmail", "v1", credentials=creds)
        results = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=5)
            .execute()
        )
        messages = results.get("messages", [])
        if not messages:
            return make_response(True, "search_emails", "No messages found.", [])

        summary = []
        for msg in messages:
            data = (
                service.users()
                .messages()
                .get(userId="me", id=msg["id"], format="metadata")
                .execute()
            )
            snippet = data.get("snippet", "")

            # Extract headers
            headers = {
                h["name"].lower(): h["value"]
                for h in data.get("payload", {}).get("headers", [])
            }
            subject = headers.get("subject", "(no subject)")
            sender = headers.get("from", "(unknown sender)")

            summary.append(
                {
                    "id": msg["id"],
                    "snippet": snippet,
                    "subject": subject,
                    "from": sender,
                }
            )

        return make_response(
            True, "search_emails", f"Found {len(summary)} messages.", summary
        )

    except Exception as e:
        return make_response(
            False, "search_emails", f"Error searching emails: {str(e)}"
        )


@mcp.tool(name="gmail_fetch_email")
def fetch_email(message_id: str, metadata: Dict = {}):
    """
    Fetch a specific email by its Gmail message ID.

    Args:
        message_id (str): The unique ID of the email message to fetch.
        metadata (Dict, optional): Dictionary containing OAuth credentials:
            - access_token (str): OAuth access token.
            - refresh_token (str, optional): OAuth refresh token.
            - email (str, optional): User email associated with credentials.

    Returns:
        dict: Standard MCP tool response with the following structure:
            {
                "success": bool,        # True if fetch succeeded, False otherwise
                "action": str,          # "fetch_email"
                "message": str,         # Human-readable status message
                "data": dict            # Email details if successful:
                    {
                        "id": str,     # Message ID
                        "from": str,   # Sender of the email
                        "subject": str,# Subject of the email
                        "body": str    # Body text of the email
                    }
            }

    Notes:
        - If credentials are missing or invalid, the response will indicate authorization failure.
        - The 'id' field in data is retained for internal tool chaining; the UI should not expose it directly.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return make_response(
            False, "fetch_email", "❌ Email not authorized. Please login first."
        )

    service = build("gmail", "v1", credentials=creds)
    full_msg = service.users().messages().get(userId="me", id=message_id).execute()
    payload = full_msg.get("payload", {})

    subject = "(no subject)"
    sender = "(unknown sender)"
    for header in payload.get("headers", []):
        if header["name"].lower() == "subject":
            subject = header["value"]
        if header["name"].lower() == "from":
            sender = header["value"]

    body = ""
    parts = payload.get("parts", [])
    if parts:
        for part in parts:
            data = part.get("body", {}).get("data")
            if data:
                try:
                    text = base64.urlsafe_b64decode(data.encode()).decode(
                        "utf-8", errors="ignore"
                    )
                    body += text + "\n"
                except Exception:
                    pass

    # return f"From: {sender}\nSubject: {subject}\n\n{body.strip()}"
    return make_response(
        True,
        "fetch_email",
        "Fetched email successfully.",
        {"id": message_id, "from": sender, "subject": subject, "body": body.strip()},
    )


def get_drive_service(metadata: Dict):
    """
    Create and return a Google Drive API service object using OAuth credentials.

    Args:
        metadata (Dict): Dictionary containing OAuth credentials, typically:
            - access_token (str): OAuth access token.
            - refresh_token (str, optional): OAuth refresh token.
            - email (str, optional): User email associated with credentials.

    Returns:
        googleapiclient.discovery.Resource: Authenticated Google Drive service object.
    """
    creds = get_google_creds(metadata)
    if not creds:
        raise ValueError("Could not get valid Google credentials from metadata")
    return build("drive", "v3", credentials=creds)


@mcp.tool(name="gdrive_search_files")
def gdrive_search_files(
    keyword: str,
    metadata: Dict = {},
    search_type: str = "both",
    file_type: str = "text",
    path: str = "root",
    depth: int = 2,
) -> dict:
    """
    Search for files in Google Drive by filename and/or file content.

    This tool combines three strategies:
        1. Filename matching (case-insensitive).
        2. Google Docs content search via Drive API (`fullText` query).
        3. Client-side text search for supported text-based files.

    Args:
        keyword (str): Keyword to search for (case-insensitive).
        metadata (Dict, optional): OAuth credentials and user info for Google Drive.
        search_type (str, optional): "filename", "content", or "both".
        file_type (str, optional): "text" for readable formats, "all" for any file type.
        path (str, optional): Folder name (case-insensitive) or "root".
        depth (int, optional): Maximum folder traversal depth. Default is 2.

    Returns:
        dict: Standard MCP tool response:
            - success (bool)
            - action (str): "gdrive_search_files"
            - message (str)
            - data (list of dict):
                Each dict contains:
                    - id (str): Internal Google Drive file ID (tool chaining only)
                    - name (str): File name (safe for UI)
                    - url (str): Google Drive file URL (safe for UI)

    Notes:
        - File `id` is for tool chaining only; UI should not display it.
        - Safe fields for UI: `name`, `url`.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return make_response(
            False, "search_files", "❌ Email not authorized or credentials expired."
        )

    service = build("drive", "v3", credentials=creds)

    try:
        folder_id = (
            resolve_drive_id_by_name(service, path, is_folder=True)
            if path != "root"
            else "root"
        )
    except FileNotFoundError as e:
        return make_response(False, "search_files", f"{str(e)}")

    all_files = _gdrive_recursive_list(service, folder_id, 0, depth, file_type)
    results = []
    seen_ids = set()

    if search_type in ["filename", "both"]:
        _search_by_filename(all_files, keyword, results, seen_ids)

    if search_type in ["content", "both"]:
        _search_google_docs_content(service, all_files, keyword, results, seen_ids)
        _search_client_side_content(service, all_files, keyword, results, seen_ids)

    # Add URLs for each file
    for f in results:
        f["url"] = f"https://drive.google.com/file/d/{f['id']}/view"

    return make_response(
        True,
        "gdrive_search_files",
        (
            f"Found {len(results)} matching files."
            if results
            else "No matching files found."
        ),
        results,
    )


def _search_by_filename(all_files: list, keyword: str, results: list, seen_ids: set):
    """
    Helper: append files whose names contain the keyword (case-insensitive).

    Args:
        all_files (list): List of file dicts from Google Drive API.
        keyword (str): Search keyword.
        results (list): Output list to append matched files (name + id).
        seen_ids (set): Set of already added file IDs to prevent duplicates.
    """
    for file in all_files:
        if keyword.lower() in file["name"].lower():
            results.append({"id": file["id"], "name": file["name"]})
            seen_ids.add(file["id"])


def _search_google_docs_content(
    service, all_files: list, keyword: str, results: list, seen_ids: set
):
    """
    Helper: append Google Docs files whose content contains the keyword via Drive fullText query.

    Args:
        service: Authenticated Google Drive service object.
        all_files (list): List of file dicts from Google Drive API.
        keyword (str): Search keyword.
        results (list): Output list to append matched files.
        seen_ids (set): Set of already added file IDs to prevent duplicates.
    """
    for file in all_files:
        if (
            file["id"] in seen_ids
            or file["mimeType"] != "application/vnd.google-apps.document"
        ):
            continue
        try:
            query = f"fullText contains '{keyword}' and trashed = false and mimeType = 'application/vnd.google-apps.document'"
            doc_match = (
                service.files()
                .list(q=query, fields="files(id)", pageSize=100)
                .execute()
            )
            doc_ids = {f["id"] for f in doc_match.get("files", [])}
            if file["id"] in doc_ids:
                results.append({"id": file["id"], "name": file["name"]})
                seen_ids.add(file["id"])
        except Exception:
            continue


def _search_client_side_content(
    service, all_files: list, keyword: str, results: list, seen_ids: set
):
    """
    Helper: append text-like files whose content contains the keyword.

    Args:
        service: Authenticated Google Drive service object.
        all_files (list): List of file dicts from Google Drive API.
        keyword (str): Search keyword.
        results (list): Output list to append matched files.
        seen_ids (set): Set of already added file IDs to prevent duplicates.
    """
    for file in all_files:
        if file["id"] in seen_ids:
            continue
        try:
            content = gdrive_download_file_content(service, file["id"])
            if keyword.lower() in content.lower():
                results.append({"id": file["id"], "name": file["name"]})
                seen_ids.add(file["id"])
        except Exception:
            continue


def resolve_drive_id_by_name(
    service,
    name: str,
    is_folder: bool = False,
    parent_id: Optional[str] = None,
    match_mode: str = "exact",  # "exact", "startswith", or "contains"
    page_size: int = 1000,
) -> str:
    """
    Resolve a file or folder name to its Drive ID.

    Args:
        service: Google Drive service object.
        name (str): Name of the file or folder.
        is_folder (bool): Whether to look for folders only.
        parent_id (Optional[str]): Scope the search within a parent folder.
        match_mode (str): "exact", "startswith", or "contains".
        page_size (int): 1000 is default value

    Returns:
        str: The ID of the first matching item.

    Raises:
        FileNotFoundError: If no match is found.
    """
    mime_filter = (
        "mimeType = 'application/vnd.google-apps.folder'"
        if is_folder
        else "mimeType != 'application/vnd.google-apps.folder'"
    )
    query_parts = [mime_filter, "trashed = false"]

    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")

    query = " and ".join(query_parts)

    response = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", pageSize=page_size)
        .execute()
    )

    items = response.get("files", [])
    name_lower = name.lower()

    for item in items:
        item_name = item["name"].lower()
        if (
            (match_mode == "exact" and item_name == name_lower)
            or (match_mode == "startswith" and item_name.startswith(name_lower))
            or (match_mode == "contains" and name_lower in item_name)
        ):
            return item["id"]

    raise FileNotFoundError(
        f"❌ {'Folder' if is_folder else 'File'} '{name}' not found with match mode '{match_mode}'."
    )


@mcp.tool(name="gdrive_get_folder_id")
def gdrive_get_folder_id(name: str, metadata: Dict) -> dict:
    """
    Resolve the unique Google Drive folder ID by its name.

    Args:
        name (str): The display name of the folder in Google Drive.
        metadata (Dict): Authentication metadata containing Google user credentials.

    Returns:
        dict: Standardized response from `make_response`:
            - success (bool): Whether the folder ID was resolved successfully.
            - tool (str): Tool name (`"get_folder_id"`).
            - message (str): Human-readable status message.
            - data (dict, optional): On success, contains:
                {
                    "folder_id": str  # Internal ID of the matched folder.
                }

    Notes:
        - `folder_id` is returned for tool chaining (e.g., listing contents).
        - The folder ID should not be directly shown to the user in the UI.
        - Requires prior authentication via Google OAuth (validated in `metadata`).
        - If no folder matches the provided name, raises a `FileNotFoundError`.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return make_response(False, "get_folder_id", "❌ Email not authorized")

    service = build("drive", "v3", credentials=creds)
    try:
        folder_id = resolve_drive_id_by_name(service, name, is_folder=True)
        return make_response(
            True,
            "get_folder_id",
            f"Folder ID for '{name}' resolved.",
            {"folder_id": folder_id},
        )
    except FileNotFoundError as e:
        return make_response(False, "get_folder_id", f"{str(e)}")


def gdrive_download_file_content(service, file_id: str) -> Dict:
    """
    Download and extract the textual content of a file from Google Drive.

    Args:
        service: An authenticated Google Drive API service instance (`googleapiclient.discovery.Resource`).
        file_id (str): The unique ID of the file to download.

    Returns:
        dict: Extracted text content from the file. Behavior varies by type:
            - For PDFs ≤ MAX_PDF_SIZE (~400KB): Returns extracted text using PyMuPDF.
            - For PDFs > MAX_PDF_SIZE: Returns a JSON string containing a "delegate" instruction
              suggesting the use of `vector_ingest_gdrive` for embedding-based search.
            - For text-like files (CSV, TXT, JSON, Markdown): Returns decoded UTF-8 text.
            - For PDFs without extractable text: Returns a warning message.

    Notes:
        - Uses Google Drive API's `files().get_media()` to fetch raw bytes.
        - Uses PyMuPDF (`fitz`) for PDF text extraction with whitespace preserved.
        - Large PDFs are not directly returned; instead, a delegation hint is provided
          to offload them to a vector ingestion pipeline.
        - Decoding errors in text files are ignored (`errors="ignore"`).
        - This function is a low-level helper used internally by higher-level tools
          (e.g., `gdrive_fetch_file`) and should not be exposed directly in the UI.
    """
    # Step 1: Check metadata for file size & type
    file_meta = (
        service.files().get(fileId=file_id, fields="name, mimeType, size").execute()
    )
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
            return {
                "delegate": "vector_ingest_gdrive",
                "key": file_meta.get("name", "unknown"),
                "reason": (
                    f"File size {file_size} exceeds threshold "
                    "{file_size/1024:.1f} KB. "
                    "Use 'vector_ingest_gdrive' for ingesting the chunks as embeddings "
                    "and searching."
                ),
            }

        # Memory-efficient PyMuPDF extraction
        text_parts = []
        with fitz.open(stream=fh.read(), filetype="pdf") as doc:
            for page in doc:
                text = page.get_text(
                    "text", flags=fitz.TEXT_PRESERVE_WHITESPACE
                )  # type: ignore
                if text.strip():
                    text_parts.append(text)
        return {
            "data": (
                "\n".join(text_parts)
                if text_parts
                else "No extractable text found in PDF."
            )
        }

    else:
        # Assume text-like file (CSV, TXT, JSON, etc.)
        return {"data": fh.read().decode("utf-8", errors="ignore")}


@mcp.tool(name="gdrive_fetch_file")
def gdrive_fetch_file(file_id: str, metadata: Dict = {}) -> dict:
    """
    Fetch the content of a file by its ID from Google Drive.

    Args:
        file_id (str): The unique ID of the file to fetch.
                       Returned `id` is for internal chaining only, not for UI display.
        metadata (Dict, optional): Dictionary containing authentication credentials
                                   (e.g., `access_token`, `refresh_token`, `email`).

    Returns:
        dict: A standardized response object from `make_response`, containing:
            - success (bool): Operation success flag.
            - source (str): The name of the tool ("gdrive_fetch_file").
            - message (str): Human-readable status message.
            - data (Dict): A dictionary with:
                - id (str): File ID (for internal use only).
                - name (str): File name (safe for UI).
                - url (str): Google Drive web URL for the file (safe for UI).
                - content (str): Extracted file content (if supported).

    Notes:
        - Supports text files (`.txt`, `.md`, `.csv`, `.json`) and PDFs.
        - PDFs larger than ~400KB are rejected to save memory.
        - For large PDFs, prefer using a vector search tool instead.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return make_response(
            False, "gdrive_fetch_file", "❌ Email not authorized. Please login first."
        )

    service = build("drive", "v3", credentials=creds)

    try:
        # Fetch file metadata (name + mimeType)
        file_metadata = (
            service.files().get(fileId=file_id, fields="id, name, mimeType").execute()
        )

        # Fetch file content
        content = gdrive_download_file_content(service, file_id)

        return make_response(
            True,
            "gdrive_fetch_file",
            "File content fetched.",
            {
                "id": file_metadata["id"],  # chaining only
                "name": file_metadata.get("name"),
                "url": f"https://drive.google.com/file/d/{file_metadata['id']}/view",
                "content": content,
            },
        )
    except Exception as e:
        return make_response(
            False, "gdrive_fetch_file", f"Error downloading file: {str(e)}"
        )


@mcp.tool(name="gdrive_list_drive_folders")
def list_drive_folders(metadata: Dict) -> dict:
    """
    List all non-trashed folders in the user's Google Drive.

    Args:
        metadata (Dict): Dictionary containing authentication credentials
                        (e.g., `access_token`, `refresh_token`, `email`).

    Returns:
        dict: A standardized response object from `make_response`, containing:
            - success (bool): Operation success flag.
            - source (str): The name of the tool ("list_drive_folders").
            - message (str): Human-readable status message.
            - data (List[Dict]): List of folders with:
                - id (str): The folder ID (For internal chaining only, not UI display).
                - name (str): Folder name (safe for UI).
                - mimeType (str): Google Drive MIME type.
                - url (str): Web URL to open the folder in Google Drive (safe for UI).

    Notes:
        - `id` is returned only for internal tool chaining and **must not** be displayed in UI.
        - Safe fields for UI: `name`, `url`.
    """
    service = get_drive_service(metadata)
    results = (
        service.files()
        .list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name, mimeType)",
        )
        .execute()
    )

    folders = [
        {
            "id": f["id"],  # kept for chaining, not for UI
            "name": f["name"],
            "mimeType": f["mimeType"],
            "url": f"https://drive.google.com/drive/folders/{f['id']}",
        }
        for f in results.get("files", [])
    ]

    return make_response(
        True, "list_drive_folders", f"Found {len(folders)} folders.", folders
    )


@mcp.tool(name="gdrive_list_drive_folder_contents")
def list_drive_folder_contents(folder_id: str, metadata: Dict) -> dict:
    """
    List files/folders inside a given folder.

    Args:
        folder_id (str): The ID of the parent folder in Google Drive.
        metadata (Dict): Authentication and context information for the request.

    Returns:
        dict: A response object from `make_response` containing:
            - success (bool): Whether the operation succeeded.
            - source (str): Tool name.
            - message (str): Human-readable description of the result.
            - data (list[dict]): List of files/folders with fields:
                - id (str): File/folder ID (for tool chaining only, not UI).
                - name (str): File/folder name.
                - type (str): MIME type (for tool chaining only, not UI).
                - url (str): Direct Google Drive web link to the file.

    NOTE:
        - `id`, `type` is returned for tool chaining (e.g., download or nested queries).
        - The UI should not display `id` and `type` directly to the user.
        - Safe fields for UI: `name`, `url`.
    """
    service = get_drive_service(metadata)
    query = f"'{folder_id}' in parents and trashed = false"
    results = (
        service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    )

    files = results.get("files", [])
    data = [
        {
            "id": f["id"],  # kept for tool chaining
            "name": f["name"],
            "type": f.get("mimeType"),
            "url": f"https://drive.google.com/file/d/{f['id']}/view",
        }
        for f in files
    ]

    return make_response(
        True,
        "gdrive_list_drive_folder_contents",
        f"Found {len(data)} items in folder.",
        data,
    )


@mcp.tool(name="gdrive_list_all_files")
def gdrive_list_all_files(
    folder_id: str, metadata: Dict = {}, depth: int = 2, file_type: str = "text"
) -> dict:
    """
    Recursively list all files in the user's Google Drive.

    Args:
        folder_id (str): The ID of the parent folder in Google Drive.
        metadata (Dict, optional): Authentication and context information for the request.
        depth (int, optional): Maximum depth of folder recursion. Defaults to 2.
        file_type (str, optional): Type of files to return:
            - "text": Only return text-like files (.txt, .csv, .json, Google Docs, etc.)
            - "all": Return all file types.

    Returns:
        dict: A response object from `make_response` containing:
            - success (bool): Whether the operation succeeded.
            - source (str): Tool name.
            - message (str): Human-readable description of the result.
            - data (list[dict]): List of files with fields:
                - id (str): File ID (for tool chaining only, not UI).
                - name (str): File name.
                - type (str): MIME type (for tool chaining only, not UI).
                - url (str): Direct Google Drive web link to the file.

    NOTE:
        - `id`, `type` is returned for tool chaining only.
        - The UI should not display `id`, `type` directly to the user.
        - Safe fields for UI: `name`, `url`.
    """
    creds = get_google_creds(metadata)
    if not creds:
        return make_response(False, "list_all_files", "❌ Email not authorized.")

    service = build("drive", "v3", credentials=creds)
    all_files = _gdrive_recursive_list(
        service, folder_id, current_depth=0, max_depth=depth, file_type=file_type
    )
    if not all_files:
        return make_response(
            True, "gdrive_list_all_files", "No matching files found.", []
        )

    data = [
        {
            "id": f["id"],  # kept for chaining
            "name": f["name"],
            "type": f.get("mimeType"),
            "url": f"https://drive.google.com/file/d/{f['id']}/view",
        }
        for f in all_files
    ]

    return make_response(
        True, "gdrive_list_all_files", f"Found {len(data)} files.", data
    )


def _gdrive_recursive_list(
    service, folder_id: str, current_depth: int, max_depth: int, file_type: str = "all"
) -> list:
    """
    Helper to recursively list all files and subfolders up to a specified depth,
    with optional filtering by file type.

    Args:
        service: An authorized Google Drive API service instance (from `googleapiclient.discovery.build`).
        folder_id (str): The ID of the current folder to list files from.
        current_depth (int): The current recursion depth.
        max_depth (int): The maximum depth to recurse into subfolders.
        file_type (str, optional): Controls which files to include.
            - "all": Include all file types.
            - "text": Include only text-like files (.txt, .md, .csv, .json, Google Docs).

    Returns:
        list: A list of file dictionaries, where each dictionary contains:
            - id (str): File ID.
            - name (str): File name.
            - mimeType (str): File MIME type.

    Notes:
        - Subfolders are recursively traversed until `max_depth` is reached.
        - Only files matching the allowed MIME types or extensions are included when `file_type="text"`.
        - Returned file objects match the structure returned by the Google Drive API.
    """
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
        response = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
            )
            .execute()
        )
        for file in response.get("files", []):
            mime = file["mimeType"]
            name = file["name"]

            if mime == "application/vnd.google-apps.folder":
                all_files.extend(
                    _gdrive_recursive_list(
                        service, file["id"], current_depth + 1, max_depth, file_type
                    )
                )
            else:
                if file_type == "all":
                    all_files.append(file)
                elif mime in allowed_mime_types or any(
                    name.lower().endswith(ext) for ext in allowed_extensions
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
        return JSONResponse(
            {"error": "Token exchange failed", "details": token_resp}, status_code=400
        )

    # Get user info using the access_token
    user_info = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo?alt=json",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    email = user_info.get("email")

    if not email:
        return JSONResponse(
            {"error": "Failed to get user email", "user_info": user_info},
            status_code=400,
        )

    print(f"✅ Authenticated: {email}")
    return JSONResponse(
        {
            "message": f"Authenticated as {email}",
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
        }
    )


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
