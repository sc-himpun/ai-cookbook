# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import boto3
import json
from typing import Dict, Tuple
import docx
from docx import Document
import fitz  # PyMuPDF
import io
import json


load_dotenv()

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

LARGE_FILE_THRESHOLD = 1_000_000  # 1MB

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


def extract_text_from_file(key: str, raw_bytes: bytes, stream: bool = False, max_chars: int = 0) -> str:
    """
    Extracts text based on file type.
    - PDF: PyMuPDF (streams page-by-page if stream=True)
    - DOCX: python-docx (streams paragraph-by-paragraph if stream=True)
    - Others: UTF-8 decode
    - If max_chars is set, stops after reaching the limit.
    """
    extracted_text_parts = []
    total_chars = 0

    def maybe_add_text(chunk):
        nonlocal total_chars
        if not chunk:
            return False
        extracted_text_parts.append(chunk)
        total_chars += len(chunk)
        if max_chars and total_chars >= max_chars:
            return True  # stop
        return False

    if key.lower().endswith(".pdf"):
        try:
            pdf_doc = fitz.open(stream=raw_bytes, filetype="pdf")
            if stream:
                for page in pdf_doc:
                    if maybe_add_text(page.get_text()):
                        break
            else:
                return "\n".join(page.get_text() for page in pdf_doc).strip()
            pdf_doc.close()
        except Exception as e:
            return f"PDF extraction failed: {str(e)}"

    elif key.lower().endswith(".docx"):
        try:
            doc = Document(io.BytesIO(raw_bytes))
            if stream:
                for para in doc.paragraphs:
                    if maybe_add_text(para.text):
                        break
            else:
                return "\n".join(para.text for para in doc.paragraphs).strip()
        except Exception as e:
            return f"DOCX extraction failed: {str(e)}"

    else:
        try:
            text = raw_bytes.decode("utf-8", errors="ignore")
            if max_chars:
                return text[:max_chars]
            return text
        except Exception as e:
            return f"Text decode failed: {str(e)}"

    return "\n".join(extracted_text_parts).strip()



# @mcp.tool(name="s3_search_file_content")
# def search_file_content(metadata: dict, filename: str, keyword: str,
#                         max_preview_chars: int = 5000) -> str:
#     """
#     Search within the content of a specific file in S3.
#     - Streams PDFs page-by-page to reduce memory usage.
#     - Supports text-based formats.
#     - Returns matching chunk/page indexes for targeted retrieval.
#     """
#     s3, bucket = get_s3_client_and_bucket(metadata)
#     results = []
#     match_found = False
#     matching_chunks = []
#     total_chunks = 0

#     # Ensure file exists
#     try:
#         s3.head_object(Bucket=bucket, Key=filename)
#     except Exception:
#         return json.dumps({"error": f"File '{filename}' not found in S3 bucket."})

#     # Content search
#     if filename.lower().endswith(".pdf"):
#         raw_bytes = s3.get_object(Bucket=bucket, Key=filename)["Body"].read()
#         pdf_document = fitz.open(stream=raw_bytes, filetype="pdf")
#         total_chunks = pdf_document.page_count
#         for idx in range(total_chunks):
#             page_text = pdf_document[idx].get_text()
#             if keyword.lower() in page_text.lower():
#                 match_found = True
#                 matching_chunks.append(idx)
#         pdf_document.close()

#     else:
#         raw_bytes = s3.get_object(Bucket=bucket, Key=filename)["Body"].read()
#         text = extract_text_from_file(filename, raw_bytes)

#         if len(text) > max_preview_chars:
#             chunks = chunk_text(text, max_chunk_size=max_preview_chars)
#             total_chunks = len(chunks)
#             for idx, chunk in enumerate(chunks):
#                 if keyword.lower() in chunk.lower():
#                     match_found = True
#                     matching_chunks.append(idx)
#         else:
#             total_chunks = 1
#             if keyword.lower() in text.lower():
#                 match_found = True
#                 matching_chunks.append(0)

#     if match_found:
#         results.append({
#             "key": filename,
#             "matches_in_chunks": matching_chunks,
#             "total_chunks": total_chunks
#         })

#     return json.dumps(results)

@mcp.tool(name="s3_search_file_content")
def search_file_content(metadata: dict, filename: str, keyword: str,
                        max_preview_chars: int = 5000,
                        large_file_threshold: int = LARGE_FILE_THRESHOLD) -> str:
    """
    Search within the content of a specific file in S3.
    - For large files, delegate to vector_search.
    - Streams PDFs page-by-page to reduce memory usage.
    - Supports text-based formats.
    - Returns matching chunk/page indexes for targeted retrieval.
    """
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []
    match_found = False
    matching_chunks = []
    total_chunks = 0

    # Ensure file exists and check size
    try:
        head = s3.head_object(Bucket=bucket, Key=filename)
    except Exception:
        return json.dumps({"error": f"File '{filename}' not found in S3 bucket."})

    file_size = head.get("ContentLength", 0)

    # 🚨 Delegate to vector_search if file is too large
    if file_size > large_file_threshold:
        return json.dumps({
                    "delegate": "vector_ingest_s3",
                    "key": filename,
                    "reason": f"File size {file_size} exceeds threshold {large_file_threshold}. Use 'vector_ingest_s3' for ingesting the chunks as embeddings and searching."
                })

    # ✅ Normal content search for smaller files
    if filename.lower().endswith(".pdf"):
        raw_bytes = s3.get_object(Bucket=bucket, Key=filename)["Body"].read()
        pdf_document = fitz.open(stream=raw_bytes, filetype="pdf")
        total_chunks = pdf_document.page_count
        for idx in range(total_chunks):
            page_text = pdf_document[idx].get_text()
            if keyword.lower() in page_text.lower():
                match_found = True
                matching_chunks.append(idx)
        pdf_document.close()

    else:
        raw_bytes = s3.get_object(Bucket=bucket, Key=filename)["Body"].read()
        text = extract_text_from_file(filename, raw_bytes)

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
            "key": filename,
            "matches_in_chunks": matching_chunks,
            "total_chunks": total_chunks
        })

    return json.dumps(results)


