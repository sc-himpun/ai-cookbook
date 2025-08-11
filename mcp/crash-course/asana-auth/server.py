import os
import requests
from typing import Dict, Any, Optional
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
from dotenv import load_dotenv

load_dotenv()

# ─── Config ─────────────────────
CLIENT_ID = os.getenv("ASANA_CLIENT_ID")
CLIENT_SECRET = os.getenv("ASANA_CLIENT_SECRET")
REDIRECT_URI = os.getenv("ASANA_REDIRECT_URI")
# PORT = 8011
PORT =  int(os.getenv("ASANA_PORT", "8011"))
AUTH_URL = "https://app.asana.com/-/oauth_authorize"
TOKEN_URL = "https://app.asana.com/-/oauth_token"
USER_API = "https://app.asana.com/api/1.0/users/me"

mcp = FastMCP("asana-auth-mcp", host="0.0.0.0", port=PORT)

class ToolResponse:
    def __init__(self, content, metadata=None):
        self.content = content
        self.metadata = metadata or {}



# ─── Helpers ─────────────────────
def get_asana_access_token(metadata: Optional[Dict]) -> str:
    if not metadata or "access_token" not in metadata:
        raise Exception("Missing Asana access_token in metadata")
    return metadata["access_token"]

def get_headers(metadata: Dict[str, Any]) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {get_asana_access_token(metadata)}",
        "Content-Type": "application/json"
    }

# ─── Tools ─────────────────────
@mcp.tool(name="asana_list_workspaces")
def asana_list_workspaces(metadata: Dict[str, Any]) -> str:
    """List all workspaces accessible by the authenticated user."""
    resp = requests.get("https://app.asana.com/api/1.0/workspaces", headers=get_headers(metadata))
    data = resp.json().get("data", [])
    return "\n".join([f"- {w['name']} ({w['gid']})" for w in data]) or "No workspaces found."

@mcp.tool(name="asana_list_projects")
def asana_list_projects(metadata: Dict[str, Any], workspace_gid: str) -> str:
    """List all projects in a workspace."""
    url = f"https://app.asana.com/api/1.0/workspaces/{workspace_gid}/projects"
    resp = requests.get(url, headers=get_headers(metadata))
    data = resp.json().get("data", [])
    return "\n".join([f"- {p['name']} ({p['gid']})" for p in data]) or "No projects found."

@mcp.tool(name="asana_list_tasks")
def asana_list_tasks(metadata: Dict[str, Any], project_gid: str) -> str:
    """List tasks in a project."""
    url = f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks"
    resp = requests.get(url, headers=get_headers(metadata))
    data = resp.json().get("data", [])
    return "\n".join([f"- {t['name']} ({t['gid']})" for t in data]) or "No tasks found."

@mcp.tool(name="asana_create_task")
def asana_create_task(metadata: Dict[str, Any], project_gid: str, name: str, notes: str = "") -> str:
    """Create a new task in the specified project."""
    url = "https://app.asana.com/api/1.0/tasks"
    payload = {
        "name": name,
        "notes": notes,
        "projects": [project_gid]
    }
    resp = requests.post(url, headers=get_headers(metadata), json={"data": payload})
    data = resp.json().get("data", {})
    return f"Task created: {data.get('name')} ({data.get('gid')})"


@mcp.tool(name="asana_update_task")
def asana_update_task(metadata: Dict[str, Any], task_gid: str, name: Optional[str] = None, notes: Optional[str] = None) -> str:
    """Update the name or notes of a task."""
    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}"
    payload = {k: v for k, v in {"name": name, "notes": notes}.items() if v}
    resp = requests.put(url, headers=get_headers(metadata), json={"data": payload})
    return "Task updated successfully" if resp.status_code == 200 else "Failed to update task"

@mcp.tool(name="asana_complete_task")
def asana_complete_task(metadata: Dict[str, Any], task_gid: str) -> str:
    """Mark a task as complete."""
    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}"
    resp = requests.put(url, headers=get_headers(metadata), json={"data": {"completed": True}})
    return "Task marked as complete." if resp.status_code == 200 else "Failed to complete task."

@mcp.tool(name="asana_delete_task")
def asana_delete_task(metadata: Dict[str, Any], task_gid: str) -> str:
    """Delete a task."""
    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}"
    resp = requests.delete(url, headers=get_headers(metadata))
    return "Task deleted successfully." if resp.status_code == 200 else "Failed to delete task."

@mcp.tool(name="asana_list_sections")
def asana_list_sections(metadata: Dict[str, Any], project_gid: str) -> str:
    """List sections in a project."""
    url = f"https://app.asana.com/api/1.0/projects/{project_gid}/sections"
    resp = requests.get(url, headers=get_headers(metadata))
    data = resp.json().get("data", [])
    return "\n".join([f"- {s['name']} ({s['gid']})" for s in data]) or "No sections found."


@mcp.tool(name="asana_list_tags")
def asana_list_tags(metadata: Dict[str, Any], workspace_gid: str) -> str:
    """List all tags in a workspace."""
    url = f"https://app.asana.com/api/1.0/workspaces/{workspace_gid}/tags"
    resp = requests.get(url, headers=get_headers(metadata))
    data = resp.json().get("data", [])
    return "\n".join([f"- {t['name']} ({t['gid']})" for t in data]) or "No tags found."


