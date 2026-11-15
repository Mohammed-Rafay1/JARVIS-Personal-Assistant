"""The board: today's plan, your inbox and your Claude Code projects on one frosted-glass screen.

Every morning (or whenever you ask) Jarvis opens the board, briefs you and asks what's on today. Your answer,
spoken, typed or sent on WhatsApp, becomes today's list. The items Jarvis can do itself (research, emails,
WhatsApp messages, Claude Code tasks) it offers to do; the rest are yours, and it ticks them off when you
tell it you've done them. Reminders, a focus timer and an evening wrap-up come with it.

Everything is kept in data/board.json, so the list survives restarts; unfinished items roll over to the
next day.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from rapidfuzz import fuzz

from ..config import DATA

log = logging.getLogger(__name__)
STORE = DATA / "board.json"

# what Jarvis can do on its own from a plan item: run straight away, or start a guided conversation
AUTO_TOOLS = {"ai_research", "web_research", "weather", "claude_project_status", "check_email"}
GUIDED_TOOLS = {"draft_email", "email_ask_content", "whatsapp_message", "whatsapp_ask", "claude_code_task",
                "emails_to_reply"}
# which guided item a finished action completes
GUIDED_KIND = {"draft_email": "email", "email_ask_content": "email", "emails_to_reply": "email",
               "whatsapp_message": "whatsapp", "whatsapp_ask": "whatsapp", "claude_code_task": "claude"}


# ---------------------------------------------------------------------------------------------- the store
class Store:
    """Today's list, earlier days, reminders and a short activity log, in one JSON file."""

    def __init__(self, path: Path = STORE, today: Callable[[], dt.date] = dt.date.today) -> None:
        self.path, self._today = path, today
        self.data: dict[str, Any] = {"days": {}, "reminders": []}
        try:
            self.data.update(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
        except Exception as e:  # a damaged file must never stop Jarvis; keep a copy and start clean
            log.warning("board store unreadable (%s); starting a new one", e)
            try:
                path.replace(path.with_suffix(".bad.json"))
            except OSError:
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        days = dict(sorted(self.data["days"].items())[-30:])  # a month of history is plenty
        self.data["days"] = days
        self.data["reminders"] = [r for r in self.data["reminders"]
                                  if not r.get("fired") or time.time() - r.get("at", 0) < 86400][-50:]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    @property
    def key(self) -> str:
        return self._today().isoformat()

    def _blank(self) -> dict:
        return {"items": [], "briefed": False, "wrapped": False, "source": "", "log": [], "opened": False}

    def day(self) -> dict:
        """Today's record; on the first look each day, the last day's unfinished items move over (items
        planned ahead for today, "tomorrow ..." said yesterday, are already in it)."""
        days = self.data["days"]
        rec = days.get(self.key)
        if rec is None or not rec.get("opened", True):
            rec = rec or self._blank()
            earlier = sorted(k for k in days if k < self.key)
            if earlier:
                have = {_key(i["text"]) for i in rec["items"]}
                for it in days[earlier[-1]].get("items", []):
                    if it["status"] != "done" and _key(it["text"]) not in have:
                        rec["items"].append({**it, "carried": True, "id": _new_id(),
                                             "status": "todo" if it["status"] != "failed" else "failed"})
            rec["opened"] = True
            days[self.key] = rec
            self.save()
        return rec

    def day_for(self, date: dt.date) -> dict:
        """Another day's record (a plan for tomorrow, Monday...)."""
        if date <= self._today():
            return self.day()
        return self.data["days"].setdefault(date.isoformat(), self._blank())

    @property
    def items(self) -> list[dict]:
        return self.day()["items"]

    def add(self, text: str, owner: str = "you", kind: str = "", tool: str = "", args: dict | None = None,
            on: dt.date | None = None) -> dict:
        text = tidy(text)
        items = self.day_for(on)["items"] if on else self.items
        same = next((i for i in items if i["status"] != "done" and _key(i["text"]) == _key(text)), None)
        if same:  # said twice: one item
            return same
        it = {"id": _new_id(), "text": text, "owner": owner, "kind": kind, "tool": tool, "args": args or {},
              "status": "todo", "result": "", "created": time.time(), "done_at": 0, "carried": False}
        items.append(it)
        self.save()
        return it

    def ahead(self, days: int = 7) -> list[tuple[dt.date, list[dict]]]:
        """Items planned for the coming days (not today), soonest first."""
        today = self._today()
        out = []
        for k in sorted(self.data["days"]):
            d = dt.date.fromisoformat(k)
            if today < d <= today + dt.timedelta(days=days):
                items = [i for i in self.data["days"][k]["items"] if i["status"] != "done"]
                if items:
                    out.append((d, items))
        return out

    def get(self, item_id: str) -> dict | None:
        hit = next((i for i in self.items if i["id"] == item_id), None)
        if hit is None:  # an item planned for a later day
            hit = next((i for k, rec in self.data["days"].items() if k > self.key for i in rec["items"]
                        if i["id"] == item_id), None)
        return hit

    def set(self, item: dict, **changes) -> None:
        item.update(changes)
        if changes.get("status") == "done" and not item.get("done_at"):
            item["done_at"] = time.time()
        if changes.get("status") and changes["status"] != "done":
            item["done_at"] = 0
        self.save()

    def remove(self, item: dict) -> None:
        self.day()
        for k, rec in self.data["days"].items():
            if k >= self.key:
                rec["items"] = [i for i in rec["items"] if i["id"] != item["id"]]
        self.save()

    def open_items(self, owner: str | None = None) -> list[dict]:
        return [i for i in self.items if i["status"] != "done" and (owner is None or i["owner"] == owner)]

    def note(self, text: str) -> None:
        """One line in today's activity log (shown on the board)."""
        lg = self.day().setdefault("log", [])
        lg.append([time.strftime("%H:%M"), text])
        del lg[:-30]
        self.save()

    # reminders ---------------------------------------------------------------------------------
    def remind(self, text: str, at: float) -> dict:
        r = {"id": _new_id(), "text": tidy(text), "at": at, "fired": False}
        self.data["reminders"].append(r)
        self.save()
        return r

    def due(self, now: float) -> list[dict]:
        return [r for r in self.data["reminders"] if not r["fired"] and r["at"] <= now]

    def upcoming(self, now: float) -> list[dict]:
        return sorted((r for r in self.data["reminders"] if not r["fired"] and r["at"] > now), key=lambda r: r["at"])


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def tidy(text: str) -> str:
    """"  call ali." -> "Call ali": spaces and stray punctuation go, the first letter is raised."""
    text = re.sub(r"\s+", " ", text).strip(" .,;:-")
    return text[:1].upper() + text[1:] if text else text


# ---------------------------------------------------------------------------------- understanding plans
_PLAN_LEAD = re.compile(
    r"^(?:(?:ok(?:ay)?|so|well|alright|right|yes|yeah|sure|hmm|um|uh|jarvis|sir)[\s,]+)*"
    r"(?:(?:today|for today|this morning|my plan(?: for today)?(?: is)?|the plan(?: is)?|my to ?do(?:s| list)?"
    r"(?: for today)?(?: (?:is|are))?|my tasks?(?: for today)?(?: (?:is|are))?|here(?:'s| is) (?:my|the) "
    r"(?:plan|list)|plan(?: for today)?)[\s,:-]*)*", re.I)
_ITEM_LEAD = re.compile(
    r"^(?:(?:and|also|then|after that|afterwards|later|first(?:ly)?|second(?:ly)?|third(?:ly)?|next|finally|"
    r"lastly|plus|oh and|maybe|probably|today|in the (?:morning|afternoon|evening)|at some point)[\s,]+)*"
    r"(?:i (?:need|have|want|got|gotta|should|must|will|would like|plan|am going|'m going|ll)(?: to)?|"
    r"i'll|i'm going to|i am going to|i gotta|i've got to|i have got to|need to|have to|got to|gotta|want to|"
    r"we need to|we have to|you (?:need|have|should|can|could) |you'll|please |jarvis[, ]+|can you |could you )?\s*",
    re.I)
_SPLIT = re.compile(r"\s*(?:[;\n•·]|(?<!\d),(?!\d)|\.\s+|\s+(?:and then|and also|after that|then|also|plus)\s+|"
                    r"\s+and\s+(?=(?:i |i'll |then |also |you |please |jarvis |ask |call |email |e-mail |mail |"
                    r"send |write |research |search |look |find |check |finish |complete |go |meet |buy |book |"
                    r"pay |prepare |review |read |reply |message |whatsapp |text |submit |fix |update |clean |"
                    r"study |learn |work |do |make |pick |order |visit |attend |schedule |plan |play |open |draft |"
                    r"give |tell |get |start |build |create |design |test |deploy |push |practice |exercise |"
                    r"workout |gym )))\s*", re.I)
_NUMBERED = re.compile(r"(?:^|\s)(?:\d{1,2}[.)]|[-*])\s+")


def split_plan(text: str) -> list[str]:
    """"Today I need to call Ali, finish the report and research AI agents on ChatGPT" ->
    ["Call Ali", "Finish the report", "Research AI agents on ChatGPT"]."""
    t = _NUMBERED.sub("; ", text.strip())
    t = _PLAN_LEAD.sub("", t.strip())
    out: list[str] = []
    for part in _SPLIT.split(t):
        part = _ITEM_LEAD.sub("", (part or "").strip(" .,;:-")).strip(" .,;:-")
        part = re.sub(r"\s+(?:today|for today|please|as well|too)$", "", part, flags=re.I).strip(" .,;:-")
        if len(part) >= 3 and re.search(r"[a-z]", part, re.I) and not re.fullmatch(
                r"(?:that'?s (?:it|all)|nothing(?: else)?|that is (?:it|all)|done|thanks?|thank you|ok(?:ay)?)",
                part, re.I):
            out.append(tidy(part))
    return out[:15]


_NOTHING = re.compile(r"^(?:nothing|no plans?|nothing (?:much|today|special|planned)|free day|day off|not much|"
                      r"i'?m free|nothing for (?:now|today)|no|nope|later|not now|skip|skip it)$")


def classify(text: str, parse: Callable[[str], Any]) -> tuple[str, str, str, dict]:
    """(owner, kind, tool, args): whether Jarvis can do this item itself, and how."""
    try:
        intent = parse(text)
    except Exception:
        intent = None
    if intent is not None:
        if intent.tool in AUTO_TOOLS:
            return "jarvis", "auto", intent.tool, dict(intent.args)
        if intent.tool in GUIDED_TOOLS:
            return "jarvis", "guided", intent.tool, dict(intent.args)
    return "you", "", "", {}


# ------------------------------------------------------------------------------- ticking things off
_STEM = [(r"ies$", "y"), (r"(?<=[^aeiou])ied$", "y"), (r"(?<=..)ed$", ""), (r"(?<=..)ing$", ""), (r"(?<=...)s$", "")]
_IRREGULAR = {"went": "go", "gone": "go", "did": "do", "done": "do", "wrote": "write", "written": "write",
              "sent": "send", "bought": "buy", "paid": "pay", "met": "meet", "read": "read", "spoke": "speak",
              "spoken": "speak", "took": "take", "taken": "take", "made": "make", "got": "get", "gave": "give",
              "ate": "eat", "built": "build", "ran": "run", "drove": "drive", "came": "come", "brought": "bring",
              "fixed": "fix", "finished": "finish", "emailed": "email", "called": "call", "rang": "call",
              "texted": "text", "messaged": "message", "submitted": "submit", "shipped": "ship", "left": "leave"}
_FILLER = {"i", "ive", "i've", "have", "has", "had", "just", "already", "finally", "the", "a", "an", "my", "to",
           "with", "it", "that", "this", "now", "today", "so", "ok", "okay", "jarvis", "sir", "all", "of", "is",
           "are", "was", "been", "done", "complete", "completed", "finished", "finish", "mark", "as", "tick", "off",
           "cross", "check", "item", "task", "from", "list", "on", "do", "did", "also", "and", "yes", "yeah",
           "please", "for", "me", "we", "be", "out"}


def _words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9']+", text.lower().replace("’", "'")):
        w = _IRREGULAR.get(w, w)
        if w in _FILLER:
            continue
        for pat, rep in _STEM:
            new = re.sub(pat, rep, w)
            if new != w:
                w = new
                break
        out.append(w)
    return out


def match_score(said: str, item_text: str) -> float:
    a, b = " ".join(_words(said)), " ".join(_words(item_text))
    if not a or not b:
        return 0.0
    return max(fuzz.token_set_ratio(a, b), fuzz.partial_ratio(a, b) - 8 if min(len(a), len(b)) >= 6 else 0)


def best_match(said: str, items: list[dict]) -> tuple[dict | None, float, bool]:
    """(item, score, clear): the item meant, how sure, and whether it stands out from the next best."""
    m = re.fullmatch(r"(?:number |item |task |the )?(\d{1,2}|first|second|third|fourth|fifth|last)"
                     r"(?: one| item| task)?", said.strip().lower())
    if m and items:
        n = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "last": len(items)}.get(m.group(1))
        n = n or int(m.group(1))
        return (items[n - 1], 100.0, True) if 1 <= n <= len(items) else (None, 0.0, False)
    scored = sorted(((match_score(said, i["text"]), i) for i in items), key=lambda x: -x[0])
    if not scored:
        return None, 0.0, False
    top, item = scored[0]
    clear = len(scored) == 1 or top - scored[1][0] >= 12
    return item, top, clear


