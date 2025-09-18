# -*- coding: utf-8 -*-
from fastmcp import FastMCP
import requests
from typing import Dict, Optional, Union
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request
import os
import requests
from fastmcp import FastMCP
from dotenv import load_dotenv
from urllib.parse import quote


mcp = FastMCP("gitlab-mcp")



load_dotenv()
mcp = FastMCP("gitlab-mcp")

CLIENT_ID = os.getenv("GITLAB_CLIENT_ID")
CLIENT_SECRET = os.getenv("GITLAB_CLIENT_SECRET")
PORT = int(os.getenv("GITLAB_MCP_PORT", "8004"))
REDIRECT_URI = os.getenv("GITLAB_REDIRECT_URI", f"http://localhost:{PORT}/oauth2callback")
SCOPES = os.getenv("GITLAB_SCOPES", "read_user api").split()

user_tokens = {}



def get_gitlab_creds(metadata: Optional[Dict]) -> Optional[Dict]:
    """
    Extracts GitLab credentials from the provided metadata.

    Args:
        metadata (dict): Metadata dictionary containing GitLab credentials or a nested 'gitlab' key.
    Returns:
        dict or None: Dictionary with 'access_token', 'refresh_token', and 'email', or None if missing.
    """
    creds = metadata.get("gitlab", metadata)
    token = creds.get("access_token")
    email = creds.get("email")
    if not token or not email:
        return None
    return {
        "access_token": token,
        "refresh_token": creds.get("refresh_token"),
        "email": email
    }

@mcp.tool(name="gitlab_get_user")
def get_gitlab_user(metadata: Dict) -> str:
    """
    Fetches the GitLab user profile for the authenticated user.

    Args:
        metadata (dict): Metadata containing GitLab credentials.
    Returns:
        str: User info string or error message.
    """
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    resp = requests.get("https://gitlab.com/api/v4/user", headers=headers)

    if resp.status_code != 200:
        return f"❌ Failed to fetch user: {resp.status_code}"
    user = resp.json()
    return f"👤 {user['username']} ({user['name']}) - {user['email']}"

@mcp.tool(name="gitlab_list_projects")
def list_projects(metadata: Dict) -> str:
    """
    Lists the first 5 GitLab projects for the authenticated user.

    Args:
        metadata (dict): Metadata containing GitLab credentials.
    Returns:
        str: List of project IDs and names or error message.
    """
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    resp = requests.get("https://gitlab.com/api/v4/projects?membership=true&simple=true", headers=headers)

    if resp.status_code != 200:
        return f"❌ Failed to fetch projects: {resp.status_code}"
    projects = resp.json()
    return "\n".join([f"{p['id']}: {p['name']}" for p in projects[:5]]) or "No projects found."


def refresh_gitlab_token(refresh_token: str) -> Optional[Dict]:
    """
    Refreshes the GitLab OAuth token using the provided refresh token.

    Args:
        refresh_token (str): The refresh token.
    Returns:
        dict or None: New token dictionary if successful, else None.
    """
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": os.getenv("GITLAB_CLIENT_ID"),
        "client_secret": os.getenv("GITLAB_CLIENT_SECRET")
    }
    resp = requests.post("https://gitlab.com/oauth/token", data=data)
    return resp.json() if resp.status_code == 200 else None


@mcp.tool(name="gitlab_create_issue")
def create_issue(metadata: Dict, project_id: str, title: str, description: str = "") -> str:
    """
    Creates a new issue in the specified GitLab project.

    Args:
        metadata (dict): Metadata containing GitLab credentials.
        project_id (str): The GitLab project ID.
        title (str): The issue title.
        description (str, optional): The issue description.
    Returns:
        str: Success message with issue info or error message.
    """
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    data = {"title": title, "description": description}
    resp = requests.post(f"https://gitlab.com/api/v4/projects/{project_id}/issues", headers=headers, data=data)

    if resp.status_code != 201:
        return f"❌ Failed to create issue: {resp.status_code} - {resp.text}"
    issue = resp.json()
    return f"✅ Created issue #{issue['iid']} in project {project_id}: {issue['title']}"


