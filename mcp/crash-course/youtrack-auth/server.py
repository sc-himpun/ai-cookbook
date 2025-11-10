# -*- coding: utf-8 -*-
import os
import requests
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from requests.auth import HTTPBasicAuth
import urllib.parse
from datetime import datetime, timedelta

# ─── Config ──────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("YOUTRACK_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YOUTRACK_CLIENT_SECRET", "")
PORT = int(os.getenv("YOUTRACK_PORT", "8089"))

print(f"Using YouTrack CLIENT_ID: {CLIENT_ID}")
print(f"Using YouTrack CLIENT_SECRET: {CLIENT_SECRET}")

REDIRECT_URI = os.getenv(
    "YOUTRACK_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback"
)
print(f"Using YouTrack REDIRECT_URI: {REDIRECT_URI}")

# Endpoints are based on YouTrack OAuth 2.0
# Example for JetBrains Cloud-hosted: https://<your-domain>.myjetbrains.com/youtrack
AUTH_PATH = "/hub/api/rest/oauth2/auth"
TOKEN_PATH = "/hub/api/rest/oauth2/token"
USERINFO_PATH = "/api/users/me"

SCOPES = os.getenv("YOUTRACK_SCOPES", "YouTrack").split()

# ─── In-memory Token Store (for testing only) ───────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP Setup ───────────────────────────────
mcp = FastMCP("youtrack-auth-mcp", host="0.0.0.0", port=PORT)


# ─── Helpers ───────────────────────────────────────────────────────────────
def refresh_access_token(base_url: str, refresh_token: str) -> Dict[str, str]:
    """Refresh YouTrack access token using refresh_token."""
    token_url = f"{base_url}/hub/api/rest/oauth2/token"

    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }

    try:
        resp = requests.post(
            token_url,
            data=data,
            auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
            timeout=10,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        raise Exception(f"❌ Failed to refresh token: {e}")

    return {
        "access_token": token_data.get("access_token"),
        # fallback if not rotated
        "refresh_token": token_data.get("refresh_token", refresh_token),
    }


def get_youtrack_creds(metadata: Optional[Dict]) -> Dict[str, Any]:
    """Extract YouTrack creds (url + access_token) from metadata, refresh if expired."""
    if not metadata:
        raise Exception("❌ Missing metadata for YouTrack")

    url = metadata.get("url")
    access_token = metadata.get("access_token")
    refresh_token = metadata.get("refresh_token")

    if not url:
        raise Exception("❌ Missing url in YouTrack metadata")
    if not access_token:
        raise Exception("❌ Missing access_token in YouTrack metadata")

    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    # 🔹 Test the token with a lightweight request
    try:
        resp = requests.get(
            f"{url.rstrip('/')}/api/users/me", headers=headers, timeout=5
        )
        if resp.status_code == 401 and refresh_token:
            # token expired → refresh it
            new_tokens = refresh_access_token(url.rstrip("/"), refresh_token)
            metadata["access_token"] = new_tokens["access_token"]
            metadata["refresh_token"] = new_tokens["refresh_token"]
            headers["Authorization"] = f"Bearer {new_tokens['access_token']}"
        elif resp.status_code != 200:
            raise Exception(f"❌ Failed auth check: {resp.status_code} {resp.text}")
    except requests.RequestException as e:
        raise Exception(f"❌ Error checking YouTrack token: {e}")

    return {"url": url.rstrip("/"), "headers": headers}


def _generate_youtrack_auth_url(base_url: str) -> str:
    """Build authorization URL for YouTrack OAuth."""
    scope = " ".join(SCOPES)
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": scope,
        "state": "debug123",
        "request_credentials": "skip",
        "access_type": "offline",
    }
    auth_url = f"{base_url}{AUTH_PATH}?{urllib.parse.urlencode(params)}"
    return auth_url


def extract_state(issue: dict) -> Optional[str]:
    """Get the issue state from either top-level 'state' or inside 'fields'."""
    # 1. Try top-level state
    state = issue.get("state")
    if state and isinstance(state, dict):
        return state.get("name")

    # 2. Try inside fields array
    fields = issue.get("fields") or []
    for f in fields:
        if f.get("name") == "State" and f.get("value"):
            return f["value"].get("name")

    # fallback
    return None


