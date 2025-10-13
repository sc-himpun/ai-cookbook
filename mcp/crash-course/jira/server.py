# -*- coding: utf-8 -*-
import os
from typing import Optional, Dict

import requests
from dotenv import load_dotenv
from fastmcp import FastMCP

from starlette.requests import Request
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse

load_dotenv()

# Configs
CLIENT_ID = os.getenv("JIRA_CLIENT_ID")
CLIENT_SECRET = os.getenv("JIRA_CLIENT_SECRET")
SCOPES = os.getenv(
    "JIRA_SCOPES",
    "offline_access read:jira-user read:jira-work read:me write:jira-work",
).split()
PORT = int(os.getenv("JIRA_MCP_PORT", "8002"))  # Port for the FastMCP server
REDIRECT_URI = os.getenv(
    "JIRA_MCP_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback"
)


# Token Store
user_tokens: Dict[str, Dict] = {}

# MCP
mcp = FastMCP("jira-mcp")


def is_access_token_valid(access_token: str) -> bool:
    """
    Check if access token is still valid by hitting /me endpoint.

    Args:
        access_token (str): The OAuth 2.0 access token to validate.

    Returns:
        bool: True if the token is valid, False otherwise.
    """
    resp = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    return resp.status_code == 200


def refresh_jira_token(refresh_token: str) -> Optional[Dict]:
    """
    Refresh the Jira OAuth 2.0 access token using the refresh token.

    Args:
        refresh_token (str): Refresh token received during initial auth.

    Returns:
        Optional[Dict]: New token dict (access_token, refresh_token, etc.) or None on failure.
    """
    client_id = os.getenv("JIRA_CLIENT_ID")
    client_secret = os.getenv("JIRA_CLIENT_SECRET")

    token_url = "https://auth.atlassian.com/oauth/token"
    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }

    response = requests.post(token_url, json=payload)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"[refresh_jira_token] Failed to refresh token: {response.text}")
        return None


def get_jira_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Extract Jira credentials from metadata and refresh if access token is expired.

    Args:
        metadata (Optional[Dict]): Metadata dict or metadata["jira"] containing required fields.

    Returns:
        Optional[Dict]: Dict with access_token, refresh_token, cloud_id, and email; refreshed if needed.
    """
    if not metadata:
        return None

    creds = metadata.get("jira", metadata)

    email = creds.get("email")
    access_token = creds.get("access_token")
    # Normalize blank to None
    refresh_token = creds.get("refresh_token") or None
    cloud_id = creds.get("cloud_id")

    if not email or not access_token:
        return None

    if is_access_token_valid(access_token):
        if not cloud_id:
            try:
                cloud_id = get_cloud_id_from_token(access_token)
            except Exception:
                return None
        return {
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "cloud_id": cloud_id,
        }

    if refresh_token:
        print("🔁 Access token expired, attempting refresh...")
        new_tokens = refresh_jira_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            access_token = new_tokens["access_token"]
            refresh_token = new_tokens.get("refresh_token", refresh_token)
            try:
                cloud_id = get_cloud_id_from_token(access_token)
            except Exception:
                return None
            return {
                "email": email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "cloud_id": cloud_id,
            }

    # If no valid access token and can't refresh, return None
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
        - For sensitive information (e.g., internal IDs), include them only if required for tool chaining,
          but avoid exposing them directly to the UI.
    """
    return {
        "success": success,
        "action": action,
        "message": message,
        "data": data if data is not None else {},
    }


