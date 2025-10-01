# -*- coding: utf-8 -*-
import os
import json
import asyncio
import datetime
import aiohttp
from io import BytesIO
from typing import Dict, Optional, List
import jwt
import nest_asyncio
import requests
from dotenv import load_dotenv
from docx import Document
import fitz  # PyMuPDF
from azure.core.credentials import TokenCredential, AccessToken
from azure.identity.aio import AuthorizationCodeCredential

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from msgraph import GraphServiceClient

nest_asyncio.apply()
load_dotenv()

CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
TENANT_ID = os.getenv("AZURE_TENANT_ID")
REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI")
PORT = int(os.getenv("AZURE_MCP_PORT", "8007"))
SCOPES = ["User.Read", "Files.Read", "Sites.Read.All", "offline_access"]

AUTH_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

user_tokens: Dict[str, Dict] = {}
graph_clients: Dict[str, GraphServiceClient] = {}

mcp = FastMCP("onedrive-mcp")


def build_auth_url() -> str:
    """
    Build the OAuth2 authorization URL for Microsoft login.

    Returns:
        str: The constructed authorization URL.
    """
    return (
        f"{AUTH_URL}?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_mode=query"
        f"&scope={' '.join(SCOPES)}"
    )


class ManualTokenCredential(TokenCredential):
    def __init__(self, access_token: str):
        """
        Initialize ManualTokenCredential with an access token.

        Args:
            access_token (str): The OAuth2 access token.
        """
        self._access_token = access_token

    async def get_token(self, *scopes, **kwargs) -> AccessToken:
        """
        Get an access token for the requested scopes.

        Args:
            *scopes: Scopes for the token.
            **kwargs: Additional keyword arguments.

        Returns:
            AccessToken: The access token object.
        """
        expires_on = int(
            (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).timestamp()
        )
        return AccessToken(self._access_token, expires_on)

    async def close(self):
        """
        Close the credential (no-op for manual token).
        """
        pass


def get_onedrive_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Retrieve and refresh OneDrive credentials from metadata.

    Args:
        metadata (Optional[Dict]): Metadata containing access and refresh tokens.

    Returns:
        Optional[Dict]: Credentials dict if valid, else None.
    """
    if not metadata:
        return None

    creds = metadata
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")

    if access_token and is_token_valid(access_token):
        return creds

    # Attempt refresh if access token is missing or expired
    if refresh_token:
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": REDIRECT_URI,
            },
        ).json()

        new_access = resp.get("access_token")
        if new_access:
            creds["access_token"] = new_access
            creds["refresh_token"] = resp.get("refresh_token", refresh_token)
            return creds

    return None


def is_token_valid(token: str) -> bool:
    """
    Check if a JWT token is valid (not expired).

    Args:
        token (str): JWT access token.

    Returns:
        bool: True if valid, False otherwise.
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        return (
            exp and datetime.datetime.utcfromtimestamp(exp) > datetime.datetime.utcnow()
        )
    except Exception:
        return False


def sanitize_folder_id(folder_id: Optional[str]) -> str:
    """
    Return folder_id or 'root' if not provided or empty.

    Args:
        folder_id (Optional[str]): Folder ID string.

    Returns:
        str: Sanitized folder ID.
    """
    return folder_id if folder_id and folder_id.strip() else "root"


async def get_graph_client(metadata: Dict) -> Optional[GraphServiceClient]:
    """
    Get an authenticated Microsoft Graph client.

    Args:
        metadata (Dict): Credentials metadata containing access token.

    Returns:
        Optional[GraphServiceClient]: Authenticated client or None.
    """
    creds = get_onedrive_creds(metadata)
    if not creds:
        raise ValueError("Missing or invalid Microsoft Graph credentials.")

    access_token = creds.get("access_token")
    credential = ManualTokenCredential(access_token)
    return GraphServiceClient(credential, scopes=SCOPES)


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


