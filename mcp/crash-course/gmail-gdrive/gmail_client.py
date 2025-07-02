import webbrowser
import requests
import time

SERVER_URL = "http://localhost:8000"

def authenticate(email_hint=None):
    print("[*] Opening browser for login...")
    webbrowser.open(f"{SERVER_URL}/authorize")

    print("[*] Waiting for user to complete login in browser...")
    email = None
    timeout = 120  # seconds
    interval = 3

    for _ in range(timeout // interval):
        if email_hint:
            email = email_hint
        else:
            try:
                # Ask the user to paste their email (one time)
                email = input("Enter your Gmail address (used in login): ").strip()
            except KeyboardInterrupt:
                return None

        resp = requests.get(f"{SERVER_URL}/status", params={"email": email})
        if resp.json().get("status") == "authenticated":
            print(f"[+] Authenticated as {email}")
            return email
        else:
            print("[*] Waiting for authentication...")
            time.sleep(interval)

    print("[-] Login timed out.")
    return None


def search_emails(email, query="subject:invoice"):
    r = requests.get(f"{SERVER_URL}/search_emails", params={"email": email, "query": query})
    if r.ok:
        print("[+] Results:")
        for item in r.json():
            print(f"- ID: {item['id']}, Snippet: {item['snippet']}")
        return r.json()
    else:
        print("[-] Search failed:", r.text)


def fetch_email(email, message_id):
    r = requests.get(f"{SERVER_URL}/fetch_email", params={"email": email, "message_id": message_id})
    if r.ok:
        msg = r.json()
        print(f"From: {msg['from']}\nSubject: {msg['subject']}\n\n{msg['body']}")
        return msg
    else:
        print("[-] Fetch failed:", r.text)


if __name__ == "__main__":
    print("== Gmail Client ==")

    email = authenticate()
    if not email:
        exit()

    while True:
        print("\nOptions:")
        print("1. Search Emails")
        print("2. Fetch Email by ID")
        print("3. Exit")

        choice = input("Enter choice: ").strip()
        if choice == "1":
            q = input("Query (e.g. subject:meeting): ")
            search_emails(email, q)
        elif choice == "2":
            mid = input("Message ID: ")
            fetch_email(email, mid)
        elif choice == "3":
            break
