"""Gmail: read the inbox, draft emails, and send only after explicit confirmation.

Sending is deliberately NOT a tool. The only path to `Gmail.send_draft` is the confirmation
handler in app.py, which requires a pending draft the user has seen and said "send it" to.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

from rapidfuzz import fuzz

from ..config import DATA
from . import CTX
from .base import Result, tool

log = logging.getLogger(__name__)
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]
KEYRING_SERVICE = "jarvis-gmail"
CONTACTS_CACHE = DATA / "contacts_cache.json"
NOT_CONNECTED = "Email isn't connected yet. Run setup email from the Jarvis folder, then restart me."
EMAIL_OFF = "Email is switched off, so I can't read or write mail. Say open Gmail and I'll bring it up in your browser."


def _not_connected() -> str:
    return NOT_CONNECTED if CTX.cfg.get("email", {}).get("enabled", True) else EMAIL_OFF


def _store_token(token_json: str) -> None:
    import keyring

    # Windows Credential Manager caps one secret at ~2.5 KB, so split just in case
    parts = [token_json[i:i + 1200] for i in range(0, len(token_json), 1200)]
    keyring.set_password(KEYRING_SERVICE, "parts", str(len(parts)))
    for i, p in enumerate(parts):
        keyring.set_password(KEYRING_SERVICE, f"token{i}", p)


def _load_token() -> str | None:
    import keyring

    n = keyring.get_password(KEYRING_SERVICE, "parts")
    if not n:
        return None
    return "".join(keyring.get_password(KEYRING_SERVICE, f"token{i}") or "" for i in range(int(n)))


class Gmail:
    def __init__(self, creds) -> None:
        from googleapiclient.discovery import build

        self.creds = creds
        self.svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.me = self.svc.users().getProfile(userId="me").execute()["emailAddress"]
        self.contacts: dict[str, str] = {}  # "sara khan" -> address

    @classmethod
    def connect(cls) -> "Gmail | None":
        raw = _load_token()
        if not raw:
            return None
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        creds = Credentials.from_authorized_user_info(json.loads(raw), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            _store_token(creds.to_json())
        return cls(creds)

    # ---- reading -------------------------------------------------------------------------
    def _meta(self, mid: str) -> dict:
        m = self.svc.users().messages().get(userId="me", id=mid, format="metadata",
                                            metadataHeaders=["From", "Subject", "Date"]).execute()
        h = {x["name"].lower(): x["value"] for x in m["payload"].get("headers", [])}
        name, addr = parseaddr(h.get("from", ""))
        return {"id": mid, "from_name": (name or addr.split("@")[0]).strip('"'), "from": addr,
                "subject": h.get("subject", "(no subject)"), "snippet": m.get("snippet", ""),
                "labels": m.get("labelIds", []), "ts": int(m.get("internalDate", 0)) // 1000}

    def search(self, q: str, n: int = 10) -> list[dict]:
        res = self.svc.users().messages().list(userId="me", q=q, maxResults=n).execute()
        return [self._meta(m["id"]) for m in res.get("messages", [])]

    def body(self, mid: str) -> str:
        m = self.svc.users().messages().get(userId="me", id=mid, format="full").execute()

        def walk(part) -> str:
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "ignore")
            for p in part.get("parts", []) or []:
                t = walk(p)
                if t:
                    return t
            return ""

        text = walk(m["payload"]) or m.get("snippet", "")
        text = re.split(r"\n(On .+ wrote:|-----Original Message-----|From: )", text)[0]  # drop quoted thread
        return re.sub(r"\n{3,}", "\n\n", text).strip()[:4000]

    def history_id(self) -> str:
        return self.svc.users().getProfile(userId="me").execute()["historyId"]

    def new_since(self, history_id: str) -> tuple[list[dict], str]:
        res = self.svc.users().history().list(userId="me", startHistoryId=history_id,
                                              historyTypes=["messageAdded"], labelId="INBOX").execute()
        ids = []
        for h in res.get("history", []):
            for ma in h.get("messagesAdded", []):
                if "INBOX" in ma["message"].get("labelIds", []) and ma["message"]["id"] not in ids:
                    ids.append(ma["message"]["id"])
        return [self._meta(i) for i in ids], res.get("historyId", history_id)

    # ---- contacts ------------------------------------------------------------------------
    def load_contacts(self) -> None:
        if CONTACTS_CACHE.exists() and time.time() - CONTACTS_CACHE.stat().st_mtime < 86400:
            self.contacts = json.loads(CONTACTS_CACHE.read_text(encoding="utf-8"))
            return
        found: dict[str, tuple[str, int]] = {}
        res = self.svc.users().messages().list(userId="me", q="in:sent", maxResults=300).execute()
        ids = [m["id"] for m in res.get("messages", [])]

        def collect(_rid, resp, exc):
            if exc:
                return
            for h in resp["payload"].get("headers", []):
                if h["name"].lower() in ("to", "cc"):
                    for name, addr in getaddresses([h["value"]]):
                        addr = addr.lower()
                        if not addr or "noreply" in addr or "no-reply" in addr or addr == self.me:
                            continue
                        key = (name.strip('"') or addr.split("@")[0].replace(".", " ")).lower()
                        found[key] = (addr, found.get(key, ("", 0))[1] + 1)

        for i in range(0, len(ids), 50):
            batch = self.svc.new_batch_http_request(callback=collect)
            for mid in ids[i:i + 50]:
                batch.add(self.svc.users().messages().get(userId="me", id=mid, format="metadata",
                                                          metadataHeaders=["To", "Cc"]))
            batch.execute()
        self.contacts = {k: v[0] for k, v in sorted(found.items(), key=lambda kv: -kv[1][1])}
        CONTACTS_CACHE.write_text(json.dumps(self.contacts, indent=1), encoding="utf-8")
        log.info("gmail contacts: %d", len(self.contacts))

    # ---- drafts --------------------------------------------------------------------------
    def _raw(self, to: str, subject: str, body: str) -> dict:
        msg = EmailMessage()
        msg["To"], msg["From"], msg["Subject"] = to, self.me, subject
        msg.set_content(body)
        return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}

    def create_draft(self, to: str, subject: str, body: str, reply_to: dict | None = None) -> str:
        message = self._raw(to, subject, body)
        if reply_to and reply_to.get("thread"):
            message["threadId"] = reply_to["thread"]
        d = self.svc.users().drafts().create(userId="me", body={"message": message}).execute()
        return d["id"]

    def update_draft(self, draft_id: str, to: str, subject: str, body: str) -> None:
        self.svc.users().drafts().update(userId="me", id=draft_id,
                                         body={"message": self._raw(to, subject, body)}).execute()

    def send_draft(self, draft_id: str) -> None:
        self.svc.users().drafts().send(userId="me", body={"id": draft_id}).execute()


def resolve_recipient(name: str) -> list[tuple[str, str]]:
    """Returns [(display, address)] best first; one entry means a confident match."""
    n = name.lower().strip()
    if re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", n):
        return [(n, n)]
    book = dict(CTX.gmail.contacts) if CTX.gmail else {}
    book.update(CTX.contacts)  # contacts.yaml wins
    if n in CTX.contacts:
        return [(n.title(), CTX.contacts[n])]
    scored = []
    for key, addr in book.items():
        first = key.split()[0] if key.split() else key
        s = max(fuzz.ratio(n, key), fuzz.ratio(n, first) + (5 if len(n) > 2 else 0),
                fuzz.partial_ratio(n, addr.split("@")[0]) - 5)
        if s >= 80:
            scored.append((s, key.title(), addr))
    scored.sort(key=lambda x: -x[0])
    if not scored:
        return []
    if len(scored) == 1 or scored[0][0] - scored[1][0] >= 8:
        return [(scored[0][1], scored[0][2])]
    seen, out = set(), []
    for _, disp, addr in scored:
        if addr not in seen:
            seen.add(addr)
            out.append((disp, addr))
    return out[:4]


def _from_dictation(about: str) -> str:
    """Turn what you said into the email's words: "tell him the demo is at 3" -> "The demo is at 3."."""
    t = about.strip().rstrip(".")
    t = re.sub(r"^(?:please )?(?:tell|inform) (?:him|her|them)(?: that)? ", "", t, flags=re.I)
    t = re.sub(r"^(?:please )?let (?:him|her|them) know(?: that)? ", "", t, flags=re.I)
    t = re.sub(r"^(?:please )?(?:say|write)(?: that)? ", "", t, flags=re.I)
    m = re.match(r"^(?:please )?ask (?:him|her|them) (?:if|whether) (.+)$", t, flags=re.I)
    if m:
        return f"Could you let me know if {m.group(1)}?"
    m = re.match(r"^(?:please )?ask (?:him|her|them) (?:to )?(.+)$", t, flags=re.I)
    if m:
        return f"Could you {m.group(1)}?"
    t = t[:1].upper() + t[1:]
    return t if t.endswith(("?", "!")) else t + "."


