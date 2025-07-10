import os
import requests
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
TENANT_ID = os.getenv("AZURE_TENANT_ID")
REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI")
PORT = 8007
SCOPES = "User.Read Files.Read Sites.Read.All"

AUTH_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

print(f"Using Azure Client ID: {CLIENT_ID}")
user_tokens = {}
mcp = FastMCP("onedrive-mcp")

@mcp.tool(name="onedrive_list_files")
def list_files(email: str) -> str:
    token = user_tokens.get(email)
    if not token:
        return "❌ Not authorized."

    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get("https://graph.microsoft.com/v1.0/me/drive/root/children", headers=headers)
    if not resp.ok:
        return f"Error: {resp.status_code} - {resp.text}"
    return resp.text

@mcp.tool(name="onedrive_list_users")
def list_users() -> str:
    return "\n".join(user_tokens.keys()) or "No users authorized yet."

def generate_auth_url() -> str:
    return (
        f"{AUTH_URL}?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_mode=query"
        f"&scope={SCOPES}"
    )

@mcp.tool(name="onedrive_get_auth_url")
def get_auth_url() -> str:
    return generate_auth_url()


# ─── Auth Endpoints ────────────────────────────────────────────────────────────

async def authorize(request: Request):
    return RedirectResponse(generate_auth_url())

async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "client_id": CLIENT_ID,
        "scope": SCOPES,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": CLIENT_SECRET,
    }

    token_resp = requests.post(TOKEN_URL, data=data).json()
    access_token = token_resp.get("access_token")
    if not access_token:
        return JSONResponse({"error": "OAuth failed", "details": token_resp}, status_code=400)

    # Get user email
    userinfo = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()

    email = userinfo.get("userPrincipalName") or userinfo.get("mail")
    if not email:
        return JSONResponse({"error": "Failed to fetch user info"}, status_code=400)

    user_tokens[email] = access_token
    return JSONResponse({"message": f"Authenticated as {email}"})


# ─── Starlette Setup ─────────────────────────────────────────────────────────────

mcp_app = mcp.http_app(transport="sse")

routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
]

app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=PORT)