@mcp.tool(name="youtrack_list_issues")
def list_issues(
    metadata: dict,
    assignee: str = "me",
    state: str = "unresolved",
    project: str = "",
    text: str = "",
    updated_since: str = "",
) -> dict:
    """
    List issues from YouTrack with flexible filtering options such as assignee, state,
    project, text search, and update timestamp.

    Parameters:
    -----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info. This is typically provided
        by the MCP server environment.

    assignee : str, optional, default="me"
        Filter issues by assignee. Acceptable values:
        - "me": issues assigned to the authenticated user
        - "all": issues assigned to all users (no assignee filter applied)
        - "<user_id>": issues assigned to a specific user (YouTrack username/login, usually firstname.lastname)

    state : str, optional, default="unresolved"
        Filter issues by workflow state. Supported values:
        - "unresolved" → `State: {Unresolved}`
        - "open"       → `State: {Open}`
        - "resolved"   → `State: {Resolved}`
        - "in progress"→ `State: {In Progress}`
        - Any other string is passed directly as a state filter.

    project : str, optional, default=""
        Limit results to a specific project by project key.

    text : str, optional, default=""
        Free-text search within issues (summary, description, comments).

    updated_since : str, optional, default=""
        Only include issues updated since the given timestamp.
        Example: "2025-01-01" or "2w" (for two weeks ago).

    Returns:
    --------
    dict
        A structured dictionary with the following fields:
        {
            "success": bool,          # True if the request succeeded
            "action": str,            # Action name ("list_issues")
            "message": str,           # Human-readable summary
            "data": list[dict]        # List of issues with:
                {
                    "id": str,       # YouTrack issue ID (readable format)
                    "summary": str,  # Issue title/summary
                    "state": str,    # Normalized issue state
                    "url": str       # Direct URL to the issue
                }
        }

    Notes:
    ------
    - This tool is versatile: it can list issues for a single user, all users, or the current user.
    - It supports combining multiple filters (assignee + project + state + text).
    - Uses YouTrack's standard search query syntax internally.
    - The `state` parameter is normalized using common mappings (Unresolved, Open, Resolved, In Progress).

    Examples:
    ---------
    # List unresolved issues assigned to me
    youtrack_list_issues(metadata=my_metadata)

    # List resolved issues for a specific user
    youtrack_list_issues(metadata=my_metadata, assignee="jane.doe", state="resolved")

    # List all open issues across all projects
    youtrack_list_issues(metadata=my_metadata, assignee="all", state="open")

    # List issues in project "ABC" updated in the last 2 weeks
    youtrack_list_issues(metadata=my_metadata, project="ABC", updated_since="2w")
    """

    creds = get_youtrack_creds(metadata)

    # Build YouTrack query
    query_parts = []
    state_map = {
        "unresolved": "State: {Unresolved}",
        "open": "State: {Open}",
        "resolved": "State: {Resolved}",
        "in progress": "State: {In Progress}",
    }
    if state:
        query_parts.append(state_map.get(state.lower(), f"State: {{{state}}}"))

    if assignee and assignee.lower() != "all":
        query_parts.append(
            "Assignee: me" if assignee.lower() == "me" else f"Assignee: {assignee}"
        )
    if project:
        query_parts.append(f"Project: {project}")
    if text:
        query_parts.append(text)
    if updated_since:
        query_parts.append(f"Updated: {{{updated_since}}}")

    query = " ".join(query_parts)
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,fields(name,value(name))"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code != 200:
        return {
            "success": False,
            "action": "list_issues",
            "message": "Failed to fetch issues",
            "data": {"error": r.text},
        }

    results = [
        {
            "id": issue.get("idReadable"),
            "summary": issue.get("summary"),
            "state": extract_state(issue),
            "url": f"{creds['url']}/issue/{issue.get('idReadable')}",
        }
        for issue in r.json()
    ]

    return {
        "success": True,
        "action": "list_issues",
        "message": f"Retrieved {len(results)} issues",
        "data": results,
    }


