# -*- coding: utf-8 -*-
# from mcp.server.fastmcp import FastMCP
from fastmcp import FastMCP, Context
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse
from typing import Dict, List, Tuple, Iterable, Optional
import hashlib
import os
import json
import tempfile
from google.oauth2.credentials import Credentials

# Text extraction
import fitz  # PyMuPDF
from docx import Document  # python-docx

from google.auth.transport.requests import Request as GoogleRequest

# S3
import boto3
from botocore.response import StreamingBody

# Google Drive
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.discovery import build

# LangChain / FAISS
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter

# OpenAI embeddings
import requests
from langchain_openai import OpenAIEmbeddings
from langchain_community.embeddings import HuggingFaceEmbeddings
from dotenv import load_dotenv
from huggingface_hub import login

# from mcp.server.fastmcp import Context
import traceback

load_dotenv()
GDRIVE_CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
GDRIVE_CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")

# MCP setup
VECTOR_MCP_PORT = int(os.getenv("VECTOR_MCP_PORT", "8060"))
EMBEDDING_PROVIDER = os.getenv("VECTORMCP_EMBEDDING_PROVIDER", "huggingface")
EMBEDDING_MODEL = os.getenv(
    "VECTORMCP_EMBEDDING_MODEL", "intfloat/multilingual-e5-small"
)

mcp = FastMCP(name="VectorToolkit")
# mcp = FastMCP(name="VectorToolkit", host="0.0.0.0", port=VECTOR_MCP_PORT)

hf_token = os.getenv("HUGGINGFACE_HUB_TOKEN")
login(hf_token)


def get_embedding_backend(
    provider: str = "huggingface", model_name: str = "intfloat/multilingual-e5-small"
):
    """
    Returns a LangChain-compatible embedding model.
    provider: "huggingface" | "openai"
    model_name: override the default model for provider
    """
    if provider == "openai":
        return OpenAIEmbeddings(model=model_name or "text-embedding-3-small")
    elif provider == "huggingface":
        print(f"Using HuggingFace embedding model: {model_name}")
        return HuggingFaceEmbeddings(
            model_name=model_name,
            model_kwargs={"device": "cpu"},  # or "cuda" for GPU
            encode_kwargs={"normalize_embeddings": True},
        )
    else:
        raise ValueError(f"Unsupported embedding provider: {provider}")


embedding = get_embedding_backend(EMBEDDING_PROVIDER, EMBEDDING_MODEL)


# ────────────────────────────────────────────────────────────────────────────
# Per-session, in-memory state
session_index: FAISS | None = None
embedded_hashes: set[str] = set()  # file content hashes already indexed
file_hash_to_docids: dict[str, List[str]] = {}  # to help removal/reset if needed


def get_index() -> FAISS:
    """
    Get or initialize the in-memory FAISS index for vector search.

    Returns:
        FAISS: The current FAISS index instance.

    Example:
        idx = get_index()
    """
    global session_index
    if session_index is None:
        dummy_text = " "  # ensures FAISS has at least one vector
        session_index = FAISS.from_texts([dummy_text], embedding)
    return session_index


# ────────────────────────────────────────────────────────────────────────────
# Helpers: S3, GDrive, box etc clients via metadata


def get_s3_client_and_bucket(metadata: Dict) -> Tuple[boto3.client, str]:
    """
    Get a boto3 S3 client and bucket name from metadata.

    Args:
        metadata (Dict): Metadata dict containing S3 credentials and config.

    Returns:
        Tuple[boto3.client, str]: S3 client and bucket name.

    Example:
        client, bucket = get_s3_client_and_bucket(metadata)

    metadata example:
    metadata = {"s3" :{
      "access_key": "...", "secret_key": "...", "bucket": "...",
      "region": "us-east-1",                           # optional
      "endpoint_url": "http://localhost:9000"          # optional (MinIO)
    }}
    """
    md = metadata.get("s3") if metadata else None
    if not md:
        raise ValueError("Missing S3 metadata")
    access_key = md.get("access_key")
    secret_key = md.get("secret_key")
    bucket = md.get("bucket")
    region = md.get("region", "us-east-1")
    endpoint = md.get("endpoint_url")

    if not (access_key and secret_key and bucket):
        raise ValueError("Missing required S3 credentials or bucket in metadata")

    params = {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
        "region_name": region,
    }
    if endpoint:
        params["endpoint_url"] = endpoint
    return boto3.client("s3", **params), bucket


