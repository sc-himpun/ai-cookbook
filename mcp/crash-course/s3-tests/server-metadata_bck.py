# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import boto3
import json
from typing import Dict, Any, Tuple, Optional
from docx import Document
import fitz  # PyMuPDF
import io

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


def extract_text_from_file(key: str, raw_bytes: bytes) -> str:
    """
    Extracts text based on file type.
    - PDF: PyMuPDF
    - DOCX: python-docx
    - Others: UTF-8 decode
    """
    if key.lower().endswith(".pdf"):
        try:
            pdf_doc = fitz.open(stream=raw_bytes, filetype="pdf")
            return "\n".join(page.get_text() for page in pdf_doc).strip()
        except Exception as e:
            return f"PDF extraction failed: {str(e)}"

    elif key.lower().endswith(".docx"):
        try:
            doc = Document(io.BytesIO(raw_bytes))
            return "\n".join(para.text for para in doc.paragraphs).strip()
        except Exception as e:
            return f"DOCX extraction failed: {str(e)}"

    else:
        try:
            return raw_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            return f"Text decode failed: {str(e)}"



# @mcp.tool(name="s3_search_files")
# def search_files(metadata: dict, keyword: str, search_type: str = "both") -> list:
#     """Used for performing search in filenames and file's content on s3 bucket"""
#     s3, bucket = get_s3_client_and_bucket(metadata)
#     results = []
#     objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])
#     for obj in objects:
#         key = obj["Key"]
#         match = False

#         if search_type in ["filename", "both"] and keyword.lower() in key.lower():
#             match = True

#         if not match and search_type in ["content", "both"]:
#             # body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", errors="ignore")
#             # if keyword.lower() in body.lower():
#             #     match = True
#             raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
#             file_text = extract_text_from_file(key, raw_bytes)
#             if keyword.lower() in file_text.lower():
#                 match = True


#         if match:
#             results.append(key)
#     return json.dumps(results)

import json
import fitz  # PyMuPDF
from io import BytesIO

@mcp.tool(name="s3_search_files")
def search_files(metadata: dict, keyword: str, search_type: str = "both",
                 max_preview_chars: int = 5000, filename_hint: str = None) -> str:
    """
    Search filenames and/or file content on S3.
    Streams PDFs page-by-page to avoid high memory usage.
    If filename_hint is provided, only that file is searched.
    """
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []

    if filename_hint:
        objects = [{"Key": filename_hint}]
    else:
        objects = s3.list_objects_v2(Bucket=bucket).get("Contents", [])

    for obj in objects:
        key = obj["Key"]
        match_found = False
        matching_chunks = []
        total_chunks = 0

        # Filename match
        if search_type in ["filename", "both"] and keyword.lower() in key.lower():
            match_found = True

        # Content match
        if search_type in ["content", "both"]:
            if key.lower().endswith(".pdf"):
                raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                pdf_document = fitz.open(stream=raw_bytes, filetype="pdf")
                total_chunks = pdf_document.page_count
                for idx in range(total_chunks):
                    page_text = pdf_document[idx].get_text()
                    if keyword.lower() in page_text.lower():
                        match_found = True
                        matching_chunks.append(idx)
                pdf_document.close()

            else:
                raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                text = extract_text_from_file(key, raw_bytes)

                if len(text) > max_preview_chars:
                    chunks = chunk_text(text, max_chunk_size=max_preview_chars)
                    total_chunks = len(chunks)
                    for idx, chunk in enumerate(chunks):
                        if keyword.lower() in chunk.lower():
                            match_found = True
                            matching_chunks.append(idx)
                else:
                    total_chunks = 1
                    if keyword.lower() in text.lower():
                        match_found = True
                        matching_chunks.append(0)

        if match_found:
            results.append({
                "key": key,
                "matches_in_chunks": matching_chunks,
                "total_chunks": total_chunks
            })

    return json.dumps(results)




def chunk_text(text: str, max_chunk_size: int = 3000, overlap: int = 200) -> list:
    """
    Splits text into overlapping chunks.
    max_chunk_size: maximum characters per chunk
    overlap: overlapping characters between chunks
    """
    chunks = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = min(start + max_chunk_size, text_len)
        chunks.append(text[start:end])
        start = end - overlap
        if start < 0:
            start = 0
    return chunks


import fitz  # PyMuPDF
import io

