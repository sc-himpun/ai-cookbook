import os
import json
import msal
import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID")
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPE = ["Files.Read"]
TOKEN_FILE = os.path.expanduser("~/.onedrive_refresh_token.json")
GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0/me/drive/root/children"

def save_token(token_response):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_response, f)

def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            return json.load(f)
    return None

def get_token():
    app = msal.PublicClientApplication(CLIENT_ID, authority=AUTHORITY)

    # Try using saved refresh token
    saved_token = load_token()
    if saved_token and "refresh_token" in saved_token:
        print("[INFO] Using stored refresh token...")
        refreshed = app.acquire_token_by_refresh_token(saved_token["refresh_token"], scopes=SCOPE)
        if "access_token" in refreshed:
            save_token(refreshed)
            return refreshed["access_token"]

    # Fallback to device flow
    print("[INFO] Falling back to device code flow...")
    flow = app.initiate_device_flow(scopes=SCOPE)
    if "user_code" not in flow:
        raise Exception("Failed to initiate device flow.")

    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" in result:
        save_token(result)
        return result["access_token"]
    else:
        raise Exception(f"Failed to acquire token: {result}")

def list_onedrive_files():
    token = get_token()
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(GRAPH_ENDPOINT, headers=headers)
    if resp.status_code == 200:
        for item in resp.json()["value"]:
            print(item["name"])
    else:
        print(f"Error {resp.status_code}: {resp.text}")

if __name__ == "__main__":
    list_onedrive_files()