def get_google_creds(metadata: Optional[Dict]) -> Optional[Credentials]:
    """
    Extract and refresh Google credentials from metadata.

    Args:
        metadata (Optional[Dict]): Dict with 'gdrive' key containing 'access_token', 'refresh_token', etc.

    Returns:
        Credentials or None: Google OAuth2 credentials object or None if missing.

    Example:
        creds = get_google_creds(metadata)
    """
    metadatag = metadata.get("gdrive") if metadata else None
    if not metadatag:
        return None

    print("DEBUG: GDrive metadata:", metadatag)
    access_token = metadatag.get("access_token")
    refresh_token = metadatag.get("refresh_token")
    token_uri = "https://oauth2.googleapis.com/token"

    if not access_token:
        return None

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token if refresh_token else None,
        client_id=GDRIVE_CLIENT_ID,
        client_secret=GDRIVE_CLIENT_SECRET,
        token_uri=token_uri,
    )

    # Refresh if expired and refresh_token is available
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
        except Exception as e:
            print(f"🔁 Failed to refresh token: {e}")
            return None

    return creds


def get_drive_service(metadata: Dict):
    """
    Create a Google Drive API service using credentials from metadata.

    Args:
        metadata (Dict): Metadata dict containing Google credentials.

    Returns:
        googleapiclient.discovery.Resource: Google Drive API service object.
    """
    creds = get_google_creds(metadata)
    if not creds:
        raise ValueError("Could not get valid Google credentials from metadata.")
    return build("drive", "v3", credentials=creds)


def get_box_file_to_tempfile(metadata: Dict, file_id: str) -> Tuple[str, dict]:
    """
    Download a Box file to a temp file using metadata access_token.

    Args:
        metadata (Dict): Metadata dict with Box access token.
        file_id (str): Box file ID.

    Returns:
        Tuple[str, dict]: Path to temp file and file metadata dict.

    Example:
        tmp_path, meta = get_box_file_to_tempfile(metadata, file_id)
    """
    md = metadata.get("box") if metadata else None
    if not md:
        raise ValueError("Missing Box metadata")
    token = md.get("access_token")
    if not token:
        raise ValueError("Missing Box access_token in metadata")

    headers = {"Authorization": f"Bearer {token}"}
    # First get metadata (name, size, etc.)
    meta_url = f"https://api.box.com/2.0/files/{file_id}"
    r = requests.get(meta_url, headers=headers)
    r.raise_for_status()
    file_meta = r.json()
    file_name = file_meta.get("name", file_id)

    # Download to temp file
    url = f"https://api.box.com/2.0/files/{file_id}/content"
    r = requests.get(url, headers=headers, stream=True)
    r.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(delete=False)
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            tmp.write(chunk)
    tmp.flush()
    tmp.close()
    return tmp.name, {"file_name": file_name, **file_meta}


# ────────────────────────────────────────────────────────────────────────────
# Helpers: hashing, chunking and streaming text extraction


def md5_stream(fobj, chunk_size: int = 1024 * 1024) -> str:
    """
    Compute MD5 hash for a file-like object (seeked to start), streaming.

    Args:
        fobj: File-like object opened in binary mode.
        chunk_size (int): Size of chunks to read at a time (default: 1MB).

    Returns:
        str: MD5 hex digest of the file contents.

    Example:
        with open('file.pdf', 'rb') as f:
            hash = md5_stream(f)
    """
    h = hashlib.md5()
    while True:
        b = fobj.read(chunk_size)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def stream_s3_to_tempfile(body: StreamingBody) -> str:
    """
    Write S3 StreamingBody to a temp file (disk-backed) to avoid large memory use.

    Args:
        body (StreamingBody): S3 streaming body object.

    Returns:
        str: Path to the temporary file containing the streamed data.
    """
    tmp = tempfile.NamedTemporaryFile(delete=False)
    try:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            if chunk:
                tmp.write(chunk)
        tmp.flush()
        tmp.close()
        return tmp.name
    except Exception as e:
        try:
            path = tmp.name
            tmp.close()
            print("Exception occured on processing file :", path)
            print("Exception: ", str(e))
        finally:
            raise