def resolve_jira_site_url(metadata: Dict) -> Optional[str]:
    """
    Resolve the Jira site base URL from provided metadata.

    The function attempts to determine the public-facing Jira site URL in the
    following order:

    1. If `metadata["base_url"]` is present, it is normalized and returned.
       - If the value already starts with "http://" or "https://" it is
         returned with any trailing slash removed.
       - Otherwise, "https://" is prefixed and any trailing slash is removed.
    2. If no `base_url` is present but `access_token` and `cloud_id` are
       available in the metadata, the function requests
       `https://api.atlassian.com/oauth/token/accessible-resources` and looks
       up the resource that matches `cloud_id` or has the `read:jira-work`
       scope. If found, the resource's `url` is normalized and returned.
    3. If none of the above succeeds, the function returns ``None``.

    Args:
        metadata (Dict): A metadata dictionary containing Jira-related fields.
            Expected keys (one or more):
                - "jira" (optional): sub-dict with the same keys listed below.
                - "base_url" (str, optional): Explicit base URL for the Jira site.
                - "access_token" (str, optional): OAuth2 access token for API calls.
                - "cloud_id" (str, optional): Atlassian cloud id to lookup accessible resources.

    Returns:
        Optional[str]: Normalized base URL (e.g. "https://example.atlassian.net")
        if resolved, otherwise ``None``.

    Notes:
        - This helper only resolves the base site URL and does not validate
          that the URL corresponds to an accessible Jira project.
        - Network requests are wrapped in a try/except; failures will result
          in ``None`` being returned rather than raising.

    Example:
        >>> resolve_jira_site_url({"base_url": "example.atlassian.net"})
        'https://example.atlassian.net'
    """
    metadata = metadata.get("jira", metadata)
    access_token = metadata.get("access_token")
    cloud_id = metadata.get("cloud_id")
    base_url = metadata.get("base_url")

    # Directly use base_url if provided and looks valid
    if base_url:
        if base_url.startswith("http://") or base_url.startswith("https://"):
            return base_url.rstrip("/")
        else:
            return f"https://{base_url.rstrip('/')}"

    # Fallback: Try fetching from Atlassian accessible-resources
    if access_token and cloud_id:
        try:
            ar_resp = requests.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if ar_resp.status_code == 200:
                resources = ar_resp.json()
                resource = next((r for r in resources if r.get("id") == cloud_id), None)
                if resource is None:
                    resource = next(
                        (
                            r
                            for r in resources
                            if "read:jira-work" in r.get("scopes", [])
                        ),
                        None,
                    )
                if resource and resource.get("url"):
                    return resource["url"].rstrip("/")
        except Exception as e:
            print(
                f"Error: Failed to fetch accessible Jira resources. Details: {e}"
            )

    # No valid site resolved
    return None


@mcp.tool(name="jira_search_issues")
def jira_search_issues(
    metadata: Dict,
    jql: str,
    max_results: int = 25,
    next_page_token: Optional[str] = None,
) -> dict:
    """
    Search Jira issues using JQL (Jira Query Language) with pagination and proper issue URLs.

    This tool uses the updated Atlassian REST API endpoint `/rest/api/3/search/jql` and returns
    a standardized MCP response via `make_response()`. Issue URLs are constructed using the
    resolved Jira site URL, which is determined from `metadata["base_url"]` if present,
    otherwise from Atlassian accessible-resources.

    Args:
        metadata (Dict): Jira cloud, base_url metadata.
        jql (str): The Jira Query Language string to filter issues (e.g., "project = TEST AND status = 'To Do'").
        max_results (int): Maximum number of issues to return per page. Default is 25.
        next_page_token (str, optional): Pagination token returned from a previous response.

    Returns:
        dict: Standardized MCP response dictionary with the following structure:
            {
                "success": True/False,
                "action": "jira_search_issues",
                "message": Human-readable summary of results or error,
                "data": {
                    "issues": [
                        {
                            "key": "MCP-123",             # Jira issue key
                            "title": "Fix OAuth bug",     # Issue summary/title
                            "url": "https://.../browse/MCP-123",  # Full issue URL if site resolved
                            "status": "In Progress",      # Current status
                            "assignee": "John Doe"        # Assigned user's display name (if any)
                        },
                        ...
                    ],
                    "nextPageToken": "<token>" or None  # For pagination
                }
            }

    Example:
        {
            'success': True,
            'action': 'jira_search_issues',
            'message': 'Found 2 issues for JQL: assignee = currentUser()',
            'data': {
                'issues': [
                    {
                        'key': 'MCPPROJECT-12',
                        'title': 'test feature for MCP',
                        'url': 'https://scryai-team-pyxk7t81.atlassian.net/browse/MCPPROJECT-12',
                        'status': 'In Progress',
                        'assignee': 'Himanshu Punetha'
                    },
                    {
                        'key': 'MCPPROJECT-4',
                        'title': '(Sample) Define Notification Payload Structure',
                        'url': 'https://scryai-team-pyxk7t81.atlassian.net/browse/MCPPROJECT-4',
                        'status': 'In Progress',
                        'assignee': 'Himanshu Punetha'
                    }
                ],
                'nextPageToken': None
            }
        }

    """
    action = "jira_search_issues"
    try:
        creds = get_jira_creds(metadata)
        if not creds:
            return make_response(
                False, action, "❌ Jira credentials not found in metadata."
            )
        access_token = creds.get("access_token")
        cloud_id = creds.get("cloud_id")

        if not access_token or not cloud_id:
            return make_response(
                False, action, "Missing Jira authentication details in metadata."
            )

        # 🔹 Resolve base site URL
        site_url = resolve_jira_site_url(metadata)

        # Build API request
        url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search/jql"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        params = {
            "jql": jql,
            "maxResults": str(max_results),
            "fields": "summary,status,assignee",
        }

        if next_page_token:
            params["nextPageToken"] = next_page_token

        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            return make_response(
                False,
                action,
                f"Failed to search issues (status {resp.status_code})",
                {"details": resp.text},
            )

        data = resp.json()
        issues_raw = data.get("issues", [])
        next_token = data.get("nextPageToken")

        issues = []
        for issue in issues_raw:
            key = issue.get("key")
            fields = issue.get("fields") or {}
            summary = fields.get("summary", "")
            status = (fields.get("status") or {}).get("name", "")
            assignee = (fields.get("assignee") or {}).get("displayName")

            issue_url = (
                f"{site_url}/browse/{key}" if (site_url and key) else f"/browse/{key}"
            )

            issues.append(
                {
                    "key": key,
                    "title": summary,
                    "url": issue_url,
                    "status": status,
                    "assignee": assignee,
                }
            )

        message = f"Found {len(issues)} issues for JQL: {jql}"
        return make_response(
            True, action, message, {"issues": issues, "nextPageToken": next_token}
        )

    except Exception as e:
        return make_response(False, action, f"Error during Jira issue search: {str(e)}")


