# -*- coding: utf-8 -*-
# fastmcp==2.12.2
# fastmcp-http==0.1.4

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
SCOPES = os.getenv(
    "CONFLUENCE_SCOPES"
    , "offline_access write:confluence-content read:me read:confluence-space.summary read:confluence-props read:confluence-content.all read:confluence-content.summary search:confluence read:confluence-user"
).split()

PORT = int(os.getenv("CONFLUENCE_MCP_PORT", "8003"))
REDIRECT_URI = os.getenv("CONFLUENCE_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")

# ─── Token Store ──────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────
mcp = FastMCP("confluence-mcp")


def is_access_token_valid(access_token: str) -> bool:
    """Check if access token is still valid by hitting /me endpoint."""
    resp = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    return resp.status_code == 200


def refresh_confluence_token(refresh_token: str) -> Optional[Dict]:
    """Refresh Confluence OAuth 2.0 access token."""
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
    """Extract Confluence credentials and refresh if needed."""
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
        return {"email": email, "access_token": access_token, "refresh_token": refresh_token, "cloud_id": cloud_id, "base_url": base_url}

    if refresh_token:
        new_tokens = refresh_confluence_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            return {
                "email": email,
                "access_token": new_tokens["access_token"],
                "refresh_token": new_tokens.get("refresh_token", refresh_token),
                "cloud_id": cloud_id,
                "base_url": creds.get("base_url")
            }

    return None


# ─── Tools ──────────────────────────────────────────────────────────────
@mcp.tool(
    name="confluence_search_pages",
    description="Search Confluence pages by title or content",
    output_schema={
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "space": {"type": "string"},
                        "space_key": {"type": ["string", "null"]},
                        "url": {"type": ["string", "null"]},
                    },
                    "required": ["id", "title", "space", "url"]
                }
            }
        },
        "required": ["results"]
    }
)
def search_pages(metadata: Dict, query: str) -> Dict:
    creds = get_confluence_creds(metadata)
    if not creds:
        return {"results": []}

    access_token, cloud_id = creds["access_token"], creds["cloud_id"]
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/rest/api/search"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"cql": f'type=page AND (title ~ "{query}" OR text ~ "{query}")', "limit": 5}

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return {"results": []}

    results = resp.json().get("results", [])
    formatted = []
    for r in results:
        content = r.get("content", {})
        page_id = str(content.get("id")) if content.get("id") else ""
        title = content.get("title", "")
        space = content.get("space", {}).get("name", "")
        webui = content.get("_links", {}).get("webui")
        base_url = creds.get("base_url")
        page_url = f"{base_url}/wiki{webui}" if base_url and webui else None

        formatted.append({
            "id": page_id,
            "title": title,
            "space": space,
            "space_key": None,
            "url": page_url,
        })

    return {"results": formatted}



@mcp.tool(name="confluence_get_space_details")
def get_space(metadata: Dict, space_id: Optional[str] = None, space_key: Optional[str] = None) -> Union[str, Dict[str, str]]:
    """
    Retrieve Confluence space details.

    This tool lets you convert between `spaceId` (numeric) and `spaceKey` (string),
    or simply fetch all details about a space.

    Args:
        metadata (Dict): Dictionary containing Confluence credentials (access_token, cloud_id, base_url).
        space_id (str, optional): The numeric ID of the space (e.g., "14188548").
                                  Use this when you only know the ID and want to fetch the key/name.
        space_key (str, optional): The unique key of the space (e.g., "ScrySpace").
                                   Use this when you know the key and want to fetch the numeric ID.

    Returns:
        Union[str, Dict[str, str]]:
            - On success: A dictionary with space details:
                {
                    "id": "<spaceId>",
                    "key": "<spaceKey>",
                    "name": "<spaceName>"
                }
            - On failure: A string describing the error (prefixed with ❌).

    Notes:
        - You must provide **either** `space_id` or `space_key` (not both).
        - The `space_id` is required by the Confluence v2 API for creating pages.
        - The `space_key` is human-friendly and often used in URLs or space references.
        - If no space is found, returns `"❌ No space found."`.
    """
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    # Case 1: Lookup by space_id
    if space_id:
        url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces/{space_id}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            return f"❌ Failed: {resp.status_code} - {resp.text}"
        data = resp.json()
        return {
            "id": data.get("id"),
            "key": data.get("key"),
            "name": data.get("name"),
        }

    # Case 2: Lookup by space_key
    if space_key:
        url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces?keys={space_key}"
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            return f"❌ Failed: {resp.status_code} - {resp.text}"
        results = resp.json().get("results", [])
        if not results:
            return "❌ No space found."
        data = results[0]
        return {
            "id": data.get("id"),
            "key": data.get("key"),
            "name": data.get("name"),
        }

    return "❌ Provide either space_id or space_key."


