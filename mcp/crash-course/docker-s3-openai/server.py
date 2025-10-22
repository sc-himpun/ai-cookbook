# -*- coding: utf-8 -*-
import io
import json
from typing import Dict, Tuple, Optional
import os
import boto3
import docx
import fitz  # PyMuPDF
from docx import Document
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
import csv
from io import StringIO

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
S3_MCP_PORT = int(os.getenv("S3_MCP_PORT", "8050"))


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
        "region_name": region,
    }

    if endpoint_url:
        s3_params["endpoint_url"] = endpoint_url

    s3_client = boto3.client("s3", **s3_params)
    return s3_client, bucket


def make_response(success: bool, action: str, message: str, data=None):
    """
    Standardize tool responses in MCP server.

    Args:
        success (bool): Indicates if the tool operation was successful.
        action (str): Name of the tool or action performed.
        message (str): Human-readable status or error message.
        data (Any, optional): Structured payload returned by the tool. Defaults to empty dict if None.

    Returns:
        dict: Standardized response with the following keys:
            - "success" (bool): Success status.
            - "action" (str): Tool/action name.
            - "message" (str): Status message.
            - "data" (dict | list | Any): Tool-specific payload. Defaults to `{}` if None.

    Notes:
        - All MCP tools should use this format to ensure consistent responses.
        - The `data` field can be a dictionary, list, or any serializable object.
        - For sensitive information (e.g., internal IDs), include them only if required for tool chaining,
          but avoid exposing them directly to the UI.
    """
    return {
        "success": success,
        "action": action,
        "message": message,
        "data": data if data is not None else {},
    }


# ─── MCP SETUP ─────────────────────────────────────────────────────────────
mcp = FastMCP(name="S3Toolkit", host="0.0.0.0", port=S3_MCP_PORT)


def _maybe_add_text(
    chunk: str, extracted_parts: list, total_chars: int, max_chars: int
) -> tuple[bool, int]:
    """
    Helper to add chunk to extracted text and track total characters.

    Args:
        chunk (str): Text chunk to add.
        extracted_parts (list): List of accumulated text chunks.
        total_chars (int): Current total character count.
        max_chars (int): Maximum allowed characters (0 for no limit).

    Returns:
        tuple[bool, int]: (stop_extraction, updated_total_chars)
            stop_extraction: True if max_chars reached.
            updated_total_chars: New total character count.
    """
    if not chunk:
        return False, total_chars
    extracted_parts.append(chunk)
    total_chars += len(chunk)
    stop = max_chars and total_chars >= max_chars
    return stop, total_chars


def _extract_pdf_text(
    raw_bytes: bytes, stream: bool = False, max_chars: int = 0
) -> str:
    """
    Extract text from a PDF file using PyMuPDF.

    Args:
        raw_bytes (bytes): The binary content of the PDF file.
        stream (bool): If True, extract text page-by-page until max_chars (if set) is reached.
        max_chars (int): Optional character limit for extraction.

    Returns:
        str: Extracted text or an error message if extraction fails.
    """
    extracted_parts = []
    total_chars = 0
    try:
        pdf_doc = fitz.open(stream=raw_bytes, filetype="pdf")
        if stream:
            for page in pdf_doc:
                stop, total_chars = _maybe_add_text(
                    page.get_text(), extracted_parts, total_chars, max_chars
                )
                if stop:
                    break
        else:
            text = "\n".join(page.get_text() for page in pdf_doc)
            pdf_doc.close()
            return text.strip()
        pdf_doc.close()
    except Exception as e:
        return f"PDF extraction failed: {str(e)}"

    return "\n".join(extracted_parts).strip()


def _extract_docx_text(
    raw_bytes: bytes, stream: bool = False, max_chars: int = 0
) -> str:
    """
    Extract text from a DOCX file using python-docx.

    Args:
        raw_bytes (bytes): The binary content of the DOCX file.
        stream (bool): If True, extract paragraph-by-paragraph until max_chars (if set) is reached.
        max_chars (int): Optional character limit for extraction.

    Returns:
        str: Extracted text or an error message if extraction fails.
    """
    extracted_parts = []
    total_chars = 0
    try:
        doc = Document(io.BytesIO(raw_bytes))
        if stream:
            for para in doc.paragraphs:
                stop, total_chars = _maybe_add_text(
                    para.text, extracted_parts, total_chars, max_chars
                )
                if stop:
                    break
        else:
            return "\n".join(para.text for para in doc.paragraphs).strip()
    except Exception as e:
        return f"DOCX extraction failed: {str(e)}"

    return "\n".join(extracted_parts).strip()


def _extract_text_file(raw_bytes: bytes, max_chars: int = 0) -> str:
    """
    Extract text from a plain UTF-8 encoded file.

    Args:
        raw_bytes (bytes): The binary content of the text file.
        max_chars (int): Optional character limit for extraction.

    Returns:
        str: Decoded text or an error message if decoding fails.
    """
    try:
        text = raw_bytes.decode("utf-8", errors="ignore")
        return text[:max_chars] if max_chars else text
    except Exception as e:
        return f"Text decode failed: {str(e)}"