# "I've called Ali", "done with the report", "finished the gym": a statement that something is done
_DONE_STATEMENT = re.compile(
    r"^(?:ok(?:ay)? |so |yes |yeah |well |alright )?(?:jarvis )?(?:"
    r"i(?:'ve| have)? (?:just |already |finally )?(?:done|did|finished|completed|wrapped up|sorted|handled|"
    r"(?:\w+ed|went|wrote|sent|bought|paid|met|spoke|took|made|got|gave|built|ran|drove|read|rang|left))\b|"
    r"(?:just |already )?(?:done|finished|completed) (?:with )?|"
    r"(?:mark|tick|check|cross)(?: off)?\b|"
    r".+ (?:is|are) (?:done|finished|complete|completed|sorted|over)$)")


def done_statement(t: str) -> bool:
    return bool(_DONE_STATEMENT.match(t)) and len(t.split()) <= 18


# ---------------------------------------------------------------------------------------- reminders
_NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
        "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90, "half an": 0.5, "half a": 0.5, "a couple of": 2,
        "couple of": 2, "a few": 3, "few": 3}
_AMOUNT = r"(?P<n>\d+(?:\.\d+)?|half an|half a|a couple of|couple of|a few|forty five|" + \
          "|".join(k for k in _NUM if " " not in k) + r")"