def stream_pdf_chunks_from_path(
    path: str, max_chunk_chars: int, overlap: int
) -> Iterable[str]:
    """
    Yield text chunks from a PDF file on disk using incremental buffer and overlap split.

    Args:
        path (str): Path to the PDF file.
        max_chunk_chars (int): Maximum characters per chunk.
        overlap (int): Number of overlapping characters between chunks.

    Yields:
        str: Text chunk from the PDF.
    """
    doc = fitz.open(path)
    try:
        buffer = ""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=max_chunk_chars,
            chunk_overlap=overlap,
            separators=["\n\n", "\n", " ", ""],
        )
        for page in doc:
            page_text = page.get_text("text") or ""
            if page_text:
                buffer += page_text
                # flush when buffer grows
                if len(buffer) >= max_chunk_chars * 2:
                    for c in splitter.split_text(buffer):
                        if c.strip():
                            yield c
                    buffer = ""
        if buffer:
            for c in splitter.split_text(buffer):
                if c.strip():
                    yield c
    finally:
        doc.close()


def stream_docx_chunks_from_path(
    path: str, max_chunk_chars: int, overlap: int
) -> Iterable[str]:
    """
    Yield text chunks from a DOCX file by reading paragraphs and splitting incrementally.

    Args:
        path (str): Path to the DOCX file.
        max_chunk_chars (int): Maximum characters per chunk.
        overlap (int): Number of overlapping characters between chunks.

    Yields:
        str: Text chunk from the DOCX file.
    """
    doc = Document(path)
    buffer = ""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chunk_chars,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    for para in doc.paragraphs:
        t = para.text or ""
        if t:
            buffer += t + "\n"
            if len(buffer) >= max_chunk_chars * 2:
                for c in splitter.split_text(buffer):
                    if c.strip():
                        yield c
                buffer = ""
    if buffer:
        for c in splitter.split_text(buffer):
            if c.strip():
                yield c


def stream_text_chunks_iter_lines(
    body: StreamingBody, max_chunk_chars: int, overlap: int, encoding: str = "utf-8"
) -> Iterable[str]:
    """
    Yield text chunks from a text file on S3 by iterating lines; avoids loading full file in memory.

    Args:
        body (StreamingBody): S3 streaming body object.
        max_chunk_chars (int): Maximum characters per chunk.
        overlap (int): Number of overlapping characters between chunks.
        encoding (str): Text encoding (default: 'utf-8').

    Yields:
        str: Text chunk from the file.
    """
    buffer = ""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chunk_chars,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""],
    )
    for raw_line in body.iter_lines():
        try:
            line = raw_line.decode(encoding, errors="ignore")
        except Exception:
            line = ""
        buffer += line + "\n"
        if len(buffer) >= max_chunk_chars * 2:
            for c in splitter.split_text(buffer):
                if c.strip():
                    yield c
            buffer = ""
    if buffer:
        for c in splitter.split_text(buffer):
            if c.strip():
                yield c


def guess_mime_from_key(key: str) -> str:
    """
    Guess MIME type from file key/extension.

    Args:
        key (str): File name or key.

    Returns:
        str: Guessed MIME type string.
    """
    k = key.lower()
    if k.endswith(".pdf"):
        return "application/pdf"
    if k.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if k.endswith(".txt") or k.endswith(".md") or k.endswith(".log"):
        return "text/plain"
    return "application/octet-stream"


# ────────────────────────────────────────────────────────────────────────────
# Core incremental insert helper


def add_texts_batch(texts: List[str], metas: List[dict]) -> None:
    """
    Embed and insert a small batch of texts into FAISS to keep memory usage low.

    Args:
        texts (List[str]): List of text chunks to embed.
        metas (List[dict]): List of metadata dicts for each chunk.

    Returns:
        None

    Example:
        add_texts_batch(["text1", "text2"], [{...}, {...}])
    """
    print(f"DEBUG: add_texts_batch got {len(texts)} texts")
    if not texts:
        print("WARNING: No texts passed to add_texts_batch, skipping")
        return
    idx = get_index()
    # FAISS.add_texts will call our embeddings wrapper; we pass only a small batch.
    idx.add_texts(texts=texts, metadatas=metas)