def extract_text_from_file(
    key: str, raw_bytes: bytes, stream: bool = False, max_chars: int = 0
) -> str:
    """
    Extract text from PDF, DOCX, or plain text files with optional streaming and max character limit.

    Args:
        key (str): Filename or object key to determine file type.
        raw_bytes (bytes): File content as bytes.
        stream (bool): If True, streams extraction page/paragraph-by-paragraph.
        max_chars (int): Maximum characters to extract; 0 means no limit.

    Returns:
        str: Extracted text or error message.
    """
    key_lower = key.lower()
    if key_lower.endswith(".pdf"):
        return _extract_pdf_text(raw_bytes, stream=stream, max_chars=max_chars)
    if key_lower.endswith(".docx"):
        return _extract_docx_text(raw_bytes, stream=stream, max_chars=max_chars)
    return _extract_text_file(raw_bytes, max_chars=max_chars)


@mcp.tool(name="s3_search_file_content")
def s3_search_file_content(
    metadata: dict,
    filename: str,
    keyword: str,
    max_preview_chars: int = 5000,
    large_file_threshold: int = LARGE_FILE_THRESHOLD,
) -> dict:
    """
    Search within the content of a specific file stored in S3 or a compatible MinIO endpoint.

    This tool extracts text from supported file formats and searches for a keyword match.
    For large files (over the configured threshold), it returns a delegate response
    suggesting use of vector-based ingestion/search.

    Args:
        metadata (dict):
            Connection metadata containing:
            - access_key (str): AWS or MinIO access key.
            - secret_key (str): AWS or MinIO secret key.
            - bucket (str): Target bucket name.
            - region (str, optional): AWS region (default "us-east-1").
            - endpoint_url (str, optional): MinIO or custom endpoint URL.
        filename (str): Key (path) of the file to search within.
        keyword (str): Keyword or phrase to search for.
        max_preview_chars (int, optional): Max characters per content chunk. Default = 5000.
        large_file_threshold (int, optional): File size limit in bytes to trigger delegation. Default = 1MB.

    Returns:
        dict: Standardized response via `make_response()` with fields:
            - success (bool): Whether search completed successfully.
            - action (str): "s3_search_file_content"
            - message (str): Summary of result or error.
            - data (dict): Contains:
                {
                    "key": <file key>,
                    "matches_in_chunks": [<chunk indexes>],
                    "total_chunks": <int>,
                    "file_size_bytes": <int>,
                    "file_url": <presigned_url or direct endpoint link>
                }

    Example:
        >>> s3_search_file_content(metadata, filename="docs/sample.pdf", keyword="invoice")
        {
            "success": True,
            "action": "s3_search_file_content",
            "message": "Keyword found in 2 chunks of 'docs/sample.pdf'",
            "data": {
                "key": "docs/sample.pdf",
                "matches_in_chunks": [0, 2],
                "total_chunks": 5,
                "file_size_bytes": 123456,
                "file_url": "https://<s3_or_minio_link>"
            }
        }
    """

    action = "s3_search_file_content"
    try:
        s3, bucket = get_s3_client_and_bucket(metadata)

        # Check if file exists
        try:
            head = s3.head_object(Bucket=bucket, Key=filename)
        except Exception:
            return make_response(
                success=False,
                action=action,
                message=f"File '{filename}' not found in bucket '{bucket}'.",
            )

        file_size = head.get("ContentLength", 0)

        # Delegate large files to vector ingestion
        if file_size > large_file_threshold:
            return make_response(
                success=True,
                action=action,
                message=(
                    f"File '{filename}' is large ({file_size} bytes). "
                    "Delegate to 'vector_ingest_s3' for efficient semantic search."
                ),
                data={
                    "delegate": "vector_ingest_s3",
                    "key": filename,
                    "reason": f"File exceeds {large_file_threshold} bytes threshold.",
                },
            )

        # --- Read and search content ---
        match_found = False
        matching_chunks = []
        total_chunks = 0

        # PDF case
        if filename.lower().endswith(".pdf"):
            raw_bytes = s3.get_object(Bucket=bucket, Key=filename)["Body"].read()
            pdf_document = fitz.open(stream=raw_bytes, filetype="pdf")
            total_chunks = pdf_document.page_count

            for idx in range(total_chunks):
                page_text = pdf_document[idx].get_text()
                if keyword.lower() in page_text.lower():
                    matching_chunks.append(idx)
                    match_found = True
            pdf_document.close()

        # Other formats
        else:
            raw_bytes = s3.get_object(Bucket=bucket, Key=filename)["Body"].read()
            text = extract_text_from_file(filename, raw_bytes)

            if len(text) > max_preview_chars:
                chunks = chunk_text(text, max_chunk_size=max_preview_chars)
                total_chunks = len(chunks)
                for idx, chunk in enumerate(chunks):
                    if keyword.lower() in chunk.lower():
                        matching_chunks.append(idx)
                        match_found = True
            else:
                total_chunks = 1
                if keyword.lower() in text.lower():
                    matching_chunks.append(0)
                    match_found = True

        # --- Generate file URL (works for AWS and MinIO) ---
        endpoint_url = metadata.get("endpoint_url")
        try:
            presigned_url = generate_presigned_url(s3, bucket, filename)
        except Exception:
            # Fallback to manual URL construction
            if endpoint_url:
                presigned_url = f"{endpoint_url}/{bucket}/{filename}"
            else:
                presigned_url = f"https://{bucket}.s3.amazonaws.com/{filename}"

        # --- Build result ---
        if match_found:
            data = {
                "key": filename,
                "matches_in_chunks": matching_chunks,
                "total_chunks": total_chunks,
                "file_size_bytes": file_size,
                "file_url": presigned_url,
            }
            return make_response(
                success=True,
                action=action,
                message=f"Keyword found in {len(matching_chunks)} chunk(s) of '{filename}'.",
                data=data,
            )
        else:
            return make_response(
                success=True,
                action=action,
                message=f"No matches found for '{keyword}' in '{filename}'.",
                data={
                    "key": filename,
                    "matches_in_chunks": [],
                    "total_chunks": total_chunks,
                    "file_size_bytes": file_size,
                    "file_url": presigned_url,
                },
            )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"Search failed: {str(e)}",
        )


