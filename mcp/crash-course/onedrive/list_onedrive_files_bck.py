# -*- coding: utf-8 -*-
# List OneDrive files using Microsoft Graph API
# Fill in your credentials below
import requests
import msal
import json
import os
from dotenv import load_dotenv
from msal import PublicClientApplication, ConfidentialClientApplication

load_dotenv()

# CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID")
# CLIENT_SECRET = os.getenv("ONEDRIVE_CLIENT_SECRET") if os.getenv("ONEDRIVE_ACCOUNT_TYPE", "personal").lower() != "personal" else None
# TENANT_ID = os.getenv("ONEDRIVE_TENANT_ID")
# ROOT_FOLDER_ID = os.getenv("ONEDRIVE_ROOT_FOLDER_ID", "root")
# REDIRECT_URI = os.getenv("REDIRECT_URI", "https://login.microsoftonline.com/common/oauth2/nativeclient")

CLIENT_ID = os.getenv("ONEDRIVE_CLIENT_ID")
CLIENT_SECRET = os.getenv("ONEDRIVE_CLIENT_SECRET") if os.getenv("ONEDRIVE_ACCOUNT_TYPE", "personal").lower() != "personal" else None
TENANT_ID = os.getenv("ONEDRIVE_TENANT_ID")
ROOT_FOLDER_ID = os.getenv("ONEDRIVE_ROOT_FOLDER_ID", "root")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://login.microsoftonline.com/common/oauth2/nativeclient")



print (CLIENT_ID, CLIENT_SECRET, TENANT_ID, ROOT_FOLDER_ID, REDIRECT_URI)
# Determine authority based on account type
ONEDRIVE_ACCOUNT_TYPE = os.getenv("ONEDRIVE_ACCOUNT_TYPE", "personal").lower()  # 'personal' or 'business'
if ONEDRIVE_ACCOUNT_TYPE == "personal":
    AUTHORITY = "https://login.microsoftonline.com/consumers"
    print("[INFO] Using personal Microsoft account (OneDrive consumer) authority.")
else:
    AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID or 'common'}"
    print("[INFO] Using business/organizational Microsoft account authority.")

SCOPE = ["Files.Read"]
TOKEN_CACHE_FILE = os.path.expanduser("~/.onedrive_token_cache.json")
ENDPOINT = f"https://graph.microsoft.com/v1.0/me/drive/items/{ROOT_FOLDER_ID}/children"

def load_cache():
    if os.path.exists(TOKEN_CACHE_FILE):
        cache = msal.SerializableTokenCache()
        with open(TOKEN_CACHE_FILE, "r") as f:
            cache.deserialize(f.read())
        return cache
    return msal.SerializableTokenCache()

def save_cache(token_cache):
    with open(TOKEN_CACHE_FILE, "w") as f:
        f.write(token_cache.serialize())

def get_access_token():
    cache = load_cache()
    if ONEDRIVE_ACCOUNT_TYPE == "personal":
        # Public client flow (no client secret)
        app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY, token_cache=cache)
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPE, account=accounts[0])
            if result and "access_token" in result:
                save_cache(cache)
                return result["access_token"]
        # Use default native client redirect URI for personal accounts (do not pass redirect_uri)
        result = app.acquire_token_interactive(scopes=SCOPE)
        if "access_token" in result:
            save_cache(cache)
            return result["access_token"]
        else:
            raise Exception(f"Could not obtain access token: {result}")
    else:
        # Confidential client flow (business/organizational)
        app = ConfidentialClientApplication(
            CLIENT_ID,
            client_credential=CLIENT_SECRET,
            authority=AUTHORITY,
            token_cache=cache
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" in result:
            save_cache(cache)
            return result["access_token"]
        else:
            raise Exception(f"Could not obtain access token: {result}")

def list_onedrive_files():
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get(ENDPOINT, headers=headers)
    if response.status_code == 200:
        files = response.json().get("value", [])
        for f in files:
            print(f['name'])
    else:
        print(f"Error: {response.status_code} - {response.text}")

if __name__ == "__main__":
    list_onedrive_files()

# In Azure Portal, ensure your app registration's 'Supported account types' is set to:
# 'Accounts in any organizational directory and personal Microsoft accounts (e.g. Skype, Xbox)'
# This is required for personal account login support.
