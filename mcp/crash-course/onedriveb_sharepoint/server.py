# -*- coding: utf-8 -*-
from mcp.server.fastmcp import FastMCP
from msgraph import GraphServiceClient
from azure.identity import ClientSecretCredential
from dotenv import load_dotenv
import os
import asyncio
import json
import nest_asyncio
nest_asyncio.apply()


# ─── Load Environment Variables ──────────────────────────────────────────────
load_dotenv()
print (os.getenv("ONEDRIVE_TENANT_ID"), os.getenv("ONEDRIVE_CLIENT_ID"), os.getenv("ONEDRIVE_CLIENT_SECRET"))
# ─── Initialize Credential and Graph Client (App-Only Auth) ──────────────────
tenant_id = os.getenv("ONEDRIVE_TENANT_ID")
client_id = os.getenv("ONEDRIVE_CLIENT_ID")
client_secret = os.getenv("ONEDRIVE_CLIENT_SECRET")

scopes = ["https://graph.microsoft.com/.default"]
credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
)
graph_client = GraphServiceClient(credential, scopes)

# ─── Initialize MCP Server ────────────────────────────────────────────────────
mcp = FastMCP(name="OneDriveSharepointBusinessToolkit", host="0.0.0.0", port=8057)

# ─── Tool: Get Preferred Drive ID ─────────────────────────────────────────────
@mcp.tool(name="onedrive_get_preferred_drive_id")
def get_preferred_drive_id(user_id: str, drive_name: str = "OneDrive") -> str:
    """
    Return the drive ID for the given user and drive name (default is 'OneDrive'), it could be a sharepoint drive as well like PersonalCacheLibrary.
    Falls back to the first available drive if no match found.
    """
    async def inner():
        result = await graph_client.users.by_user_id(user_id).drives.get()
        drives = result.value
        for drive in drives:
            if drive.name.lower() == drive_name.lower():
                return drive.id
        if drives:
            return drives[0].id
        raise Exception("No drives found.")

    drive_id = asyncio.run(inner())
    return json.dumps({"drive_id": drive_id})


# ─── Tool: List Children in Folder ────────────────────────────────────────────
@mcp.tool(name="onedrive_list_folder")
def list_children_in_drive_item(drive_id: str, folder_id: str = "root") -> str:
    """List children in a given folder of a OneDrive drive."""
    async def inner():
        response = await graph_client.drives \
            .by_drive_id(drive_id) \
            .items \
            .by_drive_item_id(folder_id) \
            .children \
            .get()
        return [{"name": item.name, "id": item.id} for item in response.value]
    result = asyncio.run(inner())
    return json.dumps(result)

# ─── Tool: Recursive File Name Search ─────────────────────────────────────────
@mcp.tool(name="onedrive_find_files_by_name")
def find_files_by_name(keyword: str, user_id: str, folder_id: str = "root") -> str:
    """Recursively search for files by name in OneDrive for the given user."""

    async def get_preferred_drive():
        result = await graph_client.users.by_user_id(user_id).drives.get()
        for drive in result.value:
            if drive.name == "OneDrive":
                return drive.id
        if result.value:
            return result.value[0].id
        raise Exception("No drives found.")

    async def recursive_search(drive_id, folder_id, keyword):
        matches = []
        response = await graph_client.drives \
            .by_drive_id(drive_id) \
            .items \
            .by_drive_item_id(folder_id) \
            .children \
            .get()

        for item in response.value:
            if keyword.lower() in item.name.lower():
                matches.append({"name": item.name, "id": item.id, "web_url": item.web_url})
            if item.folder:
                matches += await recursive_search(drive_id, item.id, keyword)
        return matches

    async def main():
        drive_id = await get_preferred_drive()
        return await recursive_search(drive_id, folder_id, keyword)

    result = asyncio.run(main())
    return json.dumps(result)


@mcp.tool(name="onedrive_sharepoint_list_all_user_drives")
def list_all_user_drives(user_id: str) -> str:
    """
    List all drives (OneDrive + others like SharePoint libraries) associated with the given user.
    """
    async def inner():
        result = await graph_client.users.by_user_id(user_id).drives.get()
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



# ─── Tool: List SharePoint Sites ──────────────────────────────────────────────
@mcp.tool(name="sharepoint_list_sites")
def sharepoint_list_sites() -> str:
    """List available SharePoint sites."""
    async def inner():
        result = await graph_client.sites.get()
        return [{"name": site.name, "id": site.id, "web_url": site.web_url} for site in result.value]
    sites = asyncio.run(inner())
    return json.dumps(sites)


@mcp.tool(name="sharepoint_list_document_libraries")
def sharepoint_list_document_libraries(site_id: str) -> str:
    """List all document libraries (drives) in a SharePoint site."""
    async def inner():
        response = await graph_client.sites \
            .by_site_id(site_id) \
            .drives \
            .get()
        return [{"name": d.name, "id": d.id} for d in response.value]

    libraries = asyncio.run(inner())
    return json.dumps(libraries)


@mcp.tool(name="sharepoint_list_site_drive_items")
def sharepoint_list_site_drive_items(site_id: str, folder_id: str = "root") -> str:
    """
    List items in the default document library of a SharePoint site.
    
    - site_id: SharePoint site ID
    - folder_id: item ID of the folder to list (use "root" for top-level)
    """
    async def inner():
        # 1️⃣ Get the default drive for the site
        drive = await graph_client.sites.by_site_id(site_id).drive.get()

        # 2️⃣ Use the drive ID and folder_id to fetch children properly
        resp = await graph_client.drives \
            .by_drive_id(drive.id) \
            .items \
            .by_drive_item_id(folder_id) \
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

    items = asyncio.run(inner())
    return json.dumps(items)





# ─── Run MCP Server ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
