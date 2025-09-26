# flake8: noqa
# -*- coding: utf-8 -*-
# vector_mcp_server.py
from mcp.server.fastmcp import FastMCP
from typing import Dict, List, Tuple, Iterator
import io, hashlib, json, os, tempfile, shutil

# Text extraction
import fitz                # PyMuPDF
from docx import Document  # python-docx

# S3
import boto3

# Google Drive
from google.oauth2.service_account import Credentials as GServiceAccountCreds
from googleapiclient.discovery import build as gbuild
from googleapiclient.http import MediaIoBaseDownload

# LangChain / FAISS
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter

# OpenAI embeddings (no extra dependency on langchain-openai)
import openai
from openai import OpenAI
client = OpenAI()
# ────────────────────────────────────────────────────────────────────────────
# MCP setup
mcp = FastMCP(name="VectorToolkit", host="0.0.0.0", port=8060)

# ────────────────────────────────────────────────────────────────────────────
# Per-session, in-memory state
session_index: FAISS | None = None
embedded_hashes: set[str] = set()            # file content hashes already indexed
file_hash_to_docids: dict[str, List[str]] = {}  # to help removal/reset if needed

# Embedding batch size (tune if needed)
EMBED_BATCH = 64

# OpenAI embeddings wrapper compatible with LangChain’s Embeddings interface
class OpenAIEmbeddingsLite:
    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None):
        self.model = model
        openai.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        # Small batches already enforced by caller; this still handles any size safely
        resp = openai.Embeddings.create(model=self.model, input=texts)
        return [d["embedding"] for d in resp["data"]]

    def embed_query(self, text: str) -> List[float]:
        resp = openai.Embeddings.create(model=self.model, input=[text])
        return resp["data"][0]["embedding"]

def get_index() -> FAISS:
    global session_index
    if session_index is None:
        session_index = FAISS.from_texts(
            texts=[],
            embedding=OpenAIEmbeddingsLite()
        )
    return session_index

# ────────────────────────────────────────────────────────────────────────────
# Helpers: S3 & GDrive clients via metadata

def get_s3_client_and_bucket(metadata: Dict) -> Tuple[boto3.client, str]:
    """
    metadata = {"s3" :{
      "access_key": "...", "secret_key": "...", "bucket": "...",
      "region": "us-east-1",                           # optional
      "endpoint_url": "http://localhost:9000"          # optional (MinIO)
    }}
    """
    meta = metadata.get("s3")
    if not meta:
        raise ValueError("Missing S3 metadata")
    access_key = meta.get("access_key")
    secret_key = meta.get("secret_key")
    bucket     = meta.get("bucket")
    region     = meta.get("region", "us-east-1")
    endpoint   = meta.get("endpoint_url")

    if not (access_key and secret_key and bucket):
        raise ValueError("Missing required S3 credentials or bucket in metadata")

    params = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region
    }
    if endpoint:
        params["endpoint_url"] = endpoint
    return boto3.client("s3", **params), bucket

def get_gdrive_service(metadata: Dict):
    """
    Supports either:
      - Service account JSON dict in metadata["service_account_info"]
      - Path to service account file in metadata["service_account_file"]
    Scopes: readonly
    """
    scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    if "service_account_info" in metadata:
        creds = GServiceAccountCreds.from_service_account_info(metadata["service_account_info"], scopes=scopes)
    elif "service_account_file" in metadata:
        creds = GServiceAccountCreds.from_service_account_file(metadata["service_account_file"], scopes=scopes)
    else:
        raise ValueError("GDrive metadata must include 'service_account_info' or 'service_account_file'")

    return gbuild("drive", "v3", credentials=creds)

# ────────────────────────────────────────────────────────────────────────────
# Helpers: hashing, chunking and streaming text extraction

def md5_hex_init():
    return hashlib.md5()

def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()

def guess_mime_from_key(key: str) -> str:
    k = key.lower()
    if k.endswith(".pdf"): return "application/pdf"
    if k.endswith(".docx"): return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if k.endswith(".txt") or k.endswith(".md") or k.endswith(".log"): return "text/plain"
    return "application/octet-stream"