async def compose(recipient_name: str, about: str, ai: bool | None = None) -> tuple[str, str]:
    """Subject + body. By default straight from your words (instant). The local AI rewrites it only when
    asked ("make it more formal") or with email.ai_compose: true, because it takes ~30 s on this laptop."""
    first = recipient_name.split()[0].title() if recipient_name and "@" not in recipient_name else ""
    sig = CTX.cfg.get("email", {}).get("signature", "") or "Best regards"
    use_ai = CTX.cfg.get("email", {}).get("ai_compose", False) if ai is None else ai
    if use_ai and CTX.llm and await CTX.llm.available():
        try:
            data = await CTX.llm.json(
                "Write a short, friendly, professional email. Return JSON with keys subject and body. "
                "Body: greeting with the first name, 1-4 short sentences, no signature, no placeholders. "
                "Do not add facts, dates or promises that are not in the request.",
                f"Recipient first name: {first or 'unknown'}\nWhat to say: {about}")
            subject, body = data.get("subject", "").strip(), data.get("body", "").strip()
            if subject and body:
                return subject, f"{body}\n\n{sig}"
        except Exception as e:
            log.warning("compose via LLM failed: %s", e)
    text = _from_dictation(about)
    body = (f"Hi {first},\n\n" if first else "Hi,\n\n") + text
    words = re.sub(r"[^\w\s'-]", "", text).split()
    subject = " ".join(words[:7]) + ("…" if len(words) > 7 else "")
    subject = subject[:1].upper() + subject[1:]  # keep "Friday", "Q3" as written
    return subject, f"{body}\n\n{sig}"


