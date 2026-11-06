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
from .whatsapp import U32, _is_front, _keys, _name, screen_locked
from .whatsapp import _bring_to_front as _front

log = logging.getLogger(__name__)
SEND_WITHIN = 60.0   # seconds to get the question typed and sent; past that, stop rather than paste late


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
                   ("ask chatgpt", "do anything", "ask anything", "message chatgpt", "chat with chatgpt"),
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


_QUESTION = re.compile(r"^(?:what|who|whom|whose|why|how|when|where|which|is|are|was|were|can|could|should|would|"
                       r"will|do|does|did|explain|compare|describe|list|tell me|give me|write|summari[sz]e|define)\b",
                       re.I)
SUMMARY_TAG = "In short:"


def research_prompt(query: str) -> str:
    """Your words as a prompt that gets a useful answer: "hitler" alone got a one-word chat. A topic becomes
    a request for a structured overview; a question stays your question. Both ask for a closing "In short:"
    line, which Jarvis reads out (faster and truer than summarising locally)."""
    q = re.sub(r"\s+", " ", query).strip().rstrip(".?!")
    q = re.sub(r"^(?:about|on|into|regarding|the topic of)\s+", "", q, flags=re.I)
    close = f' End with one line starting "{SUMMARY_TAG}" that sums up the answer in two plain sentences.'
    if _QUESTION.match(q):
        return f"{q[:1].upper()}{q[1:]}? Answer clearly and accurately, with the key facts." + close
    return (f"Research {q} and give me a clear, well-structured overview: what or who it is, the key facts "
            "and dates, why it matters, and any important context or debate. Keep it concise." + close)


def in_short(text: str) -> str:
    """The answer's own closing summary, if it wrote one."""
    m = re.search(re.escape(SUMMARY_TAG) + r"\s*(.+)", text, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:500] if m else ""


def lead(text: str, n: int = 2) -> str:
    """The opening of the answer's first real paragraph (not a title, a date line or a bullet)."""
    for line in text.splitlines():
        line = line.strip()
        if len(line.split()) >= 8 and not line.startswith(("•", "-")) and re.search(r"[.!?]", line):
            return first_sentences(line, n)
    return first_sentences(text, n)


# (text, top on screen, text height, kind): kind "p" paragraph text, "li" inside a list item, "cell" a table cell
Piece = tuple[str, "int | None", "int | None", str]


def reflow(pieces: list[Piece]) -> str:
    """Rebuild an answer's lines from its on-screen text pieces.

    ChatGPT's app exposes an answer word by word ("Born: ", "20 ", "April ", "1889"); joined one per line that
    became a single tall column of words on WhatsApp (6 Nov). Pieces on the same screen line, or a wrapped line
    after a piece ending in a space, belong to the same paragraph unless the text size changes (a heading);
    pieces inside a list item become "• ...", and a table's cells "• 1933 – Appointed Chancellor"."""
    blocks: list[tuple[str, str]] = []
    cur, cur_kind, prev = "", "p", None
    for text, top, h, kind in pieces:
        if not text.strip():
            continue
        if prev is None:
            cur, cur_kind = text, kind
        else:
            ptext, ptop, ph, pkind = prev
            same_line = top is not None and ptop is not None and abs(top - ptop) <= 8
            same_size = h is None or ph is None or abs(h - ph) <= 4
            if kind == "cell" or pkind == "cell":
                joined = kind == pkind == "cell" and same_line
                if joined:
                    cur += " – " + text.strip()
            else:
                wrapped = ptext[-1:].isspace() and kind == pkind and same_size
                joined = (same_line and same_size) or wrapped or (text[:1] in ",.;:!?)" and same_size)
                if joined:
                    glue = "" if ptext[-1:].isspace() or text[:1].isspace() or text[:1] in ",.;:!?)" else " "
                    cur += glue + text
            if not joined:
                blocks.append((cur, cur_kind))
                cur, cur_kind = text, kind
        prev = (text, top, h, kind)
    if cur.strip():
        blocks.append((cur, cur_kind))
    lines = []
    for i, (text, kind) in enumerate(blocks):
        t = re.sub(r"\s+", " ", text).strip()
        t = re.sub(r"^(?:chatgpt|claude|you) said:\s*", "", t, flags=re.I)  # a screen-reader label on the same line
        if not t or _NOISE.match(t):
            continue
        if kind == "cell" and (i == 0 or blocks[i - 1][1] != "cell"):
            lines.append(t)          # a table's heading row ("Date – Event"), shown as a title
        else:
            lines.append(f"• {t}" if kind in ("li", "cell") else t)
    return re.sub(r"[*#`>|]+", "", "\n".join(lines)).strip()


