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
    "offline_access read:jira-user read:jira-work read:me write:jira-work"
).split()
PORT = int(os.getenv("JIRA_MCP_PORT", "8002"))  # Port for the FastMCP server
REDIRECT_URI = os.getenv(
    "JIRA_MCP_REDIRECT_URI",
    f"http://localhost:{PORT}/oauth2callback"
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
        headers={"Authorization": f"Bearer {access_token}"}
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
            "cloud_id": cloud_id
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
                "cloud_id": cloud_id
            }

    # If no valid access token and can't refresh, return None
    return None


@mcp.tool(name="jira_search_issues")
def search_issues(metadata: Dict, jql: str):
    """
    Search Jira issues using a JQL query.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, etc.).
        jql (str): Jira Query Language string to filter/search issues.

    Returns:
        str: Newline-separated list of issue keys and summaries, or error message if credentials are missing or no issues found.
    """
    creds = get_jira_creds(metadata)
    if not creds:
        return "❌ Jira credentials not found in metadata."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    # access_token = user_tokens[email]["access_token"]
    # cloud_id = user_tokens[email]["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search"
    headers = {"Authorization": f"Bearer {access_token}",
               "Accept": "application/json"}
    params = {"jql": jql, "maxResults": 5}

    resp = requests.get(url, headers=headers, params=params).json()
    issues = resp.get("issues", [])
    if not issues:
        return "No matching issues found."

    return "\n".join(f"{i['key']}: {i['fields']['summary']}" for i in issues)


@mcp.tool(name="jira_get_issue_details")
def get_issue_details(metadata: Dict, issue_key: str):
    """
    Get the summary and description of a Jira issue.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").

    Returns:
        str: Formatted summary and plain-text description, or error message if credentials are missing or issue not found.
    """
    creds = get_jira_creds(metadata)
    if not creds:
        return "❌ Jira credentials not found in metadata."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}"
    headers = {"Authorization": f"Bearer {access_token}",
               "Accept": "application/json"}

    resp = requests.get(url, headers=headers).json()
    fields = resp.get("fields", {})

    summary = fields.get("summary", "N/A")
    description = fields.get("description", {}).get("content", [])

    # Convert description from Atlassian Document Format (ADF) to plain text
    plain_desc = ""
    for block in description:
        if block.get("type") == "paragraph":
            for frag in block.get("content", []):
                if frag.get("type") == "text":
                    plain_desc += frag.get("text", "") + " "
            plain_desc += "\n"

    return f"Summary: {summary}\n\nDescription:\n{plain_desc.strip() or '(empty)'}"


@mcp.tool(name="jira_get_issue_comments")
def get_issue_comments(metadata: Dict, issue_key: str):
    """
    Fetch the latest comments from a Jira issue.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").

    Returns:
        str: Up to 5 most recent comments in "Author: Comment" format, or message if none found or credentials are missing.
    """
    creds = get_jira_creds(metadata)
    if not creds:
        return "❌ Jira credentials not found in metadata."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/comment"
    headers = {"Authorization": f"Bearer {access_token}",
               "Accept": "application/json"}

    resp = requests.get(url, headers=headers).json()
    comments = resp.get("comments", [])
    if not comments:
        return "No comments found."

    return "\n---\n".join(
        f"{c['author']['displayName']}: {c['body']['content'][0]['content'][0]['text']}"
        for c in comments[:5]
    )


@mcp.tool(name="jira_add_comment")
def add_comment(metadata: Dict, issue_key: str, comment: str):
    """
    Add a comment to a Jira issue.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").
        comment (str): The text content to post as a comment.

    Returns:
        str: Success message if comment is added, or error message if credentials are missing or request fails.
    """
    creds = get_jira_creds(metadata)
    if not creds:
        return "❌ Jira credentials not found in metadata."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/comment"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    data = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": comment}
                    ]
                }
            ]
        }
    }

    response = requests.post(url, headers=headers, json=data)
    if response.status_code == 201:
        return f"✅ Comment added to {issue_key}"
    else:
        return f"❌ Failed to add comment: {response.status_code} - {response.text}"


@mcp.tool(name="jira_transition_issue")
def transition_issue(metadata: Dict, issue_key: str, target_status: str):
    """
    Transition a Jira issue to a new status.

    Args:
        metadata (Dict): Metadata containing Jira credentials (expects keys: access_token, cloud_id, etc.).
        issue_key (str): The Jira issue key (e.g., "PROJ-123").
        target_status (str): The target status name to transition to (e.g., "In Progress", "Done").

    Returns:
        str: Success message if transition is performed, or error message listing valid transitions if the target status is invalid or credentials are missing.
    """
    creds = get_jira_creds(metadata)
    if not creds:
        return "❌ Jira credentials not found in metadata."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json"
    }

    # Step 1: Get available transitions
    transitions_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions"
    transitions_resp = requests.get(transitions_url, headers=headers).json()
    transitions = transitions_resp.get("transitions", [])

    # Step 2: Find transition ID matching the target status
    transition = next(
        (t for t in transitions if t["name"].lower() == target_status.lower()), None)
    if not transition:
        options = ", ".join(t["name"] for t in transitions)
        return f"❌ Invalid status. Available transitions: {options}"

    # Step 3: Perform the transition
    transition_id = transition["id"]
    do_transition_url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/transitions"
    data = {"transition": {"id": transition_id}}
    response = requests.post(do_transition_url, headers=headers, json=data)

    if response.status_code == 204:
        return f"✅ Issue {issue_key} transitioned to {target_status}"
    else:
        return f"❌ Transition failed: {response.status_code} - {response.text}"


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
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }
    response = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources", headers=headers)
    response.raise_for_status()
    resources = response.json()
    if not resources:
        raise ValueError("No accessible Jira resources found for this token.")
    return resources[0]["id"]


@mcp.tool(name="jira_list_projects")
def list_projects(metadata: Dict) -> str:
    """
    List Jira projects available to the authenticated user.

    Args:
        metadata (Dict): Metadata containing Jira credentials under 'jira' key.

    Returns:
        str: A list of project keys and names, or an error message.
    """
    creds = get_jira_creds(metadata)
    if not creds:
        return "❌ Jira credentials not found in metadata."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/project/search"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    resp = requests.get(url, headers=headers).json()

    if "errorMessages" in resp:
        return f"❌ Error: {resp['errorMessages']}"

    projects = resp.get("values", [])
    if not projects:
        return "No projects found for this user."

    return "\n".join(f"{p['key']}: {p['name']}" for p in projects)


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
        "https://auth.atlassian.com/oauth/token", json=data).json()
    access_token = token_resp.get("access_token")
    refresh_token = token_resp.get("refresh_token")

    if not access_token:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    # Get user's email
    userinfo = requests.get(
        "https://api.atlassian.com/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    print("Userinfo from /me:", userinfo)

    email = userinfo.get("email")

    # Get cloud ID for Jira site
    resources = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    print(resources)
    jira_resources = [
        r for r in resources
        if "read:jira-work" in r.get("scopes", [])
    ]

    if not jira_resources:
        return JSONResponse({"error": "No Jira resource with required scope found"}, status_code=400)

    cloud_id = jira_resources[0]["id"]

    print("refresh_token:", refresh_token)
    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "cloud_id": cloud_id
    })


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
