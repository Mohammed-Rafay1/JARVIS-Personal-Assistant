"""Gmail over IMAP/SMTP with an app password: no Google Cloud project needed.

Same interface as tools.gmail.Gmail, so every email feature works with either. Gmail's IMAP accepts
Gmail search syntax (X-GM-RAW), so queries like "in:inbox newer_than:1d category:primary" work as-is.
The app password lives in Windows Credential Manager. Drafts are kept locally until you confirm, then
sent over SMTP as proper replies (threaded in Gmail).
"""
from __future__ import annotations

import email
import imaplib
import json
import logging
import re
import smtplib
import threading
import time
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from ..config import DATA

log = logging.getLogger(__name__)
KEYRING_SERVICE = "jarvis-mail"
IMAP_HOST, SMTP_HOST = "imap.gmail.com", "smtp.gmail.com"
CONTACTS_CACHE = DATA / "contacts_cache.json"
_HEADERS = "FROM TO CC SUBJECT DATE MESSAGE-ID REFERENCES LIST-UNSUBSCRIBE PRECEDENCE AUTO-SUBMITTED"
_ROBOT = re.compile(r"no-?reply|do-?not-?reply|notification|mailer-daemon|postmaster|alerts?@|updates?@|news(letter)?@|"
                    r"support@|billing@|calendar-notification|@.*\.(?:zendesk|hubspot|mailchimp|sendgrid)", re.I)