def _piece(e) -> Piece:
    try:
        r = e.element_info.rectangle
        top, h = int(r.top), int(r.bottom - r.top)
    except Exception:
        top = h = None
    try:
        parent = e.element_info.parent.control_type or ""
    except Exception:
        parent = ""
    kind = "cell" if _ctype(e) == "DataItem" else "li" if parent == "ListItem" else "p"
    return _name(e), top, h, kind


def _is_piece(e) -> bool:
    """Answer text: Text elements, and table cells (DataItems inside a row DataItem)."""
    c = _ctype(e)
    if c == "Text":
        return bool(_name(e).strip())
    if c == "DataItem" and _name(e).strip():
        try:
            return (e.element_info.parent.control_type or "") == "DataItem"
        except Exception:
            return False
    return False


def answer_lines(items, prompt: str) -> str:
    """The answer after your question, rebuilt into paragraphs, bullet points and table rows (one pass)."""
    pieces = [_piece(e) for e in items if _is_piece(e)]
    key = re.sub(r"\s+", " ", prompt).strip().lower()[:60]
    at = next((i for i, p in enumerate(pieces) if key and key in re.sub(r"\s+", " ", p[0]).lower()), -1)
    return reflow(pieces[at + 1:]) if at >= 0 else ""


def answer_after(texts: list[str], prompt: str) -> str:
    """The conversation's text in reading order -> what came after your question (the answer so far).
    Your question is the FIRST text that starts like it; a short question's words also appear all
    through the answer, so matching the last one never settled (it once waited out 150 s)."""
    key = re.sub(r"\s+", " ", prompt).strip().lower()[:60]
    at = next((i for i, t in enumerate(texts) if key and key in re.sub(r"\s+", " ", t).lower()), -1)
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
    """The app's main window, even when it was closed to the tray (hidden): then it is shown again.
    Plain Win32 listing: the UI Automation listing took 2-5 s a time, and a hidden window never showed
    up in it, so the old wait loop ran for minutes (4 Nov, 01:38 and 01:41)."""
    import ctypes.wintypes

    import psutil
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    pids = {p.pid for p in psutil.process_iter(["name"]) if app.process in (p.info["name"] or "").lower()}
    if not pids:
        return None
    found: list[tuple[int, int, int]] = []   # (visible, area, hwnd)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def each(hwnd, _):
        pid = ctypes.wintypes.DWORD()
        U32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and U32.GetWindow(hwnd, 4) == 0:  # GW_OWNER: top-level, not a popup
            buf = ctypes.create_unicode_buffer(256)
            U32.GetWindowTextW(hwnd, buf, 256)
            if buf.value.strip() == app.name:
                r = ctypes.wintypes.RECT()
                U32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append((int(bool(U32.IsWindowVisible(hwnd))), (r.right - r.left) * (r.bottom - r.top), hwnd))
        return True
    U32.EnumWindows(each, 0)
    if not found:
        return None
    visible, _, hwnd = max(found)
    if not visible:
        log.info("%s was hidden in the tray; showing it", app.name)
        U32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.8)
    elif U32.IsIconic(hwnd):
        U32.ShowWindow(hwnd, 9)
    return UIAWrapper(UIAElementInfo(hwnd))


