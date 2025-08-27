import os
import requests
from dotenv import load_dotenv
from typing import Dict, Optional, Any
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from simple_salesforce.api import Salesforce
from simple_salesforce.exceptions import SalesforceError
from simple_salesforce.exceptions import SalesforceAuthenticationFailed
import json
from datetime import datetime, timezone

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
def get_salesforce_creds(metadata: Optional[Dict[str, Any]]) -> Salesforce:
    """Extract Salesforce creds from metadata, refresh if expired, and return a Salesforce client."""
    if not metadata:
        raise Exception("❌ Missing metadata for Salesforce")

    instance_url = metadata.get("instance_url")
    access_token = metadata.get("access_token")
    refresh_token = metadata.get("refresh_token")
    client_id = metadata.get("client_id")
    client_secret = metadata.get("client_secret")

    if not instance_url:
        raise Exception("❌ Missing instance_url in Salesforce metadata")
    if not access_token:
        raise Exception("❌ Missing access_token in Salesforce metadata")

    # Try creating client
    try:
        sf = Salesforce(instance_url=instance_url, session_id=access_token)
        # lightweight check
        sf.query("SELECT Id FROM User LIMIT 1")
        return sf
    except SalesforceAuthenticationFailed:
        if not refresh_token or not client_id or not client_secret:
            raise Exception("❌ Access token expired and no refresh credentials provided")

        # Refresh access token
        token_url = "https://login.salesforce.com/services/oauth2/token"
        data = {
            "grant_type": "refresh_token",
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
        }
        resp = requests.post(token_url, data=data)
        if resp.status_code != 200:
            raise Exception(f"❌ Failed to refresh token: {resp.text}")

        tokens = resp.json()
        metadata["access_token"] = tokens["access_token"]  # update metadata

        sf = Salesforce(instance_url=instance_url, session_id=metadata["access_token"])
        return sf

# ─── Tools ──────────────────────────────────────────────────────────────

@mcp.tool(name="salesforce_run_soql_query")
def run_soql_query(metadata: dict, query: str) -> str:
    """Executes a SOQL query against Salesforce."""
    sf = get_salesforce_creds(metadata)
    results = sf.query_all(query)
    return json.dumps(results, indent=2)



@mcp.tool(name="salesforce_run_sosl_search")
def run_sosl_search(metadata: dict, search: str) -> str:
    """Executes a SOSL search against Salesforce."""
    sf = get_salesforce_creds(metadata)
    results = sf.search(search)
    return json.dumps(results, indent=2)

