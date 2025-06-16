import os
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2 import service_account

# Auth
creds = service_account.Credentials.from_service_account_file(
    "../credentials.json",
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive_service = build("drive", "v3", credentials=creds)

def get_folder_id_by_name(folder_name, parent_id=None):
    query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get("files", [])
    return folders[0]["id"] if folders else None

def get_file_names_in_folder(folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    return {file["name"]: file["id"] for file in results.get("files", [])}

def upload_folder(local_folder_path, parent_id=None):
    folder_name = os.path.basename(local_folder_path)

    # Check if folder already exists
    folder_id = get_folder_id_by_name(folder_name, parent_id)
    if folder_id:
        print(f"Using existing folder '{folder_name}' with ID: {folder_id}")
    else:
        # Create new folder
        metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_id:
            metadata["parents"] = [parent_id]
        folder = drive_service.files().create(body=metadata, fields="id").execute()
        folder_id = folder["id"]
        print(f"Created folder '{folder_name}' with ID: {folder_id}")

    existing_files = get_file_names_in_folder(folder_id)

    for fname in os.listdir(local_folder_path):
        full_path = os.path.join(local_folder_path, fname)
        if os.path.isfile(full_path):
            if fname in existing_files:
                print(f"Skipping existing file: {fname}")
                continue
            media = MediaFileUpload(full_path, resumable=True)
            metadata = {"name": fname, "parents": [folder_id]}
            uploaded = drive_service.files().create(
                body=metadata, media_body=media, fields="id"
            ).execute()
            print(f"Uploaded '{fname}' with ID: {uploaded['id']}")
        elif os.path.isdir(full_path):
            upload_folder(full_path, parent_id=folder_id)

def list_all_folders():
    query = "mimeType='application/vnd.google-apps.folder' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get("files", [])
    if not folders:
        print("No folders found.")
    else:
        print("Folders:")
        for folder in folders:
            print(f"  - {folder['name']} (ID: {folder['id']})")

def list_files_in_folder(folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name, mimeType)").execute()
    files = results.get("files", [])
    if not files:
        print("No files found in the folder.")
    else:
        print(f"Files in folder ID {folder_id}:")
        for file in files:
            type_desc = "Folder" if file["mimeType"] == "application/vnd.google-apps.folder" else "File"
            print(f"  - {file['name']} (ID: {file['id']}, Type: {type_desc})")

def delete_folder_by_id(folder_id):
    try:
        drive_service.files().delete(fileId=folder_id).execute()
        print(f"Deleted folder with ID: {folder_id}")
    except Exception as e:
        print(f"Error deleting folder: {e}")

# Example usage
# upload_folder("bookstypes")              # Upload folder
list_all_folders()                       # List folders
# folder_id = get_folder_id_by_name("bookstypes")
# if folder_id:
#     list_files_in_folder(folder_id)      # List files in folder
#     delete_folder_by_id(folder_id)       # Delete folder


list_files_in_folder("1l-6WAmSbWWNx3Rc10LfHXZpCWY3ShZJI")
