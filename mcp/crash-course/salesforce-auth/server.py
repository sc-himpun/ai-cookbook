import os
import requests
from dotenv import load_dotenv
from typing import Dict
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from simple_salesforce import Salesforce
from simple_salesforce.exceptions import SalesforceError
import json

# ─── Config ──────────────────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("SALESFORCE_CLIENT_ID")   # Consumer Key
CLIENT_SECRET = os.getenv("SALESFORCE_CLIENT_SECRET")  # Consumer Secret
SCOPES = os.getenv("SALESFORCE_SCOPES", "api refresh_token offline_access").split()

PORT = int(os.getenv("SALESFORCE_MCP_PORT", "8091"))
REDIRECT_URI = os.getenv("SALESFORCE_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")

AUTH_BASE = "https://login.salesforce.com/services/oauth2"  # Use test.salesforce.com for sandboxes
print(f"Using Salesforce CLIENT_ID: {CLIENT_ID}")
# ─── Token Store ─────────────────────────────────────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP ─────────────────────────────────────────────────────────────────────
mcp = FastMCP("salesforce-mcp")



# Utility: get an authenticated Salesforce client from stored tokens
def get_salesforce_client(email: str) -> Salesforce:
    if email not in user_tokens:
        raise ValueError("User not authenticated with Salesforce")
    creds = user_tokens[email]
    return Salesforce(
        instance_url=creds["instance_url"],
        session_id=creds["access_token"]
    )

# ─── Tools ──────────────────────────────────────────────────────────────

@mcp.tool(name="salesforce_run_soql_query")
def run_soql_query(email: str, query: str) -> str:
    """Executes a SOQL query against Salesforce."""
    sf = get_salesforce_client(email)
    results = sf.query_all(query)
    return json.dumps(results, indent=2)


@mcp.tool(name="salesforce_run_sosl_search")
def run_sosl_search(email: str, search: str) -> str:
    """Executes a SOSL search against Salesforce."""
    sf = get_salesforce_client(email)
    results = sf.search(search)
    return json.dumps(results, indent=2)


@mcp.tool(name="salesforce_get_object_fields")
def get_object_fields(email: str, object_name: str) -> str:
    """Retrieves field names, labels, and types for a Salesforce object."""
    sf = get_salesforce_client(email)
    sf_object = getattr(sf, object_name)
    fields = sf_object.describe()['fields']
    filtered_fields = [
        {
            'label': f['label'],
            'name': f['name'],
            'updateable': f['updateable'],
            'type': f['type'],
            'length': f['length'],
            'picklistValues': f['picklistValues']
        }
        for f in fields
    ]
    return json.dumps(filtered_fields, indent=2)


@mcp.tool(name="salesforce_get_record")
def get_record(email: str, object_name: str, record_id: str) -> str:
    """Retrieves a specific record by ID."""
    sf = get_salesforce_client(email)
    sf_object = getattr(sf, object_name)
    result = sf_object.get(record_id)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_create_record")
def create_record(email: str, object_name: str, data: dict) -> str:
    """Creates a new Salesforce record."""
    sf = get_salesforce_client(email)
    sf_object = getattr(sf, object_name)
    result = sf_object.create(data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_update_record")
def update_record(email: str, object_name: str, record_id: str, data: dict) -> str:
    """Updates an existing Salesforce record."""
    sf = get_salesforce_client(email)
    sf_object = getattr(sf, object_name)
    result = sf_object.update(record_id, data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_delete_record")
def delete_record(email: str, object_name: str, record_id: str) -> str:
    """Deletes a Salesforce record."""
    sf = get_salesforce_client(email)
    sf_object = getattr(sf, object_name)
    result = sf_object.delete(record_id)
    return str(result)


@mcp.tool(name="salesforce_tooling_execute")
def tooling_execute(email: str, action: str, method: str = "GET", data: dict = None) -> str:
    """Executes a Tooling API request."""
    sf = get_salesforce_client(email)
    result = sf.toolingexecute(action, method=method, data=data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_apex_execute")
def apex_execute(email: str, action: str, method: str = "GET", data: dict = None) -> str:
    """Executes an Apex REST request."""
    sf = get_salesforce_client(email)
    result = sf.apexecute(action, method=method, data=data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_restful")
def restful(email: str, path: str, method: str = "GET", params: dict = {}, data: dict = {}) -> str:
    """Makes a direct REST API call to Salesforce."""
    sf = get_salesforce_client(email)
    result = sf.restful(path, method=method, params=params, json=data)
    return json.dumps(result, indent=2)


def _get_authorization_url() -> str:
    scope = " ".join(SCOPES)
    url = (
        f"{AUTH_BASE}/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={scope}"
    )
    return url

@mcp.tool(name="salesforce_get_authorization_url")
def get_authorization_url() -> str:
    """
    Get the Salesforce OAuth 2.0 authorization URL for user login and consent.
    """
    
    return _get_authorization_url()


# ─── Auth Flow ───────────────────────────────────────────────────────────────

async def authorize(request: Request):
    """Redirect user to Salesforce OAuth screen."""
    return RedirectResponse(_get_authorization_url())


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

    # Fetch user info (identity URL is in id field)
    id_url = token_resp.get("id")
    userinfo = requests.get(id_url, headers={"Authorization": f"Bearer {access_token}"}).json()
    email = userinfo.get("email")

    # Save in memory
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
