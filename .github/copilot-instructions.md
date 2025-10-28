# AI Coding Agent Instructions

These instructions help an AI agent work productively in this repo. Focus on the concrete patterns actually used here.

## 1. High-Level Architecture
- "Cookbook" style mono-repo: independent example domains under `knowledge/`, `mcp/crash-course/`, `patterns/workflows/`, plus vector MCP server (`mcp/crash-course/vector-mcp`) and memory demos (`knowledge/mem0`). Don't look into other folders than `mcp/crash-course/` as active development of mcp servers is being done in (`mcp/crash-course/`)
- No centralized app framework; each folder has self-contained scripts or servers.
- MCP focus: custom servers (`mcp/crash-course/*/server.py`) + multi-server client (`mcp/crash-course/metadata_client/**`). Vector server integrates FAISS + HuggingFace/OpenAI embeddings.

## 2. Environments & Dependencies
- Global `pyproject.toml` declares base libs (fastmcp, langchain, openai, dotenv). Version pin overrides appear in `requirements-lock.txt` (fastmcp==2.10.5, mcp[cli]==1.13.1). Prefer using lock versions when reproducing bugs.
- Subfolders (`knowledge/docling`, `knowledge/mem0`, `mcp/crash-course`) have their own `requirements.txt` for isolated examples.
- Python version: `>=3.11`.

## 3. MCP Patterns
- Server creation: `mcp = FastMCP(name="VectorToolkit")` or with host/port for SSE; run via `mcp.run(transport="sse")`.
- Tools defined with `@mcp.tool()` returning serializable data (string, dict). Keep execution side-effect free unless intentionally mutating external services.
- Multi-server client aggregates tool invocations; metadata passed to tools enables cross-service access (S3, GDrive, Box, etc.). Ensure keys exist: e.g. S3 metadata structure `{"s3": {"access_key":..., "secret_key":..., "bucket":...}}`.
- Vector server keeps in-memory FAISS index (`session_index`) and caches embedding hashes; avoid global mutation races (single-threaded typical).

## 4. Vector Server Conventions (`vector-mcp/server.py`)
- Embedding backend selected by env vars: `VECTORMCP_EMBEDDING_PROVIDER`, `VECTORMCP_EMBEDDING_MODEL`.
- HuggingFace requires `HUGGINGFACE_HUB_TOKEN`; logs in early via `login()`.
- Ensures FAISS index initialized with dummy text to avoid empty index errors.
- Use helper functions for cloud sources: `get_s3_client_and_bucket(metadata)`, `get_google_creds(metadata)`, `get_drive_service(metadata)`, Box temp file download, hashing + streaming utilities.
- Long-running operations should stream progress (not fully implemented yet) — design to yield incremental updates via MCP streaming API when upgrading.

## 5. Knowledge Pipelines (`knowledge/docling`)
- Sequential scripts: `1-extraction.py` → `2-chunking.py` → `3-embedding.py` (writes LanceDB) → `4-search.py` → `5-chat.py` (Streamlit UI).
- Chunking uses `HybridChunker` with OpenAI tokenizer wrapper (`utils/tokenizer.py`). Keep `model_max_length` aligned with embedding model (8191 for `text-embedding-3-large`).
- Metadata assembly preserves page numbers, titles, filename for provenance.

## 6. Mem0 Examples (`knowledge/mem0`)
- Emphasize memory lifecycle: add / update / delete / search. Docker compose for OSS variant under `docker/` must be up before running `oss` examples.
- Use `Memory.from_config(config)` patterns; follow prompts guidance in README for operations.

## 7. Workflow Patterns (`patterns/workflows`)
- README documents building blocks: chaining, routing, parallelization, orchestrator-workers. Code examples (not shown here) should stay lightweight; prefer pure Python over heavy frameworks.

## 8. Configuration & Secrets
- `.env` loaded via `load_dotenv()` in most scripts; do not commit secrets. Environment variable names: `OPENAI_API_KEY`, `HUGGINGFACE_HUB_TOKEN`, `GMAIL_CLIENT_ID/SECRET` for GDrive OAuth.
- Client metadata supplies OAuth tokens (`access_token`, `refresh_token`) for Google/Box; refresh logic present in `get_google_creds`.

## 9. Execution & Debugging
- Run examples directly: `python knowledge/docling/1-extraction.py` etc.
- Launch Streamlit app: `streamlit run knowledge/docling/5-chat.py`.
- Start vector MCP server: `python mcp/crash-course/vector-mcp/server.py` (ensure env vars + tokens). Test with MCP CLI: `mcp dev server.py` or multi-server client script.
- If FAISS errors (empty index), confirm dummy initialization present.
- For embedding issues: check provider env vars and HF token validity.

## 10. Adding New MCP Tools
- Place in appropriate server file; decorate with `@mcp.tool()`.
- Accept `metadata: Dict` when external credentials needed.
- Return small, structured payload first; consider streaming design (chunk yields) for long processes.
- Keep error handling explicit: raise `ValueError` for missing metadata keys to surface to client.

## 11. Style & Conventions
- Minimal, tutorial-focused code; prefer clarity over abstraction.
- Use type hints (`Dict`, `List`, `Tuple`, concrete return types) for new code following existing patterns.
- Avoid hidden global state except intentional caches (`session_index`). If adding persistent state, document it.

## 12. What NOT to Do
- Don’t introduce heavy framework orchestration across folders.
- Don’t refactor examples into shared libs unless duplication is excessive.
- Don’t store secrets or tokens in source; rely on env + runtime metadata.

---
Feedback welcome: highlight unclear areas (e.g. streaming progress mechanics) or request deeper docs if expanding beyond examples.
