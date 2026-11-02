"""One-time Gmail consent. Stores the token in Windows Credential Manager (never on disk)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.config import SECRETS  # noqa: E402
from jarvis.tools.gmail import SCOPES, Gmail, _store_token  # noqa: E402


def main() -> None:
    client = SECRETS / "credentials.json"
    if not client.exists():
        SECRETS.mkdir(exist_ok=True)
        print(f"""
Missing {client}

1. Go to https://console.cloud.google.com/ and create a project (any name, e.g. "Jarvis").
2. APIs & Services > Library: enable "Gmail API".
3. APIs & Services > OAuth consent screen: choose "Internal" (Workspace) and fill in the app name.
4. APIs & Services > Credentials > Create credentials > OAuth client ID > "Desktop app".
5. Download the JSON and save it as:
   {client}
6. Run setup_email.bat again.
""")
        sys.exit(1)
    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(client), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent",
                                  success_message="Jarvis is connected to Gmail. You can close this tab.")
    _store_token(creds.to_json())
    g = Gmail.connect()
    print(f"\nConnected as {g.me}. Restart Jarvis to turn on email.")


if __name__ == "__main__":
    main()