def extract_pdf_chunk(raw_bytes: bytes, chunk_index: int, max_chars: int = 3000, overlap: int = 200):
    doc = fitz.open(stream=raw_bytes, filetype="pdf")

    chunks = []
    current_text = ""
    start_chars = chunk_index * (max_chars - overlap)

    char_count = 0
    for page in doc:
        page_text = page.get_text()
        for char in page_text:
            if char_count >= start_chars:
                current_text += char
                if len(current_text) >= max_chars:
                    chunks.append(current_text)
                    return {
                        "chunk": current_text,
                        "chunk_index": chunk_index,
                        "total_chunks": None  # Could be calculated if needed
                    }
            char_count += 1

    # If file smaller than expected chunk
    if current_text:
        return {
            "chunk": current_text,
            "chunk_index": chunk_index,
            "total_chunks": None
        }



@mcp.tool(name="s3_fetch_file_chunked")
def fetch_file_chunked(
    metadata: dict,
    key: str,
    chunk_index: int = 0,
    max_chunk_size: int = 3000,
    overlap: int = 200
) -> dict:
    """
    Fetch a chunk of text from a file stored in S3 without loading the entire file into memory.
    For PDFs, extracts text page-by-page until chunk is filled.
    For other formats, falls back to full extraction (can optimize later).
    """
    import fitz  # PyMuPDF
    import io

    s3, bucket = get_s3_client_and_bucket(metadata)
    raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

    if key.lower().endswith(".pdf"):
        doc = fitz.open(stream=raw_bytes, filetype="pdf")

        start_char_index = chunk_index * (max_chunk_size - overlap)
        current_text = ""
        char_count = 0
        total_chars = 0

        # First pass: count chars
        for page in doc:
            total_chars += len(page.get_text())
        estimated_chunks = -(-total_chars // (max_chunk_size - overlap))  # ceil

        # Second pass: extract this chunk only
        doc.close()
        doc = fitz.open(stream=raw_bytes, filetype="pdf")

        for page in doc:
            page_text = page.get_text()
            for char in page_text:
                if char_count >= start_char_index:
                    current_text += char
                    if len(current_text) >= max_chunk_size:
                        doc.close()
                        return {
                            "chunk": current_text,
                            "chunk_index": chunk_index,
                            "total_chunks": estimated_chunks
                        }
                char_count += 1

        doc.close()
        return {
            "chunk": current_text,
            "chunk_index": chunk_index,
            "total_chunks": estimated_chunks
        }

    # Non-PDF fallback
    text = extract_text_from_file(key, raw_bytes)
    chunks = chunk_text(text, max_chunk_size, overlap)
    return {
        "chunk": chunks[chunk_index] if chunk_index < len(chunks) else "",
        "chunk_index": chunk_index,
        "total_chunks": len(chunks)
    }



# @mcp.tool(name="s3_fetch_file")
# def fetch_file(metadata: dict, key: str, user_query: str = "") -> str:
#     """Fetch file from S3; PDFs and DOCX are converted to text."""
#     s3, bucket = get_s3_client_and_bucket(metadata)
#     raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
#     return extract_text_from_file(key, raw_bytes)

@mcp.tool(name="s3_fetch_file")
def fetch_file(metadata: dict, key: str, user_query: str = "", max_chars: int = 5000) -> dict:
    """
    Fetch file from S3; PDFs and DOCX are converted to text.
    If file is too large, automatically returns the first chunk using s3_fetch_file_chunked.
    """
    s3, bucket = get_s3_client_and_bucket(metadata)
    raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    text = extract_text_from_file(key, raw_bytes)

    if len(text) > max_chars:
        first_chunk_data = fetch_file_chunked(
            metadata=metadata,
            key=key,
            chunk_index=0,
            max_chunk_size=3000,
            overlap=200
        )

        note = (
            f"⚠️ File is large ({len(text)} chars). "
            f"Only chunk 0 of {first_chunk_data['total_chunks']} is returned. "
            f"Use 's3_fetch_file_chunked' for more."
        )

        return {
            "chunk": first_chunk_data["chunk"],
            "chunk_index": first_chunk_data["chunk_index"],
            "total_chunks": first_chunk_data["total_chunks"],
            "note": note
        }

    return {
        "chunk": text,
        "chunk_index": 0,
        "total_chunks": 1
    }




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