@mcp.tool(name="salesforce_identity")
def salesforce_identity(metadata: dict) -> str:
    """Check Salesforce auth works by fetching the current user’s info."""
    access_token = metadata.get("access_token")
    if not access_token:
        raise Exception("❌ Missing access_token in Salesforce metadata")

    # Always use login.salesforce.com for userinfo
    resp = requests.get(
        "https://login.salesforce.com/services/oauth2/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if resp.status_code != 200:
        raise Exception(f"❌ Identity fetch failed: {resp.text}")

    return json.dumps(resp.json(), indent=2)




from typing import Any, cast

def salesforce_list_objects(metadata: dict) -> str:
    """Lists available Salesforce objects in the org."""
    sf = get_salesforce_creds(metadata)
    result = cast(dict[str, Any], sf.describe())

    return json.dumps([obj["name"] for obj in result["sobjects"][:20]], indent=2)





@mcp.tool(name="salesforce_get_object_fields")
def get_object_fields(metadata: dict, object_name: str) -> str:
    """Retrieves field names, labels, and types for a Salesforce object."""
    sf = get_salesforce_creds(metadata)
    sf_object = getattr(sf, object_name)
    fields = sf_object.describe()["fields"]
    filtered_fields = [
        {
            "label": f["label"],
            "name": f["name"],
            "updateable": f["updateable"],
            "type": f["type"],
            "length": f["length"],
            "picklistValues": f["picklistValues"],
        }
        for f in fields
    ]
    return json.dumps(filtered_fields, indent=2)


@mcp.tool(name="salesforce_whoami")
def salesforce_whoami(metadata: dict) -> str:
    """Runs a simple SOQL query to fetch the current user’s Id and Username."""
    from simple_salesforce.api import Salesforce

    instance_url = metadata.get("instance_url")
    access_token = metadata.get("access_token")

    if not (instance_url and access_token):
        raise Exception("❌ instance_url and access_token required")

    sf = Salesforce(instance_url=instance_url, session_id=access_token)
    res = sf.query("SELECT Id, Username, Email FROM User LIMIT 1")
    return json.dumps(res, indent=2)


@mcp.tool(name="salesforce_get_record")
def get_record(metadata: dict, object_name: str, record_id: str) -> str:
    """Retrieves a specific record by ID."""
    sf = get_salesforce_creds(metadata)
    sf_object = getattr(sf, object_name)
    result = sf_object.get(record_id)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_create_record")
def create_record(metadata: dict, object_name: str, data: dict) -> str:
    """Creates a new Salesforce record."""
    sf = get_salesforce_creds(metadata)
    sf_object = getattr(sf, object_name)
    result = sf_object.create(data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_update_record")
def update_record(metadata: dict, object_name: str, record_id: str, data: dict) -> str:
    """
    Updates an existing Salesforce record.

    Args:
        metadata: Auth info (instance_url, access_token, etc.).
        object_name: Name of the Salesforce object (e.g., "Task", "Case", "Event", "Contact").
        record_id: Salesforce record ID (e.g., "00TXXXXXXXXXXXX").
        data: Fields to update, passed as a JSON dict.

    Example:
        salesforce_update_record(
            metadata={...},
            object_name="Task",
            record_id="00T5g00001AbCdE",
            data={"Status": "Completed", "Priority": "High"}
        )

    Notes:
        - Salesforce returns {} on successful update.
        - All fields in `data` must match API names (e.g., "Subject", "Status", "Priority").
    """
    sf = get_salesforce_creds(metadata)
    sf_object = getattr(sf, object_name)
    result = sf_object.update(record_id, data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_delete_record")
def delete_record(metadata: dict, object_name: str, record_id: str) -> str:
    """Deletes a Salesforce record."""
    sf = get_salesforce_creds(metadata)
    sf_object = getattr(sf, object_name)
    result = sf_object.delete(record_id)
    return str(result)


@mcp.tool(name="salesforce_tooling_execute")
def tooling_execute(metadata: dict, action: str, method: str = "GET", data: Optional[dict] = None) -> str:
    """Executes a Tooling API request."""
    sf = get_salesforce_creds(metadata)
    result = sf.toolingexecute(action, method=method, data=data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_apex_execute")
def apex_execute(metadata: dict, action: str, method: str = "GET", data: Optional[dict] = None) -> str:
    """Executes an Apex REST request."""
    sf = get_salesforce_creds(metadata)
    result = sf.apexecute(action, method=method, data=data)
    return json.dumps(result, indent=2)


@mcp.tool(name="salesforce_restful")
def salesforce_restful(metadata: dict, path: str, method: str = "GET", data: dict | None = None) -> str:
    """Generic Salesforce REST API executor."""
    sf = get_salesforce_creds(metadata)
    # Ensure path starts with /
    if not path.startswith("/"):
        path = "/" + path
    result = sf.restful(path, method=method, data=data)
    return json.dumps(result, indent=2)

# ─── Case / Ticketing Tools ─────────────────────────────────────────────────

@mcp.tool(name="salesforce_create_case")
def salesforce_create_case(
    metadata: Dict[str, Any],
    subject: str,
    description: str,
    priority: str = "Medium",
    origin: str = "Web",
    contact_id: Optional[str] = None,
    account_id: Optional[str] = None,
) -> str:
    """
    Create a new Case (ticket) in Salesforce.
    Minimal required inputs: subject, description.
    Optional: priority, origin, contact_id, account_id.
    """
    sf = get_salesforce_creds(metadata)
    payload: Dict[str, Any] = {
        "Subject": subject,
        "Description": description,
        "Priority": priority,
        "Status": "New",
        "Origin": origin,
    }
    if contact_id:
        payload["ContactId"] = contact_id
    if account_id:
        payload["AccountId"] = account_id

    try:
        case_obj: Any = sf.Case
        res = case_obj.create(payload)
        return json.dumps(res, indent=2)
    except SalesforceError as e:
        raise Exception(f"❌ Failed to create Case: {e}")


@mcp.tool(name="salesforce_get_case")
def salesforce_get_case(metadata: Dict[str, Any], case_id: str) -> str:
    """Retrieve details of a Case by Id."""
    sf = get_salesforce_creds(metadata)
    try:
        case_obj: Any = sf.Case
        case = case_obj.get(case_id)
        return json.dumps(case, indent=2)
    except SalesforceError as e:
        raise Exception(f"❌ Failed to fetch Case {case_id}: {e}")


@mcp.tool(name="salesforce_update_case_status")
def salesforce_update_case_status(metadata: Dict[str, Any], case_id: str, status: str) -> str:
    """Update a Case's status (e.g., New → In Progress → Closed)."""
    sf = get_salesforce_creds(metadata)
    try:
        case_obj: Any = sf.Case
        case_obj.update(case_id, {"Status": status})
        return f"✅ Case {case_id} updated to status '{status}'"
    except SalesforceError as e:
        raise Exception(f"❌ Failed to update Case status: {e}")


@mcp.tool(name="salesforce_add_case_comment")
def salesforce_add_case_comment(
    metadata: Dict[str, Any],
    case_id: str,
    comment_body: str,
    is_public: bool = True,
) -> str:
    """
    Add a CaseComment to a Case.
    - case_id: parent Case Id
    - comment_body: text of comment
    - is_public: whether the comment is public (visible to portal users)
    """
    sf = get_salesforce_creds(metadata)
    payload = {"ParentId": case_id, "CommentBody": comment_body, "IsPublished": is_public}
    try:
        case_comment_obj: Any = sf.CaseComment
        res = case_comment_obj.create(payload)
        return json.dumps(res, indent=2)
    except SalesforceError as e:
        raise Exception(f"❌ Failed to add CaseComment: {e}")


@mcp.tool(name="salesforce_search_cases")
def salesforce_search_cases(metadata: Dict[str, Any], query: str) -> str:
    """
    Search Cases by keyword using SOSL (searches subject/description).
    Returns matching Case Id, Subject, Status, Priority.
    """
    sf = get_salesforce_creds(metadata)
    # SOSL returns different structure; ensure it returns Case records
    sosl = f"FIND '{{{query}}}' IN ALL FIELDS RETURNING Case(Id, Subject, Status, Priority)"
    try:
        res = sf.search(sosl)
        return json.dumps(res, indent=2)
    except SalesforceError as e:
        raise Exception(f"❌ SOSL search failed: {e}")


@mcp.tool(name="salesforce_list_recent_cases")
def salesforce_list_recent_cases(metadata: Dict[str, Any], limit: int = 10) -> str:
    """
    List most recent cases (default 10). Returns Id, CaseNumber, Subject, Status, Priority, CreatedDate.
    """
    sf = get_salesforce_creds(metadata)
    soql = (
        "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate "
        f"FROM Case ORDER BY CreatedDate DESC LIMIT {int(limit)}"
    )
    try:
        res = sf.query(soql)
        # return only records for readability
        records = res.get("records", [])
        return json.dumps(records, indent=2)
    except SalesforceError as e:
        raise Exception(f"❌ Failed to list recent Cases: {e}")


@mcp.tool(name="salesforce_list_cases_by_status")
def salesforce_list_cases_by_status(metadata: Dict[str, Any], status: str = "New", limit: int = 20) -> str:
    """
    List cases filtered by status (default 'New').
    """
    sf = get_salesforce_creds(metadata)
    soql = (
        "SELECT Id, CaseNumber, Subject, Status, Priority, CreatedDate "
        f"FROM Case WHERE Status = '{status}' ORDER BY CreatedDate DESC LIMIT {int(limit)}"
    )
    try:
        res = sf.query(soql)
        return json.dumps(res.get("records", []), indent=2)
    except SalesforceError as e:
        raise Exception(f"❌ Failed to list Cases by status '{status}': {e}")
    


@mcp.tool(name="salesforce_create_task")
def create_task(metadata: dict, subject: str, due_date: str) -> str:
    """Create a new Salesforce Task.
    due_date format: YYYY-MM-DD."""
    sf = get_salesforce_creds(metadata)
    task_api = getattr(sf, "Task")
    result = task_api.create({
        "Subject": subject,
        "ActivityDate": due_date
    })
    return json.dumps(result, indent=2)

@mcp.tool(name="salesforce_get_current_time")
def get_current_time(metadata: dict, utc: bool = True) -> str:
    """Get the current time.
    By default returns UTC in ISO8601 format: YYYY-MM-DDTHH:MM:SSZ.
    Pass utc=False to get local time instead."""
    now = datetime.now(timezone.utc) if utc else datetime.now()
    # Format in ISO8601 (Salesforce expects UTC 'Z')
    formatted = now.strftime("%Y-%m-%dT%H:%M:%SZ") if utc else now.isoformat()
    return formatted


@mcp.tool(name="salesforce_list_tasks")
def list_tasks(metadata: dict, limit: int = 10) -> str:
    """List Salesforce Tasks (latest first)."""
    sf = get_salesforce_creds(metadata)
    query = f"SELECT Id, Subject, Status, Priority, ActivityDate FROM Task ORDER BY CreatedDate DESC LIMIT {limit}"
    result = sf.query_all(query)
    return json.dumps(result["records"], indent=2)


# ─── EVENT TOOLS ───────────────────────────────────────────


@mcp.tool(name="salesforce_create_event")
def create_event(metadata: dict, subject: str, start_datetime: str, end_datetime: str) -> str:
    """Create a new Salesforce Event.
    Datetime format: YYYY-MM-DDTHH:MM:SSZ (UTC ISO8601)."""
    sf = get_salesforce_creds(metadata)
    event_api = getattr(sf, "Event")
    result = event_api.create({
        "Subject": subject,
        "StartDateTime": start_datetime,
        "EndDateTime": end_datetime
    })
    return json.dumps(result, indent=2)



@mcp.tool(name="salesforce_list_events")
def list_events(metadata: dict, limit: int = 10) -> str:
    """List Salesforce Events (latest first)."""
    sf = get_salesforce_creds(metadata)
    query = f"SELECT Id, Subject, StartDateTime, EndDateTime, Location FROM Event ORDER BY StartDateTime DESC LIMIT {limit}"
    result = sf.query_all(query)
    return json.dumps(result["records"], indent=2)



    

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
    # user_tokens[email] = {
    #     "access_token": access_token,
    #     "refresh_token": refresh_token,
    #     "instance_url": instance_url
    # }

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
