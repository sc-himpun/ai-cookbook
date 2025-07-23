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
PORT = int(os.getenv("AZURE_MCP_PORT", "8007")) 
# PORT = 8007
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


def get_onedrive_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    if not metadata:
        return None

    creds = metadata
    access_token = creds.get("access_token")
    refresh_token = creds.get("refresh_token")

    if access_token and is_token_valid(access_token):
        return creds

    # Attempt refresh if access token is missing or expired
    if refresh_token:
        resp = requests.post(TOKEN_URL, data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "redirect_uri": REDIRECT_URI,
        }).json()

        new_access = resp.get("access_token")
        if new_access:
            creds["access_token"] = new_access
            creds["refresh_token"] = resp.get("refresh_token", refresh_token)
            return creds

    return None


def is_token_valid(token: str) -> bool:
    try:
        # JWT expiry check
        import jwt
        payload = jwt.decode(token, options={"verify_signature": False})
        exp = payload.get("exp")
        return exp and datetime.datetime.utcfromtimestamp(exp) > datetime.datetime.utcnow()
    except Exception:
        return False



def sanitize_folder_id(folder_id: Optional[str]) -> str:
    return folder_id if folder_id and folder_id.strip() else "root"


async def get_graph_client(metadata: Dict) -> Optional[GraphServiceClient]:
    creds = get_onedrive_creds(metadata)
    if not creds:
        raise ValueError("Missing or invalid Microsoft Graph credentials.")

    access_token = creds.get("access_token")
    credential = ManualTokenCredential(access_token)
    return GraphServiceClient(credential, scopes=SCOPES)


@mcp.tool(name="onedrive_list_files")
def list_files(metadata: Dict) -> str:
    """List files from the root of the user's default OneDrive."""
    async def inner():
        client = await get_graph_client(metadata)
        if not client:
            return "❌ Not authorized."

        # ✅ Correct way to access root folder contents
        drive = await client.me.drive.get()
        root_children = await client.drives \
            .by_drive_id(drive.id) \
            .items \
            .by_drive_item_id("root") \
            .children \
            .get()

        return [{"name": f.name, "id": f.id} for f in root_children.value]

    return json.dumps(asyncio.run(inner()))



@mcp.tool(name="list_authorized_accounts")
def list_authorized_accounts(metadata: Dict = {}) -> str:
    """
    List all Azure accounts that have been authorized.
    If user_tokens is empty, try metadata for fallback.
    """
    global user_tokens

    if not user_tokens:
        creds = get_onedrive_creds(metadata)
        if creds and "access_token" in creds:
            try:
                access_token = creds["access_token"]
                userinfo = requests.get(
                    "https://graph.microsoft.com/v1.0/me",
                    headers={"Authorization": f"Bearer {access_token}"}
                ).json()

                email = userinfo.get("userPrincipalName") or userinfo.get("mail")
                if not email:
                    return "⚠️ Email not found in metadata token."

                user_tokens[email] = {
                    "access_token": access_token,
                    "refresh_token": creds.get("refresh_token")
                }
            except Exception as e:
                return f"❌ Failed to hydrate from metadata: {e}"

    return "\n".join(user_tokens.keys()) or "No users authorized yet."


@mcp.tool(name="onedrive_get_auth_url")
def get_auth_url() -> str:
    return build_auth_url()


@mcp.tool(name="onedrive_search_file_content")
def search_file_content(metadata: Dict, drive_id: str, file_id: str, keyword: str) -> str:
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
        client = await get_graph_client(metadata)
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
def search_folder_for_content(metadata: Dict, drive_id: str, folder_id: str, keyword: str) -> str:
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
        client = await get_graph_client(metadata)
        if not client:
            return "❌ Not authorized."
        try:
            matches = await recursive_search(client, drive_id, folder_id)
            return json.dumps(matches, indent=2) if matches else "No matching files found."
        except Exception as e:
            return f"❌ Error: {str(e)}"

    return asyncio.run(inner())




@mcp.tool(name="onedrive_get_preferred_drive_id")
def get_preferred_drive_id(metadata: Dict, drive_name: str = "OneDrive") -> str:
    """Get preferred drive ID for the authenticated user by name."""
    async def inner():
        client = await get_graph_client(metadata)
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
def list_children_in_drive_item(metadata: Dict, drive_id: str, folder_id: str = "root") -> str:
    """List children in a given folder of a OneDrive drive."""
    async def inner():
        folder_id_sanitized = sanitize_folder_id(folder_id)
        client = await get_graph_client(metadata)
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
def find_files_by_name(metadata: Dict, keyword: str, folder_id: str = "root") -> str:
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
        client = await get_graph_client(metadata)
        if not client:
            return "❌ Not authorized."
        drive_id = await get_preferred_drive(client)
        return await recursive_search(client, drive_id, folder_id, keyword)

    result = asyncio.run(main())
    return json.dumps(result)