def _open(app: App, deadline: float):
    w = _window(app)
    if w is not None:
        return w
    app_id = (CTX.apps.apps.get(app.name) if CTX.apps else None) or app.app_id
    log.info("%s isn't open; starting it", app.name)
    os.startfile(f"shell:AppsFolder\\{app_id}")  # noqa: S606 - the user's own installed app
    while time.monotonic() < min(deadline, time.monotonic() + 25):
        time.sleep(0.5)
        w = _window(app)
        if w is not None:
            time.sleep(1.5)  # let it finish drawing before looking for its buttons
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
    # by its label, or (the label changes between versions: "Ask ChatGPT" since Nov 2026) the rich editor
    boxes = _find(w, lambda e: _ctype(e) == "Edit" and _visible(e) and (
        _name(e).strip().lower() in app.boxes or "prosemirror" in (e.element_info.class_name or "").lower()),
        tries=10)
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


def _bring_to_front(w, name: str = "") -> None:
    try:
        _front(w, name or "the app")
    except Exception as e:
        raise AppError("error", str(e)) from None


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
    deadline = time.monotonic() + SEND_WITHIN
    if screen_locked():
        raise AppError("locked", "Windows is locked")
    t0 = time.monotonic()
    w = _open(app, deadline)
    log.info("%s: window ready after %.1fs", app.name, time.monotonic() - t0)
    if U32.IsIconic(w.handle):
        U32.ShowWindow(w.handle, 9)
    if key == "chatgpt":  # the app also has a Codex mode, with its own box: only ever type into ChatGPT
        modes = _find(w, lambda e: _ctype(e) == "Button" and _name(e).lower().startswith("switch mode"), tries=6)
        current = _name(modes[0]).split(":")[-1].strip() if modes else ""
        if current and current.lower() != "chatgpt":
            raise AppError("error", f"the app is in {current} mode, not ChatGPT")
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
    _bring_to_front(w, app.name)
    if new:
        _press(new[0])
    elif _is_front(w):  # sidebar hidden: the app's own New chat shortcut (Ctrl+Shift+O)
        log.info("%s: no New chat button; using the shortcut", app.name)
        _keys(0x11, 0x10, 0x4F)
    else:
        raise AppError("error", "no New chat button")
    time.sleep(1.2)
    box = _box(w, app)
    if time.monotonic() > deadline:  # never paste a question minutes after it was asked
        raise AppError("error", f"took over {SEND_WITHIN:.0f}s to get ready")
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


def _final(items, prompt: str, rough: str) -> str:
    """The finished answer laid out properly; the rough per-piece text if that fails."""
    if not items:
        return rough
    try:
        return answer_lines(items, prompt) or rough
    except Exception as e:
        log.debug("answer layout failed: %s", e)
        return rough


def wait_answer(key: str, w, prompt: str, timeout: float = 150) -> Answer:
    """Read the conversation until the answer stops growing and the app no longer shows Stop."""
    _com()
    app = APPS[key]
    start = time.monotonic()
    deadline, last, stable, seen, nudged, last_items = start + timeout, "", 0, 0.0, False, None
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if not last and not nudged and time.monotonic() - start > 25 and not screen_locked():
            # nothing readable yet: the app stops updating what it shows while it's covered or minimised
            # (4 Nov, 12:01: the answer was written but never readable), so put it back in view once
            nudged = True
            log.info("%s: no answer readable yet; bringing it into view", app.name)
            try:
                if U32.IsIconic(w.handle):
                    U32.ShowWindow(w.handle, 9)
                _front(w, app.name)
            except Exception:
                pass
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
        if text:
            last_items = items
        if stable >= 2:
            log.info("%s answered: first text after %.1fs, done after %.1fs (one read %.1fs)", app.name, seen,
                     time.monotonic() - start, time.monotonic() - t0)
            return Answer(key, _final(items, prompt, text))
    log.info("%s: answer not readable within %.0fs (window switched away?)", app.name, timeout)
    return Answer(key, _final(last_items, prompt, last), "" if last else "unread")