def _split_with_overlap(text: str, chunk_size: int, overlap: int) -> List[str]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""]
    )
    return [c for c in splitter.split_text(text) if c.strip()]

def _yield_chunks_from_buffered_stream(
    text_iter: Iterator[str],
    chunk_size: int,
    overlap: int
) -> Iterator[str]:
    """
    Collects small pieces from a text iterator into a small rolling buffer.
    When buffer grows to ~3x chunk_size, split head into chunks and yield,
    while keeping an overlapped tail in memory. Keeps memory bounded.
    """
    buf = ""
    threshold = chunk_size * 3
    for piece in text_iter:
        if not piece:
            continue
        buf += piece
        while len(buf) >= threshold:
            head, buf = buf[:chunk_size*2], buf[chunk_size*2 - overlap:]
            for ch in _split_with_overlap(head, chunk_size, overlap):
                yield ch
    if buf:
        for ch in _split_with_overlap(buf, chunk_size, overlap):
            yield ch

# ── Parsers that stream from local file paths (disk, not RAM) ───────────────

def iter_pdf_text(path: str) -> Iterator[str]:
    with fitz.open(path) as doc:
        for p in doc:
            yield p.get_text()

def iter_docx_text(path: str) -> Iterator[str]:
    # python-docx needs full file, but we open from disk (not memory)
    doc = Document(path)
    for para in doc.paragraphs:
        if para.text:
            yield para.text + "\n"

def iter_text_lines(path: str, encoding: str = "utf-8") -> Iterator[str]:
    with open(path, "r", encoding=encoding, errors="ignore") as f:
        for line in f:
            yield line

# ── Downloaders that avoid holding the whole file in memory ─────────────────

def s3_download_to_temp_and_hash(metadata: Dict, key: str, chunk_bytes: int = 8 * 1024 * 1024) -> Tuple[str, str, int]:
    """
    Stream-download an S3 object to a temp file, computing md5 as we go.
    Returns (temp_path, file_hash, size_bytes).
    """
    s3, bucket = get_s3_client_and_bucket(metadata)
    obj = s3.get_object(Bucket=bucket, Key=key)
    body = obj["Body"]

    hasher = md5_hex_init()
    size = 0
    fd, path = tempfile.mkstemp(prefix="mcp_vec_", suffix="." + key.split(".")[-1].lower())
    os.close(fd)  # we'll reopen by path
    try:
        with open(path, "wb") as out:
            for chunk in body.iter_chunks(chunk_size=chunk_bytes):
                if not chunk:
                    continue
                out.write(chunk)
                hasher.update(chunk)
                size += len(chunk)
    except Exception:
        # ensure cleanup on failure
        try:
            os.remove(path)
        except Exception:
            pass
        raise
    return path, hasher.hexdigest(), size

def gdrive_download_to_temp_and_hash(service, file_id: str, file_name_hint: str | None = None) -> Tuple[str, str, int]:
    """
    Stream-download a Drive file to temp while hashing. Returns (path, md5, size).
    """
    request = service.files().get_media(fileId=file_id)
    fh = tempfile.NamedTemporaryFile(delete=False, prefix="mcp_vec_", suffix=("." + (file_name_hint or "bin")))
    hasher = md5_hex_init()
    size = 0
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    try:
        while not done:
            status, done = downloader.next_chunk()
            # read the bytes that were written by downloader since last chunk
            # We can't directly intercept; instead, flush & read from file handle's buffer is not ideal.
            # Simpler: we'll compute hash after download by reading file once (still disk, not RAM).
        fh.flush()
        fh.close()
        # Compute hash by streaming from disk
        with open(fh.name, "rb") as f:
            for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
                hasher.update(chunk)
                size += len(chunk)
    except Exception:
        try:
            os.unlink(fh.name)
        except Exception:
            pass
        raise
    return fh.name, hasher.hexdigest(), size

# ── Indexing helpers ────────────────────────────────────────────────────────