_IN = re.compile(r"\bin " + _AMOUNT + r" (?P<u>minutes?|mins?|hours?|hrs?|seconds?|secs?)\b")
_AT = re.compile(r"\b(?:at|by|around) (?P<h>\d{1,2})(?::(?P<m>\d{2})| (?P<m2>\d{2})(?= ?(?:am|pm|a m|p m)))?"
                 r"(?: ?(?P<ap>am|pm|a m|p m|o'?clock))?\b|\b(?:at|by) (?P<noon>noon|midday|midnight)\b")
_PART = re.compile(r"\b(?:this |in the )?(?P<p>morning|afternoon|evening|tonight|night)\b")


def parse_when(t: str, now: dt.datetime) -> tuple[dt.datetime, str] | None:
    """A time said in words: (when, the words that said it). "in 20 minutes", "at 5", "at 5:30 pm",
    "tomorrow at 9", "at noon"."""
    m = _IN.search(t)
    if m:
        n = m.group("n")
        amount = float(n) if re.fullmatch(r"[\d.]+", n) else float(_NUM.get(n, 1))
        unit = m.group("u")
        secs = amount * (3600 if unit.startswith(("h", "hr")) else 1 if unit.startswith("s") else 60)
        return now + dt.timedelta(seconds=secs), m.group(0)
    m = _AT.search(t)
    if not m:
        return None
    if m.group("noon"):
        h, mi = (0, 0) if m.group("noon") == "midnight" else (12, 0)
    else:
        h, mi = int(m.group("h")), int(m.group("m") or m.group("m2") or 0)
        if h > 23 or mi > 59:
            return None
        ap = (m.group("ap") or "").replace(" ", "")
        part = _PART.search(t)
        if ap == "pm" and h < 12:
            h += 12
        elif ap == "am" and h == 12:
            h = 0
        elif not ap.endswith(("am", "pm")) and h < 12:
            # no am/pm: "this evening at 7" is 19:00; otherwise the next time that hour comes round
            if part and part.group("p") in ("afternoon", "evening", "tonight", "night") or \
                    (h <= 7 and now.hour >= h):
                h += 12
    when = now.replace(hour=h % 24, minute=mi, second=0, microsecond=0)
    if re.search(r"\btomorrow\b", t):
        when += dt.timedelta(days=1)
    elif when <= now:
        when += dt.timedelta(days=1)
    return when, m.group(0)


