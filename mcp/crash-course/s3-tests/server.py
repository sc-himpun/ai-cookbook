# -*- coding: utf-8 -*-
# s3_mcp_server.py
from mcp.server.fastmcp import FastMCP
import boto3, io, json, hashlib
import fitz  # PyMuPDF
from docx import Document
from typing import Dict, Tuple

mcp = FastMCP(name="S3Toolkit", host="0.0.0.0", port=8059)

def get_s3_client_and_bucket(metadata: Dict) -> Tuple[boto3.client, str]:
    if not metadata:
        raise Exception("Missing metadata")
    access_key = metadata.get("access_key")
    secret_key = metadata.get("secret_key")
    bucket     = metadata.get("bucket")
    region     = metadata.get("region", "us-east-1")
    endpoint   = metadata.get("endpoint_url")
    if not (access_key and secret_key and bucket):
        raise Exception("Missing required S3 credentials or bucket")

    kwargs = dict(aws_access_key_id=access_key,
                  aws_secret_access_key=secret_key,
                  region_name=region)
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    return boto3.client("s3", **kwargs), bucket

def _is_pdf(key:str):  return key.lower().endswith(".pdf")
def _is_docx(key:str): return key.lower().endswith(".docx")

def chunk_text(s: str, max_chars: int = 3000, overlap: int = 200):
    if max_chars <= overlap:
        overlap = 0
    out, i, n = [], 0, len(s)
    while i < n:
        j = min(i + max_chars, n)
        out.append(s[i:j])
        if j == n: break
        i = j - overlap
        if i < 0: i = 0
    return out

@mcp.tool(name="s3_list_files")
def list_files_in_s3(metadata: dict, prefix: str = "", max_keys: int = 1000) -> str:
    """List keys (recurses prefixes)."""
    s3, bucket = get_s3_client_and_bucket(metadata)
    results = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                results.append({
                    "key": obj["Key"],
                    "size_bytes": obj["Size"],
                    "last_modified": obj["LastModified"].isoformat()
                })
                if len(results) >= max_keys:
                    return json.dumps(results)
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": str(e)})

# @mcp.tool(name="s3_count_chunks")
# def s3_count_chunks(metadata: dict, key: str, max_chars: int = 3000, overlap: int = 200) -> str:
#     """
#     Return an estimate of how many text chunks this object would produce.
#     For PDFs, counts text length across pages; for DOCX/TXT reads once then counts.
#     """
#     s3, bucket = get_s3_client_and_bucket(metadata)
#     body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

#     if _is_pdf(key):
#         doc = fitz.open(stream=body, filetype="pdf")
#         total_chars = 0
#         for page in doc:
#             total_chars += len(page.get_text())
#         doc.close()
#         eff = max(1, max_chars - overlap)
#         total = (total_chars + eff - 1) // eff
#         return json.dumps({"key": key, "total_chunks": total})

#     if _is_docx(key):
#         text = "\n".join(p.text for p in Document(io.BytesIO(body)).paragraphs)
#     else:
#         text = body.decode("utf-8", errors="ignore")

#     total = len(chunk_text(text, max_chars, overlap))
#     return json.dumps({"key": key, "total_chunks": total})

# @mcp.tool(name="s3_get_chunk")
# def s3_get_chunk(metadata: dict, key: str, index: int,
#                  max_chars: int = 3000, overlap: int = 200) -> str:
#     """
#     Return a single chunk (by index). Keeps memory low and only returns that chunk.
#     """
#     s3, bucket = get_s3_client_and_bucket(metadata)
#     body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()

#     if _is_pdf(key):
#         # Two-pass: total length then extract window
#         doc = fitz.open(stream=body, filetype="pdf")
#         eff = max(1, max_chars - overlap)

#         # Count total chars
#         total_chars = 0
#         for p in doc:
#             total_chars += len(p.get_text())
#         total_chunks = (total_chars + eff - 1) // eff

#         start = index * eff
#         if start >= total_chars:
#             doc.close()
#             return json.dumps({"key": key, "index": index, "chunk": "", "total_chunks": total_chunks})

#         # Extract only the window
#         taken, emitted = 0, []
#         seen = 0
#         doc2 = fitz.open(stream=body, filetype="pdf")
#         for p in doc2:
#             t = p.get_text()
#             n = len(t)
#             if seen + n <= start:
#                 seen += n
#                 continue
#             # slice needed part of this page
#             s = max(0, start - seen)
#             needed = max_chars - taken
#             piece = t[s:s+needed]
#             emitted.append(piece)
#             taken += len(piece)
#             seen += n
#             if taken >= max_chars:
#                 break
#         doc.close(); doc2.close()

#         chunk = "".join(emitted)
#         return json.dumps({
#             "key": key, "index": index, "chunk": chunk,
#             "total_chunks": total_chunks,
#             "chunk_sha": hashlib.sha256(chunk.encode("utf-8")).hexdigest()
#         })

#     # DOCX / TXT
#     if _is_docx(key):
#         text = "\n".join(p.text for p in Document(io.BytesIO(body)).paragraphs)
#     else:
#         text = body.decode("utf-8", errors="ignore")

#     chunks = chunk_text(text, max_chars, overlap)
#     total_chunks = len(chunks)
#     chunk = chunks[index] if 0 <= index < total_chunks else ""
#     return json.dumps({
#         "key": key, "index": index, "chunk": chunk,
#         "total_chunks": total_chunks,
#         "chunk_sha": hashlib.sha256(chunk.encode("utf-8")).hexdigest()
#     })

if __name__ == "__main__":
    mcp.run(transport="sse")
