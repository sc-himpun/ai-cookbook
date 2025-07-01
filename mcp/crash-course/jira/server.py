import os
import requests
from dotenv import load_dotenv
from typing import Dict
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("JIRA_CLIENT_ID")
CLIENT_SECRET = os.getenv("JIRA_CLIENT_SECRET")
SCOPES = os.getenv("JIRA_SCOPES", "read:jira-user read:jira-work read:me write:jira-work").split()
PORT = 8002
REDIRECT_URI = f"http://localhost:{PORT}/oauth2callback"

# ─── Token Store ─────────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("jira-mcp")


@mcp.tool(name="jira_search_issues")
def search_issues(email: str, jql: str):
    """Search issues using a custom JQL query."""
    if email not in user_tokens:
        return "❌ Email not authorized."

    access_token = user_tokens[email]["access_token"]
    cloud_id = user_tokens[email]["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/search"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"jql": jql, "maxResults": 5}

    resp = requests.get(url, headers=headers, params=params).json()
    issues = resp.get("issues", [])
    if not issues:
        return "No matching issues found."

    return "\n".join(f"{i['key']}: {i['fields']['summary']}" for i in issues)


@mcp.tool(name="jira_get_issue_details")
def get_issue_details(email: str, issue_key: str):
    """Fetch issue summary and description."""
    if email not in user_tokens:
        return "❌ Email not authorized."

    access_token = user_tokens[email]["access_token"]
    cloud_id = user_tokens[email]["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

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
def get_issue_comments(email: str, issue_key: str):
    """Fetch the latest comments on an issue."""
    if email not in user_tokens:
        return "❌ Email not authorized."

    access_token = user_tokens[email]["access_token"]
    cloud_id = user_tokens[email]["cloud_id"]

    url = f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{issue_key}/comment"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers).json()
    comments = resp.get("comments", [])
    if not comments:
        return "No comments found."

    return "\n---\n".join(
        f"{c['author']['displayName']}: {c['body']['content'][0]['content'][0]['text']}"
        for c in comments[:5]
    )


@mcp.tool(name="jira_add_comment")
def add_comment(email: str, issue_key: str, comment: str):
    """Add a comment to a Jira issue."""
    if email not in user_tokens:
        return "❌ Email not authorized."

    access_token = user_tokens[email]["access_token"]
    cloud_id = user_tokens[email]["cloud_id"]

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
def transition_issue(email: str, issue_key: str, target_status: str):
    """Transition a Jira issue to a new status (e.g., Done, In Progress)."""
    if email not in user_tokens:
        return "❌ Email not authorized."

    access_token = user_tokens[email]["access_token"]
    cloud_id = user_tokens[email]["cloud_id"]

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
    transition = next((t for t in transitions if t["name"].lower() == target_status.lower()), None)
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



@mcp.tool(name="jira_get_authorization_url")
def get_authorization_url() -> str:
    """Return the URL the user must visit to authorize Jira access."""
    return f"http://localhost:{PORT}/authorize"


@mcp.tool(name="jira_list_authorized_accounts")
def list_accounts() -> str:
    if not user_tokens:
        return "No accounts authorized yet."
    return "\n".join(user_tokens.keys())

# ─── Auth Flow ───────────────────────────────────────────────────────────────

async def authorize(request: Request):
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
    code = request.query_params.get("code")
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    token_resp = requests.post("https://auth.atlassian.com/oauth/token", json=data).json()
    access_token = token_resp.get("access_token")
    # print("Token Response:", token_resp)

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


    user_tokens[email] = {
        "access_token": access_token,
        "cloud_id": cloud_id
    }

    return JSONResponse({"message": f"Authenticated as {email}"})


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
