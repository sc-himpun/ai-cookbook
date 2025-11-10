# -*- coding: utf-8 -*-
import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Union, List
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from urllib.parse import quote_plus
import uuid


# ─── Config ──────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("CONFLUENCE_CLIENT_ID")
CLIENT_SECRET = os.getenv("CONFLUENCE_CLIENT_SECRET")
print(CLIENT_ID, CLIENT_SECRET)
DEFAULT_SCOPES = (
    "offline_access write:confluence-content read:me "
    "read:confluence-space.summary read:confluence-props "
    "read:confluence-content.all read:confluence-content.summary "
    "search:confluence read:confluence-user"
)
SCOPES = os.getenv("CONFLUENCE_SCOPES", DEFAULT_SCOPES).split()

PORT = int(os.getenv("CONFLUENCE_MCP_PORT", "8071"))
REDIRECT_URI = os.getenv(
    "CONFLUENCE_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback"
)

# ─── Token Store ──────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────
mcp = FastMCP("confluence-mcp")


def is_access_token_valid(access_token: str) -> bool:
    """
    Check if the provided Confluence access token is still valid by making a request to the /me endpoint.

    Args:
        access_token (str): The OAuth access token to validate.

    Returns:
        bool: True if the token is valid, False otherwise.
    """
    resp = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return resp.status_code == 200


def refresh_confluence_token(refresh_token: str) -> Optional[Dict]:
    """
    Refresh the Confluence OAuth 2.0 access token using a refresh token.

    Args:
        refresh_token (str): The refresh token obtained during initial authentication.

    Returns:
        Optional[Dict]: The new token response dictionary if successful, None otherwise.
    """
    token_url = "https://auth.atlassian.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    response = requests.post(token_url, json=payload)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"[refresh_confluence_token] Failed: {response.text}")
        return None


def get_confluence_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Extract Confluence credentials from metadata and refresh the access token if needed.

    Args:
        metadata (Optional[Dict]): Metadata containing Confluence credentials or a nested 'confluence' key.

    Returns:
        Optional[Dict]: Dictionary with valid credentials (email, access_token, refresh_token, cloud_id, base_url), or None if missing/invalid.
    """
    if not metadata:
        return None
    creds = metadata.get("confluence", metadata)

    email = creds.get("email")
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")
    cloud_id = creds.get("cloud_id")
    base_url = creds.get("base_url")
    if not email or not access_token:
        return None

    if is_access_token_valid(access_token):
        return {
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "cloud_id": cloud_id,
            "base_url": base_url,
        }

    if refresh_token:
        new_tokens = refresh_confluence_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            return {
                "email": email,
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens.get("refresh_token", refresh_token),
                "cloud_id": cloud_id,
                "base_url": creds.get("base_url"),
            }

    return None


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
    """
    return {
        "success": success,
        "action": action,
        "message": message,
        "data": data if data is not None else {},
    }


