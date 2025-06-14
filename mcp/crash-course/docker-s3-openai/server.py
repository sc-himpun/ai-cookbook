from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
# from openai import OpenAI
import os
from mcp.server.fastmcp import FastMCP
import boto3
import json
import openai

load_dotenv("../.env")


USE_MINIO = os.getenv("USE_MINIO", "True") == "True"
BUCKET = os.getenv("S3_BUCKET", "bucket")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "root")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "password")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


os.environ["OPENAI_API_KEY"]="lm-studio"
os.environ["OPENAI_API_BASE"]="http://192.168.29.53:1234/v1"


openai.api_base = os.environ["OPENAI_API_BASE"]
openai.api_key = os.environ["OPENAI_API_KEY"]

def get_s3_client():
    if USE_MINIO:
        return boto3.client(
            "s3",
            endpoint_url=S3_ENDPOINT_URL,
            aws_access_key_id=AWS_ACCESS_KEY_ID,
            aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
            region_name=AWS_REGION,
        )
    else:
        return boto3.client("s3", region_name=AWS_REGION)

s3 = get_s3_client()

# ─── MCP SETUP ─────────────────────────────────────────────────────────────
mcp = FastMCP(name="S3Toolkit", host="0.0.0.0", port=8050)

@mcp.tool()
def search_files(keyword: str, search_type: str = "both") -> list:
    """Used for performing search in filenames and file's content on s3 bucket"""
    results = []
    objects = s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    for obj in objects:
        key = obj["Key"]
        match = False

        if search_type in ["filename", "both"] and keyword.lower() in key.lower():
            match = True

        if not match and search_type in ["content", "both"]:
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
            if keyword.lower() in body.lower():
                match = True

        if match:
            results.append(key)
    return json.dumps(results)


@mcp.tool()
def fetch_file(key: str, user_query: str = "") -> str:
    """Used for fetching file from S3 bucket"""
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
   
    return body

if __name__ == "__main__":
    mcp.run(transport="sse")