def generate_presigned_url(
    s3_client, bucket: str, key: str, expires_in: int = 3600
) -> Optional[str]:
    """
    Generate a presigned URL for accessing an S3 or MinIO object.

    Args:
        s3_client: Boto3 S3 client instance.
        bucket (str): S3 bucket name.
        key (str): Object key.
        expires_in (int, optional): Expiration time in seconds. Defaults to 1 hour.

    Returns:
        str: Presigned URL for direct file access.
    """
    try:
        return s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expires_in,
        )
    except Exception:
        return None


@mcp.tool(name="s3_list_files")
def list_files_in_s3(
    metadata: dict,
    prefix: str = "",
    max_keys: int = 100,
    search_substring: bool = True,
) -> dict:
    """
    List files in the configured S3 or MinIO bucket.

    This tool retrieves object metadata (key, size, last modified) and also
    generates presigned URLs for direct access. Supports substring or prefix-based search.

    Args:
        metadata (dict): Contains S3 configuration details like access keys, bucket name, and endpoint URL.
        prefix (str, optional): Filter objects by key prefix or substring. Defaults to "" (no filter).
        max_keys (int, optional): Maximum number of files to return. Defaults to 100.
        search_substring (bool, optional): If True, matches anywhere in key; otherwise only prefix. Defaults to True.

    Returns:
        dict: Standardized MCP response with fields:
            - success (bool): Indicates success or failure.
            - action (str): The tool name ("s3_list_files").
            - message (str): Human-readable status.
            - data (list[dict]): List of objects, each containing:
                - key (str): Object key in the bucket.
                - size_bytes (int): Object size in bytes.
                - last_modified (str): ISO timestamp of last modification.
                - url (str): Presigned URL for direct file access.

    Example:
        >>> list_files_in_s3(metadata, prefix="reports/", max_keys=10)
        {
            "success": True,
            "action": "s3_list_files",
            "message": "10 files listed successfully from bucket 'my-bucket'.",
            "data": [
                {
                    "key": "reports/january.csv",
                    "size_bytes": 2048,
                    "last_modified": "2025-10-14T10:12:34Z",
                    "url": "https://s3.amazonaws.com/my-bucket/reports/january.csv?..."
                }
            ]
        }
    """
    action = "s3_list_files"
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []

    try:
        paginator = s3.get_paginator("list_objects_v2")

        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                key = obj["Key"]

                # Match prefix or substring
                if search_substring:
                    if prefix and prefix.lower() not in key.lower():
                        continue
                else:
                    if prefix and not key.startswith(prefix):
                        continue

                # Append file metadata
                results.append(
                    {
                        "key": key,
                        "size_bytes": obj["Size"],
                        "last_modified": obj["LastModified"].isoformat(),
                        "url": generate_presigned_url(s3, bucket, key),
                    }
                )

                if len(results) >= max_keys:
                    break
            if len(results) >= max_keys:
                break

        return make_response(
            success=True,
            action=action,
            message=f"{len(results)} files listed successfully from bucket '{bucket}'.",
            data=results,
        )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"Error listing files from bucket '{bucket}': {str(e)}",
            data=[],
        )


@mcp.tool(name="s3_search_files")
def search_files(
    metadata: dict,
    keyword: str,
    search_type: str = "both",
    max_preview_chars: int = 8000,
) -> dict:
    """
    Search filenames and/or file content in an S3 or MinIO bucket.

    This tool scans objects in the configured bucket and identifies files whose
    names or contents contain the specified keyword. It supports text, PDF, and DOCX
    files, handles large-file thresholds, and streams content efficiently.

    Args:
        metadata (dict): Connection configuration for S3/MinIO.
        keyword (str): Text to search for within filenames or file content.
        search_type (str, optional): One of {"filename", "content", "both"}. Defaults to "both".
        max_preview_chars (int, optional): Number of characters per chunk when reading text. Defaults to 8000.

    Returns:
        dict: MCP-formatted response with:
            - success (bool): Whether the search completed successfully.
            - action (str): The tool name ("s3_search_files").
            - message (str): Descriptive status message.
            - data (list[dict]): Matching file entries, each with:
                - key (str): Object key in the bucket.
                - size (int): File size in bytes.
                - matches_in_chunks (list[int]): Indices of chunks or pages with matches.
                - total_chunks (int): Total chunks/pages scanned.
                - url (str): Presigned URL for file access.
                - delegate (str, optional): Processing suggestion for large files.
                - reason (str, optional): Explanation for skipped large files.

    Example:
        >>> search_files(metadata, keyword="invoice", search_type="content")
        {
            "success": True,
            "action": "s3_search_files",
            "message": "Found 3 matching files in bucket 'my-bucket'.",
            "data": [
                {
                    "key": "finance/invoice_2025_01.pdf",
                    "size": 204800,
                    "matches_in_chunks": [2],
                    "total_chunks": 15,
                    "url": "https://s3.amazonaws.com/my-bucket/finance/invoice_2025_01.pdf?..."
                }
            ]
        }
    """
    action = "s3_search_files"
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []

    try:
        all_objects = list_all_objects(s3, bucket)

        for obj in sorted(all_objects, key=lambda x: x["Size"]):
            key = obj["Key"]
            file_size = obj["Size"]

            if file_size > LARGE_FILE_THRESHOLD:
                results.append(make_large_file_entry(s3, bucket, key, file_size))
                continue

            match_info = search_object_for_keyword(
                s3, bucket, key, keyword, search_type, max_preview_chars
            )
            if match_info:
                results.append(match_info)

        return make_response(
            success=True,
            action=action,
            message=f"Found {len(results)} matching files in bucket '{bucket}'.",
            data=results,
        )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"Error searching files in bucket '{bucket}': {str(e)}",
            data=[],
        )