# ─── Tools ──────────────────────────────────────────────────────────────
@mcp.tool(name="confluence_search_pages")
def search_pages(metadata: Dict, query: str, limit: int = 5) -> dict:
    """
    Search Confluence pages by CQL (title/content) using the v1 REST API.

    This tool queries Confluence Cloud for pages whose title or content matches
    the provided search query. It returns a standardized response using
    `make_response()` for consistency across all MCP tools.

    ---
    ### Args:
    - **metadata** (`Dict`): Dictionary containing user credentials and instance info.
        - `cloud_id` (`str`): Cloud ID of the Confluence site (from Atlassian API).
        - `base_url` (`str`): Base URL of the Confluence instance (e.g. "https://your-domain.atlassian.net").
    - **query** (`str`): The keyword or phrase to search within Confluence page titles and content.
    - **limit** (`int`): Number of pages to return in results (Default - 5)

    ---
    ### Returns:
    A standardized dictionary with the following structure:

    ```json
    {
        "success": bool,                # Whether the API call succeeded
        "action": "search_pages",       # The tool/action name
        "message": str,                 # Human-friendly summary or error message
        "data": [                       # List of page metadata objects (empty if none found)
            {
                "id": str,              # Unique Confluence page ID
                "title": str,           # Page title
                "space": str,           # Space name
                "space_key": str,       # Space key (short identifier)
                "url": str              # Direct URL to the Confluence page
            },
            ...
        ]
    }
    ```

    ---
    ### Behavior:
    - Performs a CQL query:
        `type=page AND (title ~ "{query}" OR text ~ "{query}")`
    - Limits results to 5 pages by default.
    - Returns human-readable messages for both success and error cases.
    - Converts all `None` values to empty strings to maintain schema compliance.
    - Always returns a dictionary — never raw lists or strings.

    ---
    ### Example Usage:
    ```python
    >>> response = search_pages(metadata, "project plan")
    >>> json.dumps(response, indent=2)
    {
        "success": true,
        "action": "search_pages",
        "message": "Found 2 matching pages.",
        "data": [
            {
                "id": "123456",
                "title": "Project Plan Q4",
                "space": "Engineering",
                "space_key": "ENG",
                "url": "https://<your-domain.atlassian.net>/wiki/spaces/ENG/pages/123456"
            },
            {
                "id": "789012",
                "title": "Legacy Project Plan",
                "space": "Archive",
                "space_key": "ARC",
                "url": "https://<your-domain.atlassian.net>/wiki/spaces/ARC/pages/789012"
            }
        ]
    }
    ```

    ---
    ### Error Example:
    ```python
    {
        "success": false,
        "action": "search_pages",
        "message": "❌ Failed to fetch pages (401): Unauthorized",
        "data": {}
    }
    ```
    """
    action = "search_pages"

    # --- Validate credentials ---
    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(False, action, "❌ Confluence credentials not found.")

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    # --- Prepare API request ---
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/search"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {
        "cql": f'type=page AND (title ~ "{query}" OR text ~ "{query}")',
        "limit": limit,
    }

    # --- Perform request ---
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        msg = f"❌ Failed to fetch pages ({resp.status_code}): {resp.text}"
        return make_response(False, action, msg)

    results = resp.json().get("results", [])
    if not results:
        return make_response(
            True, action, f"No pages found for query '{query}'.", data=[]
        )

    # --- Format results ---
    formatted = []
    for r in results:
        content = r.get("content", {})
        page_id = str(content.get("id", ""))  # ensure string
        title = content.get("title", "")
        space = content.get("space", {}).get("name", "")
        space_key = ""
        webui = content.get("_links", {}).get("webui", "")

        if webui and "/spaces/" in webui:
            parts = webui.split("/")
            if "spaces" in parts:
                idx = parts.index("spaces")
                if len(parts) > idx + 1:
                    space_key = parts[idx + 1]

        page_url = f"{base_url}/wiki{webui}" if base_url and webui else ""

        formatted.append(
            {
                "id": page_id,
                "title": title,
                "space": space,
                "space_key": space_key,
                "url": page_url,
            }
        )

    return make_response(
        True, action, f"Found {len(formatted)} matching pages.", data=formatted
    )


@mcp.tool(name="confluence_get_space_details")
def get_space(
    metadata: Dict, space_id: Optional[str] = None, space_key: Optional[str] = None
) -> dict:
    """
    Retrieve detailed information about a Confluence space by either its numeric ID or space key.

    This tool queries the Confluence Cloud v2 API to fetch metadata about a specific space.
    It standardizes all outputs using `make_response()` to ensure consistent structure across all tools.

    ---
    ### Args:
    - **metadata** (`Dict`):
        Dictionary containing Confluence credentials:
        - `cloud_id` (`str`): Cloud ID of the Confluence site (from Atlassian API).
        - `base_url` (`str`): Base URL of the Confluence instance (e.g. "https://your-domain.atlassian.net").

    - **space_id** (`str`, optional):
        The numeric ID of the space (e.g., `"14188548"`).
        Use this when you only know the ID and want to fetch the corresponding key and name.

    - **space_key** (`str`, optional):
        The short key of the space (e.g., `"ENG"`).
        Use this when you know the key and want to fetch the numeric ID and name.

    ---
    ### Behavior:
    - Accepts **either** `space_id` **or** `space_key` (not both).
    - Automatically queries the appropriate Confluence REST v2 endpoint.
    - Returns human-readable success/error messages and structured space details.

    ---
    ### Returns:
    A standardized dictionary in the format:

    ```json
    {
        "success": bool,               # Whether the operation succeeded
        "action": "get_space",         # Tool/action name
        "message": str,                # Summary or error message
        "data": {                      # Space metadata object
            "id": str,                 # Numeric space ID
            "key": str,                # Space key
            "name": str                # Space name
        }
    }
    ```

    ---
    ### Example Usage:
    ```python
    >>> response = get_space(metadata, space_key="ENG")
    >>> json.dumps(response, indent=2)
    {
        "success": true,
        "action": "get_space",
        "message": "Fetched space details for key 'ENG'.",
        "data": {
            "id": "14188548",
            "key": "ENG",
            "name": "Engineering"
        }
    }
    ```

    ---
    ###  Error Example:
    ```python
    {
        "success": false,
        "action": "get_space",
        "message": "❌ No space found for key 'TEST'.",
        "data": {}
    }
    ```

    ---
    ### Notes:
    - The `space_id` is required for creating or linking pages using the Confluence v2 API.
    - The `space_key` is human-readable and often appears in page URLs.
    - If neither argument is provided, returns an error message prompting the user to supply one.
    """

    action = "get_space"

    # --- Validate credentials ---
    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(False, action, "❌ Confluence credentials not found.")

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    # --- Case 1: Lookup by space_id ---
    if space_id:
        url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces/{space_id}"
        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            msg = f"❌ Failed to fetch space by ID ({resp.status_code}): {resp.text}"
            return make_response(False, action, msg)

        data = resp.json()
        space_data = {
            "id": str(data.get("id", "")),
            "key": data.get("key", ""),
            "name": data.get("name", ""),
        }
        return make_response(
            True, action, f"Fetched space details for ID '{space_id}'.", data=space_data
        )

    # --- Case 2: Lookup by space_key ---
    if space_key:
        url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces?keys={space_key}"
        resp = requests.get(url, headers=headers)

        if resp.status_code != 200:
            msg = f"❌ Failed to fetch space by key ({resp.status_code}): {resp.text}"
            return make_response(False, action, msg)

        results = resp.json().get("results", [])
        if not results:
            return make_response(
                False, action, f"❌ No space found for key '{space_key}'."
            )

        data = results[0]
        space_data = {
            "id": str(data.get("id", "")),
            "key": data.get("key", ""),
            "name": data.get("name", ""),
        }
        return make_response(
            True,
            action,
            f"Fetched space details for key '{space_key}'.",
            data=space_data,
        )

    # --- No input provided ---
    return make_response(False, action, "❌ Provide either 'space_id' or 'space_key'.")