@mcp.tool(name="confluence_get_page_content")
def get_page_content(metadata: Dict, page_id: str):
    """Fetch the body of a Confluence page (storage format)."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{page_id}?body-format=storage"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers).json()
    title = resp.get("title", "N/A")
    body = resp.get("body", {}).get("storage", {}).get("value", "")
    return f"Title: {title}\n\nContent:\n{body}"


@mcp.tool(name="confluence_create_page")
def create_page(metadata: Dict, space_id: str, title: str, content: str):
    """Create a new Confluence page in a space (needs spaceId)."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = {
        "title": title,
        "spaceId": space_id,
        "body": {"representation": "storage", "value": content}
    }

    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200 or resp.status_code == 201:
        return f"✅ Page '{title}' created in space {space_id}"
    else:
        return f"❌ Failed: {resp.status_code} - {resp.text}"


@mcp.tool(name="confluence_add_footer_comment")
def add_comment(metadata: Dict, page_id: str, comment: str, parent_comment_id: str = ""):
    """
    Add a footer comment to a Confluence page.

    Args:
        metadata (Dict): Metadata containing Confluence access credentials.
        page_id (str): The ID of the page where the comment will be added.
        comment (str): The content of the comment (in Confluence storage format).
        parent_comment_id (str, optional): If provided, the comment will be added
                                           as a reply to this parent comment.
    """
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/footer-comments"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    data = {
        "pageId": page_id,
        "body": {
            "representation": "storage",
            "value": comment
        }
    }

    if parent_comment_id:
        data["parentCommentId"] = parent_comment_id

    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code in (200, 201):
        return f"✅ Comment added to page {page_id}"
    else:
        return f"❌ Failed: {resp.status_code} - {resp.text}"

@mcp.tool(name="confluence_get_footer_comments")
def get_footer_comments(
    metadata: Dict,
    page_id: str,
    body_format: str = "storage",
    sort: str = "-created-date",
    limit: int = 25,
    cursor: Optional[str] = None,
):
    """
    Retrieve all footer comments for a given Confluence page.

    Args:
        metadata (Dict): Confluence authentication metadata (must include access_token, cloud_id).
        page_id (str): The Confluence page ID to fetch comments for.
        body_format (str, optional): The content format to return in the body. Default "storage".
                                     Valid values: storage, atlas_doc_format.
        sort (str, optional): Sort order. Default "-created-date" (newest first).
                              Valid values: created-date, -created-date, modified-date, -modified-date.
        limit (int, optional): Maximum number of comments to return. Default 25. Max 250.
        cursor (str, optional): Pagination cursor from previous response.

    Returns:
        List of dicts containing comment details (id, author, created_at, body).
        Includes pagination info if more results exist.
    """
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/footer-comments"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
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
        return f"❌ Failed: {resp.status_code} - {resp.text}"

    data = resp.json()
    results = []

    for c in data.get("results", []):
        results.append({
            "id": c.get("id"),
            "author": c.get("createdBy", {}).get("displayName"),
            "created_at": c.get("createdAt"),
            "body": (
                c.get("body", {})
                .get(body_format, {})
                .get("value")
            )
        })

    return {
        "comments": results,
        "limit": limit,
        "next_cursor": data.get("_links", {}).get("next")
    }


