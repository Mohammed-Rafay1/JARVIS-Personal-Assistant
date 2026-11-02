import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.tools import CTX  # noqa: E402
from jarvis.tools.mail_imap import ImapMail  # noqa: E402

ME = "me@example.com"
NOW = int(time.time())


def msg(uid, frm, subject, thread, ts, to=(ME,), bulk=False, answered=False, name=None):
    return {"id": uid, "from": frm, "from_name": name or frm.split("@")[0].title(), "subject": subject,
            "to": list(to), "cc": [], "message_id": f"<{uid}@x>", "references": "", "bulk": bulk,
            "answered": answered, "thread": thread, "labels": ["INBOX"], "ts": ts, "snippet": ""}


class FakeMail(ImapMail):
    def __init__(self, inbox, sent):
        self.me, self.drafts, self.contacts = ME, {}, {}
        self._inbox, self._sent = inbox, sent

    def _search(self, query):
        return [b"x"] if "in:sent" in query else [b"y"]

    def _fetch_meta(self, uids):
        return self._sent if uids == [b"x"] else self._inbox

    def body(self, mid):
        return "Can you send the Q3 numbers by Friday?"


def test_needs_reply_filters():
    inbox = [
        msg("1", "ahmed@example.com", "Q3 proposal review", "t1", NOW - 100, name="Ahmed Raza"),
        msg("2", "noreply@github.com", "CI failed", "t2", NOW - 200),
        msg("3", "news@vendor.com", "Weekly digest", "t3", NOW - 300, bulk=True),
        msg("4", "sara@example.com", "Demo moved", "t4", NOW - 400),            # you replied after this
        msg("5", "team@example.com", "FYI", "t5", NOW - 500, to=("all@example.com",)),  # not addressed to you
        msg("6", "ali@example.com", "Contract", "t6", NOW - 600, answered=True),
    ]
    sent = [msg("9", ME, "Re: Demo moved", "t4", NOW - 50)]
    got = FakeMail(inbox, sent).needs_reply()
    assert [m["id"] for m in got] == ["1"]


def test_make_reply_drafts_threaded_reply():
    from jarvis.tools import gmail

    CTX.gmail, CTX.llm, CTX.cfg = FakeMail([], []), None, {"email": {"signature": "Rafay"}}
    m = msg("1", "ahmed@example.com", "Q3 proposal review", "t1", NOW, name="Ahmed Raza")
    r = asyncio.run(gmail.make_reply(m, "tell him I'll send it Thursday"))
    d = CTX.gmail.drafts[r.pending["draft_id"]]
    assert r.pending["kind"] == "send_email" and r.pending["reply_msg"] is m
    assert d["to"] == "ahmed@example.com" and d["subject"] == "Re: Q3 proposal review"
    assert d["in_reply_to"] == "<1@x>" and d["body"].startswith("Hi Ahmed,") and d["body"].endswith("Rafay")


def _draft(body, to="basit.ahmed@example.com"):
    return {"subject": "Availability for a meeting", "body": body, "to": to}


def test_greeting_from_address():
    from jarvis.tools.gmail import greeting_name, name_from_address

    assert greeting_name("someone", "basit.ahmed@example.com") == "Basit"
    assert greeting_name("Sara Khan", "s.k@x.com") == "Sara"
    assert name_from_address("info@company.com") == "" and name_from_address("ali_raza92@x.com") == "Ali"


def test_replayed_session_edits_are_applied_never_pasted():
    """The 01:47-01:50 session: every instruction ended up inside the sent email as 'Change: ...'."""
    from jarvis.tools import gmail

    CTX.llm, CTX.cfg = None, {"email": {}}
    start = "Hi Someone,\n\nCould you let me know your availability for a meeting?\n\nBest regards"
    run = lambda body, said: asyncio.run(gmail.revise(_draft(body), said))
    assert run(start, "Please edit your name, the name is Basit.")[1].startswith("Hi Basit,")
    assert run(start, "Please read the name from the recipient address and correct the name")[1].startswith("Hi Basit,")
    assert run(start, "Remove the word Someone.")[1].startswith("Hi,")
    assert run("Hi Basit,\n\nCould you...\n\nBest regards", "I said remove his name from the email")[1].startswith("Hi,")
    # something it can't do without the AI: the draft is left alone, the words are NOT pasted in
    assert run(start, "To move someone, only they should be high and then the email.") is None