@mcp.tool(name="confluence_get_page_content")
def get_page_content(metadata: Dict, page_id: str) -> dict:
    """
    Retrieve the full body content of a Confluence page in **storage format**.

    This tool fetches a Confluence page’s **title**, **body content**, and **URL**
    using the provided `page_id`. The content is returned in the `storage`
    representation (XML-like internal format used by Confluence).

    ---
    **Args:**
        metadata (Dict):
            Dictionary containing Confluence instance details.
            Expected structure:
            ```python
            {
                "confluence": {
                    "base_url": "https://<your-domain>.atlassian.net/wiki"
                    ...,
                }
            }
            ```

        page_id (str):
            The numeric ID of the Confluence page to fetch (e.g., `"123456789"`).

    ---
    **Returns:**
        dict:
            On success:
            ```json
            {
                "success": true,
                "action": "get_page_content",
                "message": "✅ Page content retrieved successfully.",
                "data": {
                    "id": "123456789",
                    "title": "Example Page Title",
                    "content": "<p>This is the page body in <b>storage format</b>.</p>",
                    "url": "https://your-domain.atlassian.net/wiki/spaces/KEY/pages/123456789"
                }
            }
            ```

            On failure:
            ```json
            {
                "success": false,
                "action": "get_page_content",
                "message": "❌ Confluence credentials not found.",
                "data": {}
            }
            ```

    ---
    **Data Example (API Response):**
    ```json
    {
        "id": "123456789",
        "status": "current",
        "title": "My Confluence Page",
        "body": {
            "storage": {
                "value": "<p>Hello <b>world</b>!</p>",
                "representation": "storage"
            }
        },
        "_links": {
            "webui": "/spaces/KEY/pages/123456789"
        }
    }
    ```

    ---
    **Example Usage:**
    ```python
    result = get_page_content(metadata, page_id="123456789")
    print(result["data"]["content"])
    ```

    ---
    **Notes:**
    - The `storage` format may contain rich HTML and Confluence macros.
    - Use `body-format=view` or `body-format=export_view` for rendered HTML content.
    - Requires `read:confluence-content.summary` and `read:confluence-content.all` scopes.
    """
    action = "get_page_content"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{page_id}?body-format=storage"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed: {resp.status_code} - {resp.text}",
        )

    data = resp.json()
    title = data.get("title", "N/A")
    body = data.get("body", {}).get("storage", {}).get("value", "")
    webui_path = data.get("_links", {}).get("webui", "")
    page_url = f"{base_url}/wiki{webui_path}" if webui_path else None

    return make_response(
        success=True,
        action=action,
        message="✅ Page content retrieved successfully.",
        data={
            "id": data.get("id"),
            "title": title,
            "content": body,
            "url": page_url,
        },
    )


