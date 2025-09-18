# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import json
import msal
import requests

# ─── Load Environment ────────────────────────────────────────────────────────
load_dotenv()

CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPE = ["Files.Read"]
TOKEN_FILE = "./.onedrive_refresh_token.json"
GRAPH_BASE = "https://graph.microsoft.com/v1.0"

# ─── Auth Utilities ──────────────────────────────────────────────────────────
def save_token(token_response):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_response, f)

def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def get_access_token():
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    token = load_token()
    if token and "refresh_token" in token:
        refreshed = app.acquire_token_by_refresh_token(token["refresh_token"], scopes=SCOPE)
        if "access_token" in refreshed:
            save_token(refreshed)
            return refreshed["access_token"]
    flow = app.initiate_device_flow(scopes=SCOPE)
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        save_token(result)
        return result["access_token"]
    raise Exception("Unable to acquire token.")

def graph_get(endpoint: str):
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(GRAPH_BASE + endpoint, headers=headers)
    if resp.status_code != 200:
        raise Exception(f"Graph API error: {resp.status_code} - {resp.text}")
    return resp.json()

# ─── MCP Server Setup ────────────────────────────────────────────────────────
mcp = FastMCP(name="OneDriveToolkit", host="0.0.0.0", port=8056)

# ─── Tool: List Files ────────────────────────────────────────────────────────
@mcp.tool(name="onedrive_list_files")
def list_files(path: str = "") -> str:
    """List files in the given OneDrive folder (use empty path for root)."""
    endpoint = f"/me/drive/root:/{path}:/children" if path else "/me/drive/root/children"
    files = graph_get(endpoint)
    return json.dumps([{"name": f["name"], "id": f["id"]} for f in files.get("value", [])])

# ─── Tool: Search Files ──────────────────────────────────────────────────────
# @mcp.tool(name="onedrive_search_files")
# def search_files(query: str) -> str:
#     """Search OneDrive files by name containing the given keyword."""
#     files = graph_get(f"/me/drive/search(q='{query}')")
#     return json.dumps([{"name": f["name"], "id": f["id"]} for f in files.get("value", [])])

@mcp.tool(name="onedrive_search_files")
def search_files(query: str, path: str = "", mode: str = "both", limit: int = 10) -> str:
    """
    Search OneDrive files in a specific folder where the name or content contains the given query string.
    `path` is the OneDrive folder path (e.g., 'bookstypes').
    `mode` can be: "name", "content", or "both".
    """
    matches = []
    # Step 1: List files in the specified folder
    endpoint = f"/me/drive/root:/{path}:/children" if path else "/me/drive/root/children"
    try:
        files = graph_get(endpoint).get("value", [])
    except Exception as e:
        return json.dumps([{"error": f"Failed to list files in folder '{path}': {str(e)}"}])

    for f in files:
        if len(matches) >= limit:
            break
        file_id = f.get("id")
        name = f.get("name", "")
        is_text_file = name.lower().endswith((".txt", ".md", ".json", ".csv", ".log", ".xml"))
        # Filename match
        if mode in ("name", "both") and query.lower() in name.lower():
            matches.append({"name": name, "id": file_id, "match": "filename"})
        # Content match
        if mode in ("content", "both") and is_text_file:
            try:
                metadata = graph_get(f"/me/drive/items/{file_id}")
                download_url = metadata.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    continue
                resp = requests.get(download_url)
                if resp.status_code == 200:
                    content = resp.text
                    if query.lower() in content.lower():
                        snippet_start = content.lower().find(query.lower())
                        snippet = content[snippet_start:snippet_start+200].replace('\n', ' ')
                        matches.append({
                            "name": name,
                            "id": file_id,
                            "match": "content",
                            "snippet": snippet.strip()
                        })
            except Exception:
                continue

    return json.dumps(matches or [{"message": f"No files matched '{query}' in {mode} mode in folder '{path}'."}])




# ─── Tool: Get File Content (Text only) ──────────────────────────────────────
@mcp.tool(name="onedrive_get_file_content")
def get_file_content(file_id: str) -> str:
    """Download and return content of a text-based file by ID."""
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    metadata = graph_get(f"/me/drive/items/{file_id}")
    download_url = metadata["@microsoft.graph.downloadUrl"]

    resp = requests.get(download_url)
    if resp.status_code == 200:
        return resp.text[:5000]  # limit for safety
    return f"Error downloading file: {resp.status_code}"


@mcp.tool(name="onedrive_search_file_contents")
def search_file_contents(query: str, limit: int = 5) -> str:
    """Search OneDrive files whose content contains the given query string (limited to text-based files)."""
    # Step 1: Search all files (basic name search as entrypoint)
    files = graph_get(f"/me/drive/search(q='.')")  # Get all files
    results = []
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}

    for f in files.get("value", []):
        if len(results) >= limit:
            break

        file_id = f.get("id")
        name = f.get("name", "")

        # Optional: skip known non-text files
        if not name.lower().endswith((".txt", ".md", ".json", ".csv", ".log")):
            continue

        try:
            metadata = graph_get(f"/me/drive/items/{file_id}")
            download_url = metadata["@microsoft.graph.downloadUrl"]

            resp = requests.get(download_url)
            if resp.status_code == 200:
                content = resp.text
                if query.lower() in content.lower():
                    snippet_start = content.lower().find(query.lower())
                    snippet = content[snippet_start:snippet_start+200].replace('\n', ' ')
                    results.append({
                        "name": name,
                        "id": file_id,
                        "snippet": snippet.strip()
                    })
        except Exception as e:
            continue

    return json.dumps(results or [{"message": f"No file contents matched '{query}'"}])



# ─── MCP Run ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if "--login" in sys.argv:
        print("Forcing device login...")
        get_access_token()
    else:
        mcp.run(transport="sse")