_GENERIC = {"someone", "somebody", "anyone", "anybody", "him", "her", "them", "a friend", "friend", "person",
            "unknown", "recipient", "the recipient"}


def name_from_address(addr: str) -> str:
    """basit.ahmed@example.com -> 'Basit'; info@x.com / 123@x.com -> ''."""
    first = re.split(r"[._\-+0-9]+", addr.split("@")[0])[0]
    if not first.isalpha() or len(first) < 2 or first.lower() in {"info", "admin", "support", "sales", "contact",
                                                                    "hello", "mail", "team", "office", "noreply"}:
        return ""
    return first.title()


def greeting_name(to_display: str, to_addr: str) -> str:
    """A real contact name if we have one, else the first name in their address."""
    n = (to_display or "").strip()
    if not n or "@" in n or n.lower() in _GENERIC:
        return name_from_address(to_addr)
    return n.split()[0].title()


def draft_text(body: str) -> str:
    """The message itself, without greeting and sign-off, for reading back."""
    lines = [ln for ln in body.strip().splitlines() if ln.strip()]
    if lines and re.match(r"^(?:hi|hello|dear|hey)\b", lines[0], re.I):
        lines = lines[1:]
    if lines and re.match(r"^(?:best|kind|warm)? ?regards|^thanks|^thank you|^cheers", lines[-1], re.I):
        lines = lines[:-1]
    return " ".join(lines)