def adf_to_plain_text(blocks: list) -> str:
    """
    Convert Atlassian Document Format (ADF) node blocks into a readable plain-text
    representation.

    The function walks ADF block nodes recursively and extracts human-readable
    text. It supports common ADF node types encountered in Jira/Confluence
    content, including:
      - paragraph, heading, blockquote: concatenates contained text fragments
      - text: returns plain text
      - codeBlock: formats code blocks using fenced code markers
      - bulletList / orderedList: renders list items with '-' or '1.' style prefixes

    Args:
        blocks (list): A list of ADF block nodes (each node is a dict). Typical
            shape for a node::

                {
                    "type": "paragraph",
                    "content": [ {"type": "text", "text": "Hello"}, ... ]
                }

    Returns:
        str: Plain-text representation of the ADF content where each block is
        separated by a newline. Code blocks are wrapped with triple backticks.

    Behavior and edge cases:
        - Missing or empty text fragments are ignored.
        - Unknown node types are traversed by parsing their children.
        - The function is tolerant of malformed nodes (uses .get() everywhere)
          and will not raise on unexpected shapes.

    Example:
        >>> adf_to_plain_text([{"type": "paragraph", "content": [{"type": "text", "text": "Hi"}]}])
        'Hi'
    """
    lines = []

    def parse_node(node):
        """
        Each ADF node in Jira/Confluence has a `"type"` and optional `"content"`.
        This function inspects the type and handles known node structures:

        Node types and their meaning:
        - **paragraph**: A block of regular text (e.g., a comment or description line).
          Content contains `"text"` nodes.
          Example:
            {"type": "paragraph", "content": [{"type": "text", "text": "Hello"}]}

        - **heading**: A section title (level indicated by `"attrs": {"level": n}`).
          Example:
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Title"}]}

        - **blockquote**: Quoted text, often used in replies or references.

        - **text**: Leaf node containing actual text characters. May also include
          `"marks"` (e.g., bold, italic, link) which are ignored here for simplicity.

        - **codeBlock**: A fenced code snippet.
          Example:
            {
              "type": "codeBlock",
              "attrs": {"language": "python"},
              "content": [{"type": "text", "text": "print('hi')"}]
            }

        - **bulletList**: An unordered list.
          Contains one or more `"listItem"` nodes with textual `"content"` blocks.
          Example:
            {
              "type": "bulletList",
              "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Item 1"}]}]}
              ]
            }

        - **orderedList**: An ordered list (numbered).
          Similar to `bulletList`, but rendered with numerical prefixes.

        - **listItem**: A single item in a list; contains paragraph/text nodes.

        - **unknown/custom nodes**: Nodes with unrecognized `"type"` are traversed
          recursively by calling `parse_node` on their `"content"`.

        The output accumulates into the outer `lines` list, one string per block.
        """
        node_type = node.get("type")
        content = node.get("content", [])

        if node_type in ["paragraph", "heading", "blockquote"]:
            text = "".join(
                frag.get("text", "") for frag in content if frag.get("type") == "text"
            )
            if text:
                lines.append(text)
            for child in content:
                parse_node(child)

        elif node_type == "text":
            lines.append(node.get("text", ""))

        elif node_type == "codeBlock":
            code_text = "".join(
                frag.get("text", "") for frag in content if frag.get("type") == "text"
            )
            if code_text:
                lines.append(f"```\n{code_text}\n```")

        elif node_type == "bulletList":
            for li in content:
                if li.get("type") == "listItem":
                    li_text = []
                    for c in li.get("content", []):
                        li_text.append(adf_to_plain_text([c]))
                    lines.append("- " + " ".join(li_text))

        elif node_type == "orderedList":
            for idx, li in enumerate(content, start=1):
                if li.get("type") == "listItem":
                    li_text = []
                    for c in li.get("content", []):
                        li_text.append(adf_to_plain_text([c]))
                    lines.append(f"{idx}. " + " ".join(li_text))

        else:
            # fallback: parse children if present
            for child in content:
                parse_node(child)

    for block in blocks:
        parse_node(block)

    return "\n".join(lines)


