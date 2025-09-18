# -*- coding: utf-8 -*-
import os
import json
import uuid
from typing import Dict, Optional
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
import boto3

load_dotenv()

# ─── Config ─────────────────────────────────────────────────────────────
USE_SIMULATED_OAUTH = os.getenv("USE_SIMULATED_OAUTH", "true").lower() == "true"
USE_MINIO = os.getenv("USE_MINIO", "true").lower() == "true"

S3_BUCKET = os.getenv("S3_BUCKET", "bucket")
MINIO_URL = os.getenv("MINIO_URL", "http://localhost:9000")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

mcp = FastMCP("S3ToolkitOAuth", host="0.0.0.0", port=8060)

# ─── Session Store ──────────────────────────────────────────────────────
user_sessions: Dict[str, Dict[str, Optional[str]]] = {}  # email -> aws creds

# ─── S3 Client ──────────────────────────────────────────────────────────
def get_s3_client(access_key: str, secret_key: str, session_token: Optional[str] = None):
    """Creates an S3 client using provided credentials."""
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        endpoint_url=MINIO_URL if USE_MINIO else None,
        region_name=None if USE_MINIO else AWS_REGION,
    )

# ─── Simulated OAuth ────────────────────────────────────────────────────
def simulate_login(email: str) -> Dict[str, Optional[str]]:
    """Stores simulated credentials for a user and returns them."""
    creds = {
        "access_key": os.getenv("SIM_AWS_ACCESS_KEY", "root"),
        "secret_key": os.getenv("SIM_AWS_SECRET_KEY", "password"),
        "session_token": None,
    }
    user_sessions[email] = creds
    return creds

# ─── Real OAuth Placeholder ─────────────────────────────────────────────
def login_with_real_oauth(id_token: str) -> None:
    """Placeholder for real OAuth flow logic."""
    raise NotImplementedError("Real OAuth login not implemented")

# ─── MCP Tools ──────────────────────────────────────────────────────────

@mcp.tool(name="s3_list_files")
def list_files(email: str) -> str:
    """Lists file keys in the specified S3 bucket for the given user."""
    creds = user_sessions.get(email)
    if not creds:
        return "❌ Not authorized."
    s3 = get_s3_client(**creds)
    objs = s3.list_objects_v2(Bucket=S3_BUCKET).get("Contents", [])
    return json.dumps([obj["Key"] for obj in objs])

@mcp.tool(name="s3_fetch_file")
def fetch_file(email: str, key: str, user_query: str = "") -> str:
    """Fetches and returns the content of a file from S3."""
    creds = user_sessions.get(email)
    if not creds:
        return "❌ Not authorized."
    s3 = get_s3_client(**creds)
    body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()
    return body.decode("utf-8", errors="ignore")

@mcp.tool(name="s3_search_files")
def search_files(email: str, keyword: str, search_type: str = "both") -> str:
    """Searches for a keyword in filenames and/or file contents."""
    creds = user_sessions.get(email)
    if not creds:
        return "❌ Not authorized."
    s3 = get_s3_client(**creds)

    results = []
    objects = s3.list_objects_v2(Bucket=S3_BUCKET).get("Contents", [])
    for obj in objects:
        key = obj["Key"]
        match = False

        if search_type in ["filename", "both"] and keyword.lower() in key.lower():
            match = True

        if not match and search_type in ["content", "both"]:
            try:
                body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
                if keyword.lower() in body.lower():
                    match = True
            except Exception:
                continue  # skip unreadable files

        if match:
            results.append(key)
    return json.dumps(results)

@mcp.tool(name="s3_select_query")
def s3_select_query(email: str, key: str, query: str = "SELECT * FROM S3Object LIMIT 5") -> str:
    """Executes an S3 Select SQL query on CSV, JSON, or Parquet file."""
    creds = user_sessions.get(email)
    if not creds:
        return "❌ Not authorized."
    s3 = get_s3_client(**creds)

    if key.endswith(".csv"):
        input_serialization = {
            "CSV": {"FileHeaderInfo": "USE"},
            "CompressionType": "NONE"
        }
        output_serialization = {"CSV": {}}
    elif key.endswith(".json"):
        input_serialization = {
            "JSON": {"Type": "DOCUMENT"},
            "CompressionType": "NONE"
        }
        output_serialization = {"JSON": {}}
    elif key.endswith(".parquet"):
        input_serialization = {"Parquet": {}}
        output_serialization = {"JSON": {}}
    else:
        return f"Unsupported file format for key: {key}"

    try:
        response = s3.select_object_content(
            Bucket=S3_BUCKET,
            Key=key,
            ExpressionType="SQL",
            Expression=query,
            InputSerialization=input_serialization,
            OutputSerialization=output_serialization,
        )

        result = ""
        for event in response["Payload"]:
            if "Records" in event:
                result += event["Records"]["Payload"].decode("utf-8", errors="ignore")

        return result.strip() or "No results found."

    except Exception as e:
        return f"Query failed: {str(e)}"

@mcp.tool(name="s3_auth_url")
def get_auth_url() -> str:
    """Returns the authorization URL based on auth mode."""
    return "/authorize?sim=true" if USE_SIMULATED_OAUTH else "/authorize"

@mcp.tool(name="s3_list_users")
def list_users() -> str:
    """Returns a list of currently simulated user emails."""
    return json.dumps(list(user_sessions.keys()))

# ─── Auth Routes ────────────────────────────────────────────────────────
async def authorize(request: Request) -> JSONResponse:
    """Simulates or initiates OAuth login."""
    sim = request.query_params.get("sim") == "true"
    if sim:
        fake_email = f"user-{uuid.uuid4()}@test.dev"
        simulate_login(fake_email)
        return JSONResponse({"message": f"Simulated login as {fake_email}"})
    else:
        return RedirectResponse("https://your-oauth-provider/authorize")

async def oauth_callback(request: Request) -> JSONResponse:
    """Handles OAuth callback (currently not used in simulated mode)."""
    if USE_SIMULATED_OAUTH:
        return JSONResponse({"error": "Simulated mode does not use this endpoint."}, status_code=400)
    return JSONResponse({"message": "OAuth flow not implemented yet"})

# ─── Starlette App ──────────────────────────────────────────────────────
mcp_app = mcp.http_app(transport="sse")
routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth_callback),
]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=8060)