def list_all_objects(s3, bucket: str) -> list:
    """
    Retrieve all objects from a specified S3 or MinIO bucket using pagination.

    This helper paginates through `list_objects_v2` results to ensure all
    objects are returned even for large buckets.

    Args:
        s3: Boto3 S3 client instance.
        bucket (str): Name of the bucket to scan.

    Returns:
        list[dict]: List of object metadata dictionaries containing at least
        'Key' and 'Size' fields.
    """
    objects = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket):
        objects.extend(page.get("Contents", []))
    return objects


def make_large_file_entry(s3, bucket: str, key: str, size: int) -> dict:
    """
    Create a standardized entry for large files that exceed the search threshold.

    Used to mark files that are too large for inline content scanning, but still
    provide a presigned URL for external processing.

    Args:
        s3: Boto3 S3 client.
        bucket (str): Bucket name.
        key (str): Object key.
        size (int): File size in bytes.

    Returns:
        dict: Entry describing a large file and the reason it was skipped.
    """
    return {
        "key": key,
        "size": size,
        "matches_in_chunks": [],
        "total_chunks": 0,
        "url": generate_presigned_url(s3, bucket, key),
        "delegate": "vector_ingest_s3",
        "reason": f"File size {size} exceeds threshold. Possible candidate for search but content not yet searched. Use tool `vector_ingest_s3` if needed to be searched.",
    }


def search_object_for_keyword(
    s3, bucket: str, key: str, keyword: str, search_type: str, max_preview_chars: int
) -> dict | None:
    """
    Search a single object for the presence of a keyword in its filename or content.

    Depending on file extension, delegates to `search_pdf`, `search_docx`,
    or `search_text`. Returns a match record only if the keyword is found.

    Args:
        s3: Boto3 S3 client.
        bucket (str): Bucket name.
        key (str): Object key.
        keyword (str): Text to search for.
        search_type (str): One of {"filename", "content", "both"}.
        max_preview_chars (int): Number of characters per chunk for text scanning.

    Returns:
        dict | None: Matching result entry if found, otherwise None.
    """
    filename_match = False
    match_found = False
    matching_chunks = []
    total_chunks = 0

    # Filename match
    if search_type in ["filename", "both"] and keyword.lower() in key.lower():
        match_found = True
        filename_match = True

    # Content match
    if not match_found and search_type in ["content", "both"]:
        ext = key.lower().split(".")[-1]
        if ext == "pdf":
            match_found, matching_chunks, total_chunks = search_pdf(
                s3, bucket, key, keyword
            )
        elif ext == "docx":
            match_found, matching_chunks, total_chunks = search_docx(
                s3, bucket, key, keyword
            )
        else:
            match_found, matching_chunks, total_chunks = search_text(
                s3, bucket, key, keyword, max_preview_chars
            )

    if match_found:
        return {
            "key": key,
            "size": s3.head_object(Bucket=bucket, Key=key)["ContentLength"],
            "matches_in_chunks": matching_chunks,
            "total_chunks": total_chunks,
            "url": generate_presigned_url(s3, bucket, key),
            "message": "Found in filename" if filename_match else "Found in content"
        }
    return None


def search_pdf(s3, bucket: str, key: str, keyword: str):
    """
    Search for a keyword within the text content of a PDF file stored in S3.

    Each page is treated as a "chunk". The search stops once a match is found
    or all pages are scanned.

    Args:
        s3: Boto3 S3 client.
        bucket (str): Bucket name.
        key (str): PDF object key.
        keyword (str): Text to search for (case-insensitive).

    Returns:
        tuple[bool, list[int], int]: A tuple of:
            - match_found (bool)
            - matching_chunks (list[int]): Page indices containing matches
            - total_chunks (int): Total pages scanned
    """
    raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    pdf_doc = fitz.open(stream=raw_bytes, filetype="pdf")
    total_chunks = pdf_doc.page_count
    matches = [
        idx
        for idx in range(total_chunks)
        if keyword.lower() in pdf_doc[idx].get_text().lower()
    ]
    pdf_doc.close()
    return (bool(matches), matches, total_chunks)