@mcp.tool(name="youtrack_list_issues_reported_by_me")
def youtrack_list_my_reported(metadata: dict) -> dict:
    """
    List issues reported by the authenticated user in YouTrack, including URLs and normalized state.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        Typically provided automatically by the MCP server environment.

    Returns
    -------
    dict
        A normalized dictionary with the following structure:
        {
            "success": bool,        # True if the request succeeded, False otherwise
            "action": str,          # Action name ("list_my_reported")
            "message": str,         # Human-readable summary
            "data": list[dict]      # List of issues, each containing:
                {
                    "id": str,       # YouTrack issue IDReadable (e.g., "PROJ-123")
                    "summary": str,  # Short summary/title of the issue
                    "state": str,    # Current workflow state (e.g., Open, In Progress, Resolved)
                    "url": str       # Direct link to the issue in YouTrack
                }
        }

    Notes
    -----
    - Uses YouTrack REST API with a query of the form: `Reporter: {me}`
    - Automatically normalizes the issue state using the issue's 'fields' or top-level 'state'.
    - Useful for generating personal reports, dashboards, or for LLM-assisted querying.

    Examples
    --------
    # List issues reported by the authenticated user
    youtrack_list_my_reported(metadata=my_metadata)
    """
    creds = get_youtrack_creds(metadata)
    query = "Reporter: {me}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,fields(name,value(name))"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code != 200:
        return {
            "success": False,
            "action": "list_my_reported",
            "message": "Failed to fetch my reported issues",
            "data": {"error": r.text},
        }

    results = [
        {
            "id": issue.get("idReadable"),
            "summary": issue.get("summary"),
            "state": extract_state(issue),
            "url": f"{creds['url']}/issue/{issue.get('idReadable')}",
        }
        for issue in r.json()
    ]

    return {
        "success": True,
        "action": "list_my_reported",
        "message": f"Retrieved {len(results)} reported issues",
        "data": results,
    }


@mcp.tool(name="youtrack_list_my_recent")
def youtrack_list_my_recent(metadata: dict, days: int = 7) -> dict:
    """
    List issues assigned to the authenticated user that were updated within the last N days.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        This is typically provided automatically by the MCP server environment.
    days : int, optional, default=7
        Number of days to look back when filtering issues by update time.
        Example:
        - days=7 → issues updated since 7 days ago
        - days=1 → issues updated since yesterday
        - days=30 → issues updated since the last 30 days

    Returns
    -------
    dict
        {
            "success": bool,
            "action": "list_my_recent",
            "message": str,    # Summary of retrieved issues
            "data": list[dict] # Issues with id, summary, state, updated, url
        }

    Notes
    -----
    - Uses YouTrack REST API with a query like:
        `Assignee: me Updated: {2025-09-15}`
    - The cutoff date is calculated in Python as (today - days).
    - `Updated: {YYYY-MM-DD}` means "issues updated on or after this date".

    Examples
    --------
    # Issues updated in the last 7 days
    youtrack_list_my_recent(metadata=my_metadata)

    # Issues updated in the last 3 days
    youtrack_list_my_recent(metadata=my_metadata, days=3)

    # Issues updated in the last 14 days
    youtrack_list_my_recent(metadata=my_metadata, days=14)
    """
    creds = get_youtrack_creds(metadata)

    # Compute cutoff date in YYYY-MM-DD format
    cutoff_date = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

    # YouTrack query: updated on or after cutoff_date
    query = f"Assignee: me Updated: {{{cutoff_date}}} .. Today"

    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,updated,fields(name,value(name))"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code != 200:
        return {
            "success": False,
            "action": "list_my_recent",
            "message": "Failed to fetch my recent issues",
            "data": {"error": r.text},
        }

    results = [
        {
            "id": issue.get("idReadable"),
            "summary": issue.get("summary"),
            "state": extract_state(issue),
            "updated": issue.get("updated"),
            "url": f"{creds['url']}/issue/{issue.get('idReadable')}",
        }
        for issue in r.json()
    ]

    return {
        "success": True,
        "action": "list_my_recent",
        "message": f"Retrieved {len(results)} issues updated in the last {days} days",
        "data": results,
    }