@mcp.tool(name="jira_get_issue_details")
def jira_get_issue_details(metadata: Dict, issue_key: str) -> dict:
    """
    Fetch detailed information for a Jira issue, including summary, description,
    status, assignee, and a direct issue URL.

    - Uses `get_jira_creds(metadata)` to retrieve access_token and cloud_id.
    - Resolves the base site URL using `resolve_jira_site_url(metadata)`.
    - Converts the Atlassian Document Format (ADF) description into plain text, including code blocks, lists, and headings.
    - Returns standardized MCP response using `make_response`.

    Args:
        metadata (Dict): Jira authentication metadata (expects access_token, cloud_id, base_url/site optional)
        issue_key (str): The Jira issue key (e.g., "PROJ-123").

    Returns:
        dict: Standardized MCP response:
            {
                "success": True/False,
                "action": "jira_get_issue_details",
                "message": "Found issue PROJ-123",
                "data": {
                    "key": "PROJ-123",
                    "title": "Issue summary",
                    "url": "https://your-domain.atlassian.net/browse/PROJ-123",
                    "status": "In Progress",
                    "assignee": "John Doe",
                    "description": "Full plain-text description including code blocks..."
                }
            }

    Example `data`:
    {
        "key": "MCPPROJECT-12",
        "title": "test feature for MCP",
        "url": "https://scryai-team-pyxk7t81.atlassian.net/browse/MCPPROJECT-12",
        "status": "In Progress",
        "assignee": "Himanshu Punetha",
        "description": "Summary and code blocks...\nprint('Hello World')\n..."
    }
    """
    action = "jira_get_issue_details"

    try:
        creds = get_jira_creds(metadata)
        if not creds:
            return make_response(
                False, action, "❌ Jira credentials not found in metadata."
            )

        access_token = creds["access_token"]
        cloud_id = creds["cloud_id"]

        site_url = resolve_jira_site_url(metadata)

        url = (
            f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}"
        )
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return make_response(
                False,
                action,
                f"Failed to fetch issue (status {resp.status_code})",
                {"details": resp.text},
            )

        data = resp.json()
        fields = data.get("fields", {})

        key = data.get("key")
        summary = fields.get("summary", "")
        status = (fields.get("status") or {}).get("name", "")
        assignee = (fields.get("assignee") or {}).get("displayName")

        description_blocks = fields.get("description", {}).get("content", [])
        plain_desc = adf_to_plain_text(description_blocks) or "(empty)"

        issue_url = f"{site_url}/browse/{key}" if site_url and key else f"/browse/{key}"

        issue_data = {
            "key": key,
            "title": summary,
            "url": issue_url,
            "status": status,
            "assignee": assignee,
            "description": plain_desc,
        }

        return make_response(True, action, f"Found issue {key}", issue_data)

    except Exception as e:
        return make_response(False, action, f"Error fetching issue details: {str(e)}")


