# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
from mcp.server.fastmcp import FastMCP
import boto3
import json

load_dotenv()


USE_MINIO = os.getenv("USE_MINIO", "True").strip().lower() == "true"
BUCKET = os.getenv("S3_BUCKET", "bucket")

AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "root")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "password")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


def get_s3_client():
    """Get appropriate S3 client (AWS S3 or MinIO)"""
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT_URL.strip() if USE_MINIO else None,
        aws_access_key_id=AWS_ACCESS_KEY_ID.strip(),
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY.strip(),
        region_name=AWS_REGION.strip() if not USE_MINIO else None,
    )


s3 = get_s3_client()

# ─── MCP SETUP ─────────────────────────────────────────────────────────────
mcp = FastMCP(name="S3Toolkit", host="0.0.0.0", port=8050)

@mcp.tool(name="s3_search_files")
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


@mcp.tool(name="s3_fetch_file")
def fetch_file(key: str, user_query: str = "") -> str:
    """Used for fetching file from S3 bucket"""
    body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
   
    return body

@mcp.tool(name="s3_select_query")
def s3_select_query(key: str, query: str = "SELECT * FROM S3Object LIMIT 5") -> str:
    """
    Query CSV, JSON, or Parquet files on S3 using S3 Select.
    Only supports structured formats: .csv, .json, .parquet
    """
    # Determine format based on file extension
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
        input_serialization = {
            "Parquet": {}
        }
        output_serialization = {"JSON": {}}
    else:
        return f"Unsupported file format for key: {key}"

    try:
        response = s3.select_object_content(
            Bucket=BUCKET,
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

if __name__ == "__main__":
    mcp.run(transport="sse")