@mcp.tool(name="gitlab_list_issues")
def list_issues(metadata: Dict, project_id: str) -> str:
    """
    Lists the first 5 issues for a given GitLab project.

    Args:
        metadata (dict): Metadata containing GitLab credentials.
        project_id (str): The GitLab project ID.
    Returns:
        str: List of issues or error message.
    """
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    resp = requests.get(f"https://gitlab.com/api/v4/projects/{project_id}/issues", headers=headers)

    if resp.status_code != 200:
        return f"❌ Failed to fetch issues: {resp.status_code}"
    issues = resp.json()
    return "\n".join([f"#{i['iid']}: {i['title']}" for i in issues[:5]]) or "No issues found."


@mcp.tool(name="gitlab_comment_on_issue")
def comment_on_issue(metadata: Dict, project_id: str, issue_iid: str, comment: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"
    
    url = f"https://gitlab.com/api/v4/projects/{project_id}/issues/{issue_iid}/notes"
    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    data = {"body": comment}
    resp = requests.post(url, headers=headers, data=data)
    
    if resp.status_code != 201:
        return f"❌ Failed to add comment: {resp.status_code} - {resp.text}"
    return f"✅ Comment added to issue #{issue_iid}"


@mcp.tool(name="gitlab_list_merge_requests")
def list_merge_requests(metadata: Dict, project_id: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"
    
    url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests"
    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    resp = requests.get(url, headers=headers)
    
    if resp.status_code != 200:
        return f"❌ Failed to list merge requests: {resp.status_code}"
    
    mrs = resp.json()
    return "\n".join([f"!{mr['iid']}: {mr['title']} (state: {mr['state']})" for mr in mrs[:5]]) or "No merge requests found."


@mcp.tool(name="gitlab_create_merge_request")
def create_merge_request(metadata: Dict, project_id: str, source_branch: str, target_branch: str, title: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests"
    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    data = {
        "source_branch": source_branch,
        "target_branch": target_branch,
        "title": title
    }
    resp = requests.post(url, headers=headers, data=data)

    if resp.status_code != 201:
        return f"❌ Failed to create merge request: {resp.status_code} - {resp.text}"
    mr = resp.json()
    return f"✅ Created merge request !{mr['iid']}: {mr['title']}"


@mcp.tool(name="gitlab_trigger_pipeline")
def trigger_pipeline(metadata: Dict, project_id: str, ref: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"
    
    url = f"https://gitlab.com/api/v4/projects/{project_id}/pipeline"
    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    data = {"ref": ref}
    resp = requests.post(url, headers=headers, data=data)
    
    if resp.status_code != 201:
        return f"❌ Failed to trigger pipeline: {resp.status_code} - {resp.text}"
    pipeline = resp.json()
    return f"✅ Pipeline triggered on {ref} (id: {pipeline['id']})"

@mcp.tool(name="gitlab_search_files")
def search_files(metadata: Dict, project_id: str, filename: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/search"
    params = {"scope": "blobs", "search": filename}
    resp = requests.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        return f"❌ Failed to search files: {resp.status_code}"

    matches = resp.json()
    return "\n".join(f"{m['filename']}" for m in matches) or "No matches found."

@mcp.tool(name="gitlab_search_file_content")
def search_file_content(metadata: Dict, project_id: str, keyword: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/search"
    params = {"scope": "blobs", "search": keyword}
    resp = requests.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        return f"❌ Failed to search file content: {resp.status_code}"

    results = resp.json()
    return "\n".join(f"{r['path']} → {r['data']}" for r in results[:5]) or "No content match found."

@mcp.tool(name="gitlab_create_commit")
def create_commit(metadata: Dict, project_id: str, branch: str, file_path: str, content: str, commit_message: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/commits"

    payload = {
        "branch": branch,
        "commit_message": commit_message,
        "actions": [
            {
                "action": "update",  # can be 'create' if it's a new file
                "file_path": file_path,
                "content": content
            }
        ]
    }

    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code not in [200, 201]:
        return f"❌ Commit failed: {resp.status_code} - {resp.text}"

    return f"✅ Commit created: {resp.json().get('short_id')}"


@mcp.tool(name="gitlab_list_repo_folder")
def list_repo_folder(metadata: Dict, project_id: str, path: str = "") -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/tree"
    params = {"path": path}
    resp = requests.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        return f"❌ Failed to list folder: {resp.status_code}"

    items = resp.json()
    return "\n".join(f"{i['type']}: {i['name']}" for i in items) or "Folder is empty."

@mcp.tool(name="gitlab_list_branches")
def list_branches(metadata: Dict, project_id: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/branches"
    resp = requests.get(url, headers=headers)

    if resp.status_code != 200:
        return f"❌ Failed to list branches: {resp.status_code}"

    branches = resp.json()
    return "\n".join(b["name"] for b in branches) or "No branches found."


@mcp.tool(name="gitlab_create_branch")
def create_branch(metadata: Dict, project_id: str, new_branch: str, from_branch: str = "main") -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/branches"
    params = {"branch": new_branch, "ref": from_branch}
    resp = requests.post(url, headers=headers, params=params)

    if resp.status_code != 201:
        return f"❌ Failed to create branch: {resp.status_code} - {resp.text}"

    return f"✅ Branch '{new_branch}' created from '{from_branch}'"


@mcp.tool(name="gitlab_delete_branch")
def delete_branch(metadata: Dict, project_id: str, branch_name: str) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/branches/{branch_name}"
    resp = requests.delete(url, headers=headers)

    if resp.status_code == 204:
        return f"✅ Branch '{branch_name}' deleted successfully."
    elif resp.status_code == 404:
        return f"❌ Branch '{branch_name}' not found."
    else:
        return f"❌ Failed to delete branch: {resp.status_code} - {resp.text}"


@mcp.tool(name="gitlab_merge_merge_request")
def merge_merge_request(metadata: Dict, project_id: str, mr_id: int) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {
        "Authorization": f"Bearer {creds['access_token']}",
        "Content-Type": "application/json"
    }
    url = f"https://gitlab.com/api/v4/projects/{project_id}/merge_requests/{mr_id}/merge"
    resp = requests.put(url, headers=headers)

    if resp.status_code == 200:
        return f"✅ Merge Request #{mr_id} was merged."
    elif resp.status_code == 405:
        return f"❌ Merge Request #{mr_id} is not mergeable (conflicts or not ready)."
    else:
        return f"❌ Failed to merge MR: {resp.status_code} - {resp.text}"


@mcp.tool(name="gitlab_recent_commits")
def recent_commits(metadata: Dict, project_id: str, branch_name: str = "main", limit: int = 5) -> str:
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing."

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/commits"
    params = {"ref_name": branch_name, "per_page": limit}
    resp = requests.get(url, headers=headers, params=params)

    if resp.status_code != 200:
        return f"❌ Failed to fetch commits: {resp.status_code} - {resp.text}"

    commits = resp.json()
    return "\n".join(f"{c['short_id']} - {c['title']}" for c in commits) or "No commits found."


@mcp.tool(name="gitlab_search_projects_by_name")
def gitlab_search_projects_by_name(name: str, metadata: Dict) -> str:
    """
    Search for GitLab projects by name (case-insensitive).
    Returns up to 5 results with basic info (id, name, path).
    """
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    headers = {"Authorization": f"Bearer {creds['access_token']}"}
    params = {
        "search": name,
        "simple": "true",
        "per_page": 5
    }

    resp = requests.get("https://gitlab.com/api/v4/projects", headers=headers, params=params)
    if resp.status_code != 200:
        return f"❌ Failed to search projects: {resp.status_code}"

    projects = resp.json()
    if not projects:
        return f"No projects found for '{name}'"

    return "\n".join(
        f"📦 ID: {p['id']}, Name: {p['name']}, Path: {p['path_with_namespace']}, Visibility: {p['visibility']}"
        for p in projects
    )


@mcp.tool(name="gitlab_create_or_update_file")
def gitlab_create_or_update_file(
    project_id: str,
    branch: str,
    file_path: str,
    content: str,
    commit_message: str,
    metadata: Dict
) -> str:
    """
    Creates or updates a file in the specified GitLab project and branch.
    """
    creds = get_gitlab_creds(metadata)
    if not creds:
        return "❌ GitLab credentials missing"

    url = f"https://gitlab.com/api/v4/projects/{project_id}/repository/files/{quote(file_path, safe='')}"
    headers = {
        "Authorization": f"Bearer {creds['access_token']}",
        "Content-Type": "application/json"
    }
    payload = {
        "branch": branch,
        "content": content,
        "commit_message": commit_message,
        "encoding": "text"
    }

    # Try creating the file first (POST)
    resp = requests.post(url, headers=headers, json=payload)
    if resp.status_code == 400 and "already exists" in resp.text:
        # File exists, update instead (PUT)
        resp = requests.put(url, headers=headers, json=payload)

    if resp.status_code in (200, 201):
        return f"✅ File '{file_path}' committed to branch '{branch}'."
    else:
        return f"❌ File commit failed: {resp.status_code} - {resp.text}"

@mcp.tool
def gitlab_close_merge_request(
    project_id: str,
    merge_request_iid: Union[str, int],
    metadata: Optional[dict] = None
) -> str:
    """
    Closes a merge request in a GitLab project without merging.

    Args:
        project_id: The GitLab project ID (not project name).
        merge_request_iid: The internal ID of the merge request.
        metadata: Should contain 'access_token' and optionally 'gitlab_base_url'.

    Returns:
        A success message if the MR was closed, or error text.
    """
    access_token = metadata["gitlab"]["access_token"]
    base_url = metadata["gitlab"].get("gitlab_base_url", "https://gitlab.com")

    url = f"{base_url}/api/v4/projects/{quote(str(project_id), safe='')}/merge_requests/{merge_request_iid}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"state_event": "close"}

    response = requests.put(url, headers=headers, json=payload)

    if response.status_code == 200:
        return f"Merge request #{merge_request_iid} has been closed successfully."
    else:
        return f"Failed to close merge request #{merge_request_iid}: {response.status_code} - {response.text}"


@mcp.tool
def gitlab_revert_commit(
    project_id: str,
    commit_sha: str,
    branch: str,
    metadata: Optional[dict] = None
) -> str:
    """
    Reverts a specific commit in a GitLab project by creating a new commit on the specified branch.

    Args:
        project_id: The GitLab project ID (not project name).
        commit_sha: The SHA of the commit to revert.
        branch: The branch where the revert should be applied.
        metadata: Should contain 'access_token' and optionally 'gitlab_base_url'.

    Returns:
        Success message or error details.
    """
    from urllib.parse import quote  # Ensure 'quote' is available
    access_token = metadata["gitlab"]["access_token"]
    base_url = metadata["gitlab"].get("gitlab_base_url", "https://gitlab.com")

    url = f"{base_url}/api/v4/projects/{quote(str(project_id), safe='')}/repository/commits/{commit_sha}/revert"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    payload = {"branch": branch}

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 201:
        return f"Successfully reverted commit {commit_sha} on branch '{branch}'."
    else:
        return f"Failed to revert commit {commit_sha}: {response.status_code} - {response.text}"


@mcp.tool
def gitlab_revert_merge_request(
    project_id: str,
    merge_request_iid: Union[str, int],
    target_branch: Optional[str] = None,
    metadata: Optional[dict] = None
) -> str:
    """
    Reverts a merged merge request in a GitLab project.
    """
    from urllib.parse import quote

    gitlab_meta = metadata.get("gitlab", metadata or {})
    access_token = gitlab_meta["access_token"]
    base_url = gitlab_meta.get("gitlab_base_url", "https://gitlab.com")

    url = f"{base_url}/api/v4/projects/{quote(str(project_id), safe='')}/merge_requests/{merge_request_iid}/revert"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    payload = {}
    if target_branch:
        payload["target_branch"] = target_branch

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code == 201:
        return f"Successfully reverted merge request #{merge_request_iid}."
    elif response.status_code == 409:
        return f"Cannot revert MR #{merge_request_iid}: Conflict or already reverted."
    else:
        return f"Failed to revert MR #{merge_request_iid}: {response.status_code} - {response.text}"




# Authorize

async def authorize(request: Request):
    """
    Initiates the GitLab OAuth2 authorization flow by redirecting to the GitLab authorization URL.

    Args:
        request (Request): The incoming HTTP request.
    Returns:
        RedirectResponse: Redirects the user to GitLab's OAuth2 authorization page.
    """
    scope = "+".join(SCOPES)
    url = (
        f"https://gitlab.com/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
    )
    return RedirectResponse(url)

async def oauth2callback(request: Request):
    """
    Handles the OAuth2 callback from GitLab, exchanges code for tokens, and returns user info.

    Args:
        request (Request): The incoming HTTP request with the authorization code.
    Returns:
        JSONResponse: Contains authentication result and tokens or error details.
    """
    code = request.query_params.get("code")
    if not code:
        return JSONResponse({"error": "Missing code"}, status_code=400)

    token_resp = requests.post(
        "https://gitlab.com/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": REDIRECT_URI,
        },
        headers={"Accept": "application/json"}
    )

    if token_resp.status_code != 200:
        return JSONResponse({"error": "Token exchange failed", "details": token_resp.text}, status_code=400)

    tokens = token_resp.json()
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    user_info = requests.get(
        "https://gitlab.com/api/v4/user",
        headers={"Authorization": f"Bearer {access_token}"}
    ).json()
    email = user_info.get("email")

    # Store temporarily for manual testing
    # user_tokens[email] = {
    #     "access_token": access_token,
    #     "refresh_token": refresh_token,
    #     "email": email,
    # }

    return JSONResponse({
        "message": f"Authenticated as {email}",
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
    })

async def status(request: Request):
    """
    Returns the authentication status for a given email.

    Args:
        request (Request): The incoming HTTP request with the email query param.
    Returns:
        JSONResponse: Status of authentication for the email.
    """
    email = request.query_params.get("email")
    return JSONResponse({"status": "authenticated" if email in user_tokens else "pending"})

# Optional debug tool
@mcp.tool(name="gitlab_list_users_for_test")
def list_users(metadata: dict = {}) -> str:
    """
    Lists all authorized user emails for testing/debugging.

    Args:
        metadata (dict, optional): Not used.
    Returns:
        str: List of authorized user emails or a message if none.
    """
    if not user_tokens:
        return "No users authorized yet."
    return "\n".join(user_tokens.keys())


# mcp_app = mcp.http_app(transport="sse")
# app = Starlette(routes=[Mount("/mcp-server", mcp_app)], lifespan=mcp_app.lifespan)
# # MCP app + routes
mcp_app = mcp.http_app(transport="sse")
routes = [
    Mount("/mcp-server", mcp_app),
    Route("/authorize", authorize),
    Route("/oauth2callback", oauth2callback),
    Route("/status", status),
]
app = Starlette(routes=routes, lifespan=mcp_app.lifespan)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)

