import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

# ─── Config ──────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("CONFLUENCE_CLIENT_ID")
CLIENT_SECRET = os.getenv("CONFLUENCE_CLIENT_SECRET")
SCOPES = os.getenv(
    "CONFLUENCE_SCOPES",
    "offline_access write:confluence-content read:me read:confluence-space.summary read:confluence-props read:confluence-content.all read:confluence-content.summary search:confluence read:confluence-user"
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
@mcp.tool(name="confluence_search_pages")
def search_pages(metadata: Dict, query: str):
    """Search Confluence pages by title/content."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/rest/api/space"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    params = {"cql": f'title ~ "{query}"', "limit": 5}

    # resp = requests.get(url, headers=headers, params=params).json()
    # 
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code != 200:
        return f"❌ Failed: {resp.status_code} - {resp.text}"
    data = resp.json()
    results = data.get("results", [])
    
    if not results:
        return "No matching pages found."

    return "\n".join(f"{r['title']} (id: {r['id']})" for r in results)


@mcp.tool(name="confluence_get_page_content")
def get_page_content(metadata: Dict, page_id: str):
    """Fetch the body of a Confluence page."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/rest/api/content/{page_id}?expand=body.storage"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}

    resp = requests.get(url, headers=headers).json()
    title = resp.get("title", "N/A")
    body = resp.get("body", {}).get("storage", {}).get("value", "")
    return f"Title: {title}\n\nContent:\n{body}"


@mcp.tool(name="confluence_create_page")
def create_page(metadata: Dict, space_key: str, title: str, content: str):
    """Create a new Confluence page."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/rest/api/content/"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = {
        "type": "page",
        "title": title,
        "space": {"key": space_key},
        "body": {"storage": {"value": content, "representation": "storage"}}
    }

    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200:
        return f"✅ Page '{title}' created in space {space_key}"
    else:
        return f"❌ Failed: {resp.status_code} - {resp.text}"


@mcp.tool(name="confluence_add_comment")
def add_comment(metadata: Dict, page_id: str, comment: str):
    """Add a comment to a Confluence page."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/rest/api/content/{page_id}/child/comment"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    data = {
        "type": "comment",
        "container": {"id": page_id, "type": "page"},
        "body": {"storage": {"value": comment, "representation": "storage"}}
    }

    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code == 200:
        return f"✅ Comment added to page {page_id}"
    else:
        return f"❌ Failed: {resp.status_code} - {resp.text}"


@mcp.tool(name="confluence_list_spaces")
def list_spaces(metadata: Dict):
    """List available Confluence spaces."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token = creds["access_token"]
    base_url = creds.get("base_url")

    url = f"{base_url}/wiki/rest/api/space"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        return f"❌ Failed: {resp.status_code} - {resp.text}"

    spaces = resp.json().get("results", [])
    if not spaces:
        return "No spaces found."
    return "\n".join(f"{s['name']} (key: {s['key']})" for s in spaces)




@mcp.tool(name="confluence_update_page")
def update_page(metadata: Dict, page_id: str, new_content: str, new_title: Optional[str] = None):
    """Update an existing Confluence page."""
    creds = get_confluence_creds(metadata)
    if not creds:
        return "❌ Confluence credentials not found."
    access_token = creds["access_token"]
    cloud_id = creds["cloud_id"]

    # Get current version
    url = f"https://api.atlassian.com/ex/confluence/{cloud_id}/rest/api/content/{page_id}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    page = requests.get(url, headers=headers).json()
    version = page.get("version", {}).get("number", 1) + 1
    title = new_title or page.get("title", "Untitled")

    data = {
        "id": page_id,
        "type": "page",
        "title": title,
        "version": {"number": version},
        "body": {"storage": {"value": new_content, "representation": "storage"}}
    }
    headers["Content-Type"] = "application/json"
    resp = requests.put(url, headers=headers, json=data)
    if resp.status_code == 200:
        return f"✅ Page {page_id} updated successfully"
    else:
        return f"❌ Failed: {resp.status_code} - {resp.text}"



@mcp.tool(name="confluence_get_authorization_url")
def get_authorization_url() -> str:
    """Return URL for user to authorize Confluence access."""
    return f"http://localhost:{PORT}/authorize"


# ─── Auth Flow ──────────────────────────────────────────────────────────
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
    refresh_token = token_resp.get("refresh_token")
    SCOPES_GRANTED = token_resp.get("scope", "").split()
    # print("Granted scopes:", SCOPES_GRANTED)
    if not access_token:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    # Fetch user email
    userinfo = requests.get("https://api.atlassian.com/me", headers={"Authorization": f"Bearer {access_token}"}).json()
    email = userinfo.get("email")

    # Get cloud ID for Confluence site
    resources = requests.get(
        "https://api.atlassian.com/oauth/token/accessible-resources",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    confluence_resources = [
        r for r in resources if "read:confluence-content.summary" in r.get("scopes", [])
    ]
    print(confluence_resources)
    if not confluence_resources:
        return JSONResponse({"error": "No Confluence resource found"}, status_code=400)
    
    cloud_id = confluence_resources[0]["id"]
    base_url = confluence_resources[0]["url"] 

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "cloud_id": cloud_id,
        "url": base_url,
        "scopes": SCOPES_GRANTED
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
