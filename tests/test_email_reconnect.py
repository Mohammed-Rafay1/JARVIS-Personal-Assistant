"""10 Nov 11:58: Gmail timed out once at start-up and Jarvis said "Email isn't connected, run setup email" for
good. Now it retries in the background and when you ask, and says what's really wrong."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.tools import CTX, gmail, mail_imap  # noqa: E402


class _UI:
    def send(self, m):
        pass


class _Mail:
    me = "me@gmail.com"

    def load_contacts(self):
        pass

    def search(self, q, n):
        return [{"from_name": "Spotify", "subject": "x", "id": "1"}]


def _jarvis(monkeypatch, attempts):
    from jarvis import app as app_mod
    from jarvis.services import gmail_watcher

    calls = []

    def connect():
        calls.append(1)
        if len(calls) <= attempts:
            raise TimeoutError("The read operation timed out")
        return _Mail()
    monkeypatch.setattr(mail_imap.ImapMail, "connect", staticmethod(connect))

    class Watcher:
        def __init__(self, *a):
            pass

        async def run(self):
            pass
    monkeypatch.setattr(gmail_watcher, "GmailWatcher", Watcher)
    monkeypatch.setattr(CTX, "gmail", None)
    monkeypatch.setattr(CTX, "email_error", "", raising=False)
    monkeypatch.setattr(CTX, "email_reconnect", None, raising=False)
    CTX.cfg = {"email": {"enabled": True}}
    j = app_mod.Jarvis({"address_as": "sir", "email": {"enabled": True}, "music": {"default_service": "youtube"},
                        "apps": {"aliases": {}}, "websites": {}}, _UI())
    return j, calls


def test_a_timeout_at_start_up_is_retried_when_you_ask(monkeypatch):
    j, calls = _jarvis(monkeypatch, attempts=1)

    async def go():
        state = await j._connect_email()
        assert state == "error (see log)" and CTX.gmail is None
        r = await gmail.check_email("24h")       # you ask: it tries again, and this time it works
        return r
    r = asyncio.run(go())
    assert r.ok and r.say.startswith("1 email in the last 24 hours") and len(calls) == 2


def test_still_unreachable_says_so_instead_of_set_up_again(monkeypatch):
    j, calls = _jarvis(monkeypatch, attempts=99)

    async def go():
        await j._connect_email()
        return await gmail.check_email("24h")
    r = asyncio.run(go())
    assert not r.ok and r.say == gmail.UNREACHABLE and "setup" not in r.say


def test_never_set_up_still_explains_setup(monkeypatch):
    j, _ = _jarvis(monkeypatch, attempts=0)
    monkeypatch.setattr(mail_imap.ImapMail, "connect", staticmethod(lambda: None))
    monkeypatch.setattr(gmail.Gmail, "connect", staticmethod(lambda: None))

    async def go():
        assert await j._connect_email() == "not set up"
        return await gmail.check_email()
    assert asyncio.run(go()).say == gmail.NOT_CONNECTED


# ------------------------------------------------------------------ 10 Nov 12:12, on WhatsApp
from jarvis.brain.intents import parse  # noqa: E402


def test_is_email_connected_is_answered():
    assert parse("Email is connected now ? U can send the email").tool == "email_status"
    assert parse("is my email working").tool == "email_status"
    assert parse("send an email to ali").tool != "email_status"


def _flow(monkeypatch):
    from jarvis import app as app_mod
    from jarvis.tools import gmail as g

    monkeypatch.setattr(CTX, "gmail", _Mail())
    monkeypatch.setattr(g, "resolve_recipient", lambda name: [])
    CTX.cfg = {"email": {"enabled": True}}
    j = app_mod.Jarvis({"address_as": "sir", "email": {"enabled": True}, "music": {"default_service": "youtube"},
                        "apps": {"aliases": {}}, "websites": {}}, _UI())
    said, chats = [], []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def chat(text):
        chats.append(text)
    j.say, j._chat = say, chat
    return j, said, chats


def test_who_are_you_is_not_taken_as_a_recipient(monkeypatch):
    j, said, chats = _flow(monkeypatch)
    j.pending = {"kind": "email_recipient"}
    asyncio.run(j.handle_text("Jarvis who are u?", typed=True))
    assert chats == ["Jarvis who are u?"] and j.pending is None
    assert not any("address for that name" in s for s in said)


def test_not_now_cancels_the_email(monkeypatch):
    j, said, _ = _flow(monkeypatch)
    j.pending = {"kind": "email_recipient"}
    asyncio.run(j.handle_text("I don't have to send email now", typed=True))
    assert said[-1] == "Alright, no email." and j.pending is None


def test_an_unknown_name_is_asked_once_then_dropped(monkeypatch):
    j, said, _ = _flow(monkeypatch)
    j.pending = {"kind": "email_recipient"}
    asyncio.run(j.handle_text("Zubair", typed=True))
    assert "address for that name" in said[-1] and j.pending["kind"] == "email_recipient"
    asyncio.run(j.handle_text("Zubair", typed=True))
    assert said[-1].startswith("I still don't have an address for Zubair") and j.pending is None
