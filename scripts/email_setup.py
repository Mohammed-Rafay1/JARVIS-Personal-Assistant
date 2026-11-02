"""Connect Gmail with an app password (no Google Cloud project). Run via setup_email.bat."""
from __future__ import annotations

import getpass
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.tools.mail_imap import ImapMail, store_credentials  # noqa: E402

print("""
  JARVIS - connect Gmail
  ----------------------------------------------------------------
  Jarvis reads your inbox and sends the replies you approve, using a Google
  "app password" (a 16-letter password just for Jarvis; you can revoke it any time).

  1. Make sure 2-Step Verification is on:
       https://myaccount.google.com/signinoptions/two-step-verification
  2. Create the app password (name it "Jarvis"):
       https://myaccount.google.com/apppasswords
     If that page says the setting isn't available, your Workspace admin has
     turned app passwords off; ask IT to allow them for your account.
  3. Enter it below. It's stored in Windows Credential Manager, not in a file.
""")
address = input("  Your email address: ").strip()
password = getpass.getpass("  App password (hidden as you type): ").replace(" ", "").strip()
if "@" not in address or len(password) < 12:
    sys.exit("\n  That doesn't look right. Run setup_email.bat again.")

print("\n  Checking with Gmail…")
try:
    mail = ImapMail(address, password)
except Exception as e:
    msg = str(e)
    hint = ("Wrong address or app password." if "AUTHENTICATIONFAILED" in msg or "Invalid credentials" in msg
            else "IMAP may be disabled for your account (Gmail settings > Forwarding and POP/IMAP), or blocked by IT.")
    sys.exit(f"\n  Couldn't sign in: {msg[:160]}\n  {hint}")

store_credentials(address, password)
waiting = mail.needs_reply()
cfg = ROOT / "config.yaml"
text = cfg.read_text(encoding="utf-8")
text = re.sub(r"(?m)^(email:\n  enabled:\s*)false.*$", r"\g<1>true              # connected with an app password",
              text, count=1)
cfg.write_text(text, encoding="utf-8")
print(f"\n  Connected as {address}. {len(waiting)} email(s) are waiting for your reply right now.")
print("  Email is switched on. Restarting Jarvis…\n")
