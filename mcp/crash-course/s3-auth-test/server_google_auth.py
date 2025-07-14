# requires below env variables to be set:
# GOOGLE_CLIENT_ID=your-google-client-id
# GOOGLE_CLIENT_SECRET=your-google-client-secret
# REDIRECT_URI=http://localhost:8000/oauth2callback
# COGNITO_IDENTITY_POOL_ID=us-east-1:xxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# AWS_REGION=us-east-1

import os
import requests
import boto3
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from dotenv import load_dotenv
from urllib.parse import urlencode

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/oauth2callback")

COGNITO_IDENTITY_POOL_ID = os.getenv("COGNITO_IDENTITY_POOL_ID")
REGION = os.getenv("AWS_REGION", "us-east-1")

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_AUTH = requests.get(GOOGLE_DISCOVERY).json()["authorization_endpoint"]
GOOGLE_TOKEN = requests.get(GOOGLE_DISCOVERY).json()["token_endpoint"]

async def authorize(request: Request):
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    }
    return RedirectResponse(f"{GOOGLE_AUTH}?{urlencode(params)}")

async def oauth2callback(request: Request):
    code = request.query_params.get("code")

    # 1️⃣ Exchange code for tokens
    token_resp = requests.post(GOOGLE_TOKEN, data={
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }).json()

    id_token = token_resp.get("id_token")
    if not id_token:
        return JSONResponse({"error": "Google login failed"}, status_code=400)

    # 2️⃣ Exchange Google ID token for AWS creds
    cognito = boto3.client("cognito-identity", region_name=REGION)
    identity = cognito.get_id(
        IdentityPoolId=COGNITO_IDENTITY_POOL_ID,
        Logins={"accounts.google.com": id_token}
    )
    creds = cognito.get_credentials_for_identity(
        IdentityId=identity["IdentityId"],
        Logins={"accounts.google.com": id_token}
    )["Credentials"]

    # 3️⃣ Use temporary credentials to access S3
    s3 = boto3.client(
        "s3",
        aws_access_key_id=creds["AccessKeyId"],
        aws_secret_access_key=creds["SecretKey"],
        aws_session_token=creds["SessionToken"]
    )
    objects = s3.list_objects_v2(Bucket="your-bucket-name").get("Contents", [])
    return JSONResponse({"files": [obj["Key"] for obj in objects]})

app = Starlette(debug=True, routes=[
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
])