@mcp.tool(name="youtrack_add_comment")
def add_comment(metadata: dict, issue_id: str, comment_text: str) -> dict:
    """
    Add a comment to a specific YouTrack issue.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        Typically provided automatically by the MCP server environment.
    issue_id : str
        The ID of the YouTrack issue to which the comment should be added (e.g., "PROJ-123").
    comment_text : str
        The text content of the comment to be added.

    Returns
    -------
    dict
        A normalized dictionary with the following structure:
        {
            "success": bool,          # True if comment was successfully added
            "action": str,            # Action name ("add_comment")
            "message": str,           # Human-readable summary
            "data": dict              # Contains:
                {
                    "issue_id": str,   # The issue ID
                    "comment_id": str, # The newly created comment's ID
                    "url": str         # Direct link to the issue in YouTrack
                }
        }

    Notes
    -----
    - Uses the YouTrack REST API endpoint `/api/issues/{issue_id}/comments`.
    - Handles both success (HTTP 200, 201) and failure cases, returning error text if failed.
    - Useful for programmatically adding comments to issues, e.g., from an automated workflow or LLM-driven assistant.

    Examples
    --------
    # Add a comment to issue PROJ-123
    add_comment(metadata=my_metadata, issue_id="PROJ-123", comment_text="This issue needs attention.")
    """
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}/comments?fields=id"
    payload = {"text": comment_text}
    r = requests.post(
        url,
        headers={**creds["headers"], "Content-Type": "application/json"},
        json=payload,
    )

    if r.status_code in (200, 201):
        return {
            "success": True,
            "action": "add_comment",
            "message": f"Comment added to {issue_id}",
            "data": {
                "issue_id": issue_id,
                "comment_id": r.json().get("id"),
                "url": f"{creds['url']}/issue/{issue_id}",
            },
        }

    return {
        "success": False,
        "action": "add_comment",
        "message": "Failed to add comment",
        "data": {"error": r.text, "url": f"{creds['url']}/issue/{issue_id}"},
    }


@mcp.tool(name="youtrack_search_issues")
def youtrack_search_issues(metadata: dict, keyword: str, field: str = "both") -> dict:
    """
    Search for YouTrack issues by summary, description, or both, returning normalized state and URLs.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        Typically provided automatically by the MCP server environment.
    keyword : str
        The keyword to search for within issue summaries and/or descriptions.
    field : str, optional, default="both"
        The field(s) to search in:
        - "summary": search only in the issue summary/title
        - "description": search only in the issue description
        - "both": search in both summary and description

    Returns
    -------
    dict
        A normalized dictionary with the following structure:
        {
            "success": bool,          # True if request succeeded, False otherwise
            "action": str,            # Action name ("search_issues")
            "message": str,           # Human-readable summary
            "data": list[dict]        # List of issues matching the search, each containing:
                {
                    "id": str,       # YouTrack issue ID (e.g., "PROJ-123")
                    "summary": str,  # Issue summary/title
                    "state": str,    # Current workflow state (e.g., Open, Resolved)
                    "url": str       # Direct link to the issue in YouTrack
                }
        }

    Notes
    -----
    - Performs a case-insensitive match of the keyword in the specified field(s).
    - Uses the YouTrack REST API endpoint `/api/issues`.
    - Normalizes the issue state using the `fields` property or top-level state information.
    - Useful for programmatic issue discovery, report generation, or LLM-driven queries.

    Examples
    --------
    # Search all issues containing "login" in summary or description
    youtrack_search_issues(metadata=my_metadata, keyword="login")

    # Search issues containing "error" only in description
    youtrack_search_issues(metadata=my_metadata, keyword="error", field="description")

    # Search issues containing "payment" only in summary
    youtrack_search_issues(metadata=my_metadata, keyword="payment", field="summary")
    """
    creds = get_youtrack_creds(metadata)
    query = keyword
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,description,fields(name,value(name))"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code != 200:
        return {
            "success": False,
            "action": "search_issues",
            "message": "Failed to search issues",
            "data": {"error": r.text},
        }

    results = []
    for issue in r.json():
        combined_text = ""
        if field in ("summary", "both"):
            combined_text += issue.get("summary") or ""
        if field in ("description", "both"):
            combined_text += " " + (issue.get("description") or "")

        if keyword.lower() in combined_text.lower():
            results.append(
                {
                    "id": issue.get("idReadable"),
                    "summary": issue.get("summary"),
                    "state": extract_state(issue),
                    "url": f"{creds['url']}/issue/{issue.get('idReadable')}",
                }
            )

    return {
        "success": True,
        "action": "search_issues",
        "message": f"Found {len(results)} issues",
        "data": results,
    }