@mcp.tool(name="confluence_create_page")
def create_page(metadata: Dict, space_id: str, title: str, content: str) -> dict:
    """
    Create a new Confluence page in the specified space using the Confluence REST API v2.

    This tool creates a new Confluence page under a given space using the **storage format**
    for its content (rich text and macros). Returns the newly created page’s ID, title, and URL.

    ---
    **Args:**
        metadata (Dict):
            Metadata dictionary containing credentials and configuration for Confluence.
            This is automatically managed by the MCP framework.

        space_id (str):
            The numeric ID of the Confluence space where the page should be created (e.g., `"14188548"`).

        title (str):
            The title of the page to create (e.g., `"Project Overview"`).

        content (str):
            The page body in Confluence’s **storage** format.
            Example:
            ```html
            <p>This is a <b>new Confluence page</b>.</p>
            ```

    ---
    **Returns:**
        dict:
            A standardized response in the following format:
            ```json
            {
                "success": true,
                "action": "create_page",
                "message": "✅ Page 'Project Overview' created successfully.",
                "data": {
                    "id": "123456789",
                    "title": "Project Overview",
                    "space_id": "14188548",
                    "url": "https://your-domain.atlassian.net/wiki/spaces/KEY/pages/123456789"
                }
            }
            ```

            On failure:
            ```json
            {
                "success": false,
                "action": "create_page",
                "message": "❌ Failed: 400 - Bad Request",
                "data": {}
            }
            ```

    ---
    **Notes:**
    - The `content` must be provided in the `"storage"` representation.
    - To create a **child page**, include `"parentId"` in the payload.
    - Returns the new page’s details, including its Confluence web URL.
    """
    action = "create_page"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = {
        "title": title,
        "spaceId": space_id,
        "body": {"representation": "storage", "value": content},
    }

    resp = requests.post(url, headers=headers, json=payload)

    if resp.status_code not in (200, 201):
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed: {resp.status_code} - {resp.text}",
        )

    data = resp.json()
    page_id = data.get("id")
    webui_path = data.get("_links", {}).get("webui", "")
    page_url = f"{base_url}/wiki{webui_path}" if webui_path else None

    return make_response(
        success=True,
        action=action,
        message=f"✅ Page '{title}' created successfully.",
        data={"id": page_id, "title": title, "space_id": space_id, "url": page_url},
    )


@mcp.tool(name="confluence_add_footer_comment")
def confluence_add_footer_comment(
    metadata: Dict, page_id: str, comment: str, parent_comment_id: str = ""
):
    """
    Add a footer comment to a Confluence page using the REST API v2.

    This tool allows you to add a new footer comment or reply to an existing one
    at the bottom of a Confluence page. Comments use the `storage` representation format,
    which supports Confluence XHTML content such as mentions, formatting, and macros.

    Args:
        metadata (Dict): Metadata containing Confluence user credentials and `cloud_id`.
        page_id (str): The ID of the Confluence page where the comment will be added.
        comment (str): The comment body content in Confluence storage format.
                       Example: "<p>This is a <strong>test comment</strong>.</p>"
        parent_comment_id (str, optional): If provided, this comment will be added as
                                           a threaded reply to the specified comment.

    Returns:
        dict: A structured response with:
            - success (bool): Whether the operation was successful.
            - action (str): The tool/action name.
            - message (str): A short human-readable message.
            - url (str, optional): A URL pointing to the new comment (if available).

    Example:
        >>> confluence_add_footer_comment(
        ...     metadata={"confluence": {"cloud_id": "abcd1234"}},
        ...     page_id="123456789",
        ...     comment="<p>Hello from FastMCP!</p>"
        ... )
        {
            "success": True,
            "action": "confluence_add_footer_comment",
            "message": "✅ Comment added successfully to page 123456789",
            "data": {
                "url": "https://your-site.atlassian.net/wiki/pages/123456789?focusedCommentId=987654",
                "comment_id":"987654"
                }
        }

    Notes:
        - Requires `write:confluence-content` scope in the access token.
        - The returned `url` is constructed using standard Confluence patterns,
          since the REST API does not directly provide a comment URL.
    """
    action = "confluence_add_footer_comment"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/footer-comments"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    data = {"pageId": page_id, "body": {"representation": "storage", "value": comment}}

    if parent_comment_id:
        data["parentCommentId"] = parent_comment_id

    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code in (200, 201):
        result = resp.json()
        comment_id = result.get("id")
        webui = result.get("_links", {}).get("webui")
        comment_url = f"{base_url}/wiki{webui}" if webui else None

        return make_response(
            success=True,
            action=action,
            message=f"✅ Comment added successfully to page {page_id}",
            data={"url": comment_url, "comment_id": comment_id},
        )
    else:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to add comment: {resp.status_code} - {resp.text}",
        )


