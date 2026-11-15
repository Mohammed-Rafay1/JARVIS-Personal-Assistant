"""Runs the board: what's on it, when it opens, and the conversations around it (see board.py for the store
and the words). Jarvis owns one Board; app.py hands it the board commands and a few events (an email sent,
a WhatsApp message sent, a Claude Code task started) so items it was doing get ticked off."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
from typing import Any

from ..tools import CTX
from ..tools.base import Result
from . import board as B

log = logging.getLogger(__name__)


def _short(text: str, n: int = 260) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def _first_sentences(text: str, n: int = 2, limit: int = 260) -> str:
    parts = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip())
    return _short(" ".join(parts[:n]), limit)


class Board:
    def __init__(self, jarvis, store: B.Store | None = None) -> None:
        self.j = jarvis
        self.cfg: dict[str, Any] = jarvis.cfg.get("board", {}) or {}
        self.store = store or B.Store()
        self.open = False
        self.view = "all"
        self._cache: dict[str, tuple[float, Any]] = {}
        self._mail: dict[str, dict] = {}       # emails on the board, by id (a click reads one)
        self.focus_until = 0.0
        self.focus_minutes = 0
        self.active: str | None = None         # the item Jarvis is doing with you right now (an email...)
        self._vitals: asyncio.Task | None = None
        self.walk_next: tuple[str, dict] | None = None   # the walkthrough's next offer (after the emails)
        self._autos: asyncio.Task | None = None
        self._show_when_back = False

    @property
    def sir(self) -> str:
        return self.j.cfg.get("address_as", "sir")

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    # ================================================================== the screen
    async def show(self, view: str = "all", focus: bool = True, fresh: bool = False) -> None:
        if self.j.in_call:  # the call shrinks to its pill so the board is in view
            self.j.ui.send({"type": "call", "value": "mini"})
        self.view, self.open = view, True
        # on screen at once with what's already known; the inbox, projects and weather follow when fetched
        data = await self.payload(quick=True)
        self.j.ui.send({"type": "board", "action": "show", "view": view, "focus": focus, "data": data})
        if self._vitals is None or self._vitals.done():
            self._vitals = asyncio.create_task(self._vitals_loop())
        log.info("board shown (%s)", view)
        asyncio.create_task(self.push(fresh=fresh))

    def hide(self) -> None:
        if self.open:
            log.info("board hidden")
        self.open = False
        self.j.ui.send({"type": "board", "action": "hide"})

    async def push(self, fresh: bool = False) -> None:
        """Send the latest numbers to an open board."""
        if not self.open:
            return
        try:
            self.j.ui.send({"type": "board", "action": "data", "view": self.view, "data": await self.payload(fresh)})
        except Exception:
            log.exception("board update failed")

    def push_soon(self) -> None:
        if self.open:
            asyncio.create_task(self.push())

    async def payload(self, fresh: bool = False, quick: bool = False) -> dict:
        """Everything the board shows. quick: only what's cached already (no waiting on Gmail or files)."""
        now = dt.datetime.now()
        if quick:
            got = lambda name, empty: (self._cache.get(name) or (0, empty))[1]  # noqa: E731
            emails, projects, weather = (got("emails", {"connected": CTX.gmail is not None, "items": [], "loading": True}),
                                         got("projects", []), got("weather", None))
        else:
            emails, projects, weather = await asyncio.gather(self._emails(fresh), self._projects(fresh),
                                                             self._weather(), return_exceptions=True)
        emails = emails if isinstance(emails, dict) else {"connected": False, "items": [], "error": True}
        projects = projects if isinstance(projects, list) else []
        weather = weather if isinstance(weather, dict) else None
        day = self.store.day()
        upcoming = self.store.upcoming(time.time())
        return {
            "greeting": f"{_part_of_day(now)}, {self.sir.capitalize() if self.sir == 'sir' else self.sir}",
            "date": now.strftime("%A, %d %B %Y"),
            "items": [{k: i[k] for k in ("id", "text", "owner", "kind", "status", "result", "carried")}
                      for i in day["items"]],
            "emails": emails, "projects": projects, "weather": weather,
            "reminders": [{"id": r["id"], "text": r["text"],
                           "at": dt.datetime.fromtimestamp(r["at"]).strftime("%I:%M %p").lstrip("0"),
                           "day": "" if dt.datetime.fromtimestamp(r["at"]).date() == now.date() else
                           dt.datetime.fromtimestamp(r["at"]).strftime("%a")} for r in upcoming[:5]],
            "focus": {"until": int(self.focus_until * 1000), "minutes": self.focus_minutes}
            if self.focus_until > time.time() else None,
            "log": day.get("log", [])[-8:][::-1],
            "ahead": [{"day": B.day_name(d, now.date()).capitalize(),
                       "items": [{"id": i["id"], "text": i["text"]} for i in items]} for d, items in self.store.ahead(7)],
            "active": self.active,
        }

    async def _cached(self, name: str, ttl: float, fresh: bool, make):
        hit = self._cache.get(name)
        if hit and not fresh and time.time() - hit[0] < ttl:
            return hit[1]
        value = await make()
        self._cache[name] = (time.time(), value)
        return value

    async def _emails(self, fresh: bool) -> dict:
        async def make() -> dict:
            if CTX.gmail is None:
                return {"connected": False, "items": [], "error": bool(getattr(CTX, "email_error", ""))}
            q = "in:inbox newer_than:1d"
            if CTX.cfg.get("email", {}).get("only_primary", True):
                q += " category:primary"
            msgs = await asyncio.to_thread(CTX.gmail.search, q, 14)
            self._mail = {m["id"]: m for m in msgs}
            return {"connected": True, "me": CTX.gmail.me, "items": [
                {"id": m["id"], "from": m["from_name"], "subject": m["subject"],
                 "unread": "UNREAD" in m.get("labels", []), "time": _clock(m.get("ts", 0)),
                 "thread": format(int(m["thread"]), "x") if str(m.get("thread", "")).isdigit() else ""}
                for m in msgs]}
        return await self._cached("emails", 90, fresh, make)

    async def _projects(self, fresh: bool) -> list:
        from ..tools import claude_code as cc

        async def make() -> list:
            projects = await asyncio.to_thread(cc.list_projects, 6)
            running = {t.cwd: t for t in cc.running()}
            return [{"name": p.name, "cwd": p.cwd, "title": p.title, "ago": cc._ago(p.updated),
                     "last": _short(cc._speakable(p.last_prompt), 110),
                     "running": p.cwd in running,
                     "task": _short(running[p.cwd].task, 90) if p.cwd in running else ""} for p in projects]
        return await self._cached("projects", 45, fresh, make)

    async def _weather(self) -> dict | None:
        from ..tools import weather as W

        async def make() -> dict | None:
            city = CTX.cfg.get("weather", {}).get("city", "Lahore")
            got = await W.fetch(city)
            if not got:
                return None
            name, data = got
            cur, day = data.get("current", {}), data.get("daily", {})
            try:
                return {"city": name, "temp": round(cur["temperature_2m"]), "sky": W.describe(cur.get("weather_code")),
                        "hi": round(day["temperature_2m_max"][0]), "lo": round(day["temperature_2m_min"][0]),
                        "rain": day.get("precipitation_probability_max", [None])[0],
                        "humidity": cur.get("relative_humidity_2m"), "wind": cur.get("wind_speed_10m")}
            except (KeyError, IndexError, TypeError):
                return None
        try:
            return await self._cached("weather", 1800, False, make)
        except Exception as e:
            log.info("board weather unavailable: %s", e.__class__.__name__)
            return None

    async def _vitals_loop(self) -> None:
        """Live system readings while the board is open."""
        import psutil

        psutil.cpu_percent(None)
        last = psutil.net_io_counters()
        last_t = time.monotonic()
        while self.open:
            await asyncio.sleep(1.5)
            try:
                net, now = psutil.net_io_counters(), time.monotonic()
                span = max(now - last_t, 0.1)
                bat = psutil.sensors_battery()
                disk = psutil.disk_usage("C:\\")
                self.j.ui.send({"type": "board_vitals", "cpu": round(psutil.cpu_percent(None)),
                                "mem": round(psutil.virtual_memory().percent),
                                "battery": round(bat.percent) if bat else None,
                                "plugged": bool(bat.power_plugged) if bat else None,
                                "disk": round(disk.percent),
                                "down": round((net.bytes_recv - last.bytes_recv) / span / 1024),
                                "up": round((net.bytes_sent - last.bytes_sent) / span / 1024),
                                "focus_left": max(0, int(self.focus_until - time.time()))})
                last, last_t = net, now
            except Exception as e:
                log.debug("vitals: %s", e)

    # ================================================================== from the screen
    async def on_action(self, msg: dict) -> None:
        a = msg.get("action")
        if a == "ready":  # the board window (re)connected: give it what to show
            if self.open:
                await self.push()
        elif a == "open":  # "Open board view" (right-click on the orb, or the tray): show it and explain it
            await self.show("all", fresh=True)
            await self.j.board_say(await self.walkthrough())
        elif a == "closed":
            self.open = False
        elif a == "toggle":
            it = self.store.get(msg.get("id", ""))
            if it:
                done = it["status"] != "done"
                self.store.set(it, status="done" if done else "todo")
                self.store.note(("Done: " if done else "Reopened: ") + it["text"])
                await self.push()
        elif a == "remove":
            it = self.store.get(msg.get("id", ""))
            if it:
                self.store.remove(it)
                await self.push()
        elif a == "add" and (msg.get("text") or "").strip():
            for text in B.split_plan(msg["text"]) or [msg["text"]]:
                self._add(text)
            await self.push()
        elif a == "run":
            await self.j.board_run()
        elif a == "refresh":
            await self.push(fresh=True)
        elif a == "email" and msg.get("id") in self._mail:
            from ..tools.gmail import summarise_message

            await self.j.board_say(await summarise_message(self._mail[msg["id"]]))
        elif a == "project" and msg.get("cwd"):
            await self.j.board_say(await self.j.run_tool("claude_project_status", {"project": msg["cwd"]}))
        elif a == "focus":
            await self.j.board_say(self.start_focus(int(msg.get("minutes") or 25)))
        elif a == "focus_stop":
            await self.j.board_say(self.stop_focus())

    def _add(self, text: str, on: dt.date | None = None) -> dict:
        """Put one item on the list: today's, or the day it names ("tomorrow", "on Monday"). The item gets a
        "day" key (not stored) saying which day, for what Jarvis says back."""
        from ..brain.intents import parse

        day, clean = B.when_day(text, dt.date.today())
        on = on or day
        owner, kind, tool, args = B.classify(clean, lambda s: parse(s, self.j.cfg["music"]["default_service"]))
        it = self.store.add(clean, owner, kind, tool, args, on=on)
        return {**it, "day": on}

    def day_words(self, on: dt.date | None) -> str:
        """" for tomorrow", " for Monday", or "" for today."""
        return f" for {B.day_name(on, dt.date.today())}" if on and on > dt.date.today() else ""

    # ================================================================== briefing and planning
    async def brief_text(self) -> str:
        """The spoken morning briefing: weather, inbox, projects, and what's carried over."""
        data = await self.payload(fresh=True)
        now = dt.datetime.now()
        parts = [f"{_part_of_day(now)}, {self.sir}. It's {now.strftime('%A, the')} {_ordinal(now.day)}."]
        w = data["weather"]
        if w:
            rain = f", with a {w['rain']} percent chance of rain" if w.get("rain") else ""
            parts.append(f"It's {w['temp']} degrees in {w['city']}, {w['sky']}, up to {w['hi']} today{rain}.")
        em = data["emails"]
        if em.get("connected"):
            items = em["items"]
            unread = [m for m in items if m["unread"]]
            if items:
                names = list(dict.fromkeys(m["from"] for m in (unread or items)))
                parts.append(f"You have {len(items)} email{'s' if len(items) != 1 else ''} from the last day"
                             f"{f', {len(unread)} unread' if unread else ''}, from {B.listing(names, 3)}.")
            else:
                parts.append("Your inbox is quiet.")
        running = [p for p in data["projects"] if p["running"]]
        if running:
            parts.append(f"Claude Code is working in {B.listing([p['name'] for p in running], 2)}.")
        elif data["projects"]:
            p = data["projects"][0]
            parts.append(f"Your most recent project is {re.sub(r'[-_]+', ' ', p['name'])}, active {p['ago']}.")
        carried = [i["text"] for i in self.store.open_items() if i.get("carried")]
        if carried:
            parts.append(f"From yesterday you still have {B.listing(carried, 3)}.")
        ups = self.store.upcoming(time.time())
        if ups and dt.datetime.fromtimestamp(ups[0]["at"]).date() == now.date():
            r = ups[0]
            parts.append(f"Reminder at {dt.datetime.fromtimestamp(r['at']).strftime('%I:%M %p').lstrip('0')}: "
                         f"{r['text']}.")
        return " ".join(parts)

    async def needs_reply(self) -> list[dict]:
        """Emails from real people still waiting for your answer (cached for a few minutes)."""
        async def make() -> list[dict]:
            if CTX.gmail is None or not hasattr(CTX.gmail, "needs_reply"):
                return []
            return await asyncio.to_thread(CTX.gmail.needs_reply)
        try:
            return await self._cached("needs_reply", 900, False, make)  # dropped when new mail arrives
        except Exception as e:
            log.info("needs-reply check failed: %s", e)
            return []

    def _project_offer(self, p: dict) -> tuple[str, dict]:
        name = re.sub(r"[-_]+", " ", p["name"])
        return (f"Shall I give Claude Code the next prompt in {name}?",
                {"kind": "board_walk", "stage": "project", "cwd": p["cwd"], "name": p["name"]})

    async def walkthrough(self) -> Result:
        """The board explained out loud, panel by panel, ending with the first thing Jarvis can do for you:
        reply to the emails that need it, then the next prompt for your latest Claude Code project."""
        from ..tools import claude_code as cc

        # what the board already has (kept warm in the background) so Jarvis starts talking straight away
        data = await self.payload(quick=True) if "emails" in self._cache and "projects" in self._cache \
            else await self.payload()
        sir = self.sir
        parts = [f"Here's your board, {sir}."]
        items = self.store.items
        open_ = self.store.open_items()
        if not items:
            parts.append("Nothing on today's list yet.")
        elif not open_:
            parts.append(f"All {len(items)} things on today's list are done.")
        else:
            done = len(items) - len(open_)
            yours = [i["text"] for i in open_ if i["owner"] == "you"]
            mine = [i["text"] for i in open_ if i["owner"] == "jarvis"]
            parts.append(f"{done} of {len(items)} done today." if done else f"{len(items)} things on today's list.")
            if yours:
                parts.append(f"Still on yours: {B.listing(yours, 3)}.")
            if mine:
                parts.append(f"On mine: {B.listing(mine, 2)}.")
        ups = self.store.upcoming(time.time())
        if ups and dt.datetime.fromtimestamp(ups[0]["at"]).date() == dt.date.today():
            r = ups[0]
            parts.append(f"Next reminder at {dt.datetime.fromtimestamp(r['at']).strftime('%I:%M %p').lstrip('0')}: "
                         f"{r['text']}.")
        em = data["emails"]
        waiting: list[dict] = []
        if em.get("connected"):
            unread = sum(m["unread"] for m in em["items"])
            waiting = await self.needs_reply()
            if waiting:
                who = [f"{m['from_name']} about {_short(m['subject'], 60)}" for m in waiting[:3]]
                more = f", and {len(waiting) - 3} more" if len(waiting) > 3 else ""
                parts.append(f"{len(waiting)} email{'s need' if len(waiting) != 1 else ' needs'} your reply: "
                             f"from {'; '.join(who)}{more}.")
            elif unread:
                parts.append(f"{unread} unread email{'s' if unread != 1 else ''}, none waiting on a reply from you.")
            else:
                parts.append("Your inbox is clear.")
        projects = data["projects"]
        running = [p for p in projects if p["running"]]
        if running:
            p = running[0]
            parts.append(f"Claude Code is working in {re.sub(r'[-_]+', ' ', p['name'])} right now"
                         f"{': ' + p['task'] if p['task'] else ''}.")
        latest = next((p for p in projects if not p["running"]), None)
        if latest:
            name = re.sub(r"[-_]+", " ", latest["name"])
            # the session title, not your last prompt word for word (that's on the board, and reads badly aloud)
            line = f"Your latest project is {name}, active {latest['ago']}"
            parts.append(line + (f", working on {latest['title']}." if latest["title"] else "."))
        # the offers, in order: emails first, then the project
        project_offer = self._project_offer(latest) if latest and cc.tasks_allowed() else None
        self.walk_next = project_offer
        if waiting:
            parts.append("Shall I help you reply to them?" if len(waiting) > 1 else "Shall I help you reply to it?")
            return Result(" ".join(parts), icon="board",
                          pending={"kind": "board_walk", "stage": "emails", "messages": waiting})
        if project_offer:
            self.walk_next = None
            parts.append(project_offer[0])
            return Result(" ".join(parts), icon="board", pending=project_offer[1])
        return Result(" ".join(parts), icon="board")

    async def morning(self, asked: bool = False) -> None:
        """Open the board, brief you and ask what's on today: out loud, and on WhatsApp."""
        from ..tools.whatsapp import screen_locked

        day = self.store.day()
        day["briefed"] = True
        self.store.save()
        brief = await self.brief_text()
        has_plan = any(not i.get("carried") for i in self.store.items)
        question = ("Anything to add to today's list?" if has_plan else
                    "What would you like to get done today? Tell me and I'll put it on the board.")
        locked = await asyncio.to_thread(screen_locked)
        remote = self.j.remote
        if remote is not None and remote.linked and (not asked or locked):
            remote.notify(f"☀️ *{brief}*\n\n{question.replace('Tell me', 'Reply here')}")
        self.j.pending = {"kind": "plan_today"}
        if locked:
            self._show_when_back = True
            log.info("morning briefing sent to WhatsApp (laptop locked)")
            return
        await self.show("all", focus=False, fresh=True)
        await self.j.board_say(Result(f"{brief} {question}", pending={"kind": "plan_today"}), follow=True)

    async def take_plan(self, raw: str, source: str = "") -> Result | None:
        # "tomorrow I need to..." puts the whole plan on tomorrow; otherwise each item can name its own day
        whole, raw2 = B.when_day(raw, dt.date.today()) if re.match(r"\s*(?:for |on )?(?:tomorrow|the day after)", raw,
                                                                   re.I) else (None, raw)
        texts = B.split_plan(raw2)
        if not texts:
            return None
        added = [self._add(t, on=whole) for t in texts]
        day = self.store.day()
        day["source"] = source or day.get("source", "")
        self.store.note(f"Plan: {len(added)} item{'s' if len(added) != 1 else ''} added")
        later = [i for i in added if i["day"]]
        added_today = [i for i in added if not i["day"]]
        yours = [i["text"] for i in added_today if i["owner"] == "you"]
        mine = [i for i in added_today if i["owner"] == "jarvis"]
        n = len(added_today)
        say = [f"Got it, {self.sir}. {n} thing{'s' if n != 1 else ''} for today." if n else f"Got it, {self.sir}."]
        if yours:
            say.append(f"On your list: {B.listing(yours, 5)}.")
        by_day: dict = {}
        for i in later:
            by_day.setdefault(i["day"], []).append(i["text"])
        for d, texts_on in sorted(by_day.items()):
            say.append(f"For {B.day_name(d, dt.date.today())}: {B.listing(texts_on, 4)}.")
        pending = None
        if mine:
            say.append(f"I can take care of {B.listing([i['text'] for i in mine], 4)} myself. "
                       f"Shall I start on {'it' if len(mine) == 1 else 'those'} now?")
            pending = {"kind": "board_start"}
        else:
            say.append("I'll tick things off as you tell me they're done.")
        if self.j._remote_said is None:
            await self.show("all" if self.open and self.view == "all" else "todos", focus=False)
        else:
            await self.push()
        return Result(" ".join(say), icon="board", pending=pending,
                      card={"kind": "board_list", "text": self.text_list()} if self.j._remote_said is not None else None)

    async def run_jarvis(self) -> tuple[Result, str | None]:
        """Start Jarvis's own items: the quick ones run in the background; the first one that needs a
        conversation (an email to check, a message to confirm) is returned, to start right after this is said."""
        items = self.store.open_items("jarvis")
        autos = [i for i in items if i["kind"] == "auto" and i["status"] in ("todo", "failed")]
        guided = [i for i in items if i["kind"] == "guided"]
        if not autos and not guided:
            busy = [i for i in items if i["status"] in ("queued", "working")]
            return Result(f"I'm already on {B.listing([i['text'] for i in busy], 3)}, {self.sir}." if busy else
                          f"There's nothing on my side of the list, {self.sir}.", icon="board"), None
        say = []
        if autos:
            for i in autos:
                self.store.set(i, status="queued")
            if self._autos is None or self._autos.done():
                self._autos = asyncio.create_task(self._run_autos())
            say.append(f"On it. I'm doing {B.listing([i['text'] for i in autos], 3)} in the background and "
                       "I'll tick it off on the board when it's done." if len(autos) == 1 else
                       f"On it. I'm doing {B.listing([i['text'] for i in autos], 3)} in the background and "
                       "I'll tick them off on the board as they finish.")
        await self.push()
        if guided:
            first = guided[0]
            say.append(f"{'First' if not autos else 'Now'}, {first['text'][:1].lower() + first['text'][1:]}.")
            return Result(" ".join(say), icon="board"), first["id"]
        return Result(" ".join(say), icon="board"), None

    async def start_guided(self, item_id: str) -> bool:
        it = self.store.get(item_id)
        if not it:
            return False
        self.active = it["id"]
        self.store.set(it, status="working")
        await self.push()
        await self.j.handle_text(it["text"])
        return True

    async def _run_autos(self) -> None:
        while True:
            queue = [i for i in self.store.items if i["owner"] == "jarvis" and i["status"] == "queued"]
            if not queue:
                return
            it = queue[0]
            self.store.set(it, status="working")
            await self.push()
            try:
                r = await self.j.run_tool(it["tool"], it["args"])
            except asyncio.CancelledError:
                self.store.set(it, status="todo")
                raise
            except Exception as e:
                log.exception("board task failed: %s", it["text"])
                r = Result(f"That didn't work: {str(e)[:80]}.", ok=False)
            text = r.say
            if r.card and r.card.get("kind") == "research":
                from .whatsapp_remote import card_text

                text = re.sub(r"[*_]", "", card_text(r.card)) or text
            self.store.set(it, status="done" if r.ok else "failed", result=_short(text, 600))
            self.store.note(("Done: " if r.ok else "Couldn't finish: ") + it["text"])
            self.j.settle()
            await self.push()
            await self._report(it, r)

    async def _report(self, it: dict, r: Result) -> None:
        """Tell you one of Jarvis's items finished: on WhatsApp if that's where you planned the day (or the
        laptop is locked), else a note on screen and a short spoken line when Jarvis is free."""
        from ..tools.whatsapp import screen_locked
        from .whatsapp_remote import card_text

        remote = self.j.remote
        away = self.store.day().get("source") == "whatsapp" or await asyncio.to_thread(screen_locked)
        if away and remote is not None and remote.linked:
            body = card_text(r.card) if r.card else r.say
            remote.notify(f"{'✅' if r.ok else '⚠️'} *{it['text']}*\n\n{body}")
            return
        self.j.ui.send({"type": "notify", "kind": "board", "from": "Done" if r.ok else "Couldn't finish",
                        "subject": it["text"]})
        line = (f"{it['text']} is done. {_first_sentences(r.say, 2)}" if r.ok else
                f"I couldn't finish {it['text'][:1].lower() + it['text'][1:]}. {_first_sentences(r.say, 1)}")
        await self.j.board_announce(line)

    async def finished(self, kind: str, target: str = "") -> str:
        """An email / WhatsApp message was sent or a Claude Code task started: tick off the item it was for.
        Returns what to say next (the next item to do together), or ""."""
        it = self.store.get(self.active) if self.active else None
        if not (it and B.GUIDED_KIND.get(it["tool"]) == kind):
            # not started from the board, but maybe it's on the list anyway ("email Sara" done by hand)
            t = target.lower().split()[0] if target else ""
            it = next((i for i in self.store.open_items("jarvis") if B.GUIDED_KIND.get(i["tool"]) == kind
                       and t and t in i["text"].lower()), None)
        if not it:  # a reply from the board walkthrough: on to its next offer
            return self.walk_on() if kind == "email" else ""
        self.active = None
        self.store.set(it, status="done")
        self.store.note("Done: " + it["text"])
        await self.push()
        nxt = next((i for i in self.store.open_items("jarvis") if i["kind"] == "guided"), None)
        if not nxt:
            return self.walk_on()
        self.j.pending = {"kind": "board_guided", "id": nxt["id"]}
        return f"That's ticked off. Next on my list: {nxt['text'][:1].lower() + nxt['text'][1:]}. Shall I?"

    def walk_on(self) -> str:
        """The board walkthrough's next offer (the Claude Code prompt), once the emails are dealt with."""
        if not self.walk_next:
            return ""
        question, pending = self.walk_next
        self.walk_next = None
        self.j.pending = pending
        return question

    # ================================================================== your items
    def complete(self, said: str, explicit: bool) -> Result | None:
        items = self.store.open_items()
        if not items:
            return Result(f"Your list is clear, {self.sir}.", icon="board") if explicit else None
        it, score, clear = B.best_match(said, items)
        if it is None or score < (60 if explicit else 74) or (not clear and score < 85):
            if not explicit:
                return None
            if it is not None and score >= 50:
                return Result(f"Do you mean {it['text']}?", icon="board",
                              pending={"kind": "todo_confirm", "id": it["id"]})
            return Result(f"I couldn't find that on today's list, {self.sir}. On it now: "
                          f"{B.listing([i['text'] for i in items], 4)}.", icon="board")
        return self._tick(it)

    def _tick(self, it: dict) -> Result:
        self.store.set(it, status="done")
        self.store.note("Done: " + it["text"])
        if self.active == it["id"]:
            self.active = None
        self.push_soon()
        left = len(self.store.open_items())
        if left == 0:
            say = f"Ticked off {it['text']}. That's everything on today's list. Brilliant work, {self.sir}."
        else:
            praise = ("Nice work", "Well done", "Good going", "Excellent")[len(self.store.items) % 4]
            say = f"{praise}, {self.sir}. {it['text']} is ticked off. {left} to go."
        return Result(say, icon="board")

    def text_list(self) -> str:
        """Today's list as text (for WhatsApp)."""
        items = self.store.items
        if not items:
            return "Nothing on today's list yet."
        mark = {"done": "✅", "working": "⏳", "queued": "⏳", "failed": "⚠️"}
        lines = []
        for n, i in enumerate(items, 1):
            who = " _(Jarvis)_" if i["owner"] == "jarvis" else ""
            lines.append(f"{mark.get(i['status'], '▫️')} {n}. {i['text']}{who}")
        done = sum(i["status"] == "done" for i in items)
        return f"*Today: {done}/{len(items)} done*\n" + "\n".join(lines)

    def ahead_result(self, day: dt.date | None = None) -> Result:
        """What's planned for tomorrow (or another day), or for the week ahead."""
        today = dt.date.today()
        ahead = [(d, items) for d, items in self.store.ahead(7) if day is None or d == day]
        if not ahead:
            when = B.day_name(day, today) if day else "the coming days"
            return Result(f"Nothing planned for {when} yet, {self.sir}. Say something like: add call the bank "
                          f"for {B.day_name(day, today) if day else 'tomorrow'}.", icon="board")
        parts = [f"For {B.day_name(d, today)}: {B.listing([i['text'] for i in items], 5)}." for d, items in ahead]
        return Result(" ".join(parts), icon="board")

    def context_text(self) -> str:
        """The list, plans ahead and reminders, as plain facts for the language model (so it answers "what's
        my task tomorrow?" from the real list instead of guessing)."""
        today = dt.date.today()
        lines = [f"Today ({today.strftime('%A %d %B')}):"]
        items = self.store.items
        lines += [f"- {i['text']} ({'done' if i['status'] == 'done' else 'Jarvis is doing it' if i['owner'] == 'jarvis' else 'to do'})"
                  for i in items] or ["- nothing yet"]
        for d, its in self.store.ahead(7):
            lines.append(f"{B.day_name(d, today).capitalize()} ({d.strftime('%A %d %B')}):")
            lines += [f"- {i['text']}" for i in its]
        ups = self.store.upcoming(time.time())
        if ups:
            lines.append("Reminders:")
            lines += [f"- {dt.datetime.fromtimestamp(r['at']).strftime('%A %I:%M %p')}: {r['text']}" for r in ups[:5]]
        return "\n".join(lines)

    def list_result(self) -> Result:
        items = self.store.items
        if not items:
            return Result(f"Nothing on today's list yet, {self.sir}. Tell me what you'd like to get done.",
                          icon="board", pending={"kind": "plan_today"})
        open_ = self.store.open_items()
        done = len(items) - len(open_)
        if not open_:
            return Result(f"All {len(items)} done for today, {self.sir}.", icon="board",
                          card=self._list_card())
        yours = [i["text"] for i in open_ if i["owner"] == "you"]
        mine = [i["text"] for i in open_ if i["owner"] == "jarvis"]
        say = f"{done} of {len(items)} done." if done else f"{len(items)} on the list."
        if yours:
            say += f" Still on yours: {B.listing(yours, 5)}."
        if mine:
            say += f" On mine: {B.listing(mine, 3)}."
        return Result(say, icon="board", card=self._list_card())

    def _list_card(self) -> dict | None:
        return {"kind": "board_list", "text": self.text_list()} if self.j._remote_said is not None else None

    # ================================================================== reminders and focus
    def remind(self, t: str) -> Result:
        now = dt.datetime.now()
        got = B.parse_reminder(t, now)
        if not got:  # no time given: it goes on today's list instead
            what = re.sub(r"^(?:set (?:a |an )?reminder|remind me|reminder)(?: (?:to|that|about|for)\b)?\s*", "", t)
            if len(what) < 3:
                return Result("What should I remind you about, and when? Say something like: remind me at 5 to "
                              "call Ali.", icon="board")
            it = self._add(what)
            self.push_soon()
            where = f"the list{self.day_words(it['day'])}" if it["day"] else "today's list"
            return Result(f"I've put {it['text']} on {where}. Give me a time if you'd like a reminder too, "
                          "like: remind me at 5 to call Ali.", icon="board")
        what, when = got
        self.store.remind(what, when.timestamp())
        self.store.note(f"Reminder set for {when.strftime('%H:%M')}: {what}")
        self.push_soon()
        when_said = B.spoken_time(when, now)
        if what == "your reminder":
            return Result(f"Reminder set for {when_said}.", icon="board")
        if re.match(r"(?:the|a|an|my|your|our|that|this)\b", what):
            return Result(f"Reminder set for {when_said}: {what}.", icon="board")
        return Result(f"I'll remind you {when_said} to {what}.", icon="board")

    def reminders_result(self) -> Result:
        now = dt.datetime.now()
        ups = self.store.upcoming(time.time())
        if not ups:
            return Result("You have no reminders set.", icon="board")
        lines = [f"{B.spoken_time(dt.datetime.fromtimestamp(r['at']), now)}, {r['text']}" for r in ups[:4]]
        return Result(f"{len(ups)} reminder{'s' if len(ups) != 1 else ''}: " + ". ".join(lines) + ".", icon="board")

    def start_focus(self, minutes: int) -> Result:
        self.focus_minutes = minutes
        self.focus_until = time.time() + minutes * 60
        self.store.note(f"Focus session: {minutes} min")
        self.push_soon()
        return Result(f"Focus mode on for {minutes} minutes. I'll hold your email alerts until it's over.",
                      icon="board")

    def stop_focus(self) -> Result:
        if self.focus_until <= time.time():
            return Result("There's no focus session running.", icon="board")
        self.focus_until = 0
        self.push_soon()
        return Result("Focus session ended.", icon="board")

    @property
    def focusing(self) -> bool:
        return self.focus_until > time.time()

    # ================================================================== the clock
    async def run(self) -> None:
        """Reminders, the end of a focus session, the morning briefing and the evening wrap-up."""
        await asyncio.sleep(25)  # after the start-up greeting
        warmed = 0.0
        while True:
            try:
                await self._tick_clock()
            except Exception:
                log.exception("board clock")
            if time.time() - warmed > 300 and self._free():
                warmed = time.time()
                asyncio.create_task(self._warm())
            await asyncio.sleep(15)

    async def _warm(self) -> None:
        """Fetch the inbox, projects and weather every few minutes in the background, so the board and its
        walkthrough open instantly instead of waiting on Gmail."""
        try:
            await self.payload(fresh=True)
            await self.needs_reply()
        except Exception as e:
            log.debug("board warm-up: %s", e)

    def _free(self) -> bool:
        j = self.j
        return (not j.in_call and j.state in ("idle",) and j._remote_said is None
                and (j.task is None or j.task.done()))

    async def _tick_clock(self) -> None:
        from ..tools.whatsapp import screen_locked

        now_ts = time.time()
        for r in self.store.due(now_ts):
            r["fired"] = True
            self.store.save()
            await self._fire(r)
        if self.focus_until and self.focus_until <= now_ts:
            self.focus_until = 0
            self.store.note("Focus session complete")
            self.push_soon()
            await self.j.board_announce(f"Focus session complete, {self.sir}. Time for a short break.", urgent=True)
        if not self.enabled:
            return
        now = dt.datetime.now()
        day = self.store.day()
        if self._show_when_back and self._free() and not await asyncio.to_thread(screen_locked):
            self._show_when_back = False
            await self.show("all", focus=False, fresh=True)
            await self.j.board_announce(f"Welcome back, {self.sir}. Your board is up. Tell me today's plan "
                                        "whenever you're ready.")
            return
        morning = B.parse_hhmm(self.cfg.get("morning_at", "09:00"), dt.time(9, 0))
        until = B.parse_hhmm(self.cfg.get("morning_until", "14:00"), dt.time(14, 0))
        if self.cfg.get("morning", True) and not day.get("briefed") and morning <= now.time() < until \
                and self._free():
            log.info("morning briefing")
            await self.morning()
            return
        evening = B.parse_hhmm(self.cfg.get("evening_at", "18:30"), dt.time(18, 30))
        quiet = time.monotonic() - getattr(self.j, "last_heard", 0.0) > 120  # not in the middle of talking to you
        if self.cfg.get("evening", True) and not day.get("wrapped") and now.time() >= evening and day["items"] \
                and self._free() and quiet:
            day["wrapped"] = True
            self.store.save()
            await self._evening()

    async def _fire(self, r: dict) -> None:
        from ..tools.whatsapp import screen_locked

        log.info("reminder: %s", r["text"])
        self.store.note("Reminder: " + r["text"])
        self.push_soon()
        self.j.ui.send({"type": "notify", "kind": "reminder", "from": "Reminder", "subject": r["text"]})
        remote = self.j.remote
        locked = await asyncio.to_thread(screen_locked)
        if remote is not None and remote.linked and (locked or self.cfg.get("remind_on_whatsapp", True)):
            remote.notify(f"⏰ *Reminder:* {r['text']}")
        if not locked:
            await self.j.board_announce(f"{self.sir.capitalize()}, a reminder: {r['text']}.", urgent=True)

    async def _evening(self) -> None:
        items = self.store.items
        done = [i for i in items if i["status"] == "done"]
        left = [i["text"] for i in items if i["status"] != "done"]
        line = f"End of day wrap-up: you finished {len(done)} of {len(items)} things today."
        if left:
            line += f" {B.listing(left, 3)} will move to tomorrow's list."
        else:
            line += f" Everything's done. Excellent day, {self.sir}."
        remote = self.j.remote
        if remote is not None and remote.linked and self.store.day().get("source") == "whatsapp":
            remote.notify(f"🌙 {line}\n\n{self.text_list()}")
        await self.j.board_announce(line)


def _part_of_day(now: dt.datetime) -> str:
    return "Good morning" if now.hour < 12 else "Good afternoon" if now.hour < 17 else "Good evening"


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def _clock(ts: int) -> str:
    if not ts:
        return ""
    d = dt.datetime.fromtimestamp(ts)
    return d.strftime("%I:%M %p").lstrip("0") if d.date() == dt.date.today() else d.strftime("%a")
