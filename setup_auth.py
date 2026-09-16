"""ONE-TIME, run on your own PC: opens a browser, you sign in to the YouTube channel's Google
account, and this prints the three values to paste into GitHub Secrets.

  pip install google-auth-oauthlib
  python setup_auth.py client_secret.json

`client_secret.json` = OAuth client (type: Desktop app) downloaded from Google Cloud Console.
Nothing is stored anywhere except printed to your terminal.
"""
import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
with open(sys.argv[1], "r", encoding="utf-8") as f:
    client = json.load(f)["installed"]

print("\nAdd these as GitHub repository secrets (Settings -> Secrets and variables -> Actions):\n")
print(f"YT_CLIENT_ID={client['client_id']}")
print(f"YT_CLIENT_SECRET={client['client_secret']}")
print(f"YT_REFRESH_TOKEN={creds.refresh_token}")
print("\nTip: while the Google Cloud app is in 'Testing' mode the refresh token expires after 7 days."
      "\n     Publish the app (OAuth consent screen -> Publish) so the token lasts indefinitely.")
