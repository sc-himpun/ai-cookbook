# -*- coding: utf-8 -*-
import os
import json
import requests
from typing import Dict, Any, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
import base64
import requests
from requests.auth import HTTPBasicAuth
import requests
import os
import urllib.parse

# ─── Config ──────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("YOUTRACK_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YOUTRACK_CLIENT_SECRET", "")
# PORT = 8089
PORT = int(os.getenv("YOUTRACK_PORT", "8089"))

print(f"Using YouTrack CLIENT_ID: {CLIENT_ID}")
print(f"Using YouTrack CLIENT_SECRET: {CLIENT_SECRET}")

REDIRECT_URI = os.getenv("YOUTRACK_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")
print(f"Using YouTrack REDIRECT_URI: {REDIRECT_URI}")

# Endpoints are based on YouTrack OAuth 2.0
# Example for JetBrains Cloud-hosted: https://<your-domain>.myjetbrains.com/youtrack
AUTH_PATH = "/hub/api/rest/oauth2/auth"
TOKEN_PATH = "/hub/api/rest/oauth2/token"
USERINFO_PATH = "/api/users/me"

# Requested scopes (adjust if needed)
# SCOPES = [
#     "YouTrack",
#     "Scry-MCP",
# ]
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
            timeout=10
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        raise Exception(f"❌ Failed to refresh token: {e}")

    return {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token", refresh_token)  # fallback if not rotated
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

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    }

    # 🔹 Test the token with a lightweight request
    try:
        resp = requests.get(f"{url.rstrip('/')}/api/users/me", headers=headers, timeout=5)
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
        "request_credentials":"skip",
        "access_type":"offline",
    }
    auth_url = f"{base_url}{AUTH_PATH}?{urllib.parse.urlencode(params)}"
    return auth_url



# ─── Tools ────────────────────────────────────────────────────────────────
@mcp.tool(name="youtrack_get_authorization_url")
def youtrack_get_authorization_url(metadata: Dict[str, Any]) -> str:
    """Return the authorization URL for YouTrack login (requires metadata['url'])."""
    if not metadata or "url" not in metadata:
        raise Exception("❌ Missing url in metadata")
    return _generate_youtrack_auth_url(metadata["url"])


@mcp.tool(name="youtrack_list_open_issues")
def list_open_issues(metadata: dict) -> str:
    """List open issues assigned to the user."""
    creds = get_youtrack_creds(metadata)
    query = "State: {Unresolved}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to fetch issues: {r.text}"


@mcp.tool(name="youtrack_list_my_issues")
def youtrack_list_my_issues(metadata: dict) -> str:
    """List issues assigned to the authenticated user."""
    creds = get_youtrack_creds(metadata)
    query = "Assignee: {me} State: {Unresolved}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,state(name)"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to fetch my issues: {r.text}"


@mcp.tool(name="youtrack_list_my_inprogress")
def youtrack_list_my_inprogress(metadata: dict) -> str:
    """List tickets assigned to me that are in progress."""
    creds = get_youtrack_creds(metadata)
    query = "Assignee: {me} State: {In Progress}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,state(name)"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to fetch in-progress issues: {r.text}"


@mcp.tool(name="youtrack_list_my_reported")
def youtrack_list_my_reported(metadata: dict) -> str:
    """List issues reported by the authenticated user."""
    creds = get_youtrack_creds(metadata)
    query = "Reporter: {me}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,state(name)"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to fetch my reported issues: {r.text}"


@mcp.tool(name="youtrack_list_my_recent")
def youtrack_list_my_recent(metadata: dict) -> str:
    """List issues assigned to me updated in the last 7 days."""
    creds = get_youtrack_creds(metadata)
    query = "Assignee: {me} updated: {last 7 days}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,updated,state(name)"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to fetch my recent issues: {r.text}"


@mcp.tool(name="youtrack_list_my_closed")
def youtrack_list_my_closed(metadata: dict) -> str:
    """List closed/resolved issues assigned to me."""
    creds = get_youtrack_creds(metadata)
    query = "Assignee: {me} State: {Resolved}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,state(name),resolved"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to fetch my closed issues: {r.text}"


@mcp.tool(name="youtrack_add_comment")
def add_comment(metadata: dict, issue_id: str, comment_text: str) -> str:
    """Add a comment to a YouTrack issue."""
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}/comments?fields=id"
    payload = {"text": comment_text}
    r = requests.post(url, headers={**creds["headers"], "Content-Type": "application/json"}, json=payload)
    if r.status_code in (200, 201):
        return f"Comment added to {issue_id}"
    return f"Failed to add comment: {r.text}"

@mcp.tool(name="youtrack_search_issues")
def youtrack_search_issues(metadata: dict, keyword: str, field: str = "both") -> str:
    """Search for issues by summary, description, or both."""
    creds = get_youtrack_creds(metadata)
    query = keyword
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,description"
    r = requests.get(url, headers=creds["headers"])
    results = []
    if r.status_code == 200:
        for issue in r.json():
            if field == "summary" and keyword.lower() in (issue.get("summary") or "").lower():
                results.append({"id": issue["idReadable"], "summary": issue["summary"]})
            elif field == "description" and keyword.lower() in (issue.get("description") or "").lower():
                results.append({"id": issue["idReadable"], "summary": issue["summary"]})
            elif field == "both":
                combined = (issue.get("summary") or "") + " " + (issue.get("description") or "")
                if keyword.lower() in combined.lower():
                    results.append({"id": issue["idReadable"], "summary": issue["summary"]})
        return json.dumps(results)
    return f"Failed to search issues: {r.text}"


