import os
import json
import asyncio
from typing import Dict, Optional

import datetime
from dotenv import load_dotenv
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import RedirectResponse, JSONResponse
from starlette.requests import Request
from azure.core.credentials import TokenCredential, AccessToken
import requests
from azure.identity.aio import AuthorizationCodeCredential
from msgraph import GraphServiceClient

import nest_asyncio
nest_asyncio.apply()

load_dotenv()

CLIENT_ID = os.getenv("AZURE_CLIENT_ID")
CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET")
TENANT_ID = os.getenv("AZURE_TENANT_ID")
REDIRECT_URI = os.getenv("AZURE_REDIRECT_URI")
PORT = 8007
SCOPES = ["User.Read", "Files.Read", "Sites.Read.All", "offline_access"]

AUTH_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/authorize"
TOKEN_URL = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"

user_tokens: Dict[str, Dict] = {}
graph_clients: Dict[str, GraphServiceClient] = {}

mcp = FastMCP("onedrive-mcp")

def build_auth_url() -> str:
    return (
        f"{AUTH_URL}?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_mode=query"
        f"&scope={' '.join(SCOPES)}"
    )



class ManualTokenCredential(TokenCredential):
    def __init__(self, access_token: str):
        self._access_token = access_token

    async def get_token(self, *scopes, **kwargs) -> AccessToken:
        # Set token expiration to 1 hour from now
        expires_on = int((datetime.datetime.utcnow() + datetime.timedelta(hours=1)).timestamp())
        return AccessToken(self._access_token, expires_on)

    async def close(self):
        # Required to satisfy async context management in GraphServiceClient
        pass


def sanitize_folder_id(folder_id: Optional[str]) -> str:
    return folder_id if folder_id and folder_id.strip() else "root"


async def get_graph_client(email: str) -> Optional[GraphServiceClient]:
    token_data = user_tokens.get(email)
    if not token_data:
        return None

    # Refresh token if expired or missing access_token
    if "access_token" not in token_data:
        refresh_data = {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": token_data["refresh_token"],
            "redirect_uri": REDIRECT_URI,
        }
        resp = requests.post(TOKEN_URL, data=refresh_data).json()
        token_data["access_token"] = resp.get("access_token")
        token_data["refresh_token"] = resp.get("refresh_token", token_data["refresh_token"])  # update if new

    credential = ManualTokenCredential(token_data["access_token"])
    return GraphServiceClient(credential, scopes=SCOPES)


@mcp.tool(name="onedrive_list_files")
def list_files(email: str) -> str:
    """List files from the root of the user's default OneDrive."""
    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        response = await client.me.drive.root.children.get()
        return [{"name": f.name, "id": f.id} for f in response.value]
    return json.dumps(asyncio.run(inner()))


@mcp.tool(name="list_authorized_accounts")
def list_authorized_accounts() -> str:
    """List all Azure accounts that have been authorized. This tool can be used if no email is provided in query."""
    return "\n".join(user_tokens.keys()) or "No users authorized yet."

@mcp.tool(name="onedrive_get_auth_url")
def get_auth_url() -> str:
    return build_auth_url()


@mcp.tool(name="onedrive_search_file_content")
def search_file_content(email: str, drive_id: str, file_id: str, keyword: str) -> str:
    """Search inside a single OneDrive file (.txt, .docx, .pdf) for a keyword."""
    import aiohttp
    from io import BytesIO
    from docx import Document
    import fitz  # PyMuPDF

    async def fetch_and_extract_text(download_url: str, file_name: str) -> Optional[str]:
        async with aiohttp.ClientSession() as session:
            async with session.get(download_url) as resp:
                if resp.status != 200:
                    return None
                file_bytes = await resp.read()

        if file_name.endswith(".txt"):
            return file_bytes.decode("utf-8", errors="ignore")
        elif file_name.endswith(".docx"):
            return "\n".join(p.text for p in Document(BytesIO(file_bytes)).paragraphs)
        elif file_name.endswith(".pdf"):
            with fitz.open(stream=file_bytes, filetype="pdf") as doc:
                return "\n".join(page.get_text() for page in doc)
        return None

    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        file = await client.drives.by_drive_id(drive_id).items.by_drive_item_id(file_id).get()
        download_url = file.additional_data.get("@microsoft.graph.downloadUrl")
        if not download_url:
            return "❌ Download URL not found."
        content = await fetch_and_extract_text(download_url, file.name)
        if not content:
            return "❌ Could not extract content."
        if keyword.lower() in content.lower():
            return json.dumps({
                "match": True,
                "file_name": file.name,
                "web_url": file.web_url
            })
        else:
            return json.dumps({"match": False, "file_name": file.name})

    return asyncio.run(inner())


