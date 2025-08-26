# vector_mcp_server.py
from mcp.server.fastmcp import FastMCP
from typing import Dict, List, Tuple
import io, hashlib, json, os

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

# ────────────────────────────────────────────────────────────────────────────
# MCP setup
mcp = FastMCP(name="VectorToolkit", host="0.0.0.0", port=8060)

# ────────────────────────────────────────────────────────────────────────────
# Per-session, in-memory state
session_index: FAISS | None = None
embedded_hashes: set[str] = set()            # file content hashes already indexed
file_hash_to_docids: dict[str, List[str]] = {}  # to help removal/reset if needed

# OpenAI embeddings wrapper compatible with LangChain’s Embeddings interface
class OpenAIEmbeddingsLite:
    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None):
        self.model = model
        openai.api_key = api_key or os.getenv("OPENAI_API_KEY")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
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
    }
    }
    """
    metadata = metadata.get("s3")
    if not metadata:
        raise ValueError("Missing S3 metadata")
    access_key = metadata.get("access_key")
    secret_key = metadata.get("secret_key")
    bucket     = metadata.get("bucket")
    region     = metadata.get("region", "us-east-1")
    endpoint   = metadata.get("endpoint_url")

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
      - OAuth token/credentials object is out of scope here (keep SA for simplicity)
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

def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()

def stream_pdf_chunks(raw: bytes, chunk_size: int = 1200, overlap: int = 200):
    """
    Yields text chunks from a PDF without building one huge string.
    """
    doc = fitz.open(stream=raw, filetype="pdf")
    buf = ""
    for p in doc:
        buf += p.get_text() or ""
        # Split buffered text when it grows large to keep memory bounded
        while len(buf) >= (chunk_size * 3):
            head, buf = buf[:chunk_size*2], buf[chunk_size*2 - overlap:]
            yield from _split_with_overlap(head, chunk_size, overlap)
    doc.close()
    if buf:
        yield from _split_with_overlap(buf, chunk_size, overlap)

def stream_docx_chunks(raw: bytes, chunk_size: int = 1200, overlap: int = 200):
    doc = Document(io.BytesIO(raw))
    buf = ""
    for para in doc.paragraphs:
        if para.text:
            buf += para.text + "\n"
            if len(buf) >= (chunk_size * 3):
                head, buf = buf[:chunk_size*2], buf[chunk_size*2 - overlap:]
                yield from _split_with_overlap(head, chunk_size, overlap)
    if buf:
        yield from _split_with_overlap(buf, chunk_size, overlap)

def stream_text_chunks(raw: bytes, chunk_size: int = 1200, overlap: int = 200, encoding: str = "utf-8"):
    # Decode in small pieces to avoid a giant string
    stream = io.BytesIO(raw)
    buf = ""
    for line in stream:
        try:
            buf += line.decode(encoding, errors="ignore")
        except Exception:
            continue
        if len(buf) >= (chunk_size * 3):
            head, buf = buf[:chunk_size*2], buf[chunk_size*2 - overlap:]
            yield from _split_with_overlap(head, chunk_size, overlap)
    if buf:
        yield from _split_with_overlap(buf, chunk_size, overlap)

def _split_with_overlap(text: str, chunk_size: int, overlap: int):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", " ", ""]
    )
    for chunk in splitter.split_text(text):
        if chunk.strip():
            yield chunk

def guess_mime_from_key(key: str) -> str:
    k = key.lower()
    if k.endswith(".pdf"): return "application/pdf"
    if k.endswith(".docx"): return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if k.endswith(".txt") or k.endswith(".md") or k.endswith(".log"): return "text/plain"
    return "application/octet-stream"

# ────────────────────────────────────────────────────────────────────────────
# Core: add a file’s chunks to FAISS (de-duped by file hash)

def add_document_bytes_to_index(file_key: str, raw: bytes, source_meta: Dict, chunk_size=1200, overlap=200):
    file_hash = md5_bytes(raw)
    if file_hash in embedded_hashes:
        return {"status": "already_ingested", "file_hash": file_hash}

    mime = guess_mime_from_key(file_key)
    if mime == "application/pdf":
        chunks = list(stream_pdf_chunks(raw, chunk_size, overlap))
    elif mime == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        chunks = list(stream_docx_chunks(raw, chunk_size, overlap))
    elif mime.startswith("text/") or mime == "application/octet-stream":
        chunks = list(stream_text_chunks(raw, chunk_size, overlap))
    else:
        # Fall back: try decode as text
        chunks = list(stream_text_chunks(raw, chunk_size, overlap))

    if not chunks:
        return {"status": "empty", "file_hash": file_hash}

    docs = []
    # Create LangChain "Document" objects lazily (simple dicts also okay for FAISS.add_texts)
    metabase = {"source": source_meta.get("source", file_key), "file_hash": file_hash}
    for idx, c in enumerate(chunks):
        meta = {**metabase, "chunk_index": idx, **source_meta}
        docs.append((c, meta))

    index = get_index()
    texts = [t for t, _ in docs]
    metas = [m for _, m in docs]
    index.add_texts(texts=texts, metadatas=metas)

    embedded_hashes.add(file_hash)
    file_hash_to_docids[file_hash] = [f"{file_hash}:{i}" for i in range(len(texts))]
    return {"status": "ingested", "file_hash": file_hash, "chunks": len(texts)}

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



@mcp.tool(name="vector_ingest_s3")
def vector_ingest_s3(
    metadata: dict,
    key: str,
    max_chunk_size: int = 3000,
    overlap: int = 200
) -> dict:
    """
    Ingest a single S3 object (by key) into the in-memory FAISS index.
    Uses streaming where possible (PDF page-by-page, TXT line-by-line).
    """
    import fitz
    from docx import Document

    try:
        s3, bucket = get_s3_client_and_bucket(metadata)
        print(f"[S3] Fetching key='{key}' from bucket='{bucket}'")

        obj = s3.get_object(Bucket=bucket, Key=key)
        size_bytes = obj.get("ContentLength")


        raw_bytes = obj["Body"].read()


        file_hash = md5_bytes(raw_bytes)

        if file_hash in embedded_hashes:
            print("[DEDUP] Already ingested, skipping")
            return {"status": "already_ingested", "file_hash": file_hash}

        mime = guess_mime_from_key(key)
        print(f"[MIME] Detected mime={mime}")

        texts, metas = [], []
        metabase = {"source": key, "bucket": bucket, "provider": "s3", "file_hash": file_hash}

        # PDF
        if key.lower().endswith(".pdf"):
            print("[PDF] Opening PDF for streaming")
            pdf_doc = fitz.open(stream=raw_bytes, filetype="pdf")
            print(f"[PDF] Total pages: {pdf_doc.page_count}")
            full_text = ""
            for page_idx, page in enumerate(pdf_doc):
                page_text = page.get_text()
                full_text += page_text
                if page_idx % 10 == 0:  # log every 10 pages
                    print(f"[PDF] Processed {page_idx+1} pages")
                if len(full_text) > max_chunk_size * 2:
                    chunks = chunk_text(full_text, max_chunk_size, overlap)
                    for c in chunks:
                        texts.append(c)
                        metas.append({**metabase, "chunk_index": len(texts)-1})
                    print(f"[PDF] Flushed {len(chunks)} chunks to memory")
                    full_text = ""
            pdf_doc.close()
            if full_text:
                chunks = chunk_text(full_text, max_chunk_size, overlap)
                for c in chunks:
                    texts.append(c)
                    metas.append({**metabase, "chunk_index": len(texts)-1})

        # TXT
        elif key.lower().endswith(".txt"):
            print("[TXT] Streaming text file")
            obj_stream = s3.get_object(Bucket=bucket, Key=key)["Body"]
            buffer = ""
            line_count = 0
            for raw_line in obj_stream.iter_lines():
                buffer += raw_line.decode("utf-8", errors="ignore") + "\n"
                line_count += 1
                if len(buffer) > max_chunk_size * 2:
                    chunks = chunk_text(buffer, max_chunk_size, overlap)
                    for c in chunks:
                        texts.append(c)
                        metas.append({**metabase, "chunk_index": len(texts)-1})
                    print(f"[TXT] Flushed {len(chunks)} chunks after {line_count} lines")
                    buffer = ""
            if buffer:
                chunks = chunk_text(buffer, max_chunk_size, overlap)
                for c in chunks:
                    texts.append(c)
                    metas.append({**metabase, "chunk_index": len(texts)-1})
            print(f"[TXT] Completed streaming {line_count} lines")

        # DOCX
        elif key.lower().endswith(".docx"):
            print("[DOCX] Parsing docx")
            doc = Document(io.BytesIO(raw_bytes))
            text = "\n".join(para.text for para in doc.paragraphs)
            chunks = chunk_text(text, max_chunk_size, overlap)
            for idx, c in enumerate(chunks):
                texts.append(c)
                metas.append({**metabase, "chunk_index": idx})

        # Others
        else:
            print("[GENERIC] Decoding as utf-8 text")
            text = raw_bytes.decode("utf-8", errors="ignore")
            chunks = chunk_text(text, max_chunk_size, overlap)
            for idx, c in enumerate(chunks):
                texts.append(c)
                metas.append({**metabase, "chunk_index": idx})

        if not texts:
            print("[EMPTY] No text chunks produced")
            return {"status": "empty", "file_hash": file_hash}

        print(f"[CHUNKS] Prepared {len(texts)} chunks for embedding")

        index = get_index()
        print("[FAISS] Embedding and adding chunks to index")
        index.add_texts(texts=texts, metadatas=metas)

        embedded_hashes.add(file_hash)
        file_hash_to_docids[file_hash] = [f"{file_hash}:{i}" for i in range(len(texts))]

        print(f"[DONE] Ingested {key} → {len(texts)} chunks")
        return {"status": "ingested", "file_hash": file_hash, "chunks": len(texts)}

    except Exception as e:
        print(f"[ERROR] vector_ingest_s3 failed for key={key}")
        return {"status": "error", "message": str(e)}


@mcp.tool(name="vector_ingest_gdrive")
def vector_ingest_gdrive(metadata: dict, file_id: str,
                         chunk_size: int = 1200, overlap: int = 200) -> dict:
    """
    Ingest a Google Drive file (by fileId) into the in-memory FAISS index.
    metadata requires service account info/file.
    """
    svc = get_gdrive_service(metadata)
    # Get metadata
    meta = svc.files().get(fileId=file_id, fields="id,name,mimeType,size").execute()

    # Download bytes
    request = svc.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    raw = fh.getvalue()

    file_name = meta.get("name", file_id)
    source_meta = {
        "source": file_name,
        "gdrive_id": file_id,
        "provider": "gdrive",
        "mimeType": meta.get("mimeType"),
        "size_bytes": int(meta.get("size") or len(raw))
    }
    return add_document_bytes_to_index(file_name, raw, source_meta, chunk_size, overlap)

@mcp.tool(name="vector_ingest_text")
def vector_ingest_text(doc_id: str, text: str,
                       chunk_size: int = 1200, overlap: int = 200) -> dict:
    """
    Ingest raw text (already loaded externally) into the in-memory FAISS index.
    De-duplicated by text hash.
    """
    raw = text.encode("utf-8", errors="ignore")
    source_meta = {"source": doc_id, "provider": "raw"}
    return add_document_bytes_to_index(doc_id, raw, source_meta, chunk_size, overlap)

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
        # d.page_content (text), d.metadata (dict), and score not provided by default in this call
        out.append({
            "text": d.page_content[:800],  # clip to keep payload small
            "metadata": d.metadata
        })
    return json.dumps(out)

# ────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