@mcp.tool(name="youtrack_fetch_issue")
def youtrack_fetch_issue(metadata: dict, issue_id: str) -> dict:
    """
    Fetch full details of a YouTrack issue by its ID, including state and URL.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        Typically provided automatically by the MCP server environment.
    issue_id : str
        The ID of the issue to fetch (e.g., "PROJ-123").

    Returns
    -------
    dict
        A normalized dictionary with the following structure:
        {
            "success": bool,        # True if the issue was fetched successfully
            "action": str,          # Name of the action/tool ("fetch_issue")
            "message": str,         # Human-readable summary
            "data": dict            # Issue details
                {
                    "issue_id": str,       # YouTrack issue ID
                    "summary": str,        # Issue summary/title
                    "description": str,    # Full issue description
                    "state": str,          # Current workflow state (e.g., Open, In Progress, Resolved)
                    "url": str             # Direct link to the issue in YouTrack
                }
        }

    Notes
    -----
    - Uses the YouTrack REST API to retrieve issue details.
    - The `state` field is normalized using the issue's `fields`.
    - Useful for dashboards, reporting, or LLM-assisted queries.

    Examples
    --------
    # Fetch a specific issue
    issue = youtrack_fetch_issue(metadata=my_metadata, issue_id="PROJ-123")
    print(issue["data"]["summary"], issue["data"]["state"], issue["data"]["url"])
    """
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}?fields=summary,description,fields(name,value(name))"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code == 200:
        data = r.json()
        return {
            "success": True,
            "action": "fetch_issue",
            "message": f"Issue {issue_id} details retrieved",
            "data": {
                "issue_id": issue_id,
                "summary": data.get("summary"),
                "description": data.get("description"),
                "state": extract_state(data),
                "url": f"{creds['url']}/issue/{issue_id}",
            },
        }

    return {
        "success": False,
        "action": "fetch_issue",
        "message": f"Failed to fetch issue {issue_id}",
        "data": {"error": r.text},
    }


@mcp.tool(name="youtrack_get_comments")
def youtrack_get_comments(metadata: dict, issue_id: str) -> dict:
    """
    Fetch all comments from a specific YouTrack issue, including author, creation date,
    and a direct URL to the issue.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        Typically provided automatically by the MCP server environment.
    issue_id : str
        The IDReadable of the YouTrack issue for which to fetch comments
        (e.g., "PROJ-123").

    Returns
    -------
    dict
        A normalized dictionary with the following structure:
        {
            "success": bool,          # True if request succeeded, False otherwise
            "action": str,            # Action name ("get_comments")
            "message": str,           # Human-readable summary
            "data": dict              # Contains:
                {
                    "issue_id": str, # The YouTrack issue ID
                    "url": str,      # Direct link to the issue in YouTrack
                    "comments": list[dict] # List of comments, each containing:
                        {
                            "author": str,   # Login or name of the comment author
                            "created": str,  # Timestamp when the comment was created
                            "text": str      # Comment text
                        }
                }
        }

    Notes
    -----
    - Uses the YouTrack REST API endpoint `/api/issues/{issue_id}/comments`.
    - Includes both `login` and `name` of the comment author.
    - Provides a direct URL to the issue for easy navigation.
    - Useful for audits, reporting, or LLM-assisted querying of discussions.

    Examples
    --------
    # Fetch comments for a specific issue
    youtrack_get_comments(metadata=my_metadata, issue_id="PROJ-123")

    # Loop over comments and print authors
    result = youtrack_get_comments(metadata=my_metadata, issue_id="PROJ-123")
    for c in result["data"]["comments"]:
        print(c["author"], c["text"])
    """
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}/comments?fields=text,author(login,name),created"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code == 200:
        comments = [
            {
                "author": c.get("author", {}).get("login")
                or c.get("author", {}).get("name", "Unknown"),
                "created": c.get("created"),
                "text": c.get("text"),
            }
            for c in r.json()
        ]

        return {
            "success": True,
            "action": "get_comments",
            "message": f"Retrieved {len(comments)} comments from {issue_id}",
            "data": {
                "issue_id": issue_id,
                "url": f"{creds['url']}/issue/{issue_id}",
                "comments": comments,
            },
        }

    return {
        "success": False,
        "action": "get_comments",
        "message": f"Failed to fetch comments for {issue_id}",
        "data": {"error": r.text},
    }