def _without_signature(body: str) -> str:
    parts = body.strip().rsplit("\n\n", 1)
    if len(parts) == 2 and re.match(r"^(?:best|kind|warm)? ?regards|^thanks|^thank you|^cheers", parts[1], re.I):
        return parts[0]
    return body.strip()


async def make_draft(to_display: str, to_addr: str, about: str) -> Result:
    who = greeting_name(to_display, to_addr)
    subject, body = await compose(who, about)
    draft_id = await asyncio.to_thread(CTX.gmail.create_draft, to_addr, subject, body)
    card = {"kind": "email_draft", "id": draft_id, "to": to_addr, "to_name": who or to_addr,
            "subject": subject, "body": body}
    return Result(f"Here's the draft to {who or to_addr}. It says: {draft_text(body)} Shall I send it?",
                  icon="mail", card=card,
                  pending={"kind": "send_email", "draft_id": draft_id, "to": to_addr, "to_name": who or to_addr,
                           "subject": subject, "body": body, "about": about})


async def revise(p: dict, instruction: str) -> tuple[str, str] | None:
    """Apply one spoken change to the current draft. Returns (subject, body), or None if it couldn't be done.
    Never pastes the instruction into the email."""
    t = instruction.lower().strip().rstrip(".")
    subject, body = p["subject"], p["body"]
    greet = re.compile(r"^(Hi|Hello|Dear|Hey)\b[^,\n]*,", re.I)
    m = re.search(r"\b(?:his|her|their|the) name is (\w+)|\bcall (?:him|her|them) (\w+)|\bname (?:should be|to) (\w+)", t)
    if m:  # "the name is Basit"
        name = next(g for g in m.groups() if g).title()
        return subject, greet.sub(lambda g: f"{g.group(1)} {name},", body, count=1) if greet.search(body) \
            else f"Hi {name},\n\n{body}"
    if re.search(r"\b(?:name|it) from (?:the |their |his |her )?(?:recipient'?s? )?(?:e-?mail )?address\b"
                 r"|\b(?:recipient'?s?|e-?mail) address\b.*\bname\b|\bname\b.*\b(?:recipient'?s?|e-?mail) address\b", t):
        name = name_from_address(p["to"])
        if name:
            return subject, greet.sub(lambda g: f"{g.group(1)} {name},", body, count=1)
    if re.search(r"\bremove (?:his|her|their|the) name\b|\bwithout (?:a |the |any )?name\b|\bno name\b|\bjust (?:say )?hi\b", t):
        return subject, greet.sub(lambda g: f"{g.group(1)},", body, count=1)
    m = re.search(r"\bremove (?:the word |the name )?[\"']?([\w-]+)[\"']?(?: from .*)?$", t)
    if m and m.group(1) not in ("it", "that", "this", "the", "name"):
        word = re.escape(m.group(1))
        new = re.sub(rf"\s*\b{word}\b", "", body, flags=re.I)
        new = re.sub(r"^(Hi|Hello|Dear|Hey)\s*,", r"\1,", new, flags=re.I)
        if new != body:
            return subject, new
    # anything else: the AI rewrites the current draft with that one change
    if CTX.llm and await CTX.llm.available():
        sig = CTX.cfg.get("email", {}).get("signature", "") or "Best regards"
        try:
            data = await asyncio.wait_for(CTX.llm.json(
                "Apply the user's requested change to this email and return JSON with keys subject and body. Change "
                "only what they ask; keep everything else. Body: greeting line and message, no signature. Never copy "
                "the instruction itself into the email.",
                f"Current subject: {subject}\nCurrent body:\n{_without_signature(body)}"
                f"\n\nRequested change: {instruction}"), timeout=90)
            new_subject, new_body = data.get("subject", "").strip(), data.get("body", "").strip()
            if new_body and "change:" not in new_body.lower():
                return new_subject or subject, f"{new_body}\n\n{sig}"
        except Exception as e:
            log.warning("draft revision failed: %s", e)
    return None