@mcp.tool(name="confluence_get_footer_comments")
def confluence_get_footer_comments(
    metadata: Dict,
    page_id: str,
    body_format: str = "storage",
    sort: str = "-created-date",
    limit: int = 25,
    cursor: Optional[str] = None,
):
    """
    Retrieve all footer comments for a given Confluence page in a standardized format.

    This tool fetches the footer comments (bottom-of-page comments) for a Confluence page.
    Supports pagination, sorting, and multiple body formats. Each comment includes a
    clickable URL to its location on the page.

    Args:
        metadata (Dict): Confluence authentication metadata (contains cloud_id, base_url).
        page_id (str): The Confluence page ID to fetch comments for.
        body_format (str, optional): Format of the comment body. Default is "storage".
                                     Valid values: "storage", "atlas_doc_format".
        sort (str, optional): Sorting order. Default "-created-date" (newest first).
                              Valid values: "created-date", "-created-date", "modified-date", "-modified-date".
        limit (int, optional): Maximum number of comments to return (1–250). Default 25.
        cursor (str, optional): Pagination cursor from a previous response.

    Returns:
        dict: Standardized MCP response with:
            - success (bool): Whether the operation was successful.
            - action (str): Tool/action name.
            - message (str): Human-readable status message.
            - data (dict): Includes:
                - comments (list of dicts): Each comment contains:
                    - id (str): Comment ID
                    - author (str): Display name of comment author
                    - created_at (str): Timestamp of creation
                    - body (str): Comment content in requested format
                    - url (str): Direct URL to the comment
                - limit (int): Limit used in this request
                - next_cursor (str | None): Cursor for next page, if available

    Example:
        >>> confluence_get_footer_comments(
        ...     metadata={"confluence": {"cloud_id": "abcd1234", "base_url": "myteam.atlassian.net"}},
        ...     page_id="123456789",
        ...     limit=10
        ... )
        {
            "success": True,
            "action": "confluence_get_footer_comments",
            "message": "✅ Retrieved 10 comments from page 123456789",
            "data": {
                "comments": [
                    {
                        "id": "987654",
                        "author": "Jane Doe",
                        "created_at": "2025-10-08T10:12:34Z",
                        "body": "<p>This is a comment.</p>",
                        "url": "https://myteam.atlassian.net/wiki/pages/123456789?focusedCommentId=987654"
                    },
                    ...
                ],
                "limit": 10,
                "next_cursor": "abcdef123456"
            }
        }
    """
    action = "confluence_get_footer_comments"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/footer-comments"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {
        "pageId": page_id,
        "body-format": body_format,
        "sort": sort,
        "limit": limit,
    }
    if cursor:
        params["cursor"] = cursor

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to fetch comments: {resp.status_code} - {resp.text}",
        )

    data_json = resp.json()
    results = []
    for c in data_json.get("results", []):
        if str(c.get("pageId")) != str(page_id):
            continue
        comment_id = c.get("id")
        webui = c.get("_links", {}).get("webui")
        comment_url = f"{base_url}/wiki{webui}" if webui else None
        results.append(
            {
                "id": comment_id,
                "author": c.get("createdBy", {}).get("displayName"),
                "created_at": c.get("createdAt"),
                "body": c.get("body", {}).get(body_format, {}).get("value"),
                "url": comment_url,
            }
        )

    return make_response(
        success=True,
        action=action,
        message=f"✅ Retrieved {len(results)} comments from page {page_id}",
        data={
            "comments": results,
            "limit": limit,
            "next_cursor": data_json.get("_links", {}).get("next"),
        },
    )