@mcp.tool(name="jira_get_issue_comments")
def get_issue_comments(metadata: Dict, issue_key: str) -> dict:
    """
    Fetch the latest comments from a Jira issue, including full text from code blocks.
    Each comment includes a direct URL linking to that specific comment.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, base_url, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").

    Returns:
        dict: Standardized output via make_response(), example:
        {
            "success": True,
            "action": "jira_get_issue_comments",
            "message": "Found 3 comments for issue PROJ-123",
            "data": {
                "comments": [
                    {
                        "author": "John Doe",
                        "body": "Comment text or code...",
                        "created": "2025-10-08T10:00:00Z",
                        "url": "https://your-domain.atlassian.net/browse/PROJ-123?focusedCommentId=10001"
                    },
                    ...
                ]
            }
        }
    """
    action = "jira_get_issue_comments"

    try:
        creds = get_jira_creds(metadata)
        if not creds:
            return make_response(
                False, action, "Jira credentials not found in metadata."
            )

        access_token = creds["access_token"]
        cloud_id = creds["cloud_id"]

        # Resolve site URL for constructing comment links
        site_url = resolve_jira_site_url(metadata)
        if not site_url:
            return make_response(
                False, action, "Unable to resolve Jira site URL from metadata."
            )

        # Fetch comments from Jira REST API
        url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/comment"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return make_response(
                False,
                action,
                f"Failed to fetch comments (status {resp.status_code})",
                {"details": resp.text},
            )

        data = resp.json()
        comments_raw = data.get("comments", [])
        if not comments_raw:
            return make_response(
                True,
                action,
                f"No comments found for issue {issue_key}",
                {"comments": []},
            )

        comments = []
        for c in comments_raw[:5]:  # limit to 5 most recent
            body = adf_to_plain_text(c.get("body", {}).get("content", []))
            comment_id = c.get("id")
            comment_url = (
                f"{site_url}/browse/{issue_key}?focusedCommentId={comment_id}"
                if comment_id
                else None
            )

            comments.append(
                {
                    "author": c.get("author", {}).get("displayName"),
                    "body": body,
                    "created": c.get("created"),
                    "url": comment_url,
                }
            )

        message = f"Found {len(comments)} comments for issue {issue_key}"
        return make_response(True, action, message, {"comments": comments})

    except Exception as e:
        return make_response(False, action, f"Error fetching comments: {str(e)}")


@mcp.tool(name="jira_add_comment")
def add_comment(metadata: Dict, issue_key: str, comment: str) -> dict:
    """
    Add a comment to a Jira issue and return standardized MCP output including comment URL.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, base_url, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").
        comment (str): The text content to post as a comment.

    Returns:
        dict: Standardized MCP response via make_response(), example:
        {
            "success": True,
            "action": "jira_add_comment",
            "message": "Comment added successfully",
            "data": {
                "issue_key": "PROJ-123",
                "comment_url": "https://your-domain.atlassian.net/browse/PROJ-123?focusedCommentId=10001",
                "comment": "Your comment text"
            }
        }
    """
    action = "jira_add_comment"
    try:
        creds = get_jira_creds(metadata)
        if not creds:
            return make_response(
                False, action, "Jira credentials not found in metadata."
            )

        access_token = creds["access_token"]
        cloud_id = creds["cloud_id"]
        site_url = resolve_jira_site_url(metadata)
        if not site_url:
            return make_response(
                False, action, "Unable to resolve Jira site URL from metadata."
            )

        url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/comment"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        data = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": comment}],
                    }
                ],
            }
        }

        resp = requests.post(url, headers=headers, json=data, timeout=30)
        if resp.status_code != 201:
            return make_response(
                False,
                action,
                f"Failed to add comment: {resp.status_code}",
                {"details": resp.text},
            )

        resp_data = resp.json()
        comment_id = resp_data.get("id")
        comment_url = (
            f"{site_url}/browse/{issue_key}?focusedCommentId={comment_id}"
            if comment_id
            else None
        )

        return make_response(
            True,
            action,
            f"Comment added to {issue_key}",
            {"issue_key": issue_key, "comment_url": comment_url, "comment": comment},
        )

    except Exception as e:
        return make_response(False, action, f"Error adding comment: {str(e)}")