def search_docx(s3, bucket: str, key: str, keyword: str):
    """
    Search for a keyword within the paragraphs of a DOCX file.

    Each paragraph is treated as a "chunk". The search stops once the keyword
    is found or all paragraphs are scanned.

    Args:
        s3: Boto3 S3 client.
        bucket (str): Bucket name.
        key (str): DOCX object key.
        keyword (str): Text to search for (case-insensitive).

    Returns:
        tuple[bool, list[int], int]: A tuple of:
            - match_found (bool)
            - matching_chunks (list[int]): Paragraph indices containing matches
            - total_chunks (int): Total paragraphs scanned
    """
    raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    doc = docx.Document(io.BytesIO(raw_bytes))
    total_chunks = len(doc.paragraphs)
    matches = [
        idx
        for idx, para in enumerate(doc.paragraphs)
        if keyword.lower() in para.text.lower()
    ]
    return (bool(matches), matches, total_chunks)


def search_text(s3, bucket: str, key: str, keyword: str, max_preview_chars: int):
    """
    Search for a keyword within text-based files using streaming chunk reads.

    The file is read line-by-line, accumulating text into chunks of
    `max_preview_chars`. Each chunk is searched for the keyword, allowing
    efficient handling of large text files.

    Args:
        s3: Boto3 S3 client.
        bucket (str): Bucket name.
        key (str): Text file object key.
        keyword (str): Text to search for (case-insensitive).
        max_preview_chars (int): Number of characters per chunk before flushing buffer.

    Returns:
        tuple[bool, list[int], int]: A tuple of:
            - match_found (bool)
            - matching_chunks (list[int]): Chunk indices containing matches
            - total_chunks (int): Total chunks scanned
    """
    obj_stream = s3.get_object(Bucket=bucket, Key=key)["Body"]
    buffer = ""
    matches, chunk_index = [], 0
    for raw_line in obj_stream.iter_lines():
        try:
            line = raw_line.decode("utf-8", errors="ignore")
        except Exception:
            line = ""
        buffer += line + "\n"
        if len(buffer) >= max_preview_chars:
            if keyword.lower() in buffer.lower():
                matches.append(chunk_index)
            buffer = ""
            chunk_index += 1
    if buffer and keyword.lower() in buffer.lower():
        matches.append(chunk_index)
    total_chunks = chunk_index + (1 if buffer else 0)
    return (bool(matches), matches, total_chunks)


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
    overlap: int = 200,
) -> dict:
    """
    Fetch a specific text chunk from a file stored in S3 or MinIO without loading the entire file into memory.

    This tool supports PDFs (page-wise), text files (streamed line-by-line), and DOCX or other text-based formats
    via fallback full extraction. It returns one chunk of text at a time along with metadata about the total chunks.

    Args:
        metadata (dict): Configuration containing S3 or MinIO credentials, endpoint URL, and bucket name.
        key (str): Object key (file path) to fetch from S3.
        chunk_index (int, optional): Index of the chunk to retrieve (0-based). Defaults to 0.
        max_chunk_size (int, optional): Maximum number of characters per chunk. Defaults to 3000.
        overlap (int, optional): Number of overlapping characters between chunks. Defaults to 200.

    Returns:
        dict: Standardized MCP response containing:
            - success (bool): True if chunk was fetched successfully.
            - action (str): Tool name ("s3_fetch_file_chunked").
            - message (str): Status or error description.
            - data (dict): Chunk data, including:
                - key (str): Object key.
                - chunk (str): Extracted text for the requested chunk.
                - chunk_index (int): Index of the returned chunk.
                - total_chunks (int | None): Total number of chunks (None if unknown).
                - url (str): Presigned URL for the full file.

    Example:
        >>> fetch_file_chunked(metadata, key="docs/report.pdf", chunk_index=2)
        {
            "success": True,
            "action": "s3_fetch_file_chunked",
            "message": "Fetched chunk 2 of file 'docs/report.pdf'.",
            "data": {
                "key": "docs/report.pdf",
                "chunk": "Page 2 text ...",
                "chunk_index": 2,
                "total_chunks": 5,
                "url": "https://s3.amazonaws.com/my-bucket/docs/report.pdf?..."
            }
        }
    """
    action = "s3_fetch_file_chunked"

    try:
        s3, bucket = get_s3_client_and_bucket(metadata)
        raw_bytes = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

        # --- PDF handling ---
        if key.lower().endswith(".pdf"):
            doc = fitz.open(stream=raw_bytes, filetype="pdf")

            start_char_index = chunk_index * (max_chunk_size - overlap)
            current_text = ""
            char_count = 0
            total_chars = 0

            # Count total chars to estimate total chunks
            for page in doc:
                total_chars += len(page.get_text())
            estimated_chunks = -(
                -total_chars // (max_chunk_size - overlap)
            )  # ceil division

            # Extract requested chunk
            doc.close()
            doc = fitz.open(stream=raw_bytes, filetype="pdf")

            for page in doc:
                page_text = page.get_text()
                for char in page_text:
                    if char_count >= start_char_index:
                        current_text += char
                        if len(current_text) >= max_chunk_size:
                            doc.close()
                            return make_response(
                                success=True,
                                action=action,
                                message=f"Fetched chunk {chunk_index} of file '{key}'.",
                                data={
                                    "key": key,
                                    "chunk": current_text,
                                    "chunk_index": chunk_index,
                                    "total_chunks": estimated_chunks,
                                    "url": generate_presigned_url(s3, bucket, key),
                                },
                            )
                    char_count += 1

            doc.close()
            return make_response(
                success=True,
                action=action,
                message=f"Fetched final chunk {chunk_index} of file '{key}'.",
                data={
                    "key": key,
                    "chunk": current_text,
                    "chunk_index": chunk_index,
                    "total_chunks": estimated_chunks,
                    "url": generate_presigned_url(s3, bucket, key),
                },
            )

        # --- TXT files (streaming) ---
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
                            return make_response(
                                success=True,
                                action=action,
                                message=f"Fetched chunk {chunk_index} of file '{key}'.",
                                data={
                                    "key": key,
                                    "chunk": current_text,
                                    "chunk_index": chunk_index,
                                    "total_chunks": None,  # unknown for streaming files
                                    "url": generate_presigned_url(s3, bucket, key),
                                },
                            )
                    char_count += 1

            return make_response(
                success=True,
                action=action,
                message=f"Fetched final chunk {chunk_index} of file '{key}'.",
                data={
                    "key": key,
                    "chunk": current_text,
                    "chunk_index": chunk_index,
                    "total_chunks": None,
                    "url": generate_presigned_url(s3, bucket, key),
                },
            )

        # --- DOCX and other formats ---
        else:
            text = extract_text_from_file(key, raw_bytes)
            chunks = chunk_text(text, max_chunk_size, overlap)

            if chunk_index >= len(chunks):
                return make_response(
                    success=False,
                    action=action,
                    message=f"Chunk index {chunk_index} out of range for file '{key}'.",
                    data={
                        "key": key,
                        "chunk": "",
                        "chunk_index": chunk_index,
                        "total_chunks": len(chunks),
                        "url": generate_presigned_url(s3, bucket, key),
                    },
                )

            return make_response(
                success=True,
                action=action,
                message=f"Fetched chunk {chunk_index} of file '{key}'.",
                data={
                    "key": key,
                    "chunk": chunks[chunk_index],
                    "chunk_index": chunk_index,
                    "total_chunks": len(chunks),
                    "url": generate_presigned_url(s3, bucket, key),
                },
            )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"Error fetching chunk from '{key}': {str(e)}",
            data={},
        )