@mcp.tool(name="onedrive_list_files")
def list_files(metadata: Dict) -> dict:
    """
    List files from the root of the user's default OneDrive.

    Args:
        metadata (Dict): Credentials metadata for authentication.

    Returns:
        dict: Standardized response from `make_response` with:
            - success (bool): True if files retrieved, False otherwise.
            - action (str): Tool/action name (`onedrive_list_files`).
            - message (str): Human-readable summary for UI (no IDs shown).
            - data (list[dict]): Full file info for tool chaining, including:
                - name (str)  -- safe for UI
                - id (str)  -- NOT Safe for UI, only used for internal tool chaining
                - drive_id (str)  -- NOT Safe for UI, only used for internal tool chaining
                - web_url (str | None)  -- safe for UI
                - size (int | None)
                - mime_type (str | None)  -- safe for UI
                - is_folder (bool)
    """

    async def inner():
        """
        Async implementation of OneDrive file listing.

        Workflow:
            1. Initialize Microsoft Graph client using metadata credentials.
            2. Fetch the user's default drive.
            3. Retrieve children of the root folder.
            4. Normalize each item into a structured dict with:
                - metadata for tool chaining (id, drive_id, web_url, size, mime_type)
                - safe fields for UI (name, is_folder).
            5. Build a human-readable summary string for the UI.

        Returns:
            dict: Standardized `make_response` output containing both
                  a user-friendly summary (`message`) and detailed data (`data`).
        """
        client = await get_graph_client(metadata)
        if not client:
            return make_response(False, "onedrive_list_files", "❌ Not authorized.", [])

        drive = await client.me.drive.get()
        root_children = (
            await client.drives.by_drive_id(drive.id)
            .items.by_drive_item_id("root")
            .children.get()
        )

        files = []
        for f in root_children.value:
            files.append(
                {
                    "name": f.name,
                    "id": f.id,
                    "drive_id": drive.id,
                    "web_url": getattr(f, "web_url", None),
                    "size": getattr(f, "size", None),
                    "mime_type": (
                        getattr(f, "file", {}).get("mimeType") if f.file else None
                    ),
                    "is_folder": bool(f.folder),
                }
            )

        response_str = "Files retrieved successfully."
        return make_response(True, "onedrive_list_files", response_str, files)

    return asyncio.run(inner())