def test_confirmations_from_the_session():
    from jarvis.brain.intents import YES, normalize, parse

    assert YES.match(normalize("Yes, please send it.")) and YES.match(normalize("Just send it."))
    assert parse("Jarvis can you send email to someone?").args == {"to": ""}
    assert parse("I want to send an email to someone.").args == {"to": ""}


def test_whatsapp_chat_names():
    from jarvis.tools.whatsapp import chat_name

    assert chat_name("Basit Ahmed 12:45 See you tomorrow") == "Basit Ahmed"
    assert chat_name("Ali Raza Yesterday ok thanks") == "Ali Raza"
    assert chat_name("Meta AI 03/10/2026 To cancel / remove your ChatGPT") == "Meta AI"
    assert chat_name("Family Group") == "Family Group"
    assert chat_name("Mondayy Fan Club 10:01 hi") == "Mondayy Fan Club"


# ---------------------------------------------------------------- writing emails from instructions
import pytest  # noqa: E402

from jarvis.tools.gmail import _from_instruction, compose, looks_like_instruction  # noqa: E402


@pytest.mark.parametrize("said,subject,text", [
    # the email that went out word for word on 2 Nov 2026
    ("Ask about the availability for a meeting", "Availability for a meeting",
     "Could you please let me know your availability for a meeting?"),
    ("ask their availability for a call tomorrow", "Availability for a call tomorrow",
     "Could you please let me know your availability for a call tomorrow?"),
    ("ask him if he is free on Friday", "Quick question", "Could you please let me know if you are free on Friday?"),
    ("ask for the invoice", "Invoice", "Could you please send me the invoice?"),
    ("ask them about the project status", "Project status",
     "Could you please give me an update on the project status?"),
    ("remind him to submit his timesheet", "Reminder: Submit your timesheet",
     "Just a friendly reminder to submit your timesheet."),
    ("follow up on the proposal", "Following up: Proposal",
     "I'm following up on the proposal. Could you please share an update?"),
    ("tell him the demo is at 3", "Demo is at 3", "The demo is at 3."),
])
def test_instruction_becomes_the_email(said, subject, text):
    assert _from_instruction(said) == (subject, text)


@pytest.mark.parametrize("said", [
    "The meeting is moved to Monday",                                   # dictated: kept as said
    "request a refund politely, mentioning the order arrived damaged",  # too nuanced for a template
])
def test_no_template(said):
    assert _from_instruction(said) is None


def test_ai_is_not_used_when_a_template_fits():
    CTX.cfg, CTX.llm = {"email": {}}, None
    subject, body = asyncio.run(compose("Muhammad", "Ask about the availability for a meeting"))
    assert subject == "Availability for a meeting"
    assert body == "Hi Muhammad,\n\nCould you please let me know your availability for a meeting?\n\nBest regards"
    assert not looks_like_instruction("Could you please let me know your availability for a meeting?")


def test_dictated_text_is_kept():
    CTX.cfg, CTX.llm = {"email": {}}, None
    _, body = asyncio.run(compose("Ali", "The meeting is moved to Monday at 10"))
    assert "The meeting is moved to Monday at 10." in body


def test_unwritable_instruction_is_flagged_not_hidden():
    # no model available: the words stay, and the read-back warns that they read like an instruction
    CTX.cfg, CTX.llm = {"email": {}}, None
    _, body = asyncio.run(compose("Ali", "request a refund politely, mentioning the order arrived damaged"))
    from jarvis.tools.gmail import draft_text
    assert looks_like_instruction(draft_text(body))