def add_chunks_streaming_to_index(
    chunk_iter: Iterator[str],
    base_meta: Dict,
    file_hash: str,
    batch_size: int = EMBED_BATCH
) -> int:
    """
    Consume chunk iterator, embed and add to FAISS in small batches.
    Returns number of chunks added.
    """
    idx = get_index()
    texts_batch: List[str] = []
    metas_batch: List[Dict] = []
    total = 0

    def flush():
        nonlocal texts_batch, metas_batch, total
        if not texts_batch:
            return
        idx.add_texts(texts=texts_batch, metadatas=metas_batch)  # calls embeddings under the hood
        total += len(texts_batch)
        texts_batch = []
        metas_batch = []

    next_chunk_index = 0
    for ch in chunk_iter:
        texts_batch.append(ch)
        metas_batch.append({**base_meta, "chunk_index": next_chunk_index})
        next_chunk_index += 1
        if len(texts_batch) >= batch_size:
            flush()
    flush()
    # Track docids per file hash (lightweight IDs)
    file_hash_to_docids[file_hash] = [f"{file_hash}:{i}" for i in range(total)]
    return total

def make_chunk_iter_for_path(path: str, key_or_name: str, chunk_size: int, overlap: int) -> Iterator[str]:
    lower = key_or_name.lower()
    if lower.endswith(".pdf"):
        # Page-by-page → buffered chunker
        return _yield_chunks_from_buffered_stream(iter_pdf_text(path), chunk_size, overlap)
    elif lower.endswith(".docx"):
        return _yield_chunks_from_buffered_stream(iter_docx_text(path), chunk_size, overlap)
    else:
        # txt / md / log / generic utf-8
        return _yield_chunks_from_buffered_stream(iter_text_lines(path), chunk_size, overlap)

# ────────────────────────────────────────────────────────────────────────────
# Tools

@mcp.tool(name="vector_reset_index")
def vector_reset_index() -> dict:
    """Clears the in-memory FAISS index and de-dup state."""
    global session_index, embedded_hashes, file_hash_to_docids
    session_index = None
    embedded_hashes = set()
    file_hash_to_docids = {}
    return {"status": "reset"}

@mcp.tool(name="vector_status")
def vector_status() -> dict:
    """Returns simple counters for the current in-memory index."""
    idx = get_index()
    return {
        "docs": getattr(idx, "index", None).ntotal if getattr(idx, "index", None) else 0,
        "files_ingested": len(embedded_hashes),
        "file_hashes": list(embedded_hashes),
    }

def safe_extract_text_from_pdf(file_bytes: bytes) -> str:
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        texts = []
        for page in doc:
            texts.append(page.get_text("text"))
        doc.close()
        full_text = "\n".join([t for t in texts if t.strip()])
        return full_text
    except Exception as e:
        return ""


def vector_ingest_s3(key: str, max_chunk_size=1200, overlap=200, embed_batch=64):
    # ensure args are ints
    max_chunk_size = int(max_chunk_size)
    overlap = int(overlap)
    embed_batch = int(embed_batch)

    # fetch from S3
    obj = s3.get_object(Bucket=bucket, Key=key)
    file_bytes = obj["Body"].read()

    # detect type
    if key.lower().endswith(".pdf"):
        text = safe_extract_text_from_pdf(file_bytes)
    elif key.lower().endswith(".docx"):
        doc = Document(io.BytesIO(file_bytes))
        text = "\n".join([p.text for p in doc.paragraphs if p.text.strip()])
    else:
        text = file_bytes.decode("utf-8", errors="ignore")

    if not text.strip():
        return {"status": "error", "message": f"No extractable text found in {key}"}

    # split into chunks
    splitter = RecursiveCharacterTextSplitter(chunk_size=max_chunk_size, chunk_overlap=overlap)
    chunks = splitter.split_text(text)
    if not chunks:
        return {"status": "error", "message": f"No chunks generated from {key}"}

    # batch embeddings
    embeddings = []
    for i in range(0, len(chunks), embed_batch):
        batch = chunks[i : i + embed_batch]
        response = client.embeddings.create(model="text-embedding-3-small", input=batch)
        batch_vectors = [d.embedding for d in response.data]
        embeddings.extend(batch_vectors)

    # insert into FAISS
    vectors = [
        (str(hashlib.md5(c.encode()).hexdigest()), e, {"text": c, "source": key})
        for c, e in zip(chunks, embeddings)
    ]
    session_index.add(vectors)

    return {"status": "success", "chunks": len(chunks), "source": key}