def parse_reminder(t: str, now: dt.datetime) -> tuple[str, dt.datetime] | None:
    """"remind me at 5 to call Ali" / "remind me to call Ali in 20 minutes" -> ("call Ali", 17:00)."""
    m = re.match(r"^(?:set (?:a |an )?reminder|remind me|reminder)(?: (?:to|that|about|for)\b)?\s*(?P<rest>.*)$", t)
    if not m:
        return None
    clock = r"(?=\d{1,2}(?::\d{2})? ?(?:am|pm|a m|p m)\b)"
    rest = re.sub(r"^" + clock, "at ", re.sub(r"\bfor " + clock, "at ", m.group("rest")))
    got = parse_when(rest, now)
    if not got:
        return None
    when, said = got
    what = rest.replace(said, " ")
    what = re.sub(r"\b(?:tomorrow|today|this (?:morning|afternoon|evening)|tonight)\b", " ", what)
    what = re.sub(r"^\s*(?:to|that|about|for|i have to|i need to)\s+", "", re.sub(r"\s+", " ", what).strip())
    what = re.sub(r"\s+(?:to|that|about)$", "", what).strip(" ,.")
    return (what or "your reminder"), when


def spoken_time(when: dt.datetime, now: dt.datetime) -> str:
    clock = when.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
    if when.date() == now.date():
        mins = round((when - now).total_seconds() / 60)
        return f"in {mins} minute{'s' if mins != 1 else ''}" if mins < 60 else f"at {clock}"
    if when.date() == now.date() + dt.timedelta(days=1):
        return f"tomorrow at {clock}"
    return when.strftime("%A at ") + clock