def _dec(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def store_credentials(address: str, password: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, "address", address)
    keyring.set_password(KEYRING_SERVICE, "password", password)


def load_credentials() -> tuple[str, str] | None:
    import keyring

    addr = keyring.get_password(KEYRING_SERVICE, "address")
    pw = keyring.get_password(KEYRING_SERVICE, "password")
    return (addr, pw) if addr and pw else None


class ImapMail:
    def __init__(self, address: str, password: str) -> None:
        self.me = address
        self._pw = password
        self._lock = threading.Lock()
        self._conn: imaplib.IMAP4_SSL | None = None
        self._box = '"[Gmail]/All Mail"'
        self.contacts: dict[str, str] = {}
        self.drafts: dict[str, dict] = {}
        self._connect()  # fails fast if the password is wrong

    @classmethod
    def connect(cls) -> "ImapMail | None":
        creds = load_credentials()
        return cls(*creds) if creds else None

    # ---- connection -------------------------------------------------------------------------
    def _connect(self) -> None:
        c = imaplib.IMAP4_SSL(IMAP_HOST, timeout=20)
        c.login(self.me, self._pw)
        typ, boxes = c.list()
        for raw in boxes or []:  # the "All Mail" folder is named per language; find it by its \All flag
            line = raw.decode(errors="ignore")
            if "\\All" in line:
                self._box = line.split(' "/" ')[-1]
                break
        c.select(self._box, readonly=True)
        self._conn = c

    def _run(self, fn):
        with self._lock:
            for attempt in range(2):
                try:
                    if self._conn is None:
                        self._connect()
                    return fn(self._conn)
                except (imaplib.IMAP4.abort, OSError, imaplib.IMAP4.error) as e:
                    if attempt:
                        raise
                    log.info("IMAP reconnect (%s)", e)
                    self._conn = None

    def _search(self, query: str) -> list[bytes]:
        def go(c):
            typ, data = c.uid("SEARCH", "X-GM-RAW", '"' + query.replace('"', "") + '"')
            return data[0].split() if data and data[0] else []
        return self._run(go)

    def _fetch_meta(self, uids: list[bytes]) -> list[dict]:
        if not uids:
            return []

        def go(c):
            typ, data = c.uid("FETCH", b",".join(uids),
                              f"(FLAGS X-GM-LABELS X-GM-THRID INTERNALDATE BODY.PEEK[HEADER.FIELDS ({_HEADERS})])")
            return data
        out = []
        for part in self._run(go) or []:
            if not isinstance(part, tuple):
                continue
            head, raw = part[0].decode(errors="ignore"), part[1]
            uid = re.search(r"UID (\d+)", head)
            if not uid:
                continue
            h = email.message_from_bytes(raw)
            name, addr = parseaddr(_dec(h.get("From")))
            try:
                ts = int(parsedate_to_datetime(h.get("Date")).timestamp())
            except Exception:
                ts = int(time.time())
            flags = re.search(r"FLAGS \(([^)]*)\)", head)
            labels = re.search(r"X-GM-LABELS \(([^)]*)\)", head)
            thr = re.search(r"X-GM-THRID (\d+)", head)
            flag_s, label_s = (flags.group(1) if flags else ""), (labels.group(1) if labels else "")
            out.append({
                "id": uid.group(1), "from_name": (name or addr.split("@")[0]).strip('"'), "from": addr.lower(),
                "subject": _dec(h.get("Subject")) or "(no subject)", "snippet": "",
                "to": [a.lower() for _, a in getaddresses([_dec(h.get("To", ""))])],
                "cc": [a.lower() for _, a in getaddresses([_dec(h.get("Cc", ""))])],
                "message_id": h.get("Message-ID", ""), "references": h.get("References", ""),
                "bulk": bool(h.get("List-Unsubscribe")) or (h.get("Precedence", "").lower() in ("bulk", "list"))
                        or h.get("Auto-Submitted", "no").lower() != "no",
                "answered": "\\Answered" in flag_s, "thread": thr.group(1) if thr else "",
                "labels": (["INBOX"] if "\\Inbox" in label_s else []) + ([] if "\\Seen" in flag_s else ["UNREAD"]),
                "ts": ts,
            })
        return sorted(out, key=lambda m: -m["ts"])

    # ---- reading (same interface as Gmail) ------------------------------------------------------
    def search(self, q: str, n: int = 10) -> list[dict]:
        return self._fetch_meta(self._search(q)[-n:])

    def body(self, mid: str) -> str:
        def go(c):
            typ, data = c.uid("FETCH", mid.encode(), "(BODY.PEEK[])")
            return next((p[1] for p in data if isinstance(p, tuple)), b"")
        msg = email.message_from_bytes(self._run(go) or b"")
        text = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                text = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                break
        if not text:  # HTML-only email
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "ignore")
                    text = re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", "", html))
                    break
        text = re.split(r"\n(On .+ wrote:|-----Original Message-----|From: )", text)[0]
        return re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()[:4000]

    def history_id(self) -> str:
        uids = self._search("in:inbox newer_than:2d")
        return uids[-1].decode() if uids else "0"

    def new_since(self, history_id: str) -> tuple[list[dict], str]:
        uids = [u for u in self._search("in:inbox newer_than:1d category:primary") if int(u) > int(history_id)]
        if not uids:
            return [], history_id
        return self._fetch_meta(uids), uids[-1].decode()

    def needs_reply(self, days: int = 7, n: int = 8) -> list[dict]:
        """Inbox mail from real people, addressed to you, that you haven't answered yet."""
        cand = self._fetch_meta(self._search(f"in:inbox category:primary newer_than:{days}d -from:me")[-40:])
        sent = self._fetch_meta(self._search(f"in:sent newer_than:{days + 3}d")[-80:])
        last_sent = {}
        for s in sent:
            last_sent[s["thread"]] = max(last_sent.get(s["thread"], 0), s["ts"])
        me = self.me.lower()
        out = []
        for m in cand:
            if m["bulk"] or m["answered"] or _ROBOT.search(m["from"]) or m["from"] == me:
                continue
            if last_sent.get(m["thread"], 0) > m["ts"]:  # you already replied in that thread
                continue
            if me not in m["to"] and me not in m["cc"]:
                continue
            if any(o["thread"] == m["thread"] for o in out):  # newest message per thread only
                continue
            out.append(m)
        return out[:n]

    def load_contacts(self) -> None:
        if CONTACTS_CACHE.exists() and time.time() - CONTACTS_CACHE.stat().st_mtime < 86400:
            self.contacts = json.loads(CONTACTS_CACHE.read_text(encoding="utf-8"))
            return
        found: dict[str, tuple[str, int]] = {}
        for m in self._fetch_meta(self._search("in:sent")[-300:]):
            for addr in m["to"] + m["cc"]:
                if not addr or _ROBOT.search(addr) or addr == self.me.lower():
                    continue
                key = addr.split("@")[0].replace(".", " ").replace("_", " ")
                found[key] = (addr, found.get(key, ("", 0))[1] + 1)
        self.contacts = {k: v[0] for k, v in sorted(found.items(), key=lambda kv: -kv[1][1])}
        CONTACTS_CACHE.write_text(json.dumps(self.contacts, indent=1), encoding="utf-8")
        log.info("mail contacts: %d", len(self.contacts))

    # ---- drafts & sending ----------------------------------------------------------------------
    def create_draft(self, to: str, subject: str, body: str, reply_to: dict | None = None) -> str:
        did = f"local-{int(time.time() * 1000)}"
        self.drafts[did] = {"to": to, "subject": subject, "body": body,
                            "in_reply_to": (reply_to or {}).get("message_id", ""),
                            "references": ((reply_to or {}).get("references", "") + " " +
                                           (reply_to or {}).get("message_id", "")).strip()}
        return did

    def update_draft(self, draft_id: str, to: str, subject: str, body: str) -> None:
        self.drafts[draft_id].update(to=to, subject=subject, body=body)

    def send_draft(self, draft_id: str) -> None:
        d = self.drafts.pop(draft_id)
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.me, d["to"], d["subject"]
        if d["in_reply_to"]:  # keeps the reply in the same Gmail thread
            msg["In-Reply-To"] = d["in_reply_to"]
            msg["References"] = d["references"]
        msg.set_content(d["body"])
        with smtplib.SMTP(SMTP_HOST, 587, timeout=30) as s:
            s.starttls()
            s.login(self.me, self._pw)
            s.send_message(msg)
        log.info("email sent to %s: %r", d["to"], d["subject"])