@mcp.tool(name="jira_transition_issue")
def transition_issue(metadata: Dict, issue_key: str, target_status: str) -> dict:
    """
    Transition a Jira issue to a new workflow status (e.g., from "To Do" → "In Progress" → "Done").

    This tool fetches all valid transitions for the given issue, validates the requested target status,
    and performs the transition if possible.

    🔹 **Common Transition States (target_status)**
    These vary by project, but typical Jira Cloud statuses include:
    - "To Do"
    - "In Progress"
    - "In Review"
    - "Blocked"
    - "Ready for QA"
    - "Testing"
    - "Done"
    - "Closed"
    - "Reopened"
    - "Backlog"

    🔹 **Example Usages**
    - Move an issue from "To Do" → "In Progress"
    - Transition a bug from "In Progress" → "Done"
    - Reopen a completed issue ("Done" → "Reopened")

    Args:
        metadata (Dict): Metadata containing Jira credentials and context (expects keys: access_token, cloud_id, base_url, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").
        target_status (str): The exact or case-insensitive name of the desired status (e.g., "In Progress", "Done").

    Returns:
        dict: Standardized MCP response via make_response(), example:
        {
            "success": True,
            "action": "jira_transition_issue",
            "message": "Issue transitioned successfully",
            "data": {
                "issue_key": "PROJ-123",
                "new_status": "In Progress",
                "issue_url": "https://your-domain.atlassian.net/browse/PROJ-123"
            }
        }

    Notes:
        - If the provided status is invalid, the response will include a list of valid transitions.
        - This tool uses the Jira Cloud v3 REST API endpoint:
          `POST /rest/api/3/issue/{issueIdOrKey}/transitions`
    """
    action = "jira_transition_issue"
    try:
        creds = get_jira_creds(metadata)
        if not creds:
            return make_response(
                False, action, "Jira credentials not found in metadata."
            )

        access_token = creds["access_token"]
        cloud_id = creds["cloud_id"]
        site_url = resolve_jira_site_url(metadata)
        if not site_url:
            return make_response(
                False, action, "Unable to resolve Jira site URL from metadata."
            )

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        # Step 1: Fetch available transitions
        transitions_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions"
        transitions_resp = requests.get(transitions_url, headers=headers, timeout=30)
        if transitions_resp.status_code != 200:
            return make_response(
                False,
                action,
                f"Failed to fetch transitions: {transitions_resp.status_code}",
                {"details": transitions_resp.text},
            )

        transitions = transitions_resp.json().get("transitions", [])
        if not transitions:
            return make_response(
                False, action, f"No available transitions found for {issue_key}"
            )

        # Step 2: Match the desired transition by name (case-insensitive)
        transition = next(
            (t for t in transitions if t["name"].lower() == target_status.lower()), None
        )
        if not transition:
            options = [t["name"] for t in transitions]
            return make_response(
                False,
                action,
                f"Invalid target status: '{target_status}'.",
                {"available_transitions": options},
            )

        transition_id = transition["id"]

        # Step 3: Execute the transition
        transition_data = {"transition": {"id": transition_id}}
        do_transition_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions"
        do_resp = requests.post(
            do_transition_url, headers=headers, json=transition_data, timeout=30
        )

        if do_resp.status_code != 204:
            return make_response(
                False,
                action,
                f"Transition failed: {do_resp.status_code}",
                {"details": do_resp.text},
            )

        issue_url = f"{site_url}/browse/{issue_key}"
        return make_response(
            True,
            action,
            f"Issue {issue_key} transitioned to '{target_status}'.",
            {
                "issue_key": issue_key,
                "new_status": target_status,
                "issue_url": issue_url,
            },
        )

    except Exception as e:
        return make_response(False, action, f"Error performing transition: {str(e)}")


