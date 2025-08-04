from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import openai
import requests
import json
from typing import Dict, Any, Tuple, Optional

load_dotenv()

# ─── YouTrack Configuration ─────────────────────────────────────────────────
# YOUTRACK_URL = os.getenv("YOUTRACK_URL")
# YOUTRACK_TOKEN = os.getenv("YOUTRACK_TOKEN")

# HEADERS = {
#     "Authorization": f"Bearer {YOUTRACK_TOKEN}",
#     "Accept": "application/json"
# }

# ─── MCP Server Setup ───────────────────────────────────────────────────────
mcp = FastMCP(name="YouTrackToolkit", host="0.0.0.0", port=8053)

# ─── MCP Tools ──────────────────────────────────────────────────────────────

def get_youtrack_headers(metadata: Optional[dict]) -> dict:
    """
    Return request headers for YouTrack API using metadata-based token.
    """
    if not metadata or "access_token" not in metadata:
        raise Exception("❌ Missing access_token in metadata for YouTrack")
    
    return {
        "Authorization": f"Bearer {metadata['access_token']}",
        "Accept": "application/json"
    }


def get_youtrack_creds(metadata: Optional[Dict]) -> Dict[str, Any]:
    """
    Extract YouTrack credentials (access_token + url) from metadata.

    Args:
        metadata (Optional[Dict]): directly contains keys

    Returns:
        Dict with 'url' and 'headers'
    """
    if not metadata:
        raise Exception("❌ Missing Credentials for YouTrack")

    access_token = metadata.get("access_token")
    url = metadata.get("url")

    if not access_token:
        raise Exception("❌ Missing access_token in YouTrack credentials")
    if not url:
        raise Exception("❌ Missing url in YouTrack credentials")

    # Reuse existing function for headers
    headers = get_youtrack_headers(metadata)

    return {
        "url": url.rstrip("/"),
        "headers": headers
    }



@mcp.tool(name="youtrack_search_issues")
def search_issues(metadata: dict, keyword: str, field: str = "both") -> list:
    """Search for issues by summary, description, or both."""
    creds = get_youtrack_creds(metadata)
    query = keyword
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary,description"
    response = requests.get(url, headers=creds.get("headers"))
    results = []
    if response.status_code == 200:
        for issue in response.json():
            if field == "summary" and keyword.lower() in (issue.get("summary") or "").lower():
                results.append({"id": issue["idReadable"], "summary": issue["summary"]})
            elif field == "description" and keyword.lower() in (issue.get("description") or "").lower():
                results.append({"id": issue["idReadable"], "summary": issue["summary"]})
            elif field == "both":
                combined = (issue.get("summary") or "") + " " + (issue.get("description") or "")
                if keyword.lower() in combined.lower():
                    results.append({"id": issue["idReadable"], "summary": issue["summary"]})
    return json.dumps(results)


@mcp.tool(name="youtrack_fetch_issue")
def fetch_issue(metadata: dict,issue_id: str) -> str:
    """Fetch full details of an issue by ID."""
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}?fields=summary,description"
    response = requests.get(url, headers=creds.get("headers"))
    if response.status_code == 200:
        data = response.json()
        return f"Issue Summary: {data.get('summary')}\n\nDescription:\n{data.get('description')}"
    return f"Failed to fetch issue {issue_id}: {response.text}"


@mcp.tool(name="youtrack_get_comments")
def get_comments(metadata: dict,issue_id: str) -> str:
    """Fetch all comments from a YouTrack issue."""
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}/comments?fields=text,author(login,name),created"
    response = requests.get(url, headers=creds.get("headers"))

    if response.status_code == 200:
        comments = response.json()
        formatted = []
        for c in comments:
            author = c.get("author", {}).get("login") or c.get("author", {}).get("name", "Unknown")
            timestamp = c.get("created", "")
            formatted.append(f"By {author} at {timestamp}:\n{c.get('text')}\n")
        return "\n---\n".join(formatted) if formatted else "No comments found."
    return f"Failed to fetch comments: {response.text}"



@mcp.tool(name="youtrack_list_open_issues")
def list_open_issues(metadata: dict) -> list:
    """List open (unresolved) issues."""
    creds = get_youtrack_creds(metadata)
    query = "State: {Unresolved}"
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary"
    response = requests.get(url, headers=creds.get("headers"))
    if response.status_code == 200:
        return json.dumps(response.json())
    return f"Failed to list open issues: {response.text}"


@mcp.tool(name="youtrack_add_comment")
def add_comment(metadata: dict, issue_id: str, comment_text: str) -> str:
    """Add a comment to a YouTrack issue."""
    creds = get_youtrack_creds(metadata)
    url = f"{creds['url']}/api/issues/{issue_id}/comments?fields=id"
    payload = {
        "text": comment_text
    }

    response = requests.post(
        url,
        headers={**creds.get("headers"), "Content-Type": "application/json"},
        data=json.dumps(payload)
    )

    if response.status_code in (200, 201):
        return f"Comment added to issue {issue_id}."
    else:
        return f"Failed to add comment: {response.status_code} {response.text}"


@mcp.tool(name="youtrack_list_closed_issues")
def list_closed_issues(metadata: dict) -> list:
    """List closed/resolved issues."""
    creds = get_youtrack_creds(metadata)
    query = "State: {Resolved}"    
    url = f"{creds['url']}/api/issues?query={query}&fields=idReadable,summary"
    response = requests.get(url, headers=creds.get("headers"))
    if response.status_code == 200:
        return json.dumps(response.json())
    return f"Failed to list closed issues: {response.text}"

@mcp.tool(name="youtrack_issue_counts")
def issue_counts(metadata: dict) -> dict:
    """Return count of open and closed issues."""
    creds = get_youtrack_creds(metadata)
    counts = {"open": 0, "closed": 0}
    
    open_resp = requests.get(
        f"{creds['url']}/api/issues?query=State:{{Unresolved}}&fields=idReadable",
        headers=creds.get("headers")
    )
    closed_resp = requests.get(
        f"{creds['url']}/api/issues?query=State:{{Resolved}}&fields=idReadable",
        headers=creds.get("headers")
    )

    if open_resp.status_code == 200:
        counts["open"] = len(open_resp.json())
    if closed_resp.status_code == 200:
        counts["closed"] = len(closed_resp.json())

    return counts

# ─── Run Server ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