@mcp.tool(name="confluence_get_footer_comment_by_id")
def confluence_get_footer_comment_by_id(
    metadata: Dict,
    comment_id: str,
    body_format: str = "storage",
    include_properties: bool = False,
    include_operations: bool = False,
    include_likes: bool = False,
    include_versions: bool = False,
    include_version: bool = True,
):
    """
    Retrieve a specific Confluence footer comment by its ID in a standardized format.

    Args:
        metadata (Dict): Confluence authentication metadata (contains cloud_id and optional base_url).
        comment_id (str): The ID of the footer comment to retrieve.
        body_format (str, optional): Format of the comment body. Default "storage".
                                     Other valid values: atlas_doc_format, view, export_view,
                                     anonymous_export_view, styled_view, editor.
        include_properties (bool, optional): Include content properties. Default False.
        include_operations (bool, optional): Include operations. Default False.
        include_likes (bool, optional): Include likes count. Default False.
        include_versions (bool, optional): Include versions info. Default False.
        include_version (bool, optional): Include the current version. Default True.

    Returns:
        dict: Standardized MCP response with:
            - success (bool): Whether the operation was successful.
            - action (str): Tool/action name.
            - message (str): Human-readable status.
            - data (dict): Comment details including:
                - id (str): Comment ID
                - author (str): Display name of comment author
                - created_at (str): Timestamp when comment was created
                - body (str): Comment content in requested format
                - url (str): Direct URL to the comment
                - likes (int | None): Likes count (if requested)
                - properties (dict | None): Properties (if requested)

    Example:
        >>> confluence_get_footer_comment_by_id(
        ...     metadata={"confluence": {"cloud_id": "abcd1234", "base_url": "myteam.atlassian.net"}},
        ...     comment_id="987654"
        ... )
        {
            "success": True,
            "action": "confluence_get_footer_comment_by_id",
            "message": "✅ Retrieved comment 987654",
            "data": {
                "id": "987654",
                "author": "Jane Doe",
                "created_at": "2025-10-08T10:12:34Z",
                "body": "<p>This is a comment.</p>",
                "url": "https://myteam.atlassian.net/wiki/pages/123456789?focusedCommentId=987654",
                "likes": 3,
                "properties": {}
            }
        }
    """
    action = "confluence_get_footer_comment_by_id"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/footer-comments/{comment_id}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {
        "body-format": body_format,
        "include-properties": str(include_properties).lower(),
        "include-operations": str(include_operations).lower(),
        "include-likes": str(include_likes).lower(),
        "include-versions": str(include_versions).lower(),
        "include-version": str(include_version).lower(),
    }

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to fetch comment: {resp.status_code} - {resp.text}",
        )

    c = resp.json()
    webui = c.get("_links", {}).get("webui")
    comment_url = f"{base_url}/wiki{webui}" if webui else None

    data = {
        "id": c.get("id"),
        "author": c.get("createdBy", {}).get("displayName"),
        "created_at": c.get("createdAt"),
        "body": c.get("body", {}).get(body_format, {}).get("value") or c.get("body"),
        "url": comment_url,
        "likes": c.get("likes") if include_likes else None,
        "properties": c.get("properties") if include_properties else None,
    }

    return make_response(
        success=True,
        action=action,
        message=f"✅ Retrieved comment {comment_id}",
        data=data,
    )


@mcp.tool(name="confluence_list_spaces")
def confluence_list_spaces(metadata: Dict):
    """
    List all available Confluence spaces for the authenticated user in a standardized format.

    Args:
        metadata (Dict): Confluence authentication metadata (contains cloud_id and optional base_url).

    Returns:
        dict: Standardized MCP response with:
            - success (bool): Whether the operation was successful.
            - action (str): Tool/action name.
            - message (str): Human-readable status.
            - data (list of dicts): Each dict contains:
                - id (str): Space ID
                - key (str): Space key
                - name (str): Space name
                - url (str | None): URL to the space in Confluence (if base_url available)

    Example:
        >>> confluence_list_spaces(metadata={"confluence": {"cloud_id": "abcd1234", "base_url": "myteam.atlassian.net"}})
        {
            "success": True,
            "action": "confluence_list_spaces",
            "message": "✅ Retrieved 5 spaces",
            "data": [
                {"id": "12345", "key": "ENG", "name": "Engineering", "url": "https://myteam.atlassian.net/wiki/spaces/ENG"},
                {"id": "67890", "key": "HR", "name": "Human Resources", "url": "https://myteam.atlassian.net/wiki/spaces/HR"},
                ...
            ]
        }
    """
    action = "confluence_list_spaces"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to fetch spaces: {resp.status_code} - {resp.text}",
        )

    spaces = resp.json().get("results", [])
    if not spaces:
        return make_response(
            success=True, action=action, message="✅ No spaces found.", data=[]
        )

    formatted_spaces = [
        {
            "id": s.get("id"),
            "key": s.get("key"),
            "name": s.get("name"),
            "url": (
                f"https://{base_url}/wiki/spaces/{s.get('key')}"
                if s.get("key")
                else None
            ),
        }
        for s in spaces
    ]

    return make_response(
        success=True,
        action=action,
        message=f"✅ Retrieved {len(formatted_spaces)} spaces",
        data=formatted_spaces,
    )