@tool("Draft an email to someone (it is shown for confirmation, never sent automatically)",
      to="Recipient name or address", about="What the email should say")
async def draft_email(to: str, about: str) -> Result:
    if CTX.gmail is None:
        return Result(_not_connected(), ok=False, icon="mail")
    matches = resolve_recipient(to)
    if not matches:
        card = {"kind": "email_draft", "id": "", "to": "", "to_name": to, "subject": "", "body": "",
                "note": f"I don't know {to}'s address. Type it here, or add them to contacts.yaml."}
        return Result(f"I don't have an address for {to}. Type it on the card, or add them to contacts.yaml.",
                      ok=False, icon="mail", card=card,
                      pending={"kind": "need_address", "to_name": to, "about": about})
    if len(matches) > 1:
        spoken = ", ".join(f"{i}: {d}" for i, (d, _) in enumerate(matches, 1))
        return Result(f"Which {to}? {spoken}.", icon="mail",
                      card={"kind": "choices", "title": f"Which {to}?", "items": [d for d, _ in matches],
                            "subs": [a for _, a in matches]},
                      pending={"kind": "choose_recipient", "matches": matches, "about": about})
    return await make_draft(matches[0][0], matches[0][1], about)


@tool("Check the inbox: who emailed recently", period="today | unread | recent")
async def check_email(period: str = "today") -> Result:
    if CTX.gmail is None:
        return Result(_not_connected(), ok=False, icon="mail")
    q = {"unread": "in:inbox is:unread", "recent": "in:inbox newer_than:3d"}.get(period, "in:inbox newer_than:1d")
    if CTX.cfg.get("email", {}).get("only_primary", True):
        q += " category:primary"
    msgs = await asyncio.to_thread(CTX.gmail.search, q, 15)
    if not msgs:
        return Result("Nothing new in your inbox." if period != "today" else "No emails today.", icon="mail")
    senders: dict[str, int] = {}
    for m in msgs:
        senders[m["from_name"]] = senders.get(m["from_name"], 0) + 1
    top = list(senders.items())[:4]
    parts = [f"{n}{' (' + str(c) + ')' if c > 1 else ''}" for n, c in top]
    more = f" and {len(senders) - 4} others" if len(senders) > 4 else ""
    say = f"{len(msgs)} email{'s' if len(msgs) != 1 else ''}{' today' if period == 'today' else ''}, from {', '.join(parts)}{more}."
    card = {"kind": "email_list", "items": [{"from": m["from_name"], "subject": m["subject"], "id": m["id"]}
                                            for m in msgs[:8]]}
    return Result(say, icon="mail", card=card, pending={"kind": "email_list", "messages": msgs})


@tool("Read or summarise the latest email, optionally from a given sender", sender="Sender name, optional")
async def read_email(sender: str = "") -> Result:
    if CTX.gmail is None:
        return Result(_not_connected(), ok=False, icon="mail")
    q = "in:inbox" + (f" from:({sender})" if sender else "")
    msgs = await asyncio.to_thread(CTX.gmail.search, q, 1)
    if not msgs:
        return Result(f"I couldn't find an email from {sender}." if sender else "Your inbox is empty.", icon="mail")
    return await summarise_message(msgs[0])


async def summarise_message(m: dict) -> Result:
    body = await asyncio.to_thread(CTX.gmail.body, m["id"])
    summary = ""
    if CTX.llm and await CTX.llm.available():
        try:
            summary = await CTX.llm.complete(
                "Summarise this email in at most two short spoken sentences. No preamble.",
                f"From: {m['from_name']}\nSubject: {m['subject']}\n\n{body}")
        except Exception as e:
            log.warning("summary failed: %s", e)
    if not summary:
        summary = m["snippet"]
    return Result(f"{m['from_name']} wrote about {m['subject']}. {summary}", icon="mail",
                  card={"kind": "email", "from": m["from_name"], "subject": m["subject"], "body": body[:1500]})