@mcp.tool(name="onedrive_search_folder_for_content")
def search_folder_for_content(email: str, drive_id: str, folder_id: str, keyword: str) -> str:
    """Recursively search .txt, .docx, and .pdf files in OneDrive folder for keyword."""
    import aiohttp
    from io import BytesIO
    from docx import Document
    import fitz

    async def fetch_file_bytes(url):
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as r:
                if r.status == 200:
                    return await r.read()
        return None

    def extract_text(file_bytes, file_name):
        if file_name.endswith(".txt"):
            return file_bytes.decode("utf-8", errors="ignore")
        elif file_name.endswith(".docx"):
            return "\n".join(p.text for p in Document(BytesIO(file_bytes)).paragraphs)
        elif file_name.endswith(".pdf"):
            with fitz.open(stream=file_bytes, filetype="pdf") as doc:
                return "\n".join(p.get_text() for p in doc)
        return ""

    async def search_file(client, drive_id, file_item):
        url = file_item.additional_data.get("@microsoft.graph.downloadUrl")
        if not url:
            return None
        bytes_ = await fetch_file_bytes(url)
        if not bytes_:
            return None
        text = extract_text(bytes_, file_item.name)
        if keyword.lower() in text.lower():
            return {
                "name": file_item.name,
                "id": file_item.id,
                "web_url": file_item.web_url,
            }
        return None

    async def recursive_search(client, drive_id, folder_id):
        results = []
        children = await client.drives.by_drive_id(drive_id).items.by_drive_item_id(folder_id).children.get()
        for item in children.value:
            if item.folder:
                results += await recursive_search(client, drive_id, item.id)
            elif item.name.lower().endswith((".txt", ".docx", ".pdf")):
                res = await search_file(client, drive_id, item)
                if res:
                    results.append(res)
        return results

    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        try:
            matches = await recursive_search(client, drive_id, folder_id)
            return json.dumps(matches, indent=2) if matches else "No matching files found."
        except Exception as e:
            return f"❌ Error: {str(e)}"

    return asyncio.run(inner())




@mcp.tool(name="onedrive_get_preferred_drive_id")
def get_preferred_drive_id(email: str, drive_name: str = "OneDrive") -> str:
    """Get preferred drive ID for the authenticated user by name."""
    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        result = await client.me.drives.get()
        drives = result.value
        for drive in drives:
            if drive.name.lower() == drive_name.lower():
                return drive.id
        if drives:
            return drives[0].id
        raise Exception("No drives found.")

    drive_id = asyncio.run(inner())
    return json.dumps({"drive_id": drive_id})

@mcp.tool(name="onedrive_list_folder")
def list_children_in_drive_item(email: str, drive_id: str, folder_id: str = "root") -> str:
    """List children in a given folder of a OneDrive drive."""
    async def inner():
        folder_id_sanitized = sanitize_folder_id(folder_id)
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        response = await client.drives \
            .by_drive_id(drive_id) \
            .items \
            .by_drive_item_id(folder_id_sanitized) \
            .children \
            .get()
        return [{"name": item.name, "id": item.id} for item in response.value]

    result = asyncio.run(inner())
    return json.dumps(result)


@mcp.tool(name="onedrive_find_files_by_name")
def find_files_by_name(email: str, keyword: str, folder_id: str = "root") -> str:
    """Recursively search for files by name in user's default OneDrive."""

    folder_id = sanitize_folder_id(folder_id)

    async def get_preferred_drive(client):
        result = await client.me.drives.get()
        for drive in result.value:
            if drive.name == "OneDrive":
                return drive.id
        if result.value:
            return result.value[0].id
        raise Exception("No drives found.")

    async def recursive_search(client, drive_id, folder_id, keyword):
        matches = []
        response = await client.drives \
            .by_drive_id(drive_id) \
            .items \
            .by_drive_item_id(folder_id) \
            .children \
            .get()

        for item in response.value:
            if keyword.lower() in item.name.lower():
                matches.append({"name": item.name, "id": item.id, "web_url": item.web_url})
            if item.folder:
                matches += await recursive_search(client, drive_id, item.id, keyword)
        return matches

    async def main():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        drive_id = await get_preferred_drive(client)
        return await recursive_search(client, drive_id, folder_id, keyword)

    result = asyncio.run(main())
    return json.dumps(result)

@mcp.tool(name="onedrive_sharepoint_list_all_user_drives")
def list_all_user_drives(email: str) -> str:
    """List all drives (OneDrive + SharePoint) for the authenticated user."""
    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        result = await client.me.drives.get()
        return [
            {
                "name": drive.name,
                "id": drive.id,
                "drive_type": drive.drive_type,
                "web_url": getattr(drive, "web_url", None)
            }
            for drive in result.value
        ]
    drives = asyncio.run(inner())
    return json.dumps(drives)