def reset_index():
    """Clears the in-memory FAISS index and de-dup state."""
    global session_index, embedded_hashes, file_hash_to_docids
    session_index = None
    embedded_hashes = set()
    file_hash_to_docids = {}
    return {"status": "reset"}


def _vector_reset_index(req: Request) -> JSONResponse:
    return JSONResponse(reset_index())


@mcp.tool(name="vector_reset_index")
def vector_reset_index(metadata: dict) -> dict:
    return reset_index()

# ────────────────────────────────────────────────────────────────────────────
# Tools


def _get_vector_status():
    idx = get_index()
    return {
        "docs": (
            getattr(idx, "index", None).ntotal if getattr(idx, "index", None) else 0 # type: ignore
        ),
        "files_ingested": len(embedded_hashes),
        "file_hashes": list(embedded_hashes),
    }


def _vector_status(req: Request) -> JSONResponse:
    _ = req
    resp = _get_vector_status()
    return JSONResponse(resp)


@mcp.tool(name="vector_status")
def vector_status(metadata: Dict) -> dict:
    """Returns simple counters for the current in-memory index."""
    _ = metadata
    return _get_vector_status()

# ────────────────────────────────────────────────────────────────────────────
# S3 ingest (streaming, low-memory, incremental embedding)


@mcp.tool(name="vector_ingest_s3")
async def vector_ingest_s3(  # noqa: C901
    context: Context,
    metadata: dict,
    key: str,
    max_chunk_size: int = 1200,
    overlap: int = 200,
    embed_batch: int = 32,
) -> dict:
    """
    Async ingestion of an S3 object into FAISS with periodic progress updates
    sent to the client via `await context.info(...)` and
    `await context.report_progress(progress, total)`.
    """
    try:
        # Cast inputs
        max_chunk_size = int(max_chunk_size)
        overlap = int(overlap)
        embed_batch = int(embed_batch)
        if overlap >= max_chunk_size:
            return {
                "status": "error",
                "message": f"overlap ({overlap}) must be < max_chunk_size ({max_chunk_size})",
            }

        await context.info(f"📦 Fetching S3 object: {key}")
        s3, bucket = get_s3_client_and_bucket(metadata)
        obj = s3.get_object(Bucket=bucket, Key=key)
        body: StreamingBody = obj["Body"]

        # Save to temp file (disk-backed)
        tmp_path = stream_s3_to_tempfile(body)
        await context.info("✅ Download complete, computing MD5 hash...")

        # Compute MD5 for de-duplication
        with open(tmp_path, "rb") as f:
            file_hash = md5_stream(f)

        if file_hash in embedded_hashes:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            await context.info("⚡ File already ingested — skipping.")
            return {"status": "already_ingested", "file_hash": file_hash}

        mime = guess_mime_from_key(key)
        await context.info(f"📄 Processing file as MIME type: {mime}")

        metabase = {
            "source": key,
            "bucket": bucket,
            "provider": "s3",
            "file_hash": file_hash,
        }

        # Rolling batch container
        batch_texts: List[str] = []
        batch_metas: List[dict] = []
        total_chunks = 0

        def flush_batch():
            nonlocal batch_texts, batch_metas, total_chunks
            if not batch_texts:
                return
            # Filter empty texts
            filtered = [(t, m) for t, m in zip(batch_texts, batch_metas) if t.strip()]
            if filtered:
                texts, metas = zip(*filtered)
                add_texts_batch(list(texts), list(metas))
                total_chunks += len(texts)
            # clear batch
            batch_texts, batch_metas = [], []

        chunk_idx = 0

        # Choose iterator based on MIME
        if mime == "application/pdf":
            iterator = stream_pdf_chunks_from_path(tmp_path, max_chunk_size, overlap)
        elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            iterator = stream_docx_chunks_from_path(tmp_path, max_chunk_size, overlap)
        else:
            # For text-like files we re-open streaming body and iterate lines
            obj2 = s3.get_object(Bucket=bucket, Key=key)
            body2: StreamingBody = obj2["Body"]
            iterator = stream_text_chunks_iter_lines(body2, max_chunk_size, overlap, encoding="utf-8")

        # Process chunks incrementally
        # We'll report progress after each flush; total unknown until done (pass total=None)
        for chunk in iterator:
            if not chunk.strip():
                continue
            batch_texts.append(chunk)
            batch_metas.append({**metabase, "chunk_index": chunk_idx})
            chunk_idx += 1
            if len(batch_texts) >= embed_batch:
                flush_batch()
                # report progress (progress = total_chunks so far)
                try:
                    await context.report_progress(progress=total_chunks, total=None)
                except Exception:
                    # don't break ingestion if context method fails
                    await context.info(f"[progress] Embedded {total_chunks} chunks so far (report_progress failed).")
        # final flush
        flush_batch()

        # finalize
        embedded_hashes.add(file_hash)
        file_hash_to_docids[file_hash] = [f"{file_hash}:{i}" for i in range(total_chunks)]

        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if total_chunks == 0:
            await context.info("⚠️ Completed but no chunks extracted from file.")
            # report final progress = 0
            try:
                await context.report_progress(progress=0, total=0)
            except Exception:
                pass
            return {"status": "empty", "file_hash": file_hash}

        # final progress with total known
        try:
            await context.report_progress(progress=total_chunks, total=total_chunks)
        except Exception:
            await context.info(f"[progress] Completed embedding {total_chunks} chunks (report_progress failed).")

        await context.info(f"✅ Finished ingestion — {total_chunks} chunks embedded.")
        return {"status": "ingested", "file_hash": file_hash, "chunks": total_chunks}

    except Exception as e:
        # log server-side traceback and send info to client before returning error
        traceback.print_exc()
        try:
            await context.info(f"❌ Error during ingestion: {str(e)}")
        except Exception:
            pass
        return {"status": "error", "message": str(e)}