@mcp.tool(name="youtrack_list_closed_issues_by_user")
def youtrack_list_closed_issues_by_user(metadata: dict, user: str = "me") -> dict:
    """
    List closed/resolved issues in YouTrack for a specific user, the current user, or all users.

    Parameters:
    -----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info. This is typically provided
        by the MCP server environment.
    user : str, optional, default="me"
        The target user for filtering issues. Acceptable values:
        - "me": issues assigned to the authenticated user
        - "all": issues assigned to all users (no assignee filter)
        - "<user_id>": specific user's user_id in YouTrack (firstname.lastname in lowercase or part before @ in email)

    Returns:
    --------
    dict
        A dictionary with the following structure:
        {
            "success": bool,          # True if the request succeeded
            "action": str,            # Name of the action/tool
            "message": str,           # Human-readable summary
            "data": list[dict]        # List of issues, each with:
                {
                    "id": str,       # YouTrack issue IDReadable
                    "summary": str,  # Issue summary/title
                    "state": str,    # Issue state (Resolved/Closed)
                    "resolved": str, # Timestamp when issue was resolved (if available)
                    "url": str       # Direct URL to the issue in YouTrack
                }
        }

    Notes:
    ------
    - The tool queries YouTrack using the standard REST API.
    - Uses the 'State: {Resolved}' filter to select only closed/resolved issues.
    - Automatically normalizes the issue state using YouTrack's 'fields' or top-level 'state'.
    - Useful for generating reports, dashboards, or LLM-assisted querying.

    Examples:
    ---------
    # Closed issues assigned to me
    youtrack_list_closed_issues_by_user(metadata=my_metadata, user="me")

    # Closed issues for all users
    youtrack_list_closed_issues_by_user(metadata=my_metadata, user="all")

    # Closed issues for a specific user
    youtrack_list_closed_issues_by_user(metadata=my_metadata, user="jane.doe")
    """

    creds = get_youtrack_creds(metadata)
    query_parts = ["State: {Resolved}"]

    if user.lower() != "all":
        query_parts.append(f"Assignee: {user}")

    query = " ".join(query_parts)
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,fields(name,value(name)),resolved"
    r = requests.get(url, headers=creds["headers"])

    if r.status_code != 200:
        return {
            "success": False,
            "action": "list_closed_issues_by_user",
            "message": "Failed to fetch closed issues",
            "data": {"error": r.text},
        }

    results = [
        {
            "id": issue.get("idReadable"),
            "summary": issue.get("summary"),
            "state": extract_state(issue),
            "resolved": issue.get("resolved"),
            "url": f"{creds['url']}/issue/{issue.get('idReadable')}",
        }
        for issue in r.json()
    ]

    return {
        "success": True,
        "action": "list_closed_issues_by_user",
        "message": f"Retrieved {len(results)} closed issues",
        "data": results,
    }


@mcp.tool(name="youtrack_issue_counts")
def youtrack_issue_counts(metadata: dict) -> dict:
    """
    Retrieve the total count of open and closed issues in YouTrack for the authenticated user.

    Parameters
    ----------
    metadata : dict
        Dictionary containing YouTrack credentials and access info.
        Typically provided automatically by the MCP server environment.

    Returns
    -------
    dict
        A normalized dictionary with the following structure:
        {
            "success": bool,        # True if both counts were fetched successfully
            "action": str,          # Action name ("issue_counts")
            "message": str,         # Human-readable summary
            "data": dict            # Contains counts of issues:
                {
                    "open": int,   # Number of open/unresolved issues
                    "closed": int  # Number of closed/resolved issues
                }
        }

    Notes
    -----
    - Uses the YouTrack REST API with queries:
        - `State: {Unresolved}` for open issues
        - `State: {Resolved}` for closed issues
    - Counts are determined by the number of returned issues for each query.
    - Useful for dashboards, reporting, and LLM-assisted issue analysis.

    Examples
    --------
    # Get counts of open and closed issues
    counts = youtrack_issue_counts(metadata=my_metadata)
    print(counts["data"]["open"], counts["data"]["closed"])
    """
    creds = get_youtrack_creds(metadata)
    counts = {"open": 0, "closed": 0}

    open_resp = requests.get(
        f"{creds['url']}/api/issues?query=State:{{Unresolved}}&fields=idReadable",
        headers=creds["headers"],
    )
    closed_resp = requests.get(
        f"{creds['url']}/api/issues?query=State:{{Resolved}}&fields=idReadable",
        headers=creds["headers"],
    )

    if open_resp.status_code == 200:
        counts["open"] = len(open_resp.json())
    if closed_resp.status_code == 200:
        counts["closed"] = len(closed_resp.json())

    success = open_resp.status_code == 200 and closed_resp.status_code == 200
    return {
        "success": success,
        "action": "issue_counts",
        "message": "Fetched issue counts" if success else "Failed to fetch some counts",
        "data": counts,
    }