async def async_fetch_and_extract_text(download_url: str, file_name: str) -> str | None:
    """
    Download a OneDrive file and extract its plain text content.

    Supports `.txt`, `.docx`, and `.pdf` files. For unsupported file types,
    returns `None`.

    Args:
        download_url (str): Pre-authenticated Microsoft Graph download URL for the file.
        file_name (str): File name including extension (used to determine parser).

    Returns:
        str | None: Extracted text content as a string, or `None` if extraction fails.

    Raises:
        aiohttp.ClientError: If there are network-related issues while downloading.
        UnicodeDecodeError: For `.txt` files with invalid encoding.
        fitz.FileDataError: For corrupted or invalid PDF data.

    Notes:
        - `.txt` → UTF-8 decoded with errors ignored.
        - `.docx` → Concatenates all paragraph texts.
        - `.pdf` → Concatenates all pages' text.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(download_url) as resp:
            if resp.status != 200:
                return None
            file_bytes = await resp.read()

    if file_name.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore")
    elif file_name.endswith(".docx"):
        return "\n".join(p.text for p in Document(BytesIO(file_bytes)).paragraphs)
    elif file_name.endswith(".pdf"):
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    return None


async def async_get_file_info(client, drive_id: str, file_id: str):
    """
    Retrieve OneDrive file metadata and its download URL.

    Args:
        client: Authenticated Microsoft Graph client (via `get_graph_client`).
        drive_id (str): ID of the OneDrive drive that contains the file.
        file_id (str): ID of the file to retrieve.

    Returns:
        tuple[file_item | None, str | None]:
            - file_item: Microsoft Graph file object, or None if retrieval fails.
            - download_url: Pre-authenticated download URL, or None if missing.

    Raises:
        Exception: If Graph API request fails.

    Notes:
        - The `file_item` may contain additional metadata (size, mime type, web_url, etc.).
        - The `@microsoft.graph.downloadUrl` is short-lived and should be used immediately.
    """
    try:
        file_item = (
            await client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(file_id)
            .get()
        )
        download_url = file_item.additional_data.get("@microsoft.graph.downloadUrl")
        return file_item, download_url
    except Exception:
        return None, None


@mcp.tool(name="onedrive_search_file_content")
def search_file_content(metadata: Dict, drive_id: str, file_id: str, keyword: str) -> dict:
    """
    Search for a keyword inside a OneDrive file (.txt, .docx, .pdf).

    Uses Microsoft Graph API to download and extract file content, then performs
    a case-insensitive keyword search. Returns standardized response for UI and
    tool chaining.

    Args:
        metadata (Dict): Credentials metadata for Microsoft Graph authentication.
        drive_id (str): OneDrive drive ID containing the file.
        file_id (str): File ID within the drive.
        keyword (str): Keyword to search for in file content.

    Returns:
        dict: Standardized `make_response` dictionary with:
            - success (bool): True if operation succeeded.
            - action (str): "onedrive_search_file_content".
            - message (str): Human-readable summary ("Keyword found..." etc.).
            - data (list[dict]): Single-item list with:
                - name (str): File name (UI safe).
                - id (str): File ID (internal use only).
                - drive_id (str): Drive ID (internal use only).
                - web_url (str | None): File's OneDrive/SharePoint web URL.
                - size (int | None): File size in bytes.
                - mime_type (str | None): MIME type.
                - is_folder (bool): Whether the item is a folder.
                - match (bool): True if keyword was found in the content.

    Notes:
        - Only `.txt`, `.docx`, `.pdf` files are supported for content search.
        - Response is designed for chaining into other MCP tools.
    """
    async def inner():
        client = await get_graph_client(metadata)
        if not client:
            return make_response(False, "onedrive_search_file_content", "❌ Not authorized.", [])

        file_item, download_url = await async_get_file_info(client, drive_id, file_id)
        if not (file_item and download_url):
            return make_response(False, "onedrive_search_file_content", "❌ Could not access file.", [])

        content = await async_fetch_and_extract_text(download_url, file_item.name)
        if content is None:
            return make_response(False, "onedrive_search_file_content", "❌ Could not extract content.", [])

        match = keyword.lower() in content.lower()
        data = [{
            "name": file_item.name,
            "id": file_item.id,
            "drive_id": drive_id,
            "web_url": getattr(file_item, "web_url", None),
            "size": getattr(file_item, "size", None),
            "mime_type": file_item.file.mime_type if file_item.file else None,
            "is_folder": bool(file_item.folder),
            "match": match,
        }]
        msg = f"Keyword {'found' if match else 'not found'} in '{file_item.name}'."
        return make_response(True, "onedrive_search_file_content", msg, data)

    return asyncio.run(inner())


@mcp.tool(name="onedrive_get_file_content")
def get_file_content(metadata: Dict, drive_id: str, file_id: str) -> dict:
    """
    Download and extract the plain text content of a OneDrive file.

    Uses Microsoft Graph API to fetch the file, download its content, and parse
    supported formats (`.txt`, `.docx`, `.pdf`). Returns a standardized response
    suitable for UI display or tool chaining.

    Args:
        metadata (Dict): Credentials metadata for Microsoft Graph authentication.
        drive_id (str): OneDrive drive ID containing the file.
        file_id (str): File ID within the drive.

    Returns:
        dict: Standardized `make_response` dictionary with:
            - success (bool): True if operation succeeded.
            - action (str): "onedrive_get_file_content".
            - message (str): Human-readable summary ("Successfully retrieved...").
            - data (list[dict]): Single-item list with:
                - name (str): File name (UI safe).
                - id (str): File ID (internal use only).
                - drive_id (str): Drive ID (internal use only).
                - web_url (str | None): File's OneDrive/SharePoint web URL.
                - size (int | None): File size in bytes.
                - mime_type (str | None): MIME type.
                - is_folder (bool): Whether the item is a folder.
                - content (str): Extracted plain text content of the file.

    Notes:
        - Designed for LLM ingestion, indexing, or summarization pipelines.
        - Supports `.txt`, `.docx`, and `.pdf`. Unsupported formats return an error.
    """
    async def inner():
        client = await get_graph_client(metadata)
        if not client:
            return make_response(False, "onedrive_get_file_content", "❌ Not authorized.", [])

        file_item, download_url = await async_get_file_info(client, drive_id, file_id)
        if not (file_item and download_url):
            return make_response(False, "onedrive_get_file_content", "❌ Could not access file.", [])

        content = await async_fetch_and_extract_text(download_url, file_item.name)
        if content is None:
            return make_response(False, "onedrive_get_file_content", "❌ Could not extract content.", [])

        data = [{
            "name": file_item.name,
            "id": file_item.id,
            "drive_id": drive_id,
            "web_url": getattr(file_item, "web_url", None),
            "size": getattr(file_item, "size", None),
            "mime_type": file_item.file.mime_type if file_item.file else None,
            "is_folder": bool(file_item.folder),
            "content": content,
        }]
        msg = f"Successfully retrieved content for '{file_item.name}'."
        return make_response(True, "onedrive_get_file_content", msg, data)

    return asyncio.run(inner())


async def fetch_file_bytes(url: str) -> bytes | None:
    """
    Download file bytes from OneDrive given a direct download URL.

    This function creates a new aiohttp session for each request,
    retrieves the file contents, and returns the raw bytes.

    Args:
        url (str): Pre-authenticated download URL obtained from
            `@microsoft.graph.downloadUrl`.

    Returns:
        bytes | None: Raw file bytes if the request succeeds, else None.
    """
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                return await resp.read()
    return None


def extract_text(file_bytes: bytes, file_name: str) -> str:
    """
    Extract text from supported file types (.txt, .docx, .pdf).

    Supported formats:
        - `.txt` : UTF-8 decoded text
        - `.docx`: Concatenated text from all paragraphs
        - `.pdf` : Extracted text from each page

    Args:
        file_bytes (bytes): Raw bytes of the file.
        file_name (str): File name (used to determine format).

    Returns:
        str: Extracted text content, or an empty string if format is unsupported.
    """
    if file_name.endswith(".txt"):
        return file_bytes.decode("utf-8", errors="ignore")
    elif file_name.endswith(".docx"):
        return "\n".join(p.text for p in Document(BytesIO(file_bytes)).paragraphs)
    elif file_name.endswith(".pdf"):
        with fitz.open(stream=file_bytes, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    return ""


async def search_file(client, file_item, drive_id: str, keyword: str) -> dict | None:
    """
    Search a single OneDrive file for a keyword.

    Downloads the file, extracts its text (if supported), and checks if the keyword
    exists in the content. Returns a metadata dictionary if matched.

    Args:
        client: Microsoft Graph API client (unused here but kept for interface consistency).
        file_item: Graph API DriveItem object containing file metadata.
        drive_id (str): ID of the drive containing the file.
        keyword (str): Keyword to search for in the file content.

    Returns:
        dict | None: Metadata dict if keyword found, else None.
            Keys:
                - name (str)
                - id (str)
                - drive_id (str)
                - web_url (str | None)
                - size (int | None)
                - mime_type (str | None)
                - is_folder (bool)
                - match (bool) (always True if returned)
    """
    download_url = file_item.additional_data.get("@microsoft.graph.downloadUrl")
    if not download_url:
        return None

    file_bytes = await fetch_file_bytes(download_url)
    if not file_bytes:
        return None

    text = extract_text(file_bytes, file_item.name)
    if keyword.lower() not in text.lower():
        return None

    return {
        "name": file_item.name,
        "id": file_item.id,
        "drive_id": drive_id,
        "web_url": getattr(file_item, "web_url", None),
        "size": getattr(file_item, "size", None),
        "mime_type": file_item.file.mime_type if file_item.file else None,
        "is_folder": bool(file_item.folder),
        "match": True,
    }


async def recursive_search(
    client, drive_id: str, current_folder_id: str, keyword: str
) -> list:
    """
    Recursively search through a folder and its subfolders for keyword matches.

    Traverses all child items in the given folder:
        - Recurse into subfolders.
        - If file is `.txt`, `.docx`, or `.pdf`, extract text and search for keyword.

    Args:
        client: Microsoft Graph API client for OneDrive/SharePoint.
        drive_id (str): ID of the drive being searched.
        current_folder_id (str): ID of the folder to start searching from.
        keyword (str): Keyword to search for in file content.

    Returns:
        list[dict]: List of matching file metadata dictionaries.
    """
    results = []
    children = (
        await client.drives.by_drive_id(drive_id)
        .items.by_drive_item_id(current_folder_id)
        .children.get()
    )
    for item in children.value:
        if item.folder:
            results.extend(await recursive_search(client, drive_id, item.id, keyword))
        elif item.name.lower().endswith((".txt", ".docx", ".pdf")):
            file_result = await search_file(client, item, drive_id, keyword)
            if file_result:
                results.append(file_result)
    return results


@mcp.tool(name="onedrive_search_folder_for_content")
def search_folder_for_content(
    metadata: Dict, drive_id: str, folder_id: str, keyword: str
) -> dict:
    """
    Recursively search for a keyword inside `.txt`, `.docx`, and `.pdf` files
    in a specified OneDrive folder and its subfolders.

    This function:
        1. Authenticates with Microsoft Graph using metadata.
        2. Recursively traverses the folder and subfolders.
        3. Downloads and extracts text from supported file formats.
        4. Filters files that contain the given keyword.
        5. Returns standardized output for UI and tool chaining.

    Args:
        metadata (Dict): Authentication metadata containing user tokens.
        drive_id (str): ID of the OneDrive drive containing the folder.
        folder_id (str): ID of the folder to start searching in.
        keyword (str): Keyword to search for inside file contents.

    Returns:
        dict: Standardized `make_response` output containing:
            - success (bool): True if search completed without errors.
            - action (str): Tool/action name (`onedrive_search_folder_for_content`).
            - message (str): Human-readable summary for UI.
            - data (list[dict]): Matching file metadata dictionaries, each with:
                - name (str)
                - id (str)
                - drive_id (str)
                - web_url (str | None)
                - size (int | None)
                - mime_type (str | None)
                - is_folder (bool)
                - match (bool)
    """

    async def inner():
        """Async wrapper for authentication, recursive search, and response building."""
        client = await get_graph_client(metadata)
        if not client:
            return make_response(
                False, "onedrive_search_folder_for_content", "❌ Not authorized.", []
            )

        try:
            matches = await recursive_search(client, drive_id, folder_id, keyword)
            if not matches:
                return make_response(
                    True,
                    "onedrive_search_folder_for_content",
                    "No matching files found in folder.",
                    [],
                )
            message = f"✅ Found {len(matches)} file(s) containing '{keyword}'."
            return make_response(
                True, "onedrive_search_folder_for_content", message, matches
            )
        except Exception as e:
            return make_response(
                False, "onedrive_search_folder_for_content", f"❌ Error: {str(e)}", []
            )

    return asyncio.run(inner())





@mcp.tool(name="onedrive_list_all_drives")
def list_all_drives(metadata: Dict) -> dict:
    """
    List all OneDrive and SharePoint drives accessible to the authenticated user.

    Args:
        metadata (Dict): Credentials metadata for authentication.

    Returns:
        dict: Standardized `make_response` output containing:
            - success (bool): True if drives were retrieved, False otherwise.
            - action (str): Tool/action name (`onedrive_list_all_drives`).
            - message (str): Human-readable summary.
            - data (list[dict]): List of drives with detailed info for tool chaining:
                - id (str) -- drive ID (NOT Safe for UI, only for internal tool chaining)
                - name (str) -- drive name (safe for UI)
                - drive_type (str) -- "personal" or "sharepoint"
                - owner (str | None) -- owner of the drive (if available)
                - web_url (str | None) -- direct web URL for drive (if available)
    """

    async def inner():
        client = await get_graph_client(metadata)
        if not client:
            return make_response(
                False, "onedrive_list_all_drives", "❌ Not authorized.", []
            )

        try:
            result = await client.me.drives.get()
            drives = result.value
            drive_list = []

            for drive in drives:
                drive_type = getattr(drive, "driveType", "personal")
                owner = getattr(getattr(drive, "owner", None), "user", None)
                owner_name = getattr(owner, "displayName", None) if owner else None
                web_url = getattr(drive, "webUrl", None)

                drive_list.append(
                    {
                        "id": drive.id,
                        "name": drive.name,
                        "drive_type": drive_type,
                        "owner": owner_name,
                        "web_url": web_url,
                    }
                )

            message = f"{len(drive_list)} drive(s) found."
            return make_response(True, "onedrive_list_all_drives", message, drive_list)

        except Exception as e:
            return make_response(
                False, "onedrive_list_all_drives", f"❌ Error fetching drives: {e}", []
            )

    return asyncio.run(inner())


@mcp.tool(name="onedrive_list_folder")
def list_children_in_drive_item(
    metadata: Dict, drive_id: str, folder_id: str = "root"
) -> dict:
    """
    List all children (files and folders) in a specified OneDrive folder and return
    a standardized response suitable for both UI display and tool chaining.

    This tool fetches the folder contents using the Microsoft Graph API and
    normalizes each item into a structured dictionary with relevant metadata.

    Workflow:
        1. Sanitize the folder ID (defaults to 'root' if not provided).
        2. Initialize the Microsoft Graph client using provided credentials in `metadata`.
        3. Retrieve all child items in the folder.
        4. Normalize each item into a structured dictionary including:
            - `name` (str): File or folder name. Safe for UI display.
            - `id` (str): OneDrive item ID. NOT Safe for UI, only used for internal tool chaining
            - `drive_id` (str): The ID of the drive containing the item. NOT Safe for UI, only used for internal tool chaining
            - `web_url` (str | None): Public or personal web link to the item. Safe for UI display.
            - `size` (int | None): File size in bytes (folders have None). Safe for UI display but in KB or MB.
            - `mime_type` (str | None): MIME type if a file; None for folders. Safe for UI display.
            - `is_folder` (bool): True if the item is a folder, False otherwise.
        5. Build a human-readable summary string for UI display.
        6. Return a standardized `make_response` dictionary containing:
            - `success` (bool): True if retrieval succeeded, False otherwise.
            - `action` (str): Name of the tool (`onedrive_list_folder`).
            - `message` (str): Human-readable summary for UI (names only, no IDs).
            - `data` (list[dict]): Full item information for downstream tool usage.

    Args:
        metadata (Dict): Credentials metadata for authentication. Must contain
                         valid access token (and optionally refresh token).
        drive_id (str): ID of the OneDrive drive to list items from.
        folder_id (str, optional): ID of the folder to list. Defaults to 'root'.

    Returns:
        dict: Standardized response via `make_response` with keys:
            - success (bool)
            - action (str)
            - message (str)
            - data (list[dict])
    """

    async def inner():
        """
        Async implementation for fetching and normalizing OneDrive folder children.

        Returns:
            dict: Standardized `make_response` output with success, message, and data.
        """
        folder_id_sanitized = sanitize_folder_id(folder_id)
        client = await get_graph_client(metadata)
        if not client:
            return make_response(
                False, "onedrive_list_folder", "❌ Not authorized.", []
            )

        try:
            response = (
                await client.drives.by_drive_id(drive_id)
                .items.by_drive_item_id(folder_id_sanitized)
                .children.get()
            )
        except Exception as e:
            return make_response(
                False, "onedrive_list_folder", f"❌ Error accessing folder: {e}", []
            )

        items = []
        for item in response.value:
            items.append(
                {
                    "name": item.name,
                    "id": item.id,
                    "drive_id": drive_id,
                    "web_url": getattr(item, "web_url", None),
                    "size": getattr(item, "size", None),
                    "mime_type": item.file.mime_type if item.file else None,
                    "is_folder": bool(item.folder),
                }
            )

        summary = "\n".join(
            f"📄 {i['name']} ({'Folder' if i['is_folder'] else 'File'})" for i in items
        )

        return make_response(True, "onedrive_list_folder", summary, items)

    return asyncio.run(inner())


@mcp.tool(name="onedrive_find_files_by_name")
def find_files_by_name(metadata: Dict, keyword: str, folder_id: str = "root") -> str:
    """
    Recursively search for files by name in user's default OneDrive.

    Args:
        metadata (Dict): Credentials metadata for authentication.
        keyword (str): Keyword to match file names.
        folder_id (str, optional): Folder ID to start search. Defaults to "root".

    Returns:
        str: JSON string of matching files or error message.
    """

    folder_id = sanitize_folder_id(folder_id)

    async def get_preferred_drive(client):
        result = await client.me.drives.get()
        for drive in result.value:
            if drive.name == "OneDrive":
                return drive.id
        if result.value:
            return result.value[0].id
        raise Exception("No drives found.")

    async def recursive_search(client, drive_id, folder_id, keyword):
        matches = []
        response = (
            await client.drives.by_drive_id(drive_id)
            .items.by_drive_item_id(folder_id)
            .children.get()
        )

        for item in response.value:
            if keyword.lower() in item.name.lower():
                matches.append(
                    {"name": item.name, "id": item.id, "web_url": item.web_url}
                )
            if item.folder:
                matches += await recursive_search(client, drive_id, item.id, keyword)
        return matches

    async def main():
        client = await get_graph_client(metadata)
        if not client:
            return "❌ Not authorized."
        drive_id = await get_preferred_drive(client)
        return await recursive_search(client, drive_id, folder_id, keyword)

    result = asyncio.run(main())
    return json.dumps(result)


@mcp.tool(name="sharepoint_list_sites")
def sharepoint_list_sites(metadata: Dict) -> dict:
    """
    List available SharePoint sites accessible to the authenticated user.

    Args:
        metadata (Dict): Credentials metadata for authentication.

    Returns:
        dict: Standardized `make_response` output containing:
            - success (bool): True if sites were retrieved, False otherwise.
            - action (str): Tool/action name (`sharepoint_list_sites`).
            - message (str): Human-readable summary for UI.
            - data (list[dict]): List of SharePoint sites with detailed info for tool chaining:
                - site_id (str) -- site ID (NOT safe for UI, used for internal tool chaining)
                - name (str) -- site name (safe for UI)
                - web_url (str | None) -- direct URL to site (safe for UI)
    """
    import aiohttp

    async def inner():
        """
        Async implementation to list SharePoint sites using delegated auth.

        Workflow:
            1. Extract access token from metadata credentials.
            2. Call Microsoft Graph API `/sites?search=*` endpoint.
            3. Normalize each site into a structured dict with:
                - `site_id` for internal use
                - `name` and `web_url` safe for UI
            4. Build a human-readable summary of site names.

        Returns:
            dict: Standardized `make_response` output containing both
                  a user-friendly summary (`message`) and detailed data (`data`).
        """
        creds = get_onedrive_creds(metadata)
        access_token = creds.get("access_token")
        if not access_token:
            return make_response(
                False, "sharepoint_list_sites", "❌ Not authorized.", []
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        url = "https://graph.microsoft.com/v1.0/sites?search=*"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return make_response(
                        False,
                        "sharepoint_list_sites",
                        f"❌ Error fetching SharePoint sites: {resp.status} - {body}",
                        [],
                    )
                data = await resp.json()

        sites = [
            {
                "site_id": site.get("id"),
                "name": site.get("name"),
                "web_url": site.get("webUrl"),
            }
            for site in data.get("value", [])
        ]

        summary = (
            "\n".join(f"📄 {site['name']}" for site in sites)
            or "No SharePoint sites found."
        )
        return make_response(True, "sharepoint_list_sites", summary, sites)

    return asyncio.run(inner())


@mcp.tool(name="sharepoint_list_document_libraries")
def sharepoint_list_document_libraries(metadata: Dict, site_id: str) -> dict:
    """
    List document libraries (drives) in a SharePoint site.
    Always prefer site_id form for SharePoint (hostname, site-id, web-id). Do not pass only short names or URLs.

    Args:
        metadata (Dict): Credentials metadata for authentication.
        site_id (str): SharePoint site ID (e.g., "contoso.sharepoint.com,123,456")
    Returns:
        dict: Standardized `make_response` output containing:
            - success (bool): True if libraries were retrieved, False otherwise.
            - action (str): Tool/action name (`sharepoint_list_document_libraries`).
            - message (str): Human-readable summary for UI.
            - data (list[dict]): List of document libraries with detailed info for tool chaining:
                - drive_id (str) -- drive/library ID (NOT safe for UI, internal use)
                - name (str) -- library name (safe for UI)
                - web_url (str | None) -- web URL of library (safe for UI)
    """

    async def inner():
        """
        Async implementation to list document libraries in a SharePoint site.

        Workflow:
            1. Initialize Microsoft Graph client using metadata credentials.
            2. Determine if `site_id` is in complex format:
                - If complex, extract the actual site ID (middle part).
            3. Fetch drives (document libraries) for the site using Graph API.
            4. Normalize each library into structured dict with:
                - `drive_id` for internal use
                - `name` and `web_url` safe for UI
            5. Build a human-readable summary string for UI.

        Returns:
            dict: Standardized `make_response` output with summary and detailed data.
        """
        client = await get_graph_client(metadata)
        if not client:
            return make_response(
                False, "sharepoint_list_document_libraries", "❌ Not authorized.", []
            )

        try:
            site_id_only = site_id.split(",")[1] if "," in site_id else site_id

            drives = await client.sites.by_site_id(site_id_only).drives.get()

            libraries = [
                {
                    "drive_id": d.id,
                    "name": d.name,
                    "drive_type": getattr(d, "driveType", None),
                    "web_url": getattr(d, "web_url", None),
                }
                for d in drives.value
            ]

            summary = (
                "\n".join(f"📄 {lib['name']}" for lib in libraries)
                or "No document libraries found."
            )
            return make_response(
                True, "sharepoint_list_document_libraries", summary, libraries
            )

        except Exception as e:
            return make_response(
                False,
                "sharepoint_list_document_libraries",
                f"❌ Error listing document libraries: {e}",
                [],
            )

    return asyncio.run(inner())


@mcp.tool(name="sharepoint_list_drive_items")
def sharepoint_list_drive_items(
    metadata: Dict, drive_id: str, folder_id: str = "root"
) -> dict:
    """
    List items in a SharePoint document library (drive) folder.

    This tool normalizes responses from Microsoft Graph into a safe,
    standardized format for LLM use and tool chaining.

    Args:
        metadata (Dict): Credentials metadata for authentication.
        drive_id (str): SharePoint drive identifier (This is not site_id). Supported formats:
            - Complex format: "<hostname>,<site-id>,<drive-id>"
              Example: "contoso.sharepoint.com,abc123,def456"
            - Raw drive ID (Graph `drive.id`).

        folder_id (str, optional): Folder ID within the library. Defaults to "root".

    Returns:
        dict: Standardized response from `make_response`, with keys:
            - success (bool): True if items were retrieved, False otherwise.
            - action (str): Always "sharepoint_list_drive_items".
            - message (str): Human-readable summary for UI (safe text).
            - data (list[dict]): Normalized item details:
                - name (str) -- safe for UI
                - id (str) -- internal only, not UI-safe
                - drive_id (str) -- internal only, not UI-safe
                - web_url (str | None) -- safe for UI
                - size (int | None) -- file size in bytes
                - mime_type (str | None) -- safe for UI
                - is_folder (bool) -- True if folder, False if file

    Notes:
        - If `drive_id` is in complex format, only the last segment is used.
        - site_id should not be passed in-place of drive_id.
        - Items are returned both as a UI-friendly summary and structured data.
        - if only site id is available, use `sharepoint_list_document_libraries` first
          to get the drive_id for the desired library.
    """

    async def inner():
        """
        Async implementation to list items in a SharePoint library folder.

        Workflow:
            1. Initialize Microsoft Graph client using metadata credentials.
            2. Sanitize `folder_id` to ensure valid format.
            3. Resolve `drive_id`:
                - If `drive_id` is a library URL, resolve site → drives → pick matching drive.
                - If `drive_id` is in complex format, extract last part as actual drive ID.
                - Otherwise, reject input as invalid for SharePoint.
            4. Fetch folder children items from Graph API.
            5. Normalize items into structured dicts with safe UI fields.
            6. Build a human-readable summary for UI.

        Returns:
            dict: Standardized `make_response` output with summary and detailed data.
        """
        folder_id_sanitized = sanitize_folder_id(folder_id)
        client = await get_graph_client(metadata)
        if not client:
            return make_response(
                False, "sharepoint_list_drive_items", "❌ Not authorized.", []
            )

        try:
            resolved_drive_id = None

            # Handle complex SharePoint ID (hostname, site-id, drive-id)
            if "," in drive_id:
                resolved_drive_id = drive_id.split(",")[-1]  # take only last part
            else:
                resolved_drive_id = drive_id

            # Fetch items
            resp = (
                await client.drives.by_drive_id(resolved_drive_id)
                .items.by_drive_item_id(folder_id_sanitized)
                .children.get()
            )

            items = [
                {
                    "name": item.name,
                    "id": item.id,
                    "drive_id": resolved_drive_id,
                    "web_url": getattr(item, "web_url", None),
                    "size": getattr(item, "size", None),
                    "mime_type": (
                        getattr(item.file, "mime_type", None) if item.file else None
                    ),
                    "is_folder": bool(item.folder),
                }
                for item in resp.value
            ]

            summary = (
                "\n".join(
                    f"📄 {i['name']} ({'Folder' if i['is_folder'] else 'File'})"
                    for i in items
                )
                or "No items found."
            )

            return make_response(True, "sharepoint_list_drive_items", summary, items)

        except Exception as e:
            return make_response(
                False,
                "sharepoint_list_drive_items",
                f"❌ Error accessing SharePoint drive items: {e}",
                [],
            )

    return asyncio.run(inner())


@mcp.tool(name="onedrive_search_document_libraries")
def onedrive_search_document_libraries(
    metadata: Dict, query: str, drive_ids: List[str]
) -> dict:
    """
    Search for files in one or more OneDrive/SharePoint document libraries.
    First list the libraries with `sharepoint_list_document_libraries`, then
    pass the desired `drive_id`s to this tool to search across them. drive_ids list should have atleast one drive_id.

    Args:
        metadata (Dict): Credentials metadata containing `access_token`.
        query (str): Search string.
        drive_ids (List[str]): List of drive IDs to search.

    Returns:
        dict: Standardized `make_response` output containing:
            - success (bool)
            - action (str)
            - message (str)
            - data (list[dict]): Matching items with:
                - name (str)
                - id (str)
                - drive_id (str)
                - web_url (str | None)
                - size (int | None)
                - mime_type (str | None)
                - is_folder (bool)
    """

    async def inner():
        """Async implementation to search files in document libraries.

        Returns:
            dict: Standardized `make_response` output.
        """
        creds = get_onedrive_creds(metadata)
        if not creds or not creds.get("access_token"):
            return make_response(
                False, "onedrive_search_document_libraries", "❌ Not authorized.", []
            )

        token = creds["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        items = []

        async with aiohttp.ClientSession(headers=headers) as session:
            for drive_id in drive_ids:
                url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/search(q='{query}')"
                try:
                    async with session.get(url) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            return make_response(
                                False,
                                "onedrive_search_document_libraries",
                                f"❌ Error {resp.status}: {text}",
                                [],
                            )
                        data = await resp.json()
                        for item in data.get("value", []):
                            items.append(
                                {
                                    "name": item.get("name"),
                                    "id": item.get("id"),
                                    "drive_id": drive_id,
                                    "web_url": item.get("webUrl"),
                                    "size": item.get("size"),
                                    "mime_type": (
                                        item.get("file", {}).get("mimeType")
                                        if item.get("file")
                                        else None
                                    ),
                                    "is_folder": "folder" in item,
                                }
                            )
                except Exception as e:
                    return make_response(
                        False,
                        "onedrive_search_document_libraries",
                        f"❌ Exception while searching drive {drive_id}: {e}",
                        [],
                    )

        summary = (
            "\n".join(
                f"📄 {i['name']} ({'Folder' if i['is_folder'] else 'File'})"
                for i in items
            )
            or f"No results found for query '{query}'."
        )

        return make_response(True, "onedrive_search_document_libraries", summary, items)

    return asyncio.run(inner())


# ────── OAuth Endpoints ──────
async def authorize(request: Request):
    """
    Redirect user to Microsoft OAuth2 authorization URL.

    Args:
        request (Request): Starlette request object.

    Returns:
        RedirectResponse: Redirect to authorization URL.
    """
    return RedirectResponse(build_auth_url())


async def oauth2callback(request: Request):
    """
    Handle OAuth2 callback and exchange code for tokens.

    Args:
        request (Request): Starlette request object containing OAuth2 code.

    Returns:
        JSONResponse: Authentication result and tokens or error.
    """
    code = request.query_params.get("code")
    data = {
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": CLIENT_SECRET,
    }

    token_resp = requests.post(TOKEN_URL, data=data).json()
    access_token = token_resp.get("access_token")
    if not access_token:
        return JSONResponse(
            {"error": "OAuth failed", "details": token_resp}, status_code=400
        )

    userinfo = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    email = userinfo.get("userPrincipalName") or userinfo.get("mail")
    if not email:
        return JSONResponse({"error": "Failed to fetch user info"}, status_code=400)

    refresh_token = token_resp.get("refresh_token", None)

    return JSONResponse(
        {
            "message": f"Authenticated as {email}",
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "code": code,
        }
    )


# ────── Starlette App ──────
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