# ────────────────────────────────────────────────────────────────────────────
# Google Drive ingest


@mcp.tool(name="vector_ingest_gdrive")
def vector_ingest_gdrive(  # noqa: C901
    metadata: dict,
    file_id: str,
    max_chunk_size: int = 1200,
    overlap: int = 200,
    embed_batch: int = 32,
    file_name: Optional[str] = None,
    url: Optional[str] = None,
) -> dict:
    """
    Ingest a single Google Drive file into FAISS using disk-backed streaming and incremental chunk → embed → insert.
    Handles empty/invalid files gracefully without crashing.

    Args:
        metadata (dict): Metadata with Google credentials.
        file_id (str): Google Drive file ID.
        max_chunk_size (int): Max characters per chunk (default: 1200).
        overlap (int): Overlap between chunks (default: 200).
        embed_batch (int): Batch size for embedding (default: 32).
        file_name (Optional[str]): Optional file name override.
        url (Optional[str]): Optional file URL.

    Returns:
        dict: Status and chunk info.

    Example:
        vector_ingest_gdrive(metadata, file_id)
    """
    max_chunk_size = int(max_chunk_size)
    overlap = int(overlap)
    embed_batch = int(embed_batch)
    if overlap >= max_chunk_size:
        return {
            "status": "error",
            "message": f"overlap ({overlap}) must be < max_chunk_size ({max_chunk_size})",
        }

    try:

        svc = get_drive_service(metadata)

        # Get metadata (name & mime)
        meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,size").execute()
        file_name = meta.get("name", file_id) if not file_name else file_name
        mimeType = meta.get("mimeType", "")
        # Download to temp file
        request = svc.files().get_media(fileId=file_id)
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = tmp.name
        downloader = MediaIoBaseDownload(tmp, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        tmp.flush()
        tmp.close()

        # Hash
        with open(tmp_path, "rb") as f:
            file_hash = md5_stream(f)
        if file_hash in embedded_hashes:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
            return {"status": "already_ingested", "file_hash": file_hash}

        metabase = {
            "source": file_name,
            "gdrive_id": file_id,
            "provider": "gdrive",
            "mimeType": mimeType,
            "file_hash": file_hash,
            "url": url or f"https://drive.google.com/file/d/{file_id}/view",
        }

        batch_texts: List[str] = []
        batch_metas: List[dict] = []
        total_chunks = 0

        def flush_batch():
            nonlocal batch_texts, batch_metas, total_chunks
            if batch_texts:
                add_texts_batch(batch_texts, batch_metas)
                total_chunks += len(batch_texts)
                batch_texts, batch_metas = [], []

        # Determine by extension for parsing
        mime = guess_mime_from_key(file_name)

        if mime == "application/pdf":
            chunk_idx = 0
            for chunk in stream_pdf_chunks_from_path(tmp_path, max_chunk_size, overlap):
                batch_texts.append(chunk)
                batch_metas.append({**metabase, "chunk_index": chunk_idx})
                chunk_idx += 1
                if len(batch_texts) >= embed_batch:
                    flush_batch()
            flush_batch()

        elif (
            mime
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            chunk_idx = 0
            for chunk in stream_docx_chunks_from_path(
                tmp_path, max_chunk_size, overlap
            ):
                batch_texts.append(chunk)
                batch_metas.append({**metabase, "chunk_index": chunk_idx})
                chunk_idx += 1
                if len(batch_texts) >= embed_batch:
                    flush_batch()
            flush_batch()

        else:
            # Treat as text by reading file in small blocks and splitting on the fly
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=max_chunk_size,
                chunk_overlap=overlap,
                separators=["\n\n", "\n", " ", ""],
            )
            buffer = ""
            chunk_idx = 0
            with open(tmp_path, "rb") as f:
                for raw_line in f:
                    try:
                        line = raw_line.decode("utf-8", errors="ignore")
                    except Exception:
                        line = ""
                    buffer += line
                    if len(buffer) >= max_chunk_size * 2:
                        for c in splitter.split_text(buffer):
                            if c.strip():
                                batch_texts.append(c)
                                batch_metas.append(
                                    {**metabase, "chunk_index": chunk_idx}
                                )
                                chunk_idx += 1
                                if len(batch_texts) >= embed_batch:
                                    flush_batch()
                        buffer = ""
            if buffer:
                for c in splitter.split_text(buffer):
                    if c.strip():
                        batch_texts.append(c)
                        batch_metas.append({**metabase, "chunk_index": chunk_idx})
                        chunk_idx += 1
                        if len(batch_texts) >= embed_batch:
                            flush_batch()
            flush_batch()

        embedded_hashes.add(file_hash)
        file_hash_to_docids[file_hash] = [
            f"{file_hash}:{i}" for i in range(total_chunks)
        ]
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

        if total_chunks == 0:
            return {"status": "empty", "file_hash": file_hash}
        return {"status": "ingested", "file_hash": file_hash, "chunks": total_chunks}

    except Exception as e:
        return {"status": "error", "message": str(e)}


# ────────────────────────────────────────────────────────────────────────────
# Raw text ingest (already loaded externally)


@mcp.tool(name="vector_ingest_text")
def vector_ingest_text(
    doc_id: str,
    text: str,
    max_chunk_size: int = 1200,
    overlap: int = 200,
    embed_batch: int = 32,
) -> dict:
    """
    Ingest raw text (already loaded externally) into FAISS using chunking and embedding.

    Args:
        doc_id (str): Document identifier.
        text (str): Raw text to ingest.
        max_chunk_size (int): Max characters per chunk (default: 1200).
        overlap (int): Overlap between chunks (default: 200).
        embed_batch (int): Batch size for embedding (default: 32).

    Returns:
        dict: Status and chunk info.

    Example:
        vector_ingest_text("doc1", "Some text...")
    """
    max_chunk_size = int(max_chunk_size)
    overlap = int(overlap)
    embed_batch = int(embed_batch)
    if overlap >= max_chunk_size:
        return {
            "status": "error",
            "message": f"overlap ({overlap}) must be < max_chunk_size ({max_chunk_size})",
        }

    raw = (text or "").encode("utf-8", errors="ignore")
    # Hash without keeping large buffers
    h = hashlib.md5()
    h.update(raw)
    file_hash = h.hexdigest()
    if file_hash in embedded_hashes:
        return {"status": "already_ingested", "file_hash": file_hash}

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=max_chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""],
    )

    metabase = {"source": doc_id, "provider": "raw", "file_hash": file_hash}
    batch_texts: List[str] = []
    batch_metas: List[dict] = []
    total_chunks = 0
    chunk_idx = 0

    for c in splitter.split_text(text or ""):
        if not c.strip():
            continue
        batch_texts.append(c)
        batch_metas.append({**metabase, "chunk_index": chunk_idx})
        chunk_idx += 1
        if len(batch_texts) >= embed_batch:
            add_texts_batch(batch_texts, batch_metas)
            total_chunks += len(batch_texts)
            batch_texts, batch_metas = [], []

    if batch_texts:
        add_texts_batch(batch_texts, batch_metas)
        total_chunks += len(batch_texts)

    embedded_hashes.add(file_hash)
    file_hash_to_docids[file_hash] = [f"{file_hash}:{i}" for i in range(total_chunks)]
    if total_chunks == 0:
        return {"status": "empty", "file_hash": file_hash}
    return {"status": "ingested", "file_hash": file_hash, "chunks": total_chunks}