@mcp.tool(name="s3_fetch_file")
def fetch_file(
    metadata: dict,
    key: str,
    user_query: str = "",
    max_chars: int = 5000,
) -> dict:
    """
    Fetch a file from S3 and return its content or initial chunk.

    This tool automatically converts supported file types (PDF, DOCX, TXT)
    into readable text and returns additional metadata such as file size,
    presigned URL, and last modified date.

    If the file is large, only the first chunk is returned along with
    guidance to use `s3_fetch_file_chunked` for retrieving further chunks.

    Args:
        metadata (dict): Configuration metadata containing AWS or MinIO credentials and endpoint.
        key (str): The full S3 object key (path) of the file to fetch.
        user_query (str, optional): Optional search or processing hint from user. Defaults to "".
        max_chars (int, optional): Maximum number of characters to return directly. Defaults to 5000.

    Returns:
        dict: Standardized response from `make_response` with the following structure:
            - success (bool): Whether the fetch operation was successful.
            - action (str): Tool name (`s3_fetch_file`).
            - message (str): Summary or error message.
            - data (dict): Includes:
                - file_id (str): Unique identifier in form "bucket:key"
                - file_name (str): File’s basename
                - bucket (str): Bucket name
                - key (str): Full object key
                - file_size_bytes (int): File size in bytes
                - last_modified (str | None): ISO timestamp of last modification
                - presigned_url (str | None): Downloadable link (valid for both AWS and MinIO)
                - chunk (str): Extracted text content (partial or full)
                - chunk_index (int): Index of returned chunk
                - total_chunks (int | None): Total chunks (if available)
                - note (str, optional): Note for large files

    Example:
        >>> fetch_file(metadata, "reports/summary.pdf")
        {
            "success": True,
            "action": "s3_fetch_file",
            "message": "Fetched file successfully.",
            "data": {
                "file_name": "summary.pdf",
                "presigned_url": "https://minio.local/.../summary.pdf?...",
                "chunk": "Invoice summary for Q3...",
                "chunk_index": 0,
                "total_chunks": 5
            }
        }
    """
    action = "s3_fetch_file"

    try:
        s3, bucket = get_s3_client_and_bucket(metadata)
        presigned_url = generate_presigned_url(s3, bucket, key)

        obj = s3.get_object(Bucket=bucket, Key=key)
        raw_bytes = obj["Body"].read()

        file_size = obj.get("ContentLength", len(raw_bytes))
        last_modified = (
            obj.get("LastModified").isoformat() if obj.get("LastModified") else None
        )

        text = extract_text_from_file(key, raw_bytes)

        # If file is too large, return first chunk only
        if len(text) > max_chars:
            first_chunk_data = fetch_file_chunked(
                metadata=metadata,
                key=key,
                chunk_index=0,
                max_chunk_size=3000,
                overlap=200,
            )

            note = (
                f"⚠️ File is large ({len(text)} characters). "
                f"Only chunk 0 of {first_chunk_data.get('total_chunks', '?')} is returned. "
                f"Use 's3_fetch_file_chunked' for more."
            )

            return make_response(
                success=True,
                action=action,
                message=f"Fetched initial chunk of large file '{key}'.",
                data={
                    "file_id": f"{bucket}:{key}",
                    "file_name": key.split("/")[-1],
                    "bucket": bucket,
                    "key": key,
                    "file_size_bytes": file_size,
                    "last_modified": last_modified,
                    "presigned_url": presigned_url,
                    "chunk": first_chunk_data.get("chunk", ""),
                    "chunk_index": first_chunk_data.get("chunk_index", 0),
                    "total_chunks": first_chunk_data.get("total_chunks", None),
                    "note": note,
                },
            )

        # Small file: return full content
        return make_response(
            success=True,
            action=action,
            message=f"Fetched file '{key}' successfully.",
            data={
                "file_id": f"{bucket}:{key}",
                "file_name": key.split("/")[-1],
                "bucket": bucket,
                "key": key,
                "file_size_bytes": file_size,
                "last_modified": last_modified,
                "presigned_url": presigned_url,
                "chunk": text,
                "chunk_index": 0,
                "total_chunks": 1,
            },
        )

    except s3.exceptions.NoSuchKey:
        return make_response(
            success=False,
            action=action,
            message=f"❌ File not found: {key}",
            data=None,
        )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to fetch file '{key}': {str(e)}",
            data=None,
        )