@mcp.tool(name="confluence_update_page")
def confluence_update_page(
    metadata: Dict,
    page_id: str,
    new_content: str,
    new_title: Optional[str] = None,
):
    """
    Update an existing Confluence page (REST API v2) with standardized response.

    Args:
        metadata (Dict): Confluence authentication metadata (contains cloud_id and optional base_url).
        page_id (str): The page ID to update.
        new_content (str): New page body content (storage format).
        new_title (Optional[str]): Optional new page title.

    Returns:
        dict: Standardized MCP response with:
            - success (bool): Whether the operation was successful.
            - action (str): Tool/action name.
            - message (str): Human-readable status.
            - data (dict): Updated page details:
                - id (str): Page ID
                - title (str): Updated page title
                - url (str): URL to the updated page

    Example:
        >>> confluence_update_page(metadata={"confluence": {"cloud_id": "abcd1234", "base_url": "myteam.atlassian.net"}},
        ...                        page_id="12345",
        ...                        new_content="<p>Updated content</p>",
        ...                        new_title="Updated Page")
        {
            "success": True,
            "action": "confluence_update_page",
            "message": "✅ Page 12345 updated successfully",
            "data": {
                "id": "12345",
                "title": "Updated Page",
                "url": "https://myteam.atlassian.net/wiki/pages/12345"
            }
        }
    """
    action = "confluence_update_page"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    base_url = creds.get("base_url")
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    # Step 1: Get current page details to fetch version
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{page_id}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    page_resp = requests.get(url, headers=headers)
    if page_resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to fetch current page: {page_resp.status_code} - {page_resp.text}",
        )

    page = page_resp.json()
    current_version = page.get("version", {}).get("number", 1)
    new_version = current_version + 1
    title = new_title or page.get("title", "Untitled")

    # Step 2: Prepare payload for v2 update
    data_payload = {
        "id": page_id,
        "status": "current",
        "title": title,
        "body": {"representation": "storage", "value": new_content},
        "version": {"number": new_version},
    }

    headers["Content-Type"] = "application/json"
    update_resp = requests.put(url, headers=headers, json=data_payload)
    if update_resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to update page: {update_resp.status_code} - {update_resp.text}",
        )

    data = update_resp.json()
    webui = data.get("_links", {}).get("webui", "")
    page_url = f"{base_url}/wiki{webui}" if webui else None
    return make_response(
        success=True,
        action=action,
        message=f"✅ Page {page_id} updated successfully",
        data={"id": page_id, "title": title, "url": page_url},
    )


@mcp.tool(name="confluence_list_pages")
def list_pages_in_space(metadata: Dict, space_id: str, limit: int = 10):
    """
    List pages in a given Confluence space with standardized MCP response.

    Args:
        metadata (Dict): Confluence authentication metadata (must include cloud_id and base_url).
        space_id (str): The ID of the space to list pages from.
        limit (int, optional): Maximum number of pages to return. Default is 10.

    Returns:
        dict: Standardized MCP response with:
            - success (bool): Whether the operation was successful.
            - action (str): Tool/action name.
            - message (str): Human-readable status.
            - data (list): List of pages with each page containing:
                - id (str): Page ID
                - title (str): Page title
                - url (str): URL to the page

    Example:
        >>> list_pages_in_space(metadata={"confluence": {"cloud_id": "abcd1234", "base_url": "https://myteam.atlassian.net"}},
        ...                     space_id="ENG", limit=5)
        {
            "success": True,
            "action": "confluence_list_pages",
            "message": "✅ 5 pages retrieved from space ENG",
            "data": [
                {"id": "123", "title": "Project Plan", "url": "https://myteam.atlassian.net/wiki/pages/123"},
                {"id": "124", "title": "Design Doc", "url": "https://myteam.atlassian.net/wiki/pages/124"},
                ...
            ]
        }
    """
    action = "confluence_list_pages"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id, base_url = (
        creds["access_token"],
        creds["cloud_id"],
        creds.get("base_url"),
    )
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces/{space_id}/pages"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"limit": limit}

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed: {resp.status_code} - {resp.text}",
        )

    data = resp.json()
    pages = data.get("results", [])
    if not pages:
        return make_response(
            success=True,
            action=action,
            message=f"No pages found in space {space_id}.",
            data=[],
        )

    page_list = []
    for p in pages:
        webui = p.get("_links", {}).get("webui", "")
        page_list.append(
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "url": f"{base_url}/wiki{webui}" if webui else "",
            }
        )

    return make_response(
        success=True,
        action=action,
        message=f"✅ {len(page_list)} pages retrieved from space {space_id}",
        data=page_list,
    )