# ────────────────────────────────────────────────────────────────────────────
#  Box Ingestion


@mcp.tool(name="vector_ingest_box")
def vector_ingest_box(  # noqa: C901
    metadata: dict,
    file_id: str,
    max_chunk_size: int = 1200,
    overlap: int = 200,
    embed_batch: int = 32,
    url: Optional[str] = None,
) -> dict:
    """
    Ingest a single Box file into FAISS using streaming split/insert.
    Requires metadata = {"box": {"access_token": "..."}}

    Args:
        metadata (dict): Metadata with Box access token.
        file_id (str): Box file ID.
        max_chunk_size (int): Max characters per chunk (default: 1200).
        overlap (int): Overlap between chunks (default: 200).
        embed_batch (int): Batch size for embedding (default: 32).
        url (Optional[str]): Optional file URL.

    Returns:
        dict: Status and chunk info.

    Example:
        vector_ingest_box(metadata, file_id)
    """
    max_chunk_size = int(max_chunk_size)
    overlap = int(overlap)
    embed_batch = int(embed_batch)
    if overlap >= max_chunk_size:
        return {
            "status": "error",
            "message": f"overlap ({overlap}) must be < max_chunk_size ({max_chunk_size})",
        }

    try:
        tmp_path, meta = get_box_file_to_tempfile(metadata, file_id)
        file_name = meta["file_name"]

        # Hash
        with open(tmp_path, "rb") as f:
            file_hash = md5_stream(f)
        if file_hash in embedded_hashes:
            os.unlink(tmp_path)
            return {"status": "already_ingested", "file_hash": file_hash}

        mime = guess_mime_from_key(file_name)
        metabase = {
            "source": file_name,
            "box_id": file_id,
            "provider": "box",
            "file_hash": file_hash,
            "url": url or f"https://app.box.com/file/{file_id}",
        }

        batch_texts: List[str] = []
        batch_metas: List[dict] = []
        total_chunks = 0

        def flush_batch():
            nonlocal batch_texts, batch_metas, total_chunks
            if not batch_texts:
                return
            add_texts_batch(batch_texts, batch_metas)
            total_chunks += len(batch_texts)
            batch_texts, batch_metas = [], []

        # PDF
        if mime == "application/pdf":
            chunk_idx = 0
            for chunk in stream_pdf_chunks_from_path(tmp_path, max_chunk_size, overlap):
                batch_texts.append(chunk)
                batch_metas.append({**metabase, "chunk_index": chunk_idx})
                chunk_idx += 1
                if len(batch_texts) >= embed_batch:
                    flush_batch()
            flush_batch()

        # DOCX
        elif (
            mime
            == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ):
            chunk_idx = 0
            for chunk in stream_docx_chunks_from_path(
                tmp_path, max_chunk_size, overlap
            ):
                batch_texts.append(chunk)
                batch_metas.append({**metabase, "chunk_index": chunk_idx})
                chunk_idx += 1
                if len(batch_texts) >= embed_batch:
                    flush_batch()
            flush_batch()

        # TEXT
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=max_chunk_size,
                chunk_overlap=overlap,
                separators=["\n\n", "\n", " ", ""],
            )
            buffer = ""
            chunk_idx = 0
            with open(tmp_path, "rb") as f:
                for raw_line in f:
                    try:
                        line = raw_line.decode("utf-8", errors="ignore")
                    except Exception:
                        line = ""
                    buffer += line
                    if len(buffer) >= max_chunk_size * 2:
                        for c in splitter.split_text(buffer):
                            if c.strip():
                                batch_texts.append(c)
                                batch_metas.append(
                                    {**metabase, "chunk_index": chunk_idx}
                                )
                                chunk_idx += 1
                                if len(batch_texts) >= embed_batch:
                                    flush_batch()
                        buffer = ""
            if buffer:
                for c in splitter.split_text(buffer):
                    if c.strip():
                        batch_texts.append(c)
                        batch_metas.append({**metabase, "chunk_index": chunk_idx})
                        chunk_idx += 1
                        if len(batch_texts) >= embed_batch:
                            flush_batch()
            flush_batch()

        embedded_hashes.add(file_hash)
        file_hash_to_docids[file_hash] = [
            f"{file_hash}:{i}" for i in range(total_chunks)
        ]
        os.unlink(tmp_path)

        if total_chunks == 0:
            return {"status": "empty", "file_hash": file_hash}
        return {"status": "ingested", "file_hash": file_hash, "chunks": total_chunks}

    except Exception as e:
        return {"status": "error", "message": str(e)}


