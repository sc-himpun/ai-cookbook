import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import RedirectResponse
from typing import Dict
import requests
import base64
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
import os


# ===== CONFIG =====
CLIENT_ID = os.getenv("GMAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("GMAIL_CLIENT_SECRET")
REDIRECT_URI = "http://localhost:8000/oauth2callback"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "openid", "email", "profile"]

user_tokens: Dict[str, Dict] = {}

app = FastAPI()

# ===== ROUTES =====

@app.get("/")
def home():
    return {"message": "Go to /authorize to sign in with Gmail."}


@app.get("/authorize")
def authorize():
    scope = " ".join(SCOPES)
    url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
        f"&access_type=offline"
        f"&prompt=consent"
    )
    return RedirectResponse(url)


@app.get("/oauth2callback")
def oauth2callback(code: str):
    data = {
        "code": code,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code"
    }
    token_resp = requests.post("https://oauth2.googleapis.com/token", data=data)
    tokens = token_resp.json()

    # Get user email
    creds = Credentials(tokens['access_token'])
    userinfo = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {tokens['access_token']}"}
    ).json()
    email = userinfo['email']
    user_tokens[email] = tokens

    # return RedirectResponse(f"http://localhost:8000/success?email={email}")
    return {"message": f"Authorized as {email}. You can now use /search_emails or /fetch_email with your email."}


@app.get("/status")
def status(email: str):
    """Check if user is authenticated"""
    if email in user_tokens:
        return {"status": "authenticated"}
    return {"status": "pending"}


@app.get("/success")
def success(email: str):
    return {"message": f"Authorized as {email}"}


@app.get("/search_emails")
def search_emails(email: str = Query(...), query: str = Query("subject:invoice")):
    """Search emails by Gmail query string"""
    if email not in user_tokens:
        return {"error": "Email not authorized. Visit /authorize first."}

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('gmail', 'v1', credentials=creds)

    results = service.users().messages().list(userId='me', q=query, maxResults=5).execute()
    messages = results.get('messages', [])

    output = []
    for msg in messages:
        msg_meta = service.users().messages().get(userId='me', id=msg['id'], format='metadata').execute()
        output.append({
            "id": msg['id'],
            "snippet": msg_meta.get('snippet'),
            "threadId": msg.get('threadId')
        })

    return output


@app.get("/fetch_email")
def fetch_email(email: str = Query(...), message_id: str = Query(...)):
    """Fetch full email content by message ID"""
    if email not in user_tokens:
        return {"error": "Email not authorized. Visit /authorize first."}

    creds = Credentials(token=user_tokens[email]['access_token'])
    service = build('gmail', 'v1', credentials=creds)

    msg = service.users().messages().get(userId='me', id=message_id).execute()
    payload = msg.get('payload', {})
    parts = payload.get('parts', [])
    body = ""

    for part in parts:
        data = part.get('body', {}).get('data')
        if data:
            try:
                decoded = base64.urlsafe_b64decode(data.encode()).decode('utf-8', errors='ignore')
                body += decoded + "\n\n"
            except Exception:
                continue

    return {
        "subject": _get_header(payload, "Subject"),
        "from": _get_header(payload, "From"),
        "date": _get_header(payload, "Date"),
        "body": body.strip()
    }


def _get_header(payload, name):
    for h in payload.get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