@mcp.tool(name="asana_get_user_info")
def asana_get_user_info(metadata: Dict[str, Any]) -> str:
    """Get authenticated user's info."""
    resp = requests.get(USER_API, headers=get_headers(metadata))
    data = resp.json().get("data", {})
    return f"Name: {data.get('name')}, Email: {data.get('email')}, GID: {data.get('gid')}"


@mcp.tool(name="asana_read_task_comments")
def asana_read_task_comments(task_gid: str, metadata: dict) -> str:
    """Read all comments on an Asana task."""
    access_token = metadata.get("access_token")
    if not access_token:
        return "Missing Asana access token."

    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = requests.get(url, headers=headers)
    if response.status_code != 200:
        return f"Failed to fetch comments: {response.text}"
    
    data = response.json()
    comments = [
        story["text"]
        for story in data.get("data", [])
        if story.get("type") == "comment"
    ]
    
    if not comments:
        return "No comments found."
    
    return "\n\n".join(comments)


@mcp.tool(name="asana_add_task_comment")
def asana_add_task_comment(task_gid: str, comment: str, metadata: dict) -> str:
    """Add a comment to an Asana task."""
    access_token = metadata.get("access_token")
    if not access_token:
        return "Missing Asana access token."

    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "data": {
            "text": comment
        }
    }

    response = requests.post(url, headers=headers, json=payload)
    if response.status_code != 201:
        return f"Failed to add comment: {response.text}"

    return "Comment added successfully."

@mcp.tool(name="asana_move_task_to_section")
def asana_move_task_to_section(task_gid: str, project_gid: str, target_section_name: str, metadata: dict) -> str:
    """Move a task to a specific section in an Asana project (e.g., 'To do', 'In Progress', 'Done')."""
    access_token = metadata.get("access_token")
    if not access_token:
        return "Missing Asana access token."

    # Step 1: Get all sections of the project
    section_url = f"https://app.asana.com/api/1.0/projects/{project_gid}/sections"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(section_url, headers=headers)
    
    if response.status_code != 200:
        return f"Failed to get sections: {response.text}"
    
    sections = response.json().get("data", [])
    target_section = next((s for s in sections if s["name"].lower() == target_section_name.lower()), None)
    
    if not target_section:
        return f"Section '{target_section_name}' not found in project."

    # Step 2: Move task to that section
    move_url = f"https://app.asana.com/api/1.0/sections/{target_section['gid']}/addTask"
    payload = {"data": {"task": task_gid}}
    response = requests.post(move_url, headers=headers, json=payload)

    if response.status_code != 200:
        return f"Failed to move task: {response.text}"

    return f"Task moved to section '{target_section_name}'."


@mcp.tool(name="asana_get_task_custom_fields")
def asana_get_task_custom_fields(task_gid: str, metadata: dict) -> str:
    """Fetch custom fields and their values for a given Asana task."""
    access_token = metadata.get("access_token")
    if not access_token:
        return "Missing Asana access token."

    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}?opt_fields=custom_fields"
    headers = {"Authorization": f"Bearer {access_token}"}
    response = requests.get(url, headers=headers)

    if response.status_code != 200:
        return f"Failed to fetch task fields: {response.text}"

    custom_fields = response.json().get("data", {}).get("custom_fields", [])
    if not custom_fields:
        return "No custom fields found."

    result = []
    for field in custom_fields:
        name = field.get("name")
        gid = field.get("gid")
        field_type = field.get("type")
        value = field.get("display_value")
        enum_options = field.get("enum_options", [])
        options = ", ".join([opt.get("name") for opt in enum_options]) if enum_options else "N/A"
        result.append(f"- **{name}** (GID: {gid}, Type: {field_type}, Value: {value}, Options: {options})")

    return "\n".join(result)

@mcp.tool(name="asana_set_task_custom_field")
def asana_set_task_custom_field(task_gid: str, field_gid: str, enum_option_gid: str, metadata: dict) -> str:
    """
    Set a custom enum field (e.g., status or priority) for a task.
    You must know the custom field's GID and the desired option's GID.
    """
    access_token = metadata.get("access_token")
    if not access_token:
        return "Missing Asana access token."

    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {
        "data": {
            "custom_fields": {
                field_gid: enum_option_gid
            }
        }
    }

    response = requests.put(url, headers=headers, json=payload)
    if response.status_code != 200:
        return f"Failed to update custom field: {response.text}"

    return "Custom field updated successfully."


def _generate_asana_auth_url() -> str:
    return f"{AUTH_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code"

@mcp.tool(name="asana_get_authorization_url")
def asana_get_authorization_url(metadata: Dict[str, Any]) -> str:
    """Tool: Get the authorization URL for Asana login."""
    return _generate_asana_auth_url()


# ─── OAuth Routes ─────────────────────
async def authorize(request: Request):
    return RedirectResponse(_generate_asana_auth_url())

async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error": "No code provided"}, status_code=400)

    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "code": code,
    }

    token_resp = requests.post(TOKEN_URL, data=data).json()
    access_token = token_resp.get("access_token")

    if not access_token:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp}, status_code=400)

    user_resp = requests.get(USER_API, headers={"Authorization": f"Bearer {access_token}"})
    email = user_resp.json().get("data", {}).get("email", "unknown@asana.com")

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token
    })

# ─── App ─────────────────────
mcp_app = mcp.http_app(transport="sse")
app = Starlette(
    routes=[
        Mount("/mcp-server", app=mcp_app),
        Route("/authorize", authorize),
        Route("/oauth2callback", oauth2callback),
    ],
    lifespan=mcp_app.lifespan
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