@mcp.tool(name="confluence_get_page_attachments")
def get_page_attachments(metadata: Dict, page_id: str):
    """
    List all attachments for a Confluence page with standardized MCP response.

    Requires granular scopes:
      - read:content:confluence
      - read:attachment:confluence

    Args:
        metadata (Dict): Confluence authentication metadata (must include cloud_id and base_url).
        page_id (str): The ID of the Confluence page.

    Returns:
        dict: Standardized MCP response with:
            - success (bool): Whether the operation was successful.
            - action (str): Tool/action name.
            - message (str): Human-readable status.
            - data (list): List of attachments, each containing:
                - id (str): Attachment ID
                - title (str): Attachment title
                - media_type (str): MIME type
                - download_url (str): Full download URL
                - page_url (str): URL to the page containing the attachment

    Example:
        >>> get_page_attachments(metadata={"confluence": {"cloud_id": "abcd1234", "base_url": "https://myteam.atlassian.net"}},
        ...                      page_id="12345")
        {
            "success": True,
            "action": "confluence_get_page_attachments",
            "message": "✅ 2 attachments retrieved from page 12345",
            "data": [
                {
                    "id": "1",
                    "title": "diagram.png",
                    "media_type": "image/png",
                    "download_url": "https://myteam.atlassian.net/wiki/download/attachments/12345/diagram.png",
                    "page_url": "https://myteam.atlassian.net/wiki/pages/12345"
                },
                ...
            ]
        }
    """
    action = "confluence_get_page_attachments"

    creds = get_confluence_creds(metadata)
    if not creds:
        return make_response(
            success=False, action=action, message="❌ Confluence credentials not found."
        )

    access_token, cloud_id, base_url = (
        creds["access_token"],
        creds["cloud_id"],
        creds.get("base_url"),
    )
    if not base_url:
        return make_response(
            success=False,
            action=action,
            message="❌ base_url not found in credentials.",
        )

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{page_id}/attachments"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed: {resp.status_code} - {resp.text}",
        )

    items = resp.json().get("results", [])
    if not items:
        return make_response(
            success=True,
            action=action,
            message=f"ℹ️ No attachments found for page {page_id}",
            data=[],
        )

    attachments = []
    for item in items:
        download_link = item.get("_links", {}).get("download")
        webui = download_link.get("webui", "")
        page_url = f"{base_url}/wiki{webui}" if webui else None
        attachments.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "media_type": item.get("mediaType"),
                "download_url": (
                    f"{base_url}/wiki{download_link}" if download_link else None
                ),
                "page_url": page_url,
            }
        )

    return make_response(
        success=True,
        action=action,
        message=f"✅ {len(attachments)} attachments retrieved from page {page_id}",
        data=attachments,
    )


# ─── Auth Flow ──────────────────────────────────────────────────────────
async def authorize(request: Request):
    """
    Starlette route handler to redirect the user to the Confluence OAuth authorization URL.

    Args:
        request (Request): The incoming HTTP request object.

    Returns:
        RedirectResponse: Redirects the user to the Confluence OAuth authorization page.
    """
    scope = " ".join(SCOPES)
    state = str(uuid.uuid4())  # or pull from request/session

    url = (
        "https://auth.atlassian.com/authorize"
        f"?audience=api.atlassian.com"
        f"&client_id={CLIENT_ID}"
        f"&scope={quote_plus(scope)}"
        f"&redirect_uri={quote_plus(REDIRECT_URI)}"
        f"&state={state}"
        f"&response_type=code"
        f"&prompt=consent"
    )

    print("Redirecting to:", url)
    return RedirectResponse(url)


async def oauth2callback(request: Request):
    """
    Starlette route handler for the Confluence OAuth2 callback.
    Exchanges the authorization code for access and refresh tokens, fetches user info and Confluence cloud details.

    Args:
        request (Request): The incoming HTTP request object containing the authorization code.

    Returns:
        JSONResponse: Contains authentication result, user email, access token, refresh token, cloud ID, base URL, and granted scopes.
    """
    code = request.query_params.get("code")
    # state = request.query_params.get("state")

    if not code:
        return JSONResponse({"error": "Missing code"}, status_code=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    token_resp = requests.post(
        "https://auth.atlassian.com/oauth/token", json=data
    ).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")
    scopes_granted = token_resp.get("scope", "").split()

    if not access_token:
        return JSONResponse(
            {"error": "Token exchange failed", "details": token_resp}, status_code=400
        )

    # Fetch user email
    userinfo = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    email = userinfo.get("email")

    # Get Confluence cloud info
    resources = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    print("Accessible resources:", resources)

    # Just take the first resource with Confluence scopes
    confluence_resources = [
        r
        for r in resources
        if any(s.startswith("read:confluence") for s in r.get("scopes", []))
    ]

    if not confluence_resources:
        return JSONResponse(
            {"error": "No Confluence resource found", "resources": resources},
            status_code=400,
        )

    cloud_id = confluence_resources[0]["id"]
    base_url = confluence_resources[0]["url"]

    return JSONResponse(
        {
            "message": f"Authenticated as {email}",
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "cloud_id": cloud_id,
            "base_url": base_url,
            "scopes": scopes_granted,
        }
    )


async def status(request: Request):
    """
    Starlette route handler to check authentication status for a given email.

    Args:
        request (Request): The incoming HTTP request object containing the email query parameter.

    Returns:
        JSONResponse: Returns status 'authenticated' if the email is found in user_tokens, otherwise 'pending'.
    """
    email = request.query_params.get("email")
    if email in user_tokens:
        return JSONResponse({"status": "authenticated"})
    return JSONResponse({"status": "pending"})


# ─── App Setup ──────────────────────────────────────────────────────────
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