@mcp.tool(name="s3_get_file_schema")
def s3_get_file_schema(metadata: dict, key: str) -> dict:
    """
    Infer and return the schema of a structured file stored in S3.

    Supports:
        - CSV: Reads header line directly.
        - JSON: Uses S3 Select to sample one document.
        - Parquet: Uses S3 Select to sample one row.

    Args:
        metadata (dict): AWS or MinIO configuration metadata used to create the S3 client.
        key (str): The S3 object key (path) for which to infer the schema.

    Returns:
        dict: Standardized response from `make_response()` with structure:
            - success (bool): Whether schema inference succeeded.
            - action (str): Tool name (`s3_get_file_schema`).
            - message (str): Status message.
            - data (dict): Schema and metadata including:
                - file_id (str): Unique identifier combining bucket and key.
                - file_name (str): The base name of the file.
                - bucket (str): The S3 bucket name.
                - key (str): Full object key.
                - file_type (str): Detected file type (csv/json/parquet/unknown).
                - presigned_url (str): Direct download URL for file (works for S3/MinIO).
                - schema (list[dict]): List of inferred columns with optional types.

    Example:
        >>> s3_get_file_schema(metadata, "data/customers.csv")
        {
            "success": True,
            "action": "s3_get_file_schema",
            "message": "Schema inferred successfully from CSV file.",
            "data": {
                "file_name": "customers.csv",
                "file_type": "csv",
                "schema": [
                    {"column": "id"},
                    {"column": "name"},
                    {"column": "email"}
                ]
            }
        }
    """
    action = "s3_get_file_schema"

    try:
        s3, bucket = get_s3_client_and_bucket(metadata)
        presigned_url = generate_presigned_url(s3, bucket, key)
        file_name = key.split("/")[-1]
        file_id = f"{bucket}:{key}"

        # --- CSV Files ---
        if key.lower().endswith(".csv"):
            try:
                response = s3.get_object(Bucket=bucket, Key=key, Range="bytes=0-2048")
                content = response["Body"].read().decode("utf-8", errors="ignore")
                header_line = content.splitlines()[0]
                reader = csv.reader([header_line])
                headers = next(reader)
                schema = [{"column": col.strip()} for col in headers]

                return make_response(
                    success=True,
                    action=action,
                    message=f"Schema inferred successfully from CSV file '{key}'.",
                    data={
                        "file_id": file_id,
                        "file_name": file_name,
                        "bucket": bucket,
                        "key": key,
                        "file_type": "csv",
                        "presigned_url": presigned_url,
                        "schema": schema,
                    },
                )

            except Exception as e:
                return make_response(
                    success=False,
                    action=action,
                    message=f"CSV schema inference failed for '{key}': {str(e)}",
                    data={
                        "file_id": file_id,
                        "file_name": file_name,
                        "bucket": bucket,
                        "key": key,
                        "file_type": "csv",
                        "presigned_url": presigned_url,
                    },
                )

        # --- JSON Files ---
        elif key.lower().endswith(".json"):
            try:
                input_serialization = {
                    "JSON": {"Type": "DOCUMENT"},
                    "CompressionType": "NONE",
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
                        raw += event["Records"]["Payload"].decode(
                            "utf-8", errors="ignore"
                        )

                record = json.loads(raw.strip().splitlines()[0])
                schema = [
                    {"column": k, "type": type(v).__name__} for k, v in record.items()
                ]

                return make_response(
                    success=True,
                    action=action,
                    message=f"Schema inferred successfully from JSON file '{key}'.",
                    data={
                        "file_id": file_id,
                        "file_name": file_name,
                        "bucket": bucket,
                        "key": key,
                        "file_type": "json",
                        "presigned_url": presigned_url,
                        "schema": schema,
                    },
                )

            except Exception as e:
                return make_response(
                    success=False,
                    action=action,
                    message=f"JSON schema inference failed for '{key}': {str(e)}",
                    data={
                        "file_id": file_id,
                        "file_name": file_name,
                        "bucket": bucket,
                        "key": key,
                        "file_type": "json",
                        "presigned_url": presigned_url,
                    },
                )

        # --- Parquet Files ---
        elif key.lower().endswith(".parquet"):
            try:
                input_serialization = {"Parquet": {}}
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
                        raw += event["Records"]["Payload"].decode(
                            "utf-8", errors="ignore"
                        )

                record = json.loads(raw.strip().splitlines()[0])
                schema = [
                    {"column": k, "type": type(v).__name__} for k, v in record.items()
                ]

                return make_response(
                    success=True,
                    action=action,
                    message=f"Schema inferred successfully from Parquet file '{key}'.",
                    data={
                        "file_id": file_id,
                        "file_name": file_name,
                        "bucket": bucket,
                        "key": key,
                        "file_type": "parquet",
                        "presigned_url": presigned_url,
                        "schema": schema,
                    },
                )

            except Exception as e:
                return make_response(
                    success=False,
                    action=action,
                    message=f"Parquet schema inference failed for '{key}': {str(e)}",
                    data={
                        "file_id": file_id,
                        "file_name": file_name,
                        "bucket": bucket,
                        "key": key,
                        "file_type": "parquet",
                        "presigned_url": presigned_url,
                    },
                )

        # --- Unsupported Format ---
        else:
            return make_response(
                success=False,
                action=action,
                message=f"Unsupported file format for key '{key}'.",
                data={
                    "file_id": file_id,
                    "file_name": file_name,
                    "bucket": bucket,
                    "key": key,
                    "file_type": "unknown",
                    "presigned_url": presigned_url,
                },
            )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"❌ Failed to infer schema for '{key}': {str(e)}",
            data={},
        )


