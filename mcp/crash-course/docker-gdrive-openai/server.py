# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import json
import io
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

# ─── Google Drive Configuration ─────────────────────────────────────────────
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
SERVICE_ACCOUNT_FILE = 'credentials.json'
ROOT_FOLDER_ID = os.getenv("GDRIVE_ROOT_FOLDER_ID") 
# ROOT_FOLDER_ID = "1SYeijTDz1msCFvNbN4U6ai2Zol1AJh-i"  # personal folder
# ROOT_FOLDER_ID = "1l-6WAmSbWWNx3Rc10LfHXZpCWY3ShZJI"  # service account folder

credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=credentials)

# ─── Helper Functions ───────────────────────────────────────────────────────

def list_all_files(folder_id):
    """Recursively list all files under a folder."""
    query = f"'{folder_id}' in parents and trashed = false"
    files = []
    page_token = None
    while True:
        response = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name, mimeType, parents)",
            pageToken=page_token
        ).execute()
        for file in response['files']:
            if file['mimeType'] == 'application/vnd.google-apps.folder':
                files += list_all_files(file['id'])
            else:
                files.append(file)
        page_token = response.get('nextPageToken')
        if not page_token:
            break
        
    return files

def download_file_content(file_id):
    """Download text content of a file from Google Drive."""
    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    fh.seek(0)
    return fh.read().decode("utf-8", errors="ignore")

# ─── MCP Server Setup ───────────────────────────────────────────────────────

mcp = FastMCP(name="GoogleDriveToolkit", host="0.0.0.0", port=8051)

@mcp.tool(name="gdrive_search_files")
def search_files(keyword: str, search_type: str = "both") -> list:
    """Search for files in Google Drive by name or content."""
    results = []
    files = list_all_files(ROOT_FOLDER_ID)
    print("listed files:", files)
    for file in files:
        match = False
        if search_type in ["filename", "both"] and keyword.lower() in file["name"].lower():
            match = True

        if not match and search_type in ["content", "both"]:
            try:
                content = download_file_content(file["id"])
                if keyword.lower() in content.lower():
                    match = True
            except Exception as e:
                continue  # skip binary or unreadable files

        if match:
            results.append({"name": file["name"], "id": file["id"]})
    return json.dumps(results)

@mcp.tool(name="gdrive_fetch_file")
def fetch_file(file_id: str, user_query: str = "") -> str:
    """Fetch content of a file by its Google Drive file ID."""
    return download_file_content(file_id)

if __name__ == "__main__":
    mcp.run(transport="sse")
