from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
from mcp.server.fastmcp import FastMCP
import boto3
import json
from typing import Dict, Any, Tuple, Optional

load_dotenv()


# USE_MINIO = os.getenv("USE_MINIO", "True").strip().lower() == "true"
# BUCKET = os.getenv("S3_BUCKET", "bucket")

# AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "root")
# AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "password")
# S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "http://minio:9000")
# AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

"""
Metadata for AWS s3 -
    metadata = {
    "access_key": "<AWS_ACCESS_KEY>",
    "secret_key": "<AWS_SECRET_KEY>",
    "bucket": "your-aws-bucket",
    "region": "us-west-2"
    }

Metadata for MinIO -
    metadata = {
    "access_key": "<minio_user>",
    "secret_key": "<minio_key>",
    "bucket": "<bucket>",
    "endpoint_url": "http://localhost:9000",
    "region": "us-east-1"
    }


"""


def get_s3_client_and_bucket(metadata: Dict) -> Tuple[boto3.client, str]:
    """
    Returns a boto3 S3 client and target bucket name, using metadata.
    Supports both AWS S3 and S3-compatible endpoints like MinIO.

    Args:
        metadata (dict): Metadata dict with keys:
            - access_key
            - secret_key
            - bucket
            - endpoint_url (optional for MinIO)
            - region (optional for MinIO, default "us-east-1")

    Returns:
        (s3_client, bucket) tuple
    """
    if not metadata:
        raise Exception("❌ Missing metadata")

    access_key = metadata.get("access_key")
    secret_key = metadata.get("secret_key")
    bucket = metadata.get("bucket")
    region = metadata.get("region", "us-east-1")
    endpoint_url = metadata.get("endpoint_url")  # Optional for MinIO

    if not access_key or not secret_key or not bucket:
        raise Exception("❌ Missing required S3 credentials or bucket in metadata")

    s3_params = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region
    }

    if endpoint_url:
        s3_params["endpoint_url"] = endpoint_url

    s3_client = boto3.client("s3", **s3_params)
    return s3_client, bucket


# ─── MCP SETUP ─────────────────────────────────────────────────────────────
mcp = FastMCP(name="S3Toolkit", host="0.0.0.0", port=8050)

@mcp.tool(name="s3_search_files")
def search_files(metadata: dict, keyword: str, search_type: str = "both") -> list:
    """Used for performing search in filenames and file's content on s3 bucket"""
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []
    objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
    for obj in objects:
        key = obj["Key"]
        match = False

        if search_type in ["filename", "both"] and keyword.lower() in key.lower():
            match = True

        if not match and search_type in ["content", "both"]:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", errors="ignore")
            if keyword.lower() in body.lower():
                match = True

        if match:
            results.append(key)
    return json.dumps(results)


@mcp.tool(name="s3_fetch_file")
def fetch_file(metadata: dict, key: str, user_query: str = "") -> str:
    """Used for fetching file from S3 bucket"""
    s3, bucket = get_s3_client_and_bucket(metadata)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", errors="ignore")
   
    return body


@mcp.tool(name="s3_get_file_schema")
def s3_get_file_schema(metadata: dict, key: str) -> str:
    """
    Efficiently infers schema from a CSV (by reading header line directly),
    JSON (via S3 Select sample), or Parquet (via S3 Select sample).
    """
    import csv
    s3, bucket = get_s3_client_and_bucket(metadata)
    if key.endswith(".csv"):
        try:            
            # Read only first 1KB to get the header line
            response = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-1024")
            content = response["Body"].read().decode("utf-8", errors="ignore")
            header_line = content.splitlines()[0]
            reader = csv.reader([header_line])
            headers = next(reader)
            return json.dumps([{"column": col.strip()} for col in headers])
        except Exception as e:
            return f"CSV schema inference failed: {str(e)}"

    elif key.endswith(".json"):
        try:
            input_serialization = {
                "JSON": {"Type": "DOCUMENT"},
                "CompressionType": "NONE"
            }
            output_serialization = {"JSON": {}}
            response = s3.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType="SQL",
                Expression="SELECT * FROM S3Object LIMIT 1",
                InputSerialization=input_serialization,
                OutputSerialization=output_serialization,
            )
            raw = ""
            for event in response["Payload"]:
                if "Records" in event:
                    raw += event["Records"]["Payload"].decode("utf-8", errors="ignore")
            record = json.loads(raw.strip().splitlines()[0])
            return json.dumps([{"column": k, "type": type(v).__name__} for k, v in record.items()], indent=2)
        except Exception as e:
            return f"JSON schema inference failed: {str(e)}"

    elif key.endswith(".parquet"):
        try:
            input_serialization = {
                "Parquet": {}
            }
            output_serialization = {"JSON": {}}
            response = s3.select_object_content(
                Bucket=bucket,
                Key=key,
                ExpressionType="SQL",
                Expression="SELECT * FROM S3Object LIMIT 1",
                InputSerialization=input_serialization,
                OutputSerialization=output_serialization,
            )
            raw = ""
            for event in response["Payload"]:
                if "Records" in event:
                    raw += event["Records"]["Payload"].decode("utf-8", errors="ignore")
            record = json.loads(raw.strip().splitlines()[0])
            return json.dumps([{"column": k, "type": type(v).__name__} for k, v in record.items()], indent=2)
        except Exception as e:
            return f"Parquet schema inference failed: {str(e)}"

    else:
        return f"Unsupported file format for key: {key}"



@mcp.tool(name="s3_select_query")
def s3_select_query(metadata: dict, key: str, query: str = "SELECT * FROM S3Object LIMIT 5") -> str:
    """
    Query CSV, JSON, or Parquet files on S3 using S3 Select.
    Only supports structured formats: .csv, .json, .parquet
    """
    s3, bucket = get_s3_client_and_bucket(metadata)
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
            Bucket=bucket,
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