@mcp.tool(name="s3_select_query")
def s3_select_query(
    metadata: dict,
    key: str,
    query: str = "SELECT * FROM S3Object LIMIT 5",
) -> dict:
    """
    Execute SQL-like queries on structured files stored in S3 using S3 Select.

    Supports CSV, JSON, and Parquet formats. S3 Select runs the query
    directly on the object within S3, returning filtered or sampled data
    without downloading the entire file.

    Args:
        metadata (dict): Connection metadata for S3 access.
        key (str): The S3 object key (path).
        query (str, optional): SQL-like query (default: SELECT * LIMIT 5).

    Returns:
        dict: Response via `make_response()` with:
            - success (bool)
            - message (str)
            - data:
                - file_id (str)
                - bucket (str)
                - query (str)
                - format (str)
                - rows (list or str)
                - row_count (int, optional)
    """
    action = "s3_select_query"
    s3, bucket = get_s3_client_and_bucket(metadata)
    file_format = None

    # --- Detect format ---
    if key.endswith(".csv"):
        file_format = "csv"
        input_serialization = {
            "CSV": {"FileHeaderInfo": "USE"},
            "CompressionType": "NONE",
        }
        output_serialization = {"CSV": {}}
    elif key.endswith(".json"):
        file_format = "json"
        input_serialization = {"JSON": {"Type": "DOCUMENT"}, "CompressionType": "NONE"}
        output_serialization = {"JSON": {}}
    elif key.endswith(".parquet"):
        file_format = "parquet"
        input_serialization = {"Parquet": {}}
        output_serialization = {"JSON": {}}
    else:
        return make_response(
            success=False,
            action=action,
            message=f"Unsupported file format for key: {key}",
            data={"key": key, "bucket": bucket, "query": query},
        )

    try:
        # --- Run S3 Select ---
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

        result = result.strip()

        if not result:
            return make_response(
                success=True,
                action=action,
                message="Query executed successfully, but no results found.",
                data={
                    "file_id": key,
                    "bucket": bucket,
                    "query": query,
                    "format": file_format,
                    "rows": [],
                },
            )

        # --- Parse based on format ---
        parsed_rows = []
        try:
            if file_format in ("json", "parquet"):
                parsed_rows = [
                    json.loads(line) for line in result.splitlines() if line.strip()
                ]
            elif file_format == "csv":
                reader = csv.reader(StringIO(result))
                rows = list(reader)
                if not rows:
                    parsed_rows = []
                else:
                    headers = rows[0]
                    # If headers look like data (because of FileHeaderInfo=USE), fallback
                    if any(h.isdigit() for h in headers):
                        parsed_rows = [dict(enumerate(row)) for row in rows]
                    else:
                        parsed_rows = [dict(zip(headers, row)) for row in rows[1:]]
        except Exception:
            parsed_rows = result  # fallback to raw text

        return make_response(
            success=True,
            action=action,
            message=f"Query executed successfully on {file_format.upper()} file.",
            data={
                "file_id": key,
                "bucket": bucket,
                "query": query,
                "format": file_format,
                "row_count": (
                    len(parsed_rows) if isinstance(parsed_rows, list) else None
                ),
                "rows": parsed_rows,
            },
        )

    except Exception as e:
        return make_response(
            success=False,
            action=action,
            message=f"S3 Select query failed: {str(e)}",
            data={
                "file_id": key,
                "bucket": bucket,
                "query": query,
                "format": file_format,
            },
        )


if __name__ == "__main__":
    mcp.run(transport="sse")