@mcp.tool(name="confluence_get_footer_comment_by_id")
def get_footer_comment_by_id(
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
    Retrieve a specific Confluence footer comment by its ID.

    Args:
        metadata (Dict): Confluence authentication metadata (must include access_token, cloud_id).
        comment_id (str): The ID of the footer comment to retrieve.
        body_format (str, optional): The content format to return in the body. Default is "storage".
                                     Other valid values: atlas_doc_format, view, export_view,
                                     anonymous_export_view, styled_view, editor.
        include_properties (bool, optional): Whether to include content properties. Default False.
        include_operations (bool, optional): Whether to include operations. Default False.
        include_likes (bool, optional): Whether to include likes. Default False.
        include_versions (bool, optional): Whether to include versions. Default False.
        include_version (bool, optional): Whether to include the current version. Default True.

    Returns:
        Dict containing comment details (id, author, created_at, body).
        Returns error message if not found or unauthorized.
    """
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/footer-comments/{comment_id}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
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
        return f"❌ Failed: {resp.status_code} - {resp.text}"

    c = resp.json()

    return {
        "id": c.get("id"),
        "author": c.get("createdBy", {}).get("displayName"),
        "created_at": c.get("createdAt"),
        "body": c.get("body", {}).get(body_format, {}).get("value") or c.get("body"),
        "likes": c.get("likes") if include_likes else None,
        "properties": c.get("properties") if include_properties else None,
    }


@mcp.tool(name="confluence_list_spaces")
def list_spaces(metadata: Dict):
    """List available Confluence spaces."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        return f"❌ Failed: {resp.status_code} - {resp.text}"

    spaces = resp.json().get("results", [])
    if not spaces:
        return "No spaces found."
    return "\n".join(f"{s['name']} (id: {s['id']}, key: {s.get('key')})" for s in spaces)


@mcp.tool(name="confluence_update_page")
def update_page(metadata: Dict, page_id: str, new_content: str, new_title: Optional[str] = None):
    """
    Update an existing Confluence page (REST API v2).

    Args:
        metadata (Dict): Confluence auth metadata.
        page_id (str): The page ID to update.
        new_content (str): New page body content (storage format).
        new_title (Optional[str]): Optional new page title.
    """
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    # Step 1: Get current page details to fetch version
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{page_id}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    page = requests.get(url, headers=headers).json()

    current_version = page.get("version", {}).get("number", 1)
    new_version = current_version + 1
    title = new_title or page.get("title", "Untitled")

    # Step 2: Prepare payload for v2
    data = {
        "id": page_id,
        "status": "current",
        "title": title,
        "body": {
            "representation": "storage",
            "value": new_content
        },
        "version": {
            "number": new_version
        }
    }

    headers["Content-Type"] = "application/json"
    resp = requests.put(url, headers=headers, json=data)

    if resp.status_code == 200:
        return f"✅ Page {page_id} updated successfully"
    else:
        return f"❌ Failed: {resp.status_code} - {resp.text}"



@mcp.tool(name="confluence_list_pages")
def list_pages_in_space(metadata: Dict, space_id: str, limit: int = 10):
    """List pages in a given Confluence space (default: first 10)."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/spaces/{space_id}/pages"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"limit": limit}

    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return f"❌ Failed: {resp.status_code} - {resp.text}"

    data = resp.json()
    pages = data.get("results", [])
    if not pages:
        return f"No pages found in space {space_id}."

    return "\n".join(f"{p['title']} (id: {p['id']})" for p in pages)


@mcp.tool(name="confluence_get_page_attachments")
def get_page_attachments(metadata: Dict, page_id: str):
    """
    List all attachments for a Confluence page.

    Requires granular scopes: 
      - read:content:confluence
      - read:attachment:confluence

    Returns a list with attachment metadata (id, title, media type, download links).
    """
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token, cloud_id = creds["access_token"], creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/wiki/api/v2/pages/{page_id}/attachments"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return f"❌ Error: {resp.status_code} - {resp.text}"

    attachments = []
    for item in resp.json().get("results", []):
        attachments.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "media_type": item.get("mediaType"),
            "download_link": item.get("_links", {}).get("download"),
            "base_url": creds.get("base_url", "") + '/wiki'
        })
    return attachments or "ℹ️ No attachments found."


@mcp.tool(name="confluence_get_authorization_url")
def get_authorization_url() -> str:
    """Return URL for user to authorize Confluence access."""
    return f"http://localhost:{PORT}/authorize"


# ─── Auth Flow ──────────────────────────────────────────────────────────
async def authorize(request: Request):
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
    code = request.query_params.get("code")
    state = request.query_params.get("state")

    if not code:
        return JSONResponse({"error": "Missing code"}, status_code=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    token_resp = requests.post("https://auth.atlassian.com/oauth/token", json=data).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")
    scopes_granted = token_resp.get("scope", "").split()

    if not access_token:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    # Fetch user email
    userinfo = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()
    email = userinfo.get("email")

    # Get Confluence cloud info
    resources = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources",
        headers={"Authorization": f"Bearer {access_token}"}
        ).json()
    
    print("Accessible resources:", resources)

    # Just take the first resource with Confluence scopes
    confluence_resources = [
        r for r in resources if any(s.startswith("read:confluence") for s in r.get("scopes", []))
    ]

    if not confluence_resources:
        return JSONResponse({"error": "No Confluence resource found", "resources": resources}, status_code=400)

    cloud_id = confluence_resources[0]["id"]
    base_url = confluence_resources[0]["url"]

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "cloud_id": cloud_id,
        "base_url": base_url,
        "scopes": scopes_granted,
    })



async def status(request: Request):
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
