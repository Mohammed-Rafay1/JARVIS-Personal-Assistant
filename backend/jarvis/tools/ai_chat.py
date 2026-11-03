"""Research with the ChatGPT and Claude desktop apps: a new chat in each, your question pasted in and sent,
their answers read back and summarised.

Driven through Windows UI Automation, like WhatsApp. Safety rules:
- "New chat" and "Send" are pressed through the apps' own buttons (UI Automation Invoke), not by
  clicking screen positions.
- The question is pasted (Ctrl+V) only after checking that the app is the foreground window AND that
  its message box has the keyboard focus; otherwise Jarvis stops without typing anything. Your clipboard
  is put back afterwards.
- Answers are read from the apps' accessibility text; nothing is clicked while waiting.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass

from . import CTX
from .base import Result, tool
from .whatsapp import U32, _bring_to_front, _is_front, _keys, _name

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class App:
    name: str
    app_id: str           # Start-menu ID, used if it isn't running
    process: str          # executable name contains this
    boxes: tuple          # names of the message box, lower case
    stops: tuple          # names of the "stop" button shown while it writes, lower case
    mode: str = ""        # a mode switch to select first (Claude's "Chat and Cowork", not its Code tab)
    new_chat: tuple = ("new chat",)   # names of the new-chat button, lower case


APPS = {
    "chatgpt": App("ChatGPT", "OpenAI.Codex_2p2nqsd0c76g0!App", "chatgpt",
                   ("do anything", "ask anything", "message chatgpt", "chat with chatgpt"),
                   ("stop", "stop streaming", "stop generating", "stop response")),
    "claude": App("Claude", "Claude_pzs8sxrjxfjjc!Claude", "claude",
                  ("prompt", "write your prompt to claude", "reply to claude", "how can i help you today?"),
                  ("stop response", "stop", "stop generating"), mode="chat and cowork",
                  new_chat=("new chat", "new")),
}
# labels of buttons and badges that appear as text inside a conversation, not part of the answer
_NOISE = re.compile(r"^(?:copy|edit|retry|share|good response|bad response|read aloud|more actions|thought for .+|"
                    r"searched the web.*|searching.*|thinking.*|chatgpt|claude|you said:|chatgpt said:|"
                    r"claude can make mistakes.*|chatgpt can make mistakes.*|new chat|send|stop.*|"
                    r"\d{1,2}:\d{2}\s?(?:am|pm)?|latest response|work with codex|approve for me|"
                    r"(?:today|yesterday)(?: at .+)?)$", re.I)


class AppError(RuntimeError):
    """str(e) is the problem code; e.why says which safety check stopped it (for the log)."""
    def __init__(self, code: str, why: str = "") -> None:
        super().__init__(code)
        self.why = why


@dataclass
class Answer:
    service: str
    text: str = ""
    problem: str = ""   # "not_installed", "error" (not sent), "unread" (sent, but the answer couldn't be read)


def parse_services(services: str) -> list[str]:
    s = (services or "").lower()
    picked = [k for k, words in (("chatgpt", ("chatgpt", "chat gpt", "gpt", "openai")), ("claude", ("claude",)))
              if any(w in s for w in words)]
    return picked or ["chatgpt", "claude"]


def clean(text: str) -> str:
    """An answer as plain text: no markdown symbols, no button labels, tidy spacing."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not _NOISE.match(ln.strip())]
    t = re.sub(r"[*#`>|]+", "", "\n".join(lines))
    return re.sub(r"[ \t]+", " ", t).strip()


def first_sentences(text: str, n: int = 2) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return " ".join(re.split(r"(?<=[.!?])\s+", flat)[:n])[:400]


def answer_after(texts: list[str], prompt: str) -> str:
    """The conversation's text in reading order -> what came after your question (the answer so far)."""
    key = re.sub(r"\s+", " ", prompt).strip().lower()[:40]
    at = max((i for i, t in enumerate(texts) if key and key in re.sub(r"\s+", " ", t).lower()), default=-1)
    if at < 0:
        return ""
    return clean("\n".join(t for t in texts[at + 1:] if not _NOISE.match(t.strip())))


# ---------------------------------------------------------------- driving an app (runs in a worker thread)
def _com() -> None:
    import comtypes

    try:
        comtypes.CoInitialize()
    except OSError:
        pass


def _window(app: App):
    import psutil
    from pywinauto import Desktop

    pids = {p.pid for p in psutil.process_iter(["name"]) if app.process in (p.info["name"] or "").lower()}
    for w in Desktop(backend="uia").windows():
        try:
            if w.process_id() in pids and (w.window_text() or "").strip() == app.name:
                return w
        except Exception:
            continue
    return None


