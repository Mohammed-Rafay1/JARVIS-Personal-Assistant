"""Replies on the board (17 Nov): ask about your emails and the board opens with every email that needs your
reply, each with a reply Jarvis drafted from what the email says, a Send button, and Jarvis asking you about
them one by one.

Nothing is sent without you: a click on Send, or "yes" to "Shall I send it?" for that one reply.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from ..tools import CTX

log = logging.getLogger(__name__)

# what Jarvis drafts from when you haven't said what to reply: the email itself decides
AUTO = "on your own"
_SEND_OK = re.compile(r"(?:(?:yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|please|alright|fine)[, ]*)*"
                      r"(?:send(?: it| that| this| the reply| that one| this one)?)?(?: now)?(?: please)?")


def _first(name: str) -> str:
    return (name or "them").split()[0]


def gist(body: str, limit: int = 170) -> str:
    """The reply's main line, to read out: the first sentence after the greeting."""
    lines = [ln.strip() for ln in body.strip().splitlines() if ln.strip()]
    if lines and re.match(r"^(?:hi|hello|dear|hey|good (?:morning|afternoon|evening))\b", lines[0], re.I) \
            and len(lines[0]) < 40:
        lines = lines[1:]
    text = " ".join(lines)
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    s = (m.group(1) if m else text).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


