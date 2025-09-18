# -*- coding: utf-8 -*-
import os
import json
import requests
from typing import Dict
from dotenv import load_dotenv
from github import Github
from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, RedirectResponse
from starlette.requests import Request

# ─── Config ──────────────────────────────────────────────────────
load_dotenv()
CLIENT_ID = os.getenv("GITHUB_CLIENT_ID")
CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET")
PORT = 8010
SCOPES = [
    "repo",
    "read:user",
    "user:email",
    "write:discussion",
    "workflow"
]
REDIRECT_URI = f"http://localhost:{PORT}/oauth2callback"
AUTH_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
EMAIL_API = "https://api.github.com/user/emails"
USER_API = "https://api.github.com/user"

# ─── In-memory Token Store ───────────────────────────────
user_tokens: Dict[str, Dict] = {}

# ─── MCP Setup ───────────────────────────────
mcp = FastMCP("github-auth-mcp", host="0.0.0.0", port=PORT)

# ─── Tool Helpers ─────────────────────────────

def get_github_client(email: str) -> Github:
    if email not in user_tokens:
        raise Exception("❌ Email not authenticated. Kindly authenticate or provide correct email id.")
    return Github(user_tokens[email]["access_token"])

def _generate_github_auth_url() -> str:
    scope = "+".join(SCOPES)
    return f"{AUTH_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&scope={scope}&response_type=code"


@mcp.tool(name="github_list_pull_requests")
def github_list_pull_requests(email: str, repo_name: str, state: str = "open") -> str:
    """List pull requests in a repository (state: open, closed, all) as a formatted string."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    pulls = repo.get_pulls(state=state)

    result = ""
    for pr in pulls:
        result += f"- #{pr.number}: **{pr.title}** [{pr.state}] - {pr.html_url}\n"

    return result or f"No {state} pull requests found in repository."

@mcp.tool(name="github_create_branch")
def github_create_branch(email: str, repo_name: str, new_branch: str, base_branch: str) -> str:
    """Create a new branch from an existing one."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    source = repo.get_branch(base_branch)
    repo.create_git_ref(ref=f"refs/heads/{new_branch}", sha=source.commit.sha)
    return f"Created new branch '{new_branch}' from '{base_branch}'"

@mcp.tool(name="github_list_files_in_path")
def github_list_files_in_path(email: str, repo_name: str, path: str = "", ref: str = "main") -> str:
    """List files in a repository path at a given ref (formatted string)."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    contents = repo.get_contents(path, ref=ref)

    files = [item.path for item in contents if item.type == "file"]
    result = "\n".join(f"- {file_path}" for file_path in files)

    return result or f"No files found at path '{path}' on branch '{ref}'."

@mcp.tool(name="github_list_folders_in_path")
def github_list_folders_in_path(email: str, repo_name: str, path: str = "", ref: str = "main") -> str:
    """List folders in a repository path at a given ref (formatted string)."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    contents = repo.get_contents(path, ref=ref)

    folders = [item.path for item in contents if item.type == "dir"]
    result = "\n".join(f"- {folder_path}/" for folder_path in folders)

    return result or f"No folders found at path '{path}' on branch '{ref}'."

@mcp.tool(name="github_get_file_content")
def github_get_file_content(email: str, repo_name: str, file_path: str, ref: str = "main") -> str:
    """Get raw file content from a repo."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    file = repo.get_contents(file_path, ref=ref)
    return file.decoded_content.decode("utf-8")

@mcp.tool(name="github_search_files")
def github_search_files(email: str, repo_name: str, query: str, ref: str = "main") -> str:
    """Search for files by name (or partial match) in a GitHub repo."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    results = []

    def walk_dir(path=""):
        try:
            contents = repo.get_contents(path, ref=ref)
            for item in contents:
                if item.type == "file" and query.lower() in item.name.lower():
                    results.append(f"- `{item.path}` ({item.download_url})")
                elif item.type == "dir":
                    walk_dir(item.path)
        except Exception:
            pass

    walk_dir()
    return "\n".join(results) or f"No files found matching '{query}' in repo '{repo_name}' on branch '{ref}'."

@mcp.tool(name="github_delete_branch")
def github_delete_branch(email: str, repo_name: str, branch_name: str) -> str:
    """Delete a branch from a GitHub repository (must not be the default branch)."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)

    if branch_name == repo.default_branch:
        return f"Cannot delete the default branch '{branch_name}'."

    try:
        ref = repo.get_git_ref(f"heads/{branch_name}")
        ref.delete()
        return f"Branch '{branch_name}' deleted successfully from repository '{repo_name}'."
    except Exception as e:
        return f"Failed to delete branch '{branch_name}': {str(e)}"



@mcp.tool(name="github_create_pull_request")
def github_create_pull_request(email: str, repo_name: str, source_branch: str, target_branch: str, title: str, body: str = "") -> str:
    """Create a pull request from source_branch to target_branch in the given repository."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    pr = repo.create_pull(title=title, body=body, head=source_branch, base=target_branch)
    return f"Pull request created: {pr.html_url}"

@mcp.tool(name="github_close_pull_request")
def github_close_pull_request(email: str, repo_name: str, pr_number: int) -> str:
    """Close a pull request by its number."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    pr.edit(state="closed")
    return f"Pull request #{pr_number} closed."

@mcp.tool(name="github_create_commit")
def github_create_commit(email: str, repo_name: str, branch: str, file_path: str, message: str, content: str) -> str:
    """Create a commit to modify or add a file in the specified repository and branch."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    try:
        contents = repo.get_contents(file_path, ref=branch)
        repo.update_file(contents.path, message, content, contents.sha, branch=branch)
    except:
        repo.create_file(file_path, message, content, branch=branch)
    return f"Commit pushed to {repo_name}@{branch} modifying {file_path}"