@mcp.tool(name="sharepoint_list_sites")
def sharepoint_list_sites(email: str) -> str:
    """List all available SharePoint sites."""
    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        result = await client.sites.get()
        return [{"name": site.name, "id": site.id, "web_url": site.web_url} for site in result.value]
    return json.dumps(asyncio.run(inner()))

@mcp.tool(name="sharepoint_list_document_libraries")
def sharepoint_list_document_libraries(email: str, site_id: str) -> str:
    """List all document libraries in a SharePoint site."""
    async def inner():
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."
        response = await client.sites.by_site_id(site_id).drives.get()
        return [{"name": d.name, "id": d.id} for d in response.value]
    return json.dumps(asyncio.run(inner()))

@mcp.tool(name="sharepoint_list_site_drive_items")
def sharepoint_list_site_drive_items(email: str, site_id: str, folder_id: str = "root") -> str:
    """List items in a SharePoint site folder."""
    async def inner():
        folder_id_sanitized = sanitize_folder_id(folder_id)
        client = await get_graph_client(email)
        if not client:
            return "❌ Not authorized."

        try:
            # Validate and get site's drive
            drive = await client.sites.by_site_id(site_id).drive.get()
            resp = await client.drives \
                .by_drive_id(drive.id) \
                .items \
                .by_drive_item_id(folder_id_sanitized) \
                .children \
                .get()

            return [
                {
                    "name": item.name,
                    "id": item.id,
                    "web_url": item.web_url,
                    "is_folder": bool(item.folder),
                }
                for item in resp.value
            ]
        except Exception as e:
            return f"❌ Error accessing SharePoint site drive items: {str(e)}"

    return json.dumps(asyncio.run(inner()))

@mcp.tool(name="onedrive_search_any")
def search_any(
    email: str,
    keyword: str,
    scope: str = "both",  # 'file', 'folder', 'name', or 'both'
    drive_id: Optional[str] = None,
    folder_id: Optional[str] = None,
    file_id: Optional[str] = None
) -> str:
    """Unified wrapper: search by name, keywords in content, or both inside OneDrive."""

    if scope == "file":
        if not file_id or not drive_id:
            return "❌ file_id and drive_id are required for file scope"
        return search_file_content(email, drive_id, file_id, keyword)

    elif scope == "folder":
        if not folder_id or not drive_id:
            return "❌ folder_id and drive_id are required for folder scope"
        return search_folder_for_content(email, drive_id, folder_id, keyword)

    elif scope == "name":
        return find_files_by_name(email, keyword, folder_id or "root")

    elif scope == "both":
        name_results_raw = find_files_by_name(email, keyword, folder_id or "root")
        try:
            name_results = json.loads(name_results_raw)
            if not isinstance(name_results, list):
                return f"❌ Unexpected result from name search: {name_results_raw}"
        except Exception:
            return f"❌ Failed to parse name search result: {name_results_raw}"

        if not name_results:
            return "No files matched by name."

        matches = []
        for f in name_results:
            try:
                content_result_raw = search_file_content(email, drive_id, f["id"], keyword)
                content_result = json.loads(content_result_raw)
                if content_result.get("match"):
                    matches.append(content_result)
            except Exception as e:
                matches.append({"file_name": f.get("name"), "error": str(e)})

        return json.dumps(matches, indent=2) if matches else "No matching content found in name-matched files."

    return "❌ Invalid scope. Use 'file', 'folder', 'name', or 'both'."





# ────── OAuth Endpoints ──────
async def authorize(request: Request):
    return RedirectResponse(build_auth_url())

async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "client_id": CLIENT_ID,
        "scope": " ".join(SCOPES),
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
        "client_secret": CLIENT_SECRET,
    }

    token_resp = requests.post(TOKEN_URL, data=data).json()
    access_token = token_resp.get("access_token")
    if not access_token:
        return JSONResponse({"error": "OAuth failed", "details": token_resp}, status_code=400)

    userinfo = requests.get(
        "https://graph.microsoft.com/v1.0/me",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()
    email = userinfo.get("userPrincipalName") or userinfo.get("mail")
    if not email:
        return JSONResponse({"error": "Failed to fetch user info"}, status_code=400)

    user_tokens[email] = {
        "access_token": access_token,
        "refresh_token": token_resp.get("refresh_token"),
        "code": code
    }
    return JSONResponse({"message": f"Authenticated as {email}"})

# ────── Starlette App ──────
mcp_app = mcp.http_app(transport="sse")
routes = [
    Mount("/mcp-server", app=mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, port=PORT)