class ReplyDesk:
    def __init__(self, board) -> None:
        self.b = board
        self.j = board.j
        self.items: dict[str, dict] = {}      # message id -> the email, its draft and where it stands
        self._drafting: asyncio.Task | None = None
        self._asking = False                  # a round of "shall I send it?" is going on
        self._passed: set[str] = set()        # "no" this round: not asked again until the next time you ask

    # ------------------------------------------------------------------ what the board shows
    def payload(self) -> list[dict]:
        return [{"id": mid, "from": it["msg"]["from_name"], "addr": it["msg"]["from"], "subject": it["subject"],
                 "time": it["time"], "preview": it["preview"], "body": it["body"], "status": it["status"]}
                for mid, it in self.items.items() if it["status"] != "skipped"]

    # ------------------------------------------------------------------ drafting
    async def open(self, waiting: list[dict]) -> None:
        """These emails need a reply: put them on the board and draft the ones not drafted yet."""
        from .briefing import _clock

        live = {m["id"] for m in waiting}
        for mid in [k for k, it in self.items.items() if k not in live and it["status"] not in ("sent",)]:
            self.items.pop(mid)   # answered elsewhere meanwhile
        for m in waiting:
            if m["id"] not in self.items:
                subject = m["subject"] if m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
                self.items[m["id"]] = {"msg": m, "subject": subject, "time": _clock(m.get("ts", 0)), "preview": "",
                                       "body": "", "status": "drafting", "draft_id": "", "original": None}
        self._passed.clear()
        self.b.push_soon()
        if self._drafting is None or self._drafting.done():
            self._drafting = asyncio.create_task(self._draft_all())

    async def _draft_all(self) -> None:
        try:
            for mid in list(self.items):
                it = self.items.get(mid)
                if it and it["status"] == "drafting":
                    await self._draft(it, AUTO)
            await self.offer_next()
        except Exception:
            log.exception("drafting replies")

    async def _draft(self, it: dict, instruction: str) -> None:
        from ..tools.gmail import compose_reply

        it["status"] = "drafting"
        self.b.push_soon()
        m = it["msg"]
        try:
            if it["original"] is None:
                it["original"] = await asyncio.to_thread(CTX.gmail.body, m["id"])
                it["preview"] = re.sub(r"\s+", " ", it["original"])[:260]
            body = await compose_reply(m, instruction, original=it["original"])
            if it["draft_id"]:
                await asyncio.to_thread(CTX.gmail.update_draft, it["draft_id"], m["from"], it["subject"], body)
            else:
                it["draft_id"] = await asyncio.to_thread(CTX.gmail.create_draft, m["from"], it["subject"], body, m)
            it.update(body=body, status="ready")
            log.info("reply drafted for %s: %r", m["from_name"], gist(body, 80))
        except Exception as e:
            log.warning("couldn't draft the reply to %s: %s", m.get("from_name"), e)
            it["status"] = "failed"
        self.b.push_soon()

    async def redraft(self, mid: str, instruction: str = AUTO) -> dict | None:
        it = self.items.get(mid)
        if it and it["status"] not in ("sent", "sending"):
            await self._draft(it, instruction or AUTO)
        return it

    def skip(self, mid: str) -> None:
        it = self.items.get(mid)
        if it and it["status"] not in ("sent", "sending"):
            it["status"] = "skipped"
            self._passed.add(mid)
            self.b.push_soon()

    # ------------------------------------------------------------------ sending (only on your say-so)
    async def send(self, mid: str, body: str | None = None) -> str:
        """Send one reply. Called only for a click on its Send button or your "yes" to that reply."""
        it = self.items.get(mid)
        if not it or it["status"] not in ("ready", "failed") or not it["draft_id"]:
            return ""
        m = it["msg"]
        it["status"] = "sending"
        self.b.push_soon()
        try:
            if body is not None and body.strip() and body.strip() != it["body"].strip():
                it["body"] = body.strip()
                await asyncio.to_thread(CTX.gmail.update_draft, it["draft_id"], m["from"], it["subject"], it["body"])
            await asyncio.to_thread(CTX.gmail.send_draft, it["draft_id"])
        except Exception as e:
            log.exception("reply send failed")
            it["status"] = "ready"
            self.b.push_soon()
            return f"Sending to {_first(m['from_name'])} failed: {str(e)[:60]}. Nothing was sent."
        it["status"] = "sent"
        self.b._cache.pop("needs_reply", None)
        self.b.store.note(f"Replied to {m['from_name']}: {m['subject']}")
        self.b.push_soon()
        return f"Sent to {_first(m['from_name'])}."

    # ------------------------------------------------------------------ asking you, one by one
    def _next(self) -> dict | None:
        return next((it for mid, it in self.items.items() if it["status"] == "ready" and mid not in self._passed),
                    None)

    async def offer_next(self, wait: float = 120) -> None:
        """Ask about the next ready reply once Jarvis is free (not over you, not over another question)."""
        it = self._next()
        if it is None:
            self._asking = False
            return
        end = time.monotonic() + wait
        while not self.j.is_quiet():
            if time.monotonic() > end:
                return   # the drafts stay on the board; you'll be asked next time
            await asyncio.sleep(0.5)
        it = self._next()
        if it is None:
            return
        self._asking = True
        mid = next(k for k, v in self.items.items() if v is it)
        self.j.pending = {"kind": "reply_board", "id": mid}
        self.j.speak_aloud = True
        await self.j.say(f"The reply to {_first(it['msg']['from_name'])} is ready: {gist(it['body'])} "
                         "Shall I send it?", then_idle=False, follow=True)

    async def answer(self, t: str, raw: str) -> bool:
        """Your answer to "Shall I send it?" for one reply on the board."""
        from ..app import _NOT_NOW
        from ..brain.intents import NO, STOP, YES

        p = self.j.pending or {}
        self.j.pending = None
        mid = p.get("id", "")
        it = self.items.get(mid)
        if it is None:
            return False
        name = _first(it["msg"]["from_name"])
        if STOP.match(t) or re.search(r"\b(?:later|that's all|thats all|enough|stop asking)\b", t):
            self._asking = False
            await self.j.say("Alright. The drafts are on the board whenever you want them.", then_idle=True)
            return True
        if NO.match(t) or _NOT_NOW.search(t) or re.search(r"\b(?:don'?t send|do not send|not yet|skip|leave it)\b", t):
            self._passed.add(mid)
            more = self._next() is not None
            await self.j.say(f"Okay, I won't send the reply to {name}." + ("" if more else
                             " That's all of them; the drafts stay on the board."), then_idle=not more)
            if more:
                await self.offer_next(wait=10)
            return True
        # sending needs a clear yes: "please change it to..." must never send it
        if t.strip() and (YES.match(t) or _SEND_OK.fullmatch(t.strip())):
            said = await self.send(mid)
            more = self._next() is not None
            await self.j.say((said or "That one's already gone.") + ("" if more else " That's all of them."),
                             then_idle=not more)
            if more:
                await self.offer_next(wait=10)
            return True
        if len(t.split()) >= 2:   # "say I'm free on Monday instead": rewrite it that way and ask again
            self.j._set_state("thinking")
            await self.redraft(mid, raw.strip())
            self.j.pending = {"kind": "reply_board", "id": mid}
            await self.j.say(f"Changed. Now it says: {gist(it['body'])} Shall I send it?", then_idle=False,
                             follow=True)
            return True
        return False