@mcp.tool(name="github_merge_pull_request")
def github_merge_pull_request(email: str, repo_name: str, pr_number: int, merge_message: str = "") -> str:
    """Merge a pull request by its number with an optional message."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    result = pr.merge(commit_message=merge_message)
    return f"PR #{pr_number} merge status: {result.merged}"

@mcp.tool(name="github_search_file_contents")
def github_search_file_contents(email: str, repo_name: str, keyword: str, ref: str = "main") -> str:
    """Search for a keyword inside file contents in a GitHub repository."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    matches = []

    def walk_and_search(path=""):
        try:
            contents = repo.get_contents(path, ref=ref)
            for item in contents:
                if item.type == "dir":
                    walk_and_search(item.path)
                elif item.type == "file":
                    try:
                        content = repo.get_contents(item.path, ref=ref).decoded_content.decode("utf-8", errors="ignore")
                        if keyword.lower() in content.lower():
                            matches.append(f"- `{item.path}` ({item.download_url})")
                    except Exception:
                        continue
        except Exception:
            pass

    walk_and_search()
    return "\n".join(matches) or f"No file contents matched '{keyword}' in repo '{repo_name}' on branch '{ref}'."

@mcp.tool(name="github_list_user_repositories")
def github_list_user_repositories(email: str) -> str:
    """List all repositories accessible by the authenticated user."""
    gh = get_github_client(email)
    repos = gh.get_user().get_repos()
    return "\n".join([f"- {repo.full_name}" for repo in repos]) or "No repositories found."

@mcp.tool(name="github_list_open_issues")
def github_list_open_issues(email: str, repo_name: str) -> str:
    """List open issues (excluding pull requests) in a GitHub repository."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    open_issues = repo.get_issues(state="open")

    result = ""
    for issue in open_issues:
        # if hasattr(issue, "pull_request"):
        #     continue
        result += f"- #{issue.number}: **{issue.title}** (created at {issue.created_at})\n"

    return result or "No open issues (excluding pull requests) found."

@mcp.tool(name="github_list_closed_issues")
def github_list_closed_issues(email: str, repo_name: str) -> str:
    """List closed issues (excluding pull requests) in a GitHub repository."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    closed_issues = repo.get_issues(state="closed")

    result = ""
    for issue in closed_issues:
        if hasattr(issue, "pull_request"):
            continue
        result += f"- #{issue.number}: **{issue.title}** (closed at {issue.closed_at})\n"

    return result or "No closed issues (excluding pull requests) found."

@mcp.tool(name="github_issue_counts")
def github_issue_counts(email: str, repo_name: str) -> str:
    """Return the count of open and closed issues in a GitHub repository."""
    gh = get_github_client(email)
    repo = gh.get_repo(repo_name)
    open_issues = list(repo.get_issues(state="open"))
    closed_issues = list(repo.get_issues(state="closed"))

    return (
        f"Issue counts for `{repo_name}`:\n"
        f"- Open issues: {len(open_issues)}\n"
        f"- Closed issues: {len(closed_issues)}\n"
        f"- Total: {len(open_issues) + len(closed_issues)}"
    )

@mcp.tool(name="github_get_authorization_url")
def github_get_authorization_url() -> str:
    """Get the URL users should visit to authenticate with GitHub."""
    return _generate_github_auth_url()

@mcp.tool(name="github_list_authorized_accounts")
def github_list_authorized_accounts() -> str:
    """List all GitHub email accounts that have been authenticated."""
    return "\n".join(user_tokens.keys()) if user_tokens else "No accounts authorized."

# ─── OAuth Routes ────────────────────────────
async def authorize(request: Request):
    return RedirectResponse(_generate_github_auth_url())

async def oauth2callback(request: Request):
    code = request.query_params.get("code")
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    headers = {"Accept": "application/json"}
    token_response = requests.post(TOKEN_URL, data=data, headers=headers).json()
    access_token = token_response.get("access_token")

    if not access_token:
        return JSONResponse({"error": "Failed to get access_token", "details": token_response}, status_code=400)

    user_profile = requests.get(USER_API, headers={"Authorization": f"token {access_token}"}).json()
    primary_email = user_profile.get("email")

    if not primary_email:
        email_list = requests.get(EMAIL_API, headers={"Authorization": f"token {access_token}"}).json()
        if isinstance(email_list, list):
            primary_email = next((e["email"] for e in email_list if e.get("primary")), None)

    if not primary_email:
        return JSONResponse({"error": "Could not fetch email"}, status_code=400)

    user_tokens[primary_email] = {"access_token": access_token}
    return JSONResponse({"message": f"Authenticated as {primary_email}"})

async def status(request: Request):
    email = request.query_params.get("email")
    if email in user_tokens:
        return JSONResponse({"status": "authenticated"})
    return JSONResponse({"status": "unauthenticated"})

# ─── Starlette App ────────────────────────
mcp_app = mcp.http_app(transport="sse")
app = Starlette(
    routes=[
        Mount("/mcp-server", app=mcp_app),
        Route("/authorize", authorize),
        Route("/oauth2callback", oauth2callback),
        Route("/status", status),
    ],
    lifespan=mcp_app.lifespan
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)