"""Replies on the board (17 Nov): asking about emails opens the board with a drafted reply to each email that
needs yours, with Send; Jarvis asks about each and sends only on a click or a clear yes."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.services import replies as R  # noqa: E402
from jarvis.tools import CTX  # noqa: E402
from jarvis.tools import gmail as G  # noqa: E402

MSGS = [{"id": "11", "from": "murtaza@x.com", "from_name": "Murtaza Khalid", "subject": "Availability for a meeting",
         "ts": 0, "message_id": "<a@x>", "references": ""},
        {"id": "12", "from": "hr@agcp.com", "from_name": "AGCP Recruiting Team", "subject": "Re: Next steps",
         "ts": 0, "message_id": "<b@x>", "references": ""}]


class FakeMail:
    def __init__(self):
        self.drafts, self.sent, self.n = {}, [], 0

    def body(self, mid):
        return f"Email {mid}: are you free on Thursday?"

    def create_draft(self, to, subject, body, reply_to=None):
        self.n += 1
        self.drafts[f"d{self.n}"] = {"to": to, "subject": subject, "body": body}
        return f"d{self.n}"

    def update_draft(self, did, to, subject, body):
        self.drafts[did].update(to=to, subject=subject, body=body)

    def send_draft(self, did):
        self.sent.append(self.drafts.pop(did))


class FakeJarvis:
    def __init__(self):
        self.said, self.pending, self.speak_aloud, self.state = [], None, False, "idle"

    def is_quiet(self):
        return self.pending is None

    async def say(self, text, then_idle=True, follow=False):
        self.said.append(text)

    def _set_state(self, v, **k):
        self.state = v


class FakeBoard:
    def __init__(self):
        self.j, self._cache, self.pushed, self.notes = FakeJarvis(), {"needs_reply": 1}, 0, []
        self.store = type("S", (), {"note": lambda s, t: self.notes.append(t)})()

    def push_soon(self):
        self.pushed += 1


def _setup(monkeypatch):
    mail = FakeMail()
    monkeypatch.setattr(CTX, "gmail", mail, raising=False)

    async def compose(m, instruction, original=None):
        if instruction == R.AUTO:
            return f"Hi {m['from_name'].split()[0]},\n\nThanks, Thursday works for me. I'll confirm the time shortly."
        return f"Hi {m['from_name'].split()[0]},\n\n{instruction}."
    monkeypatch.setattr(G, "compose_reply", compose)
    board = FakeBoard()
    return mail, board, R.ReplyDesk(board)


def test_drafts_every_email_and_asks_about_the_first(monkeypatch):
    mail, board, desk = _setup(monkeypatch)

    async def go():
        await desk.open(MSGS)
        await desk._drafting
    asyncio.run(go())
    shown = desk.payload()
    assert [r["status"] for r in shown] == ["ready", "ready"] and len(mail.drafts) == 2 and not mail.sent
    assert shown[0]["subject"] == "Re: Availability for a meeting" and shown[1]["subject"] == "Re: Next steps"
    assert "Email 11" in shown[0]["preview"]
    assert board.j.pending == {"kind": "reply_board", "id": "11"}
    assert "The reply to Murtaza is ready: Thanks, Thursday works for me." in board.j.said[-1]
    assert board.j.said[-1].endswith("Shall I send it?")


def _answer(desk, board, text):
    return asyncio.run(desk.answer(text, text))


def test_yes_sends_only_that_one_then_asks_the_next(monkeypatch):
    mail, board, desk = _setup(monkeypatch)

    async def drafted():
        await desk.open(MSGS)
        await desk._drafting
    asyncio.run(drafted())
    assert _answer(desk, board, "yes send it")
    assert len(mail.sent) == 1 and mail.sent[0]["to"] == "murtaza@x.com"
    assert desk.items["11"]["status"] == "sent" and desk.items["12"]["status"] == "ready"
    assert board.j.pending == {"kind": "reply_board", "id": "12"} and "Replied to Murtaza Khalid" in board.notes[0]


def test_a_change_is_never_taken_as_yes(monkeypatch):
    mail, board, desk = _setup(monkeypatch)

    async def drafted():
        await desk.open(MSGS[:1])
        await desk._drafting
    asyncio.run(drafted())
    assert _answer(desk, board, "please change it to say I'm free on Monday")
    assert not mail.sent and "Monday" in desk.items["11"]["body"]
    assert board.j.pending == {"kind": "reply_board", "id": "11"} and "Shall I send it?" in board.j.said[-1]


def test_no_keeps_the_draft_and_stop_ends_the_round(monkeypatch):
    mail, board, desk = _setup(monkeypatch)

    async def drafted():
        await desk.open(MSGS)
        await desk._drafting
    asyncio.run(drafted())
    assert _answer(desk, board, "no") and not mail.sent
    assert board.j.pending == {"kind": "reply_board", "id": "12"}
    assert _answer(desk, board, "later") and not mail.sent and board.j.pending is None
    assert all(r["status"] == "ready" for r in desk.payload())   # the drafts stay on the board


def test_send_button_with_your_edits(monkeypatch):
    mail, board, desk = _setup(monkeypatch)

    async def go():
        await desk.open(MSGS[:1])
        await desk._drafting
        return await desk.send("11", "Hi Murtaza,\n\nThursday at 4 works.")
    said = asyncio.run(go())
    assert said == "Sent to Murtaza." and mail.sent[0]["body"] == "Hi Murtaza,\n\nThursday at 4 works."
    assert asyncio.run(desk.send("11")) == ""   # never twice


def test_skip_hides_it(monkeypatch):
    mail, board, desk = _setup(monkeypatch)

    async def go():
        await desk.open(MSGS[:1])
        await desk._drafting
    asyncio.run(go())
    desk.skip("11")
    assert desk.payload() == [] and not mail.sent


def test_gist_reads_the_main_line():
    assert R.gist("Hi Murtaza,\n\nThanks for reaching out. Thursday works.\n\nBest") == "Thanks for reaching out."


def test_send_needs_a_clear_yes():
    for t in ("yes", "send it", "okay send it now", "yes please"):
        assert R._SEND_OK.fullmatch(t), t
    for t in ("please change it to say monday", "yes but say monday", "make it shorter"):
        assert not R._SEND_OK.fullmatch(t), t


def test_asking_about_emails_opens_the_board_and_drafts(monkeypatch, tmp_path):
    from jarvis.services import board as B
    from jarvis.services.briefing import Board
    from jarvis.tools.base import Result

    mail, _, _ = _setup(monkeypatch)
    mail.needs_reply = lambda: MSGS
    sent = []
    j = FakeJarvis()
    j.cfg, j.in_call = {"address_as": "sir"}, False
    j.ui = type("UI", (), {"send": lambda s, m: sent.append(m)})()
    board = Board(j, B.Store(tmp_path / "b.json"))

    async def go():
        async def quick(**k):
            return {}
        board.payload = lambda fresh=False, quick=False: quick_payload()

        async def quick_payload():
            return {"replies": board.replies.payload()}
        r = await board.email_desk(Result("3 emails today, from Murtaza, AGCP and LinkedIn.", icon="mail"),
                                   "check_email")
        await board.replies._drafting
        return r
    r = asyncio.run(go())
    assert r.say.startswith("3 emails today") and "2 emails need your reply, from Murtaza Khalid and AGCP" in r.say
    assert "ask you before sending" in r.say and r.pending is None
    assert any(m.get("type") == "board" and m.get("view") == "emails" for m in sent)
    assert [x["status"] for x in board.replies.payload()] == ["ready", "ready"] and not mail.sent