_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_DAY_PHRASE = re.compile(
    r"\s*\b(?:(?:for|on|by|in|this|next|coming)\s+)*(?:the\s+)?(?P<d>day after tomorrow|tomorrow(?:'s)?|"
    + "|".join(_WEEKDAYS) + r")\b(?:'s)?(?:\s+(?:list|plan|mission|board|to ?do list))?", re.I)


def when_day(text: str, today: dt.date) -> tuple[dt.date | None, str]:
    """(the day an item is for, the item without those words). "Deploy Polani mono tomorrow" ->
    (tomorrow, "Deploy Polani mono"); "Meeting with CCL's CTO on Monday 3pm" -> (Monday, "Meeting with CCL's CTO
    3pm"). (None, text) for today."""
    m = _DAY_PHRASE.search(text)
    if not m:
        return None, text
    d = m.group("d").lower().replace("'s", "")
    if d == "day after tomorrow":
        day = today + dt.timedelta(days=2)
    elif d == "tomorrow":
        day = today + dt.timedelta(days=1)
    else:
        ahead = (_WEEKDAYS.index(d) - today.weekday()) % 7
        day = today + dt.timedelta(days=ahead or 7)   # "Monday" said on a Monday means next week's
    rest = re.sub(r"\s+", " ", (text[:m.start()] + " " + text[m.end():])).strip(" ,.-")
    return day, rest or text


def day_name(day: dt.date, today: dt.date) -> str:
    if day == today:
        return "today"
    if day == today + dt.timedelta(days=1):
        return "tomorrow"
    return day.strftime("%A") if (day - today).days < 7 else day.strftime("%A %d %B")


def focus_minutes(t: str) -> int:
    m = re.search(r"(\d{1,3}) ?(?:minutes?|mins?)", t)
    if m:
        return max(1, min(int(m.group(1)), 240))
    m = re.search(r"(\d(?:\.\d)?) ?(?:hours?|hrs?)", t)
    if m:
        return max(1, min(int(float(m.group(1)) * 60), 240))
    if re.search(r"\b(?:an|one) hour\b", t):
        return 60
    if re.search(r"\bhalf an hour\b", t):
        return 30
    return 25  # a classic pomodoro


def parse_hhmm(text: str, default: dt.time) -> dt.time:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(text or ""))
    return dt.time(int(m.group(1)), int(m.group(2))) if m else default


# ----------------------------------------------------------------------------------- words for speech
def listing(items: list[str], limit: int = 4) -> str:
    items = [i[:1].lower() + i[1:] if i[:2].isalpha() and not i[:2].isupper() else i for i in items]
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    return ", ".join(items[:-1]) + f" and {items[-1]}" if len(items) > 1 else (items[0] if items else "")


def progress_line(store: Store) -> str:
    items = store.items
    done = sum(i["status"] == "done" for i in items)
    left = len(items) - done
    if not items:
        return "Your list is empty."
    if left == 0:
        return "That's everything on today's list."
    return f"{left} to go." if done else f"{left} on the list."