@mcp.tool(name="youtrack_fetch_issue")
def youtrack_fetch_issue(metadata: dict, issue_id: str) -> str:
    """Fetch full details of an issue by ID."""
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}?fields=summary,description"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        data = r.json()
        return f"Issue Summary: {data.get('summary')}\n\nDescription:\n{data.get('description')}"
    return f"Failed to fetch issue {issue_id}: {r.text}"


@mcp.tool(name="youtrack_get_comments")
def youtrack_get_comments(metadata: dict, issue_id: str) -> str:
    """Fetch all comments from a YouTrack issue."""
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}/comments?fields=text,author(login,name),created"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        comments = r.json()
        formatted = []
        for c in comments:
            author = c.get("author", {}).get("login") or c.get("author", {}).get("name", "Unknown")
            timestamp = c.get("created", "")
            formatted.append(f"By {author} at {timestamp}:\n{c.get('text')}\n")
        return "\n---\n".join(formatted) if formatted else "No comments found."
    return f"Failed to fetch comments: {r.text}"


@mcp.tool(name="youtrack_list_closed_issues")
def youtrack_list_closed_issues(metadata: dict) -> str:
    """List closed/resolved issues."""
    creds = get_youtrack_creds(metadata)
    query = "State: {Resolved}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary"
    r = requests.get(url, headers=creds["headers"])
    if r.status_code == 200:
        return json.dumps(r.json())
    return f"Failed to list closed issues: {r.text}"


@mcp.tool(name="youtrack_issue_counts")
def youtrack_issue_counts(metadata: dict) -> str:
    """Return count of open and closed issues."""
    creds = get_youtrack_creds(metadata)
    counts = {"open": 0, "closed": 0}

    open_resp = requests.get(
        f"{creds['url']}/api/issues?query=State:{{Unresolved}}&fields=idReadable",
        headers=creds["headers"]
    )
    closed_resp = requests.get(
        f"{creds['url']}/api/issues?query=State:{{Resolved}}&fields=idReadable",
        headers=creds["headers"]
    )

    if open_resp.status_code == 200:
        counts["open"] = len(open_resp.json())
    if closed_resp.status_code == 200:
        counts["closed"] = len(closed_resp.json())

    return json.dumps(counts)

async def authorize(request: Request):
    """Redirect user to YouTrack OAuth page (Hub)."""
    base_url = os.getenv("YOUTRACK_URL")
    if not base_url:
        return JSONResponse({"error": "Missing YOUTRACK_URL in environment"}, status_code=400)

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
    # base_url = os.getenv("YOUTRACK_URL")  # e.g., https://<org>.myjetbrains.com
    base_url = "https://scryanalytics.myjetbrains.com"

    if not code or not base_url:
        return JSONResponse({"error": "Missing code or YOUTRACK_URL"}, status_code=400)

    token_url = f"{base_url}{TOKEN_PATH}"

    # Form body (redirect_uri must match exactly what’s registered in Hub + used in /authorize)
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    print("🔍 /oauth2callback debug (about to exchange code)")
    print(f"  token_url:            {token_url}")
    print(f"  redirect_uri (body):  {form['redirect_uri']}")
    print(f"  code:                 <redacted>")
    print("🔐 Using confidential client flow...")

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
            {"error": "Confidential flow failed", "status": resp.status_code, "details": details},
            status_code=resp.status_code,
        )

    token_json = resp.json()
    access_token = token_json.get("access_token")
    refresh_token = token_json.get("refresh_token") 
    expires_in = token_json.get("expires_in", 0)

    if not access_token:
        return JSONResponse(
            {"error": "No access_token in response", "details": token_json}, status_code=400
        )

    print("  ✅ Confidential flow succeeded")
    return await _finalize_login(base_url, access_token, refresh_token, expires_in)


async def _finalize_login(base_url: str, access_token: str, refresh_token: str, expires_in: int):
    """Fetch user info and return auth success payload."""
    userinfo_url = f"{base_url}{USERINFO_PATH}?fields=id,login,email,fullName"
    user_resp = requests.get(userinfo_url, headers={
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json"
    })

    if user_resp.status_code != 200:
        return JSONResponse({"error": "Failed to fetch user info", "details": user_resp.text}, status_code=400)

    user_info = user_resp.json()
    email = user_info.get("email") or user_info.get("login") or "unknown"

    # # You may want to persist this securely (DB, Redis, etc.)
    # user_tokens[email] = {
    #     "access_token": access_token,
    #     "refresh_token": refresh_token,
    #     "expires_in": expires_in,
    #     "url": base_url,
    # }

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_in": expires_in,
        "url": base_url,
    })



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
    lifespan=mcp_app.lifespan
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
