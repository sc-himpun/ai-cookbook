# -*- coding: utf-8 -*-
import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")       # Consumer Key
CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")  # Consumer Secret
SCOPES = os.getenv("SALESFORCE_SCOPES", "api refresh_token offline_access").split()

PORT = int(os.getenv("SALESFORCE_MCP_PORT", "8091"))
REDIRECT_URI = os.getenv("SALESFORCE_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")

AUTH_BASE = "https://login.salesforce.com/services/oauth2"  # For sandbox: test.salesforce.com

# ─── Token Store ─────────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("salesforce-mcp")


# ─── Helper Functions ────────────────────────────────────────────────────────

def refresh_salesforce_token(refresh_token: str) -> Optional[Dict]:
    """
    Refresh the Salesforce OAuth 2.0 access token using the refresh token.
    """
    payload = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    response = requests.post(f"{AUTH_BASE}/token", data=payload)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"[refresh_salesforce_token] Failed: {response.text}")
        return None


def get_salesforce_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Extract Salesforce credentials from metadata and refresh if access token expired.
    """
    if not metadata:
        return None

    creds = metadata.get("salesforce", metadata)

    email = creds.get("email")
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")
    instance_url = creds.get("instance_url")

    if not email or not access_token or not instance_url:
        return None

    # Test if token is valid by calling identity endpoint
    test_resp = requests.get(
        f"{instance_url}/services/oauth2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if test_resp.status_code == 200:
        return {
            "email": email,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "instance_url": instance_url
        }

    # If expired, try refresh
    if refresh_token:
        print("🔁 Access token expired, refreshing...")
        new_tokens = refresh_salesforce_token(refresh_token)
        if new_tokens and "access_token" in new_tokens:
            access_token = new_tokens["access_token"]
            # Salesforce usually doesn’t issue a new refresh_token unless explicitly rotated
            return {
                "email": email,
                "access_token": access_token,
                "refresh_token": refresh_token,
                "instance_url": new_tokens.get("instance_url", instance_url)
            }

    return None


# ─── MCP Tool: Get Authorization URL ─────────────────────────────────────────
@mcp.tool(name="salesforce_get_authorization_url")
def get_authorization_url() -> str:
    """
    Get the Salesforce OAuth 2.0 authorization URL for user login and consent.
    """
    scope = " ".join(SCOPES)
    url = (
        f"{AUTH_BASE}/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope}"
    )
    return url


# ─── Auth Flow ───────────────────────────────────────────────────────────────
async def authorize(request: Request):
    """Redirect user to Salesforce OAuth screen."""
    return RedirectResponse(get_authorization_url())


async def oauth2callback(request: Request):
    """Handle Salesforce OAuth2 callback and exchange code for tokens."""
    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error": "Missing authorization code"}, status_code=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }

    token_resp = requests.post(f"{AUTH_BASE}/token", data=data).json()

    access_token = token_resp.get("access_token")
    instance_url = token_resp.get("instance_url")
    refresh_token = token_resp.get("refresh_token")

    if not access_token or not instance_url:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    # Get user info
    userinfo = requests.get(
        f"{instance_url}/services/oauth2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()
    email = userinfo.get("email")

    user_tokens[email] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "instance_url": instance_url
    }

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "instance_url": instance_url
    })


async def status(request: Request):
    """Check if user is authenticated."""
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