def get_cloud_id_from_token(access_token: str) -> str:
    """
    Fetch cloud_id from Jira using the access_token.

    Args:
        access_token (str): The OAuth 2.0 access token.

    Returns:
        str: The cloud_id associated with the Jira account.

    Raises:
        ValueError: If no accessible Jira resources are found for this token.
    """
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    response = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources", headers=headers
    )
    response.raise_for_status()
    resources = response.json()
    if not resources:
        raise ValueError("No accessible Jira resources found for this token.")
    return resources[0]["id"]


@mcp.tool(name="jira_list_projects")
def list_projects(metadata: Dict) -> dict:
    """
    List Jira projects available to the authenticated user.

    Returns standardized MCP response including project keys, names, and URLs.

    Args:
        metadata (Dict): Metadata containing Jira credentials under 'jira' key (expects access_token, cloud_id, base_url, etc.).

    Returns:
        dict: Standardized output via make_response(), example:
        {
            "success": True,
            "action": "jira_list_projects",
            "message": "Found 5 projects",
            "data": {
                "projects": [
                    {
                        "key": "MCPPROJECT",
                        "name": "MCP Project",
                        "url": "https://your-domain.atlassian.net/projects/MCPPROJECT"
                    },
                    ...
                ]
            }
        }
    """
    action = "jira_list_projects"
    try:
        creds = get_jira_creds(metadata)
        if not creds:
            return make_response(
                False, action, "Jira credentials not found in metadata."
            )

        access_token = creds["access_token"]
        cloud_id = creds["cloud_id"]

        site_url = resolve_jira_site_url(metadata)
        if not site_url:
            return make_response(
                False, action, "Unable to resolve Jira site URL from metadata."
            )

        url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project/search"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

        resp = requests.get(url, headers=headers, timeout=30)
        if resp.status_code != 200:
            return make_response(
                False,
                action,
                f"Failed to fetch projects (status {resp.status_code})",
                {"details": resp.text},
            )

        data = resp.json()
        if "errorMessages" in data:
            return make_response(False, action, f"Error: {data['errorMessages']}")

        projects_raw = data.get("values", [])
        if not projects_raw:
            return make_response(
                True, action, "No projects found for this user.", {"projects": []}
            )

        projects = []
        for p in projects_raw:
            key = p.get("key")
            name = p.get("name")
            project_url = f"{site_url}/projects/{key}" if key else None
            projects.append({"key": key, "name": name, "url": project_url})

        message = f"Found {len(projects)} projects"
        return make_response(True, action, message, {"projects": projects})

    except Exception as e:
        return make_response(False, action, f"Error fetching projects: {str(e)}")


# Auth Flow
async def authorize(request: Request):
    """
    Redirects user to Jira OAuth2 authorization page.

    Args:
        request (Request): The incoming HTTP request object.

    Returns:
        RedirectResponse: Redirects to Jira OAuth2 authorization URL.
    """
    scope = " ".join(SCOPES)
    url = (
        "https://auth.atlassian.com/authorize"
        f"?audience=api.atlassian.com"
        f"&client_id={CLIENT_ID}"
        f"&scope={scope}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&prompt=consent"
    )
    return RedirectResponse(url)


async def oauth2callback(request: Request):
    """
    Handles Jira OAuth2 callback, exchanges code for tokens and returns user info.

    Args:
        request (Request): The incoming HTTP request object containing the authorization code.

    Returns:
        JSONResponse: Contains authentication result, tokens, and cloud_id.
    """
    code = request.query_params.get("code")
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

    if not access_token:
        return JSONResponse(
            {"error": "Token exchange failed", "details": token_resp}, status_code=400
        )

    # Get user's email
    userinfo = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    print("Userinfo from /me:", userinfo)

    email = userinfo.get("email")

    # Get cloud ID for Jira site
    resources = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()

    print(resources)
    jira_resources = [r for r in resources if "read:jira-work" in r.get("scopes", [])]

    if not jira_resources:
        return JSONResponse(
            {"error": "No Jira resource with required scope found"}, status_code=400
        )

    cloud_id = jira_resources[0]["id"]

    print("refresh_token:", refresh_token)
    return JSONResponse(
        {
            "message": f"Authenticated as {email}",
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "cloud_id": cloud_id,
        }
    )


async def status(request: Request):
    """
    Returns authentication status for a given email.

    Args:
        request (Request): The incoming HTTP request object containing the email query param.

    Returns:
        JSONResponse: Status of authentication ("authenticated" or "pending").
    """
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
