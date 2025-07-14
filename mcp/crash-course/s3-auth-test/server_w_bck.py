import os
import json
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
import boto3
import uuid

load_dotenv()

# ─── Config ─────────────────────────────────────────────────────────────
USE_SIMULATED_OAUTH = os.getenv("USE_SIMULATED_OAUTH", "true").lower() == "true"
USE_MINIO = os.getenv("USE_MINIO", "true").lower() == "true"

S3_BUCKET = os.getenv("S3_BUCKET", "bucket")
MINIO_URL = os.getenv("MINIO_URL", "http://localhost:9000")

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

mcp = FastMCP("S3ToolkitOAuth", host="0.0.0.0", port=8060)

# ─── Session Store ──────────────────────────────────────────────────────
user_sessions = {}  # email -> aws creds

# ─── S3 Client ──────────────────────────────────────────────────────────
def get_s3_client(access_key, secret_key, session_token=None):
    return boto3.client(
        "s3",
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        aws_session_token=session_token,
        endpoint_url=MINIO_URL if USE_MINIO else None,
        region_name=None if USE_MINIO else AWS_REGION,
    )

# ─── Simulated OAuth ────────────────────────────────────────────────────
def simulate_login(email: str):
    creds = {
        "access_key": os.getenv("SIM_AWS_ACCESS_KEY", "root"),
        "secret_key": os.getenv("SIM_AWS_SECRET_KEY", "password"),
        "session_token": None,
    }
    user_sessions[email] = creds
    return creds

# ─── Real OAuth Placeholder ─────────────────────────────────────────────
def login_with_real_oauth(id_token: str):
    # TODO: Exchange Google/Azure token for AWS STS creds via Cognito
    raise NotImplementedError("Real OAuth login not implemented")

# ─── MCP Tools ──────────────────────────────────────────────────────────
@mcp.tool(name="s3_list_files")
def list_files(email: str) -> str:
    creds = user_sessions.get(email)
    if not creds:
        return "❌ Not authorized."
    s3 = get_s3_client(**creds)
    objs = s3.list_objects_v2(Bucket=S3_BUCKET).get("Contents", [])
    return json.dumps([obj["Key"] for obj in objs])

@mcp.tool(name="s3_fetch_file")
def fetch_file(email: str, key: str) -> str:
    creds = user_sessions.get(email)
    if not creds:
        return "❌ Not authorized."
    s3 = get_s3_client(**creds)
    body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()
    return body.decode("utf-8", errors="ignore")

@mcp.tool(name="s3_auth_url")
def get_auth_url() -> str:
    if USE_SIMULATED_OAUTH:
        return "/authorize?sim=true"
    return "/authorize"  # replace with real OAuth login

@mcp.tool(name="s3_list_users")
def list_users() -> str:
    return json.dumps(list(user_sessions.keys()))

# ─── Auth Routes ────────────────────────────────────────────────────────
async def authorize(request: Request):
    sim = request.query_params.get("sim") == "true"
    if sim:
        fake_email = f"user-{uuid.uuid4()}@test.dev"
        simulate_login(fake_email)
        return JSONResponse({"message": f"Simulated login as {fake_email}"})
    else:
        # Redirect to real OAuth flow (e.g., Cognito)
        return RedirectResponse("https://your-oauth-provider/authorize")

async def oauth_callback(request: Request):
    if USE_SIMULATED_OAUTH:
        return JSONResponse({"error": "Simulated mode does not use this endpoint."}, status_code=400)
    # Handle real OAuth callback here
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
