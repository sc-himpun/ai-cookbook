from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
from github import Github
import os
# from typing import List, Dict, Any

# Load environment variables
load_dotenv()
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# GitHub client
gh = Github(GITHUB_TOKEN)

# MCP Server
mcp = FastMCP(name="GitHubToolkit", host="0.0.0.0", port=8052)


@mcp.tool(name="github_create_pull_request")
def create_pull_request(repo_name: str, source_branch: str, target_branch: str, title: str, body: str = "") -> str:
    """Create a pull request from source_branch to target_branch."""
    repo = gh.get_repo(repo_name)
    pr = repo.create_pull(title=title, body=body, head=source_branch, base=target_branch)
    return f"Pull request created: {pr.html_url}"


@mcp.tool(name="github_close_pull_request")
def close_pull_request(repo_name: str, pr_number: int) -> str:
    """Close a pull request by number."""
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    pr.edit(state="closed")
    return f"Pull request #{pr_number} closed."


@mcp.tool(name="github_create_commit")
def create_commit(repo_name: str, branch: str, file_path: str, message: str, content: str) -> str:
    """Create a commit with changes to a file on a branch."""
    repo = gh.get_repo(repo_name)
    source = repo.get_branch(branch)
    try:
        # Try to get file if it exists
        contents = repo.get_contents(file_path, ref=branch)
        repo.update_file(contents.path, message, content, contents.sha, branch=branch)
    except:
        # Create file if not exists
        repo.create_file(file_path, message, content, branch=branch)
    return f"Commit pushed to {repo_name}@{branch} modifying {file_path}"


@mcp.tool(name="github_list_pull_requests")
def list_pull_requests(repo_name: str, state: str = "open") -> str:
    """List pull requests in a repository (state: open, closed, all) as a formatted string."""
    repo = gh.get_repo(repo_name)
    pulls = repo.get_pulls(state=state)

    result = ""
    for pr in pulls:
        result += f"- #{pr.number}: **{pr.title}** [{pr.state}] - {pr.html_url}\n"

    return result or f"No {state} pull requests found in repository."


@mcp.tool(name="github_merge_pull_request")
def merge_pull_request(repo_name: str, pr_number: int, merge_message: str = "") -> str:
    """Merge a pull request."""
    repo = gh.get_repo(repo_name)
    pr = repo.get_pull(pr_number)
    result = pr.merge(commit_message=merge_message)
    return f"PR #{pr_number} merge status: {result.merged}"


@mcp.tool(name="github_create_branch")
def create_branch(repo_name: str, new_branch: str, base_branch: str) -> str:
    """Create a new branch from an existing one."""
    repo = gh.get_repo(repo_name)
    source = repo.get_branch(base_branch)
    ref = repo.create_git_ref(ref=f"refs/heads/{new_branch}", sha=source.commit.sha)
    return f"Created new branch '{new_branch}' from '{base_branch}'"


@mcp.tool(name="github_list_files_in_path")
def list_files(repo_name: str, path: str = "", ref: str = "main") -> str:
    """List files in a repository path at a given ref (formatted string)."""
    repo = gh.get_repo(repo_name)
    contents = repo.get_contents(path, ref=ref)

    files = [item.path for item in contents if item.type == "file"]
    result = "\n".join(f"- {file_path}" for file_path in files)

    return result or f"No files found at path '{path}' on branch '{ref}'."


@mcp.tool(name="github_list_folders_in_path")
def list_folders(repo_name: str, path: str = "", ref: str = "main") -> str:
    """List folders in a repository path at a given ref (formatted string)."""
    repo = gh.get_repo(repo_name)
    contents = repo.get_contents(path, ref=ref)

    folders = [item.path for item in contents if item.type == "dir"]
    result = "\n".join(f"- {folder_path}/" for folder_path in folders)

    return result or f"No folders found at path '{path}' on branch '{ref}'."


@mcp.tool(name="github_get_file_content")
def get_file_content(repo_name: str, file_path: str, ref: str = "main") -> str:
    """Get raw file content from a repo."""
    repo = gh.get_repo(repo_name)
    file = repo.get_contents(file_path, ref=ref)
    return file.decoded_content.decode("utf-8")


@mcp.tool(name="github_search_files")
def search_files(repo_name: str, query: str, ref: str = "main") -> str:
    """Search for files by name (or partial match) in a GitHub repo."""
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
            pass  # skip inaccessible paths

    walk_dir()
    return "\n".join(results) or f"No files found matching '{query}' in repo '{repo_name}' on branch '{ref}'."



@mcp.tool(name="github_search_file_contents")
def search_file_contents(repo_name: str, keyword: str, ref: str = "main") -> str:
    """Search for a keyword inside file contents in a GitHub repo."""
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
            pass  # skip inaccessible paths

    walk_and_search()
    return "\n".join(matches) or f"No file contents matched '{keyword}' in repo '{repo_name}' on branch '{ref}'."



@mcp.tool(name="github_delete_branch")
def delete_branch(repo_name: str, branch_name: str) -> str:
    """Delete a branch from a GitHub repository (must not be the default branch)."""
    repo = gh.get_repo(repo_name)

    if branch_name == repo.default_branch:
        return f"Cannot delete the default branch '{branch_name}'."

    try:
        ref = repo.get_git_ref(f"heads/{branch_name}")
        ref.delete()
        return f"Branch '{branch_name}' deleted successfully from repository '{repo_name}'."
    except Exception as e:
        return f"Failed to delete branch '{branch_name}': {str(e)}"


@mcp.tool(name="github_list_open_issues")
def list_open_issues(repo_name: str) -> str:
    """List open issues (excluding pull requests) in a GitHub repository."""
    repo = gh.get_repo(repo_name)
    open_issues = repo.get_issues(state="open")

    result = ""
    for issue in open_issues:
        # Skip if it's a pull request (GitHub marks PRs with this attribute)
        if hasattr(issue, "pull_request"):
            continue
        result += f"- #{issue.number}: **{issue.title}** (created at {issue.created_at})\n"

    return result or "No open issues (excluding pull requests) found."


@mcp.tool(name="github_list_closed_issues")
def list_closed_issues(repo_name: str) -> str:
    """List closed issues (excluding pull requests) in a GitHub repository."""
    repo = gh.get_repo(repo_name)
    closed_issues = repo.get_issues(state="closed")

    result = ""
    for issue in closed_issues:
        if hasattr(issue, "pull_request"):
            continue  # Exclude pull requests
        result += f"- #{issue.number}: **{issue.title}** (closed at {issue.closed_at})\n"

    return result or "No closed issues (excluding pull requests) found."


@mcp.tool(name="github_issue_counts")
def get_issue_counts(repo_name: str) -> str:
    """Return the count of open and closed issues in a GitHub repository."""
    repo = gh.get_repo(repo_name)
    
    open_issues = repo.get_issues(state="open")
    closed_issues = repo.get_issues(state="closed")

    open_count = open_issues.totalCount if hasattr(open_issues, "totalCount") else len(list(open_issues))
    closed_count = closed_issues.totalCount if hasattr(closed_issues, "totalCount") else len(list(closed_issues))

    return (
        f"Issue counts for `{repo_name}`:\n"
        f"- Open issues: {open_count}\n"
        f"- Closed issues: {closed_count}\n"
        f"- Total: {open_count + closed_count}"
    )




if __name__ == "__main__":
    mcp.run(transport="sse")