async def authorize(request: Request):
    """Redirect user to YouTrack OAuth page (Hub)."""
    base_url = os.getenv("YOUTRACK_URL")
    if not base_url:
        return JSONResponse(
            {"error": "Missing YOUTRACK_URL in environment"}, status_code=400
        )

    auth_url = _generate_youtrack_auth_url(base_url)

    # Debug: print exactly what we're using
    print("🔍 /authorize debug")
    print(f"  HUB base_url:     {base_url}")
    print(f"  AUTH endpoint:    {base_url}{AUTH_PATH}")
    print(f"  redirect_uri:     {REDIRECT_URI}")
    print(f"  full auth url:    {auth_url}")

    return RedirectResponse(auth_url)


async def oauth2callback(request: Request):
    """Handle OAuth2 callback and exchange code for token (confidential client only)."""
    code = request.query_params.get("code")
    base_url = os.getenv("YOUTRACK_URL")  # e.g., https://<org>.myjetbrains.com

    if not code or not base_url:
        return JSONResponse({"error": "Missing code or YOUTRACK_URL"}, status_code=400)

    token_url = f"{base_url}{TOKEN_PATH}"

    # Form body (redirect_uri must match exactly what’s registered in Hub + used in /authorize)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    print("Using confidential client flow...")

    # Confidential flow: Basic Auth with client_id:client_secret
    headers = {"Accept": "application/json"}
    resp = requests.post(
        token_url,
        data=form,
        headers=headers,
        auth=HTTPBasicAuth(CLIENT_ID, CLIENT_SECRET),
    )
    print(f"  confidential attempt -> status: {resp.status_code}")

    if resp.status_code != 200:
        try:
            details = resp.json()
        except Exception:
            details = {"raw": resp.text}
        print(f"  ❌ Confidential flow failed: {resp.status_code} {details}")
        return JSONResponse(
            {
                "error": "Confidential flow failed",
                "status": resp.status_code,
                "details": details,
            },
            status_code=resp.status_code,
        )

    token_json = resp.json()
    access_token = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token")
    expires_in = token_json.get("expires_in", 0)

    if not access_token:
        return JSONResponse(
            {"error": "No access_token in response", "details": token_json},
            status_code=400,
        )

    print("  ✅ Confidential flow succeeded")
    return await _finalize_login(base_url, access_token, refresh_token, expires_in)


async def _finalize_login(
    base_url: str, access_token: str, refresh_token: str, expires_in: int
):
    """Fetch user info and return auth success payload."""
    userinfo_url = f"{base_url}{USERINFO_PATH}?fields=id,login,email,fullName"
    user_resp = requests.get(
        userinfo_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )

    if user_resp.status_code != 200:
        return JSONResponse(
            {"error": "Failed to fetch user info", "details": user_resp.text},
            status_code=400,
        )

    user_info = user_resp.json()
    email = user_info.get("email") or user_info.get("login") or "unknown"

    return JSONResponse(
        {
            "message": f"Authenticated as {email}",
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_in": expires_in,
            "url": base_url,
        }
    )


async def oauth_debug(request: Request):
    base_url = os.getenv("YOUTRACK_URL")
    out = {
        "CLIENT_ID_present": bool(CLIENT_ID),
        "CLIENT_SECRET_present": bool(CLIENT_SECRET),
        "YOUTRACK_URL": base_url,
        "AUTH_ENDPOINT": f"{base_url}{AUTH_PATH}" if base_url else None,
        "TOKEN_ENDPOINT": f"{base_url}{TOKEN_PATH}" if base_url else None,
        "USERINFO_ENDPOINT": f"{base_url}{USERINFO_PATH}" if base_url else None,
        "REDIRECT_URI": REDIRECT_URI,
        "SCOPES": SCOPES,
        "NOTE": "client_secret not shown; Basic Auth will be used for confidential flow",
    }
    return JSONResponse(out, status_code=200)


# ─── Starlette App ─────────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")
app = Starlette(
    routes=[
        Mount("/mcp-server", app=mcp_app),
        Route("/authorize", authorize),
        Route("/oauth2callback", oauth2callback),
        Route("/oauth-debug", oauth_debug),
    ],
    lifespan=mcp_app.lifespan,
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