@mcp.tool(name="onedrive_sharepoint_list_all_user_drives")
def list_all_user_drives(metadata: Dict) -> str:
    """List all drives (OneDrive + SharePoint) for the authenticated user."""
    async def inner():
        client = await get_graph_client(metadata)
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
def sharepoint_list_sites(metadata: Dict) -> str:
    """List available SharePoint sites using delegated auth and raw Graph API call."""

    import aiohttp

    async def inner():
        creds = get_onedrive_creds(metadata)
        access_token = creds["access_token"]
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json"
        }

        url = "https://graph.microsoft.com/v1.0/sites?search=*"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    return f"❌ Error fetching SharePoint sites: {resp.status} - {body}"
                data = await resp.json()

        sites = [
            {
                "name": site.get("name"),
                "id": site.get("id"),
                "web_url": site.get("webUrl")
            }
            for site in data.get("value", [])
        ]
        return json.dumps(sites, indent=2)

    return asyncio.run(inner())


@mcp.tool(name="sharepoint_list_document_libraries")
def sharepoint_list_document_libraries(metadata: Dict, site_id: str) -> str:
    """List document libraries in a site (drives)."""
    async def inner():
        client = await get_graph_client(metadata)
        if not client:
            return "❌ Not authorized."

        try:
            site_parts = site_id.split(",")
            site_id_only = site_parts[1]

            drives = await client.sites.by_site_id(site_id_only).drives.get()
            return [
                {"name": d.name, "id": d.id}
                for d in drives.value
            ]
        except Exception as e:
            return f"❌ Error listing document libraries: {str(e)}"

    return json.dumps(asyncio.run(inner()))


# @mcp.tool(name="sharepoint_list_document_libraries")
# def sharepoint_list_document_libraries(metadata: Dict, site_id: str) -> str:
#     """List all document libraries in a SharePoint site."""
#     async def inner():
#         client = await get_graph_client(metadata)
#         if not client:
#             return "❌ Not authorized."
#         response = await client.sites.by_site_id(site_id).drives.get()
#         return [{"name": d.name, "id": d.id} for d in response.value]
#     return json.dumps(asyncio.run(inner()))

# @mcp.tool(name="sharepoint_list_site_drive_items")
# def sharepoint_list_site_drive_items(metadata: Dict, site_id: str, folder_id: str = "root") -> str:
#     """List items in a SharePoint site folder."""
#     async def inner():
#         folder_id_sanitized = sanitize_folder_id(folder_id)
#         client = await get_graph_client(metadata)
#         if not client:
#             return "❌ Not authorized."

#         try:
#             # Validate and get site's drive
#             drive = await client.sites.by_site_id(site_id).drive.get()
#             resp = await client.drives \
#                 .by_drive_id(drive.id) \
#                 .items \
#                 .by_drive_item_id(folder_id_sanitized) \
#                 .children \
#                 .get()

#             return [
#                 {
#                     "name": item.name,
#                     "id": item.id,
#                     "web_url": item.web_url,
#                     "is_folder": bool(item.folder),
#                 }
#                 for item in resp.value
#             ]
#         except Exception as e:
#             return f"❌ Error accessing SharePoint site drive items: {str(e)}"

#     return json.dumps(asyncio.run(inner()))

@mcp.tool(name="sharepoint_list_drive_items")
def sharepoint_list_drive_items(metadata: Dict, drive_id: str, folder_id: str = "root") -> str:
    """List items in a SharePoint drive folder."""
    async def inner():
        folder_id_sanitized = sanitize_folder_id(folder_id)
        client = await get_graph_client(metadata)
        if not client:
            return "❌ Not authorized."

        try:
            resp = await client.drives \
                .by_drive_id(drive_id) \
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
            return f"❌ Error accessing SharePoint drive items: {str(e)}"

    return json.dumps(asyncio.run(inner()))





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
    
    refresh_token = token_resp.get("refresh_token", None)

    # user_tokens[email] = {
    #     "access_token": access_token,
    #     "refresh_token": token_resp.get("refresh_token"),
    #     "code": code
    # }
    
    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "code":code
    })




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
    uvicorn.run(app, host="0.0.0.0", port=PORT)