# ---- replying: what's waiting for you, what should I say, draft, confirm, send -----------------------

@tool("List emails that are waiting for the user's reply and ask which one to answer")
async def emails_to_reply() -> Result:
    if CTX.gmail is None:
        return Result(_not_connected(), ok=False, icon="mail")
    if not hasattr(CTX.gmail, "needs_reply"):
        return Result("I can list unanswered emails once email is connected with an app password.", ok=False, icon="mail")
    msgs = await asyncio.to_thread(CTX.gmail.needs_reply)
    if not msgs:
        return Result("You're all caught up, sir. Nothing in your inbox is waiting for a reply.", icon="mail")
    lines = [f"{i}, {m['from_name']} about {m['subject']}" for i, m in enumerate(msgs[:4], 1)]
    more = f" and {len(msgs) - 4} more on screen" if len(msgs) > 4 else ""
    n = len(msgs)
    say = (f"{n} email{'s are' if n != 1 else ' is'} waiting for your reply. " + ". ".join(lines) + more +
           (". Which one shall we answer?" if n > 1 else ". Shall we answer it?"))
    card = {"kind": "email_list", "title": "Waiting for your reply",
            "items": [{"from": m["from_name"], "subject": m["subject"], "id": m["id"]} for m in msgs]}
    return Result(say, icon="mail", card=card, pending={"kind": "reply_list", "messages": msgs})


async def start_reply(m: dict) -> Result:
    """Brief the chosen email, then ask what to say back."""
    briefed = await summarise_message(m)
    return Result(f"{briefed.say} What would you like me to reply?", icon="mail", card=briefed.card,
                  pending={"kind": "reply_text", "msg": m})


async def compose_reply(m: dict, instruction: str) -> str:
    """Write the reply body from the user's spoken instruction (their words if the LLM is unavailable)."""
    sig = CTX.cfg.get("email", {}).get("signature", "")
    first = (m.get("from_name") or "").split()[0] if m.get("from_name") else ""
    if CTX.llm and await CTX.llm.available():
        try:
            original = await asyncio.to_thread(CTX.gmail.body, m["id"])
            body = await CTX.llm.complete(
                "Write the body of an email reply on the user's behalf. Follow their instruction exactly; do not "
                "add facts, promises or dates they did not give. Greeting with the sender's first name, 1-4 short "
                "professional sentences, no subject line, no signature, no placeholders.",
                f"Sender first name: {first}\nTheir email (subject: {m['subject']}):\n{original[:1500]}\n\n"
                f"The user's instruction for the reply: {instruction}", max_tokens=220)
            if body.strip():
                return body.strip() + (f"\n\n{sig}" if sig else "")
        except Exception as e:
            log.warning("reply compose failed: %s", e)
    text = instruction.strip().rstrip(".") + "."
    return (f"Hi {first},\n\n" if first else "Hi,\n\n") + text[0].upper() + text[1:] + (f"\n\n{sig}" if sig else "")


async def make_reply(m: dict, instruction: str) -> Result:
    body = await compose_reply(m, instruction)
    subject = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
    draft_id = await asyncio.to_thread(CTX.gmail.create_draft, m["from"], subject, body, m)
    card = {"kind": "email_draft", "id": draft_id, "to": m["from"], "to_name": m["from_name"],
            "subject": subject, "body": body}
    first = m["from_name"].split()[0] if m["from_name"] else "them"
    return Result(f"Here's the reply to {first}. Shall I send it?", icon="mail", card=card,
                  pending={"kind": "send_email", "draft_id": draft_id, "to": m["from"], "to_name": m["from_name"],
                           "subject": subject, "body": body, "about": instruction, "reply_msg": m})