async def ask_all(keys: list[str], prompt: str) -> list[Answer]:
    """Type into the apps one at a time (keyboard focus is shared), then wait for all answers together."""
    started, answers = [], {}
    for k in keys:
        try:
            started.append((k, await asyncio.to_thread(submit, k, prompt)))
        except AppError as e:
            log.warning("%s: stopped before sending: %s", APPS[k].name, e.why or e)
            answers[k] = Answer(k, problem=str(e))
            if str(e) == "locked":  # the same for every app: don't try the next one
                answers.update({o: Answer(o, problem="locked") for o in keys if o not in answers})
                break
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
    own = [in_short(a.text) for a in got if in_short(a.text)]
    if own and len(own) == len(got) == 1:
        return own[0]  # the answer's own closing summary: instant
    if CTX.llm and await CTX.llm.available():
        body = "\n\n".join(f"{APPS[a.service].name}'s answer:\n{a.text[:1500]}" for a in got)
        try:
            # 6 Nov: 30 s wasn't enough for this CPU to read 1500 characters and write a summary
            text = await asyncio.wait_for(CTX.llm.complete(
                "Summarise these answers for the user, to be read aloud: two or three short plain sentences, "
                "the key facts only, no lists, no markdown, no mention of which assistant said what unless they "
                "disagree.", f"Question: {question}\n\n{body}", max_tokens=120), timeout=60)
            if text:
                return re.sub(r"\s+", " ", re.sub(r"[*#`]", "", text)).strip()
        except Exception as e:
            log.warning("research summary failed: %s", e.__class__.__name__)
    # no summary: the opening of the first real paragraph, not the title and date lines
    return own[0] if own else lead(got[0].text, 3)


def _names(keys: list[str]) -> str:
    return " and ".join(APPS[k].name for k in keys)


async def _from_web(query: str) -> tuple[str, list[tuple[str, str]]]:
    """The same question answered from web results, when the apps couldn't answer."""
    from .web_research import answer_from, fetch_results, search_query

    q = search_query(query)
    results = await fetch_results(q)
    return await answer_from(q, results), results


@tool("Research a question: ask it in new ChatGPT and Claude chats, then summarise their answers",
      query="The question or topic", services="chatgpt, claude, or both (default)")
async def ai_research(query: str, services: str = "") -> Result:
    keys = parse_services(services)
    prompt = research_prompt(query)
    log.info("research prompt: %r", prompt)
    answers = await ask_all(keys, prompt)
    summary = await summarise(query, answers)
    sir = CTX.cfg.get("address_as", "sir")
    missing = [a.service for a in answers if a.problem == "not_installed"]
    failed = [a.service for a in answers if a.problem == "error"]
    unread = [a.service for a in answers if a.problem == "unread"]
    locked = any(a.problem == "locked" for a in answers)
    notes = []
    if locked:
        notes.append("Your laptop is locked, so I couldn't use the apps.")
    if missing:
        notes.append(f"I couldn't open the {_names(missing)} app.")
    if failed:
        notes.append(f"I couldn't safely type into {_names(failed)}, so I didn't ask it.")
    if unread:
        notes.append(f"{_names(unread)} has your answer in its app, but I couldn't read it back.")
    answered = [a.service for a in answers if a.text]
    card = {"kind": "research", "query": query, "summary": summary,
            "answers": [{"service": APPS[a.service].name, "text": a.text[:4000], "problem": a.problem}
                        for a in answers]}
    if summary:
        say = f"{summary} The full answer{'s are' if len(answered) > 1 else ' is'} open in {_names(answered)}."
        return Result(" ".join([say] + notes), icon="web", card=card)
    # never come back empty-handed: answer from the web instead, and say why
    web, results = await _from_web(query)
    if web:
        log.info("research answered from the web instead")
        card["summary"] = web
        card["answers"].append({"service": "Web results", "problem": "",
                                "text": "\n".join(f"{t}: {s}" for t, s in results)[:1500]})
        return Result(" ".join(notes + [f"Here's what the web says: {web}"]), icon="web", card=card)
    return Result(" ".join([f"I couldn't get an answer, {sir}."] + notes), ok=False, icon="web", card=card)