@mcp.tool(name="vector_ingest_gdrive")
def vector_ingest_gdrive(
    metadata: dict,
    file_id: str,
    max_chunk_size: int = 1200,
    overlap: int = 200,
    embed_batch: int = EMBED_BATCH
) -> dict:
    """
    Ingest a Google Drive file (by fileId) into the in-memory FAISS index.
    Streaming to temp + incremental parsing & batching like S3.
    """
    temp_path = None
    try:
        svc = get_gdrive_service(metadata)
        # metadata fetch (name/mime is optional for path-based parsers, but helpful for suffix)
        meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,size").execute()
        name = meta.get("name", file_id)
        suffix = "." + (name.split(".")[-1].lower() if "." in name else "bin")

        temp_path, file_hash, size_bytes = gdrive_download_to_temp_and_hash(svc, file_id, file_name_hint=suffix.lstrip("."))

        if file_hash in embedded_hashes:
            try:
                os.remove(temp_path)
            except Exception:
                pass
            return {"status": "already_ingested", "file_hash": file_hash, "size_bytes": size_bytes}

        base_meta = {
            "source": name,
            "provider": "gdrive",
            "mimeType": meta.get("mimeType"),
            "size_bytes": size_bytes,
            "gdrive_id": file_id,
            "file_hash": file_hash
        }
        chunk_iter = make_chunk_iter_for_path(temp_path, name, max_chunk_size, overlap)
        added = add_chunks_streaming_to_index(chunk_iter, base_meta, file_hash, batch_size=embed_batch)

        embedded_hashes.add(file_hash)
        try:
            os.remove(temp_path)
        except Exception:
            pass

        if added == 0:
            return {"status": "empty", "file_hash": file_hash, "size_bytes": size_bytes}
        return {"status": "ingested", "file_hash": file_hash, "size_bytes": size_bytes, "chunks": added}

    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return {"status": "error", "message": str(e)}

@mcp.tool(name="vector_ingest_text")
def vector_ingest_text(
    doc_id: str,
    text: str,
    max_chunk_size: int = 1200,
    overlap: int = 200,
    embed_batch: int = EMBED_BATCH
) -> dict:
    """
    Ingest raw text into the in-memory FAISS index with streaming chunking + batched embedding.
    """
    # Hash the provided text (dedup)
    raw = text.encode("utf-8", errors="ignore")
    file_hash = md5_bytes(raw)
    if file_hash in embedded_hashes:
        return {"status": "already_ingested", "file_hash": file_hash, "size_bytes": len(raw)}

    def text_iter() -> Iterator[str]:
        # yield moderately sized pieces to the buffered chunker
        stream = io.StringIO(text)
        for line in stream:
            yield line

    chunk_iter = _yield_chunks_from_buffered_stream(text_iter(), max_chunk_size, overlap)
    base_meta = {"source": doc_id, "provider": "raw", "file_hash": file_hash, "size_bytes": len(raw)}
    added = add_chunks_streaming_to_index(chunk_iter, base_meta, file_hash, batch_size=embed_batch)

    embedded_hashes.add(file_hash)
    if added == 0:
        return {"status": "empty", "file_hash": file_hash, "size_bytes": len(raw)}
    return {"status": "ingested", "file_hash": file_hash, "size_bytes": len(raw), "chunks": added}

@mcp.tool(name="vector_query")
def vector_query(question: str, top_k: int = 5) -> str:
    """
    Semantic search over the in-memory FAISS index.
    Returns the top_k matches with metadata and content snippets.
    """
    idx = get_index()
    if getattr(idx, "index", None) is None or idx.index.ntotal == 0:
        return json.dumps({"error": "index_empty"})

    docs = idx.similarity_search(question, k=max(1, top_k))
    out = []
    for d in docs:
        out.append({
            "text": d.page_content[:800],  # clip to keep payload small
            "metadata": d.metadata
        })
    return json.dumps(out)

# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