def _open(app: App):
    w = _window(app)
    if w is not None:
        return w
    app_id = (CTX.apps.apps.get(app.name) if CTX.apps else None) or app.app_id
    os.startfile(f"shell:AppsFolder\\{app_id}")  # noqa: S606 - the user's own installed app
    for _ in range(50):
        time.sleep(0.4)
        w = _window(app)
        if w is not None:
            return w
    raise AppError("not_installed")


def _find(w, pred, tries: int = 1, wait: float = 0.5) -> list:
    """Elements of the window matching pred. The apps switch their accessibility on lazily, so the first
    look can come back nearly empty; retry a few times."""
    for i in range(tries):
        try:
            hits = [e for e in w.descendants() if pred(e)]
        except Exception:
            hits = []
        if hits or i == tries - 1:
            return hits
        time.sleep(wait)
    return []


def _ctype(e) -> str:
    try:
        return e.element_info.control_type or ""
    except Exception:
        return ""


def _press(button) -> None:
    try:
        button.invoke()
    except Exception:
        button.iface_legacy_iaccessible.DoDefaultAction()


def _box(w, app: App):
    boxes = _find(w, lambda e: _ctype(e) == "Edit" and _name(e).strip().lower() in app.boxes and _visible(e), tries=10)
    if not boxes:  # the label changes between versions; the rich editor is the only one
        boxes = _find(w, lambda e: _ctype(e) == "Edit" and _visible(e)
                      and "prosemirror" in (e.element_info.class_name or "").lower(), tries=4)
    if not boxes:
        raise AppError("error", "no visible message box")
    return boxes[-1]


def _visible(e) -> bool:
    try:
        return e.is_visible() and not e.element_info.element.CurrentIsOffscreen
    except Exception:
        return False


def _selected(e) -> bool:
    try:
        return bool(e.is_selected())
    except Exception:
        try:
            return bool(e.iface_selection_item.CurrentIsSelected)
        except Exception:
            return False


def _has_focus(e) -> bool:
    try:
        return bool(e.element_info.element.CurrentHasKeyboardFocus)
    except Exception:
        return False


def _paste(w, box, text: str) -> None:
    import win32clipboard as cb

    _bring_to_front(w)
    box.set_focus()
    time.sleep(0.3)
    if not (_is_front(w) and _has_focus(box)):
        raise AppError("error", f"not safe to type (front={_is_front(w)}, focus={_has_focus(box)})")  # never type elsewhere
    previous = None
    cb.OpenClipboard()
    try:
        if cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT):
            previous = cb.GetClipboardData(cb.CF_UNICODETEXT)
        cb.EmptyClipboard()
        cb.SetClipboardData(cb.CF_UNICODETEXT, text)
    finally:
        cb.CloseClipboard()
    try:
        _keys(0x11, 0x56)  # Ctrl+V
        time.sleep(0.6)
    finally:
        cb.OpenClipboard()
        try:
            cb.EmptyClipboard()
            if previous is not None:
                cb.SetClipboardData(cb.CF_UNICODETEXT, previous)
        finally:
            cb.CloseClipboard()


def submit(key: str, prompt: str):
    """Open the app, start a new chat, paste the question and send it. Returns the window."""
    _com()
    app = APPS[key]
    w = _open(app)
    if U32.IsIconic(w.handle):
        U32.ShowWindow(w.handle, 9)
    if app.mode:  # Claude's window also holds Claude Code sessions: switch to chats first, never type into Code
        modes = _find(w, lambda e: _ctype(e) == "RadioButton" and _name(e).strip().lower() == app.mode, tries=10)
        if not modes:
            raise AppError("error", "no chat mode switch")
        if not _selected(modes[0]):
            _bring_to_front(w)
            try:
                modes[0].select()
            except Exception:
                _press(modes[0])
            time.sleep(1.5)
        if not _selected(modes[0]):
            raise AppError("error", "chat mode not selected")
    new = _find(w, lambda e: _ctype(e) == "Button" and _name(e).strip().lower() in app.new_chat, tries=10)
    if not new:
        raise AppError("error", "no New chat button")
    _bring_to_front(w)
    _press(new[0])
    time.sleep(1.2)
    box = _box(w, app)
    _paste(w, box, prompt)
    sends = _find(w, lambda e: _ctype(e) == "Button" and _name(e).strip().lower() in ("send", "send message",
                                                                                      "send prompt"), tries=6)
    if sends:
        _press(sends[-1])
    elif _is_front(w) and _has_focus(box):
        _keys(0x0D)  # Enter
    else:
        raise AppError("error", "no Send button")
    log.info("%s: question sent", app.name)
    return w


