from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import openai
import requests
import json

load_dotenv()

# ─── OpenAI Configuration ───────────────────────────────────────────────────
os.environ["OPENAI_API_KEY"] = "lm-studio"
os.environ["OPENAI_API_BASE"] = "http://192.168.29.53:1234/v1"
openai.api_key = os.environ["OPENAI_API_KEY"]
openai.api_base = os.environ["OPENAI_API_BASE"]

# ─── YouTrack Configuration ─────────────────────────────────────────────────
YOUTRACK_URL = os.getenv("YOUTRACK_URL")
YOUTRACK_TOKEN = os.getenv("YOUTRACK_TOKEN")

HEADERS = {
    "Authorization": f"Bearer {YOUTRACK_TOKEN}",
    "Accept": "application/json"
}

# ─── MCP Server Setup ───────────────────────────────────────────────────────
mcp = FastMCP(name="YouTrackToolkit", host="0.0.0.0", port=8053)

# ─── MCP Tools ──────────────────────────────────────────────────────────────

@mcp.tool(name="youtrack_search_issues")
def search_issues(keyword: str, field: str = "both") -> list:
    """Search for issues by summary, description, or both."""
    query = keyword
    url = f"{YOUTRACK_URL}/api/issues?query={query}&fields=idReadable,summary,description"
    response = requests.get(url, headers=HEADERS)
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
def fetch_issue(issue_id: str) -> str:
    """Fetch full details of an issue by ID."""
    url = f"{YOUTRACK_URL}/api/issues/{issue_id}?fields=summary,description"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        data = response.json()
        return f"Issue Summary: {data.get('summary')}\n\nDescription:\n{data.get('description')}"
    return f"Failed to fetch issue {issue_id}: {response.text}"


@mcp.tool(name="youtrack_get_comments")
def get_comments(issue_id: str) -> str:
    """Fetch all comments from a YouTrack issue."""
    url = f"{YOUTRACK_URL}/api/issues/{issue_id}/comments?fields=text,author(login,name),created"
    response = requests.get(url, headers=HEADERS)

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
def list_open_issues() -> list:
    """List open (unresolved) issues."""
    query = "State: {Unresolved}"
    url = f"{YOUTRACK_URL}/api/issues?query={query}&fields=idReadable,summary"
    response = requests.get(url, headers=HEADERS)
    if response.status_code == 200:
        return json.dumps(response.json())
    return f"Failed to list open issues: {response.text}"


@mcp.tool(name="youtrack_add_comment")
def add_comment(issue_id: str, comment_text: str) -> str:
    """Add a comment to a YouTrack issue."""
    url = f"{YOUTRACK_URL}/api/issues/{issue_id}/comments?fields=id"
    payload = {
        "text": comment_text
    }

    response = requests.post(
        url,
        headers={**HEADERS, "Content-Type": "application/json"},
        data=json.dumps(payload)
    )

    if response.status_code in (200, 201):
        return f"Comment added to issue {issue_id}."
    else:
        return f"Failed to add comment: {response.status_code} {response.text}"


# @mcp.tool(name="youtrack_list_closed_issues")
# def list_closed_issues() -> list:
#     """List closed/resolved issues."""
#     query = "State: {Resolved}"
#     url = f"{YOUTRACK_URL}/api/issues?query={query}&fields=idReadable,summary"
#     response = requests.get(url, headers=HEADERS)
#     if response.status_code == 200:
#         return json.dumps(response.json())
#     return f"Failed to list closed issues: {response.text}"

# @mcp.tool(name="youtrack_issue_counts")
# def issue_counts() -> dict:
#     """Return count of open and closed issues."""
#     counts = {"open": 0, "closed": 0}
    
#     open_resp = requests.get(
#         f"{YOUTRACK_URL}/api/issues?query=State:{{Unresolved}}&fields=idReadable",
#         headers=HEADERS
#     )
#     closed_resp = requests.get(
#         f"{YOUTRACK_URL}/api/issues?query=State:{{Resolved}}&fields=idReadable",
#         headers=HEADERS
#     )

#     if open_resp.status_code == 200:
#         counts["open"] = len(open_resp.json())
#     if closed_resp.status_code == 200:
#         counts["closed"] = len(closed_resp.json())

#     return counts

# ─── Run Server ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run(transport="sse")