# ────────────────────────────────────────────────────────────────────────────
# Query


@mcp.tool(name="vector_query")
def vector_query(metadata: dict, question: str, top_k: int = 3) -> dict:
    """
    Semantic search over the in-memory FAISS index.

    Args:
        question (str): Natural language query.
        top_k (int, optional): Number of results to return (default: 3).

    Returns:
        dict: Structured response with results and metadata.

    Example:
        vector_query("What is the capital of France?", top_k=5)
    """
    _ = metadata
    idx = get_index()
    if getattr(idx, "index", None) is None or idx.index.ntotal == 0:
        return {"error": "index_empty"}

    docs = idx.similarity_search(question, k=max(1, int(top_k)))
    results = []
    for d in docs:
        meta = d.metadata or {}
        source = (
            meta.get("url") or meta.get("file_id") or meta.get("source") or "unknown"
        )

        results.append(
            {
                "text": (d.page_content or "")[:800],
                "metadata": meta,
                "source": source,
            }
        )
    return {
        "results": results,
        "total": len(results),
    }


@mcp.tool(name="vector_test_context_progress")
async def vector_test_context_progress(
    context: Context,
    metadata: dict,
    steps: int = 10,
    delay: float = 1,
) -> dict:
    """
    Test tool that uses context.info() and context.report_progress()
    to simulate a streaming operation with incremental updates.
    Works in FastMCP 2.10.5 (messages will appear in server logs).
    """
    import asyncio
    import time
    start = time.time()

    print(f"Context type: {type(context)}")
    print(f"Context has info: {hasattr(context, 'info')}")
    print(f"Context has report_progress: {hasattr(context, 'report_progress')}")                
    await context.info("🚀 vector_test_context_progress started")

    for i in range(1, steps + 1):
        await asyncio.sleep(delay)
        await context.info(f"🧩 Step {i}/{steps} in progress...")
        await context.report_progress(progress=i, total=steps)
        # try:
        #     await context.report_progress(progress=i, total=steps)
        # except Exception as e:
        #     await context.info(f"⚠️ report_progress failed: {str(e)}")

    total_time = round(time.time() - start, 2)
    await context.info(f"✅ Completed all {steps} steps in {total_time}s")

    return {
        "status": "ok",
        "steps": steps,
        "duration": total_time,
        "note": "testing"
    }


mcp_app = mcp.http_app(transport="sse")
routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/resetindex", _vector_reset_index),
    Route("/status", _vector_status),
]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=VECTOR_MCP_PORT, log_level="debug")