def wait_answer(key: str, w, prompt: str, timeout: float = 150) -> Answer:
    """Read the conversation until the answer stops growing and the app no longer shows Stop."""
    _com()
    app = APPS[key]
    start = time.monotonic()
    deadline, last, stable, seen = start + timeout, "", 0, 0.0
    while time.monotonic() < deadline:
        time.sleep(1.0)
        t0 = time.monotonic()
        try:
            items = w.descendants()
        except Exception:
            continue
        texts = [_name(e) for e in items if _ctype(e) == "Text" and _name(e).strip()]
        busy = any(_ctype(e) == "Button" and _name(e).strip().lower() in app.stops for e in items)
        text = answer_after(texts, prompt)
        if text and not seen:
            seen = time.monotonic() - start
        stable = stable + 1 if text and text == last and not busy else 0
        last = text
        if stable >= 2:
            log.info("%s answered: first text after %.1fs, done after %.1fs (one read %.1fs)", app.name, seen,
                     time.monotonic() - start, time.monotonic() - t0)
            return Answer(key, text)
    log.info("%s: answer not readable within %.0fs (window switched away?)", app.name, timeout)
    return Answer(key, last, "" if last else "unread")


async def ask_all(keys: list[str], prompt: str) -> list[Answer]:
    """Type into the apps one at a time (keyboard focus is shared), then wait for all answers together."""
    started, answers = [], {}
    for k in keys:
        try:
            started.append((k, await asyncio.to_thread(submit, k, prompt)))
        except AppError as e:
            log.warning("%s: stopped before sending: %s", APPS[k].name, e.why or e)
            answers[k] = Answer(k, problem=str(e))
        except Exception as e:
            log.warning("%s: couldn't send the question: %s", APPS[k].name, e)
            answers[k] = Answer(k, problem="error")
    done = await asyncio.gather(*(asyncio.to_thread(wait_answer, k, w, prompt) for k, w in started))
    answers.update({a.service: a for a in done})
    return [answers[k] for k in keys]


# ---------------------------------------------------------------- summary and the tool
async def summarise(question: str, answers: list[Answer]) -> str:
    """Two or three spoken sentences from the answers; the local model within 30 s, else the first answer's opening."""
    got = [a for a in answers if a.text]
    if not got:
        return ""
    if CTX.llm and await CTX.llm.available():
        body = "\n\n".join(f"{APPS[a.service].name}'s answer:\n{a.text[:1500]}" for a in got)
        try:
            text = await asyncio.wait_for(CTX.llm.complete(
                "Summarise these answers for the user, to be read aloud: two or three short plain sentences, "
                "the key facts only, no lists, no markdown, no mention of which assistant said what unless they "
                "disagree.", f"Question: {question}\n\n{body}", max_tokens=160), timeout=30)
            if text:
                return re.sub(r"\s+", " ", re.sub(r"[*#`]", "", text)).strip()
        except Exception as e:
            log.warning("research summary failed: %s", e.__class__.__name__)
    return first_sentences(got[0].text, 3)


def _names(keys: list[str]) -> str:
    return " and ".join(APPS[k].name for k in keys)


@tool("Research a question: ask it in new ChatGPT and Claude chats, then summarise their answers",
      query="The question or topic", services="chatgpt, claude, or both (default)")
async def ai_research(query: str, services: str = "") -> Result:
    keys = parse_services(services)
    answers = await ask_all(keys, query)
    summary = await summarise(query, answers)
    sir = CTX.cfg.get("address_as", "sir")
    missing = [a.service for a in answers if a.problem == "not_installed"]
    failed = [a.service for a in answers if a.problem == "error"]
    unread = [a.service for a in answers if a.problem == "unread"]
    notes = []
    if missing:
        notes.append(f"I couldn't open the {_names(missing)} app.")
    if failed:
        notes.append(f"I couldn't safely type into {_names(failed)}, so I didn't ask it.")
    if unread:
        notes.append(f"{_names(unread)} has your answer in its app, but I couldn't read it back.")
    answered = [a.service for a in answers if a.text]
    say = (f"{summary} The full answer{'s are' if len(answered) > 1 else ' is'} open in {_names(answered)}."
           if summary else f"I couldn't get an answer, {sir}.")
    card = {"kind": "research", "query": query, "summary": summary,
            "answers": [{"service": APPS[a.service].name, "text": a.text[:1500], "problem": a.problem}
                        for a in answers]}
    return Result(" ".join([say] + notes), ok=bool(summary), icon="web", card=card)