@mcp.tool(name="s3_list_files")
def list_files_in_s3(metadata: dict, prefix: str = "", max_keys: int = 100, search_substring: bool = True) -> str:
    """
    List files in the configured S3 bucket.
    - Uses metadata for AWS/S3 configuration.
    - Returns file key, size (bytes), and last modified date.
    - If search_substring=True, will search for `prefix` anywhere in the key (not just start).
    """
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []

    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if search_substring:
                    if prefix.lower() not in key.lower():
                        continue
                else:
                    if not key.startswith(prefix):
                        continue

                results.append({
                    "key": key,
                    "size_bytes": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat()
                })
                if len(results) >= max_keys:
                    break
            if len(results) >= max_keys:
                break
    except Exception as e:
        return json.dumps({"error": str(e)})

    return json.dumps(results)



@mcp.tool(name="s3_search_files")
def search_files(metadata: dict, keyword: str, search_type: str = "both",
                 max_preview_chars: int = 8000) -> str:
    """
    Search filenames and/or file content in S3.
    - Streams large files and stops early when a match is found.
    - Processes smaller files first.
    - Supports all prefixes (subdirectories) using pagination.
    """

    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []
    all_objects = []

    # ✅ Use paginator to get *all* objects across all prefixes
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            all_objects.append(obj)

    
    # Sort by size so smaller files are searched first
    all_objects.sort(key=lambda x: x["Size"])
    print(all_objects)

    for obj in all_objects:
        key = obj["Key"]
        file_size = obj["Size"]
        match_found = False
        matching_chunks = []
        total_chunks = 0
        
        if file_size > LARGE_FILE_THRESHOLD:
                # Instead of brute-force scanning, tell LLM to use vector search
                return json.dumps({
                    "delegate": "vector_ingest_s3",
                    "key": key,
                    "reason": f"File size {file_size} exceeds threshold. Use 'vector_ingest_s3' for ingesting the chunks as embeddings and searching."
                })
        
        # Filename match
        if search_type in ["filename", "both"] and keyword.lower() in key.lower():
            match_found = True

        # Content match
        if not match_found and search_type in ["content", "both"]:
            if key.lower().endswith(".pdf"):
                raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                pdf_document = fitz.open(stream=raw_bytes, filetype="pdf")
                total_chunks = pdf_document.page_count
                for idx in range(total_chunks):
                    if keyword.lower() in pdf_document[idx].get_text().lower():
                        match_found = True
                        matching_chunks.append(idx)
                        break
                pdf_document.close()

            elif key.lower().endswith(".docx"):
                raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
                doc = docx.Document(io.BytesIO(raw_bytes))
                total_chunks = len(doc.paragraphs)
                for idx, para in enumerate(doc.paragraphs):
                    if keyword.lower() in para.text.lower():
                        match_found = True
                        matching_chunks.append(idx)
                        break

            else:
                # Stream and chunk text files
                obj_stream = s3.get_object(Bucket=bucket, Key=key)["Body"]
                buffer = ""
                chunk_index = 0
                match_found = False
                matching_chunks = []

                for raw_line in obj_stream.iter_lines():
                    try:
                        line = raw_line.decode("utf-8", errors="ignore")
                    except Exception:
                        line = ""
                    buffer += line + "\n"

                    if len(buffer) >= max_preview_chars:
                        if keyword.lower() in buffer.lower():
                            match_found = True
                            matching_chunks.append(chunk_index)
                            break  # stop after first match
                        buffer = ""
                        chunk_index += 1

                # Check the last chunk if loop ends without hitting max_preview_chars
                if not match_found and buffer:
                    if keyword.lower() in buffer.lower():
                        match_found = True
                        matching_chunks.append(chunk_index)

                total_chunks = chunk_index + (1 if buffer else 0)

                # body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", errors="ignore")
                # if keyword.lower() in body.lower():
                #     match_found = True

        if match_found:
            results.append({
                "key": key,
                "size": file_size,
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

    elif key.lower().endswith(".txt"):
        obj_stream = s3.get_object(Bucket=bucket, Key=key)["Body"]
        start_char_index = chunk_index * (max_chunk_size - overlap)
        current_text = ""
        char_count = 0

        for raw_line in obj_stream.iter_lines():
            line = raw_line.decode("utf-8", errors="ignore") + "\n"
            for char in line:
                if char_count >= start_char_index:
                    current_text += char
                    if len(current_text) >= max_chunk_size:
                        return {
                            "chunk": current_text,
                            "chunk_index": chunk_index,
                            "total_chunks": None  # unknown without scanning whole file
                        }
                char_count += 1

        return {
            "chunk": current_text,
            "chunk_index": chunk_index,
            "total_chunks": None
        }
    
     # DOCX and others - still need full read
    else:
        raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        text = extract_text_from_file(key, raw_bytes)
        chunks = chunk_text(text, max_chunk_size, overlap)
        return {
            "chunk": chunks[chunk_index] if chunk_index < len(chunks) else "",
            "chunk_index": chunk_index,
            "total_chunks": len(chunks)
        }



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
