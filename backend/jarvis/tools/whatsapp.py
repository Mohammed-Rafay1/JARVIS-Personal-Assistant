"""WhatsApp Desktop: find a contact, open the chat, put a message in the box, and send only on your word.

WhatsApp has no API on Windows, so this drives its window through UI Automation. Safety rules:
- The search box and message box are filled through UI Automation, aimed at WhatsApp's own controls.
- A real click is needed to open a chat; it happens only after checking that WhatsApp is the foreground
  window AND that the exact point belongs to WhatsApp. Otherwise it stops instead of clicking.
- Sending is a separate step that runs only after you confirm, and re-checks that the message box still
  holds exactly the text you approved.
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import time

from rapidfuzz import fuzz

from .base import Result, tool

log = logging.getLogger(__name__)
U32 = ctypes.windll.user32


class WhatsAppError(RuntimeError):
    pass


def _uia():
    from pywinauto.uia_defines import IUIA

    return IUIA().UIA_dll


def _name(e) -> str:
    try:
        return e.element_info.element.CurrentName or ""
    except Exception:
        return ""


def _bfs(root, pred, limit: int = 12000) -> list:
    q, out = [root], []
    while q and limit:
        node = q.pop(0)
        limit -= 1
        try:
            if pred(node):
                out.append(node)
            q += node.children()
        except Exception:
            pass
    return out


def _window():
    import psutil
    from pywinauto import Desktop

    pids = {p.pid for p in psutil.process_iter(["name"]) if "whatsapp" in (p.info["name"] or "").lower()}
    for w in Desktop(backend="uia").windows():
        try:
            if w.process_id() in pids and "whatsapp" in (w.window_text() or "").lower():
                return w
        except Exception:
            continue
    return None


def _open_window():
    os.startfile("whatsapp:")  # opens / restores WhatsApp Desktop
    for _ in range(30):
        time.sleep(0.4)
        w = _window()
        if w is not None:
            return w
    raise WhatsAppError("WhatsApp didn't open. Is WhatsApp Desktop installed and signed in?")


def _bring_to_front(w, name: str = "WhatsApp") -> None:
    hwnd = w.handle
    if U32.IsIconic(hwnd):
        U32.ShowWindow(hwnd, 9)  # SW_RESTORE
    k32 = ctypes.windll.kernel32
    for i in range(4):
        # Windows only lets the process that last received input change the foreground; a tap of Alt
        # (no other key) is the standard way to be allowed to
        U32.keybd_event(0x12, 0, 0, 0)
        U32.keybd_event(0x12, 0, 2, 0)
        if i == 0:
            U32.SetForegroundWindow(hwnd)
        else:
            # still refused (it was on 4 Nov): share input with the window in front for a moment, which
            # Windows does allow to hand over the foreground
            fg_thread = U32.GetWindowThreadProcessId(U32.GetForegroundWindow(), None)
            me = k32.GetCurrentThreadId()
            attached = fg_thread and fg_thread != me and U32.AttachThreadInput(me, fg_thread, True)
            try:
                U32.ShowWindow(hwnd, 5)  # SW_SHOW
                U32.BringWindowToTop(hwnd)
                U32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    U32.AttachThreadInput(me, fg_thread, False)
        time.sleep(0.3)
        if _is_front(w):
            return
    raise WhatsAppError(f"I couldn't bring {name} to the front, so I stopped rather than click anything.")


def screen_locked() -> bool:
    """True while Windows is locked (or at the sign-in screen): apps can't be driven then."""
    try:
        h = U32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not h:
            return True
        ok = U32.SwitchDesktop(h)  # switching to the desktop already in use: fails only when locked
        U32.CloseDesktop(h)
        return not ok
    except Exception:
        return False


def _is_front(w) -> bool:
    fg = U32.GetForegroundWindow()
    return fg == w.handle or U32.GetAncestor(fg, 2) == w.handle  # GA_ROOT


def _point_is_whatsapp(w, x: int, y: int) -> bool:
    hwnd = U32.WindowFromPoint(ctypes.wintypes.POINT(x, y))
    return U32.GetAncestor(hwnd, 2) == w.handle


def _safe_click(w, elem) -> None:
    import ctypes.wintypes  # noqa: F401  (POINT)
    from pywinauto import mouse

    r = elem.rectangle()
    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    _bring_to_front(w)
    if not (_is_front(w) and _point_is_whatsapp(w, x, y)):
        raise WhatsAppError("WhatsApp wasn't safely in front, so I didn't click.")
    mouse.click(coords=(x, y))


def _box_text(compose) -> str:
    try:
        return (compose.iface_value.CurrentValue or "").strip()
    except Exception:
        return ""


def _keys(*vks: int) -> None:
    """Press a key combo (e.g. Ctrl+V). Callers verify WhatsApp is in front first."""
    for vk in vks:
        U32.keybd_event(vk, 0, 0, 0)
    for vk in reversed(vks):
        U32.keybd_event(vk, 0, 2, 0)


def _paste_into(w, compose, text: str) -> None:
    """WhatsApp's message box is a rich editor that ignores text set directly, so: verified click into it,
    paste, and put the user's clipboard back."""
    import win32clipboard as cb

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
        _safe_click(w, compose)
        if not _is_front(w):
            raise WhatsAppError("WhatsApp lost focus, so I stopped typing.")
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


def _clear(w, compose) -> None:
    if not _box_text(compose):
        return
    _safe_click(w, compose)
    if _is_front(w):
        _keys(0x11, 0x41)  # Ctrl+A
        _keys(0x2E)        # Delete


def _search_box(w):
    boxes = _bfs(w, lambda e: e.element_info.control_type == "Edit" and
                 (e.element_info.automation_id == "_r_f_" or _name(e).startswith("Search or start")))
    if not boxes:
        raise WhatsAppError("I couldn't find WhatsApp's search box.")
    return boxes[0]


def _compose_box(w):
    edits = _bfs(w, lambda e: e.element_info.control_type == "Edit" and "type a message" in _name(e).lower())
    return edits[0] if edits else None


def chat_name(row_text: str) -> str:
    """'Basit Ahmed 12:45 See you' / 'Ali Yesterday ok' / 'Meta AI 03/10/2026 ...' -> the chat's name."""
    return re.split(r"\s+\d{1,2}[/:.]\d{1,2}|\s+(?:yesterday|today|monday|tuesday|wednesday|thursday|friday|"
                    r"saturday|sunday)\b", row_text, maxsplit=1, flags=re.I)[0].strip()


def _chat_rows(w, contact: str) -> list[tuple[int, str, object]]:
    """Search results as (score, chat name, row), best first. A row's name is 'Chat name date last message'."""
    rows = _bfs(w, lambda e: e.element_info.control_type == "DataItem" and _name(e)
                and e.element_info.element.GetCurrentPropertyValue(_uia().UIA_IsSelectionItemPatternAvailablePropertyId))
    out, seen = [], set()
    for r in rows:
        chat = chat_name(_name(r))
        if not chat or chat in seen:
            continue
        seen.add(chat)
        score = max(fuzz.WRatio(contact.lower(), chat.lower()), 100 if chat.lower().startswith(contact.lower()) else 0)
        out.append((score, chat, r))
    return sorted(out, key=lambda x: -x[0])


def open_chat_and_type(contact: str, message: str) -> str:
    """Opens the best-matching chat and puts the message in the box (not sent). Returns the chat name."""
    w = _open_window()
    box = _search_box(w)
    box.iface_value.SetValue(contact)
    time.sleep(1.8)
    rows = _chat_rows(w, contact)
    if not rows or rows[0][0] < 70:
        box.iface_value.SetValue("")
        raise WhatsAppError(f"I couldn't find {contact} in your WhatsApp chats.")
    score, chat, row = rows[0]
    _safe_click(w, row)
    compose = None
    for _ in range(15):
        time.sleep(0.3)
        compose = _compose_box(w)
        if compose is not None:
            break
    if compose is None:
        raise WhatsAppError(f"I opened {chat}, but couldn't find the message box.")
    _paste_into(w, compose, message)
    if _box_text(compose) != message.strip():
        _clear(w, compose)
        raise WhatsAppError("WhatsApp didn't accept the text, so I cleared it.")
    log.info("whatsapp: chat %r (match %d) has the message ready", chat, score)
    return chat


def send_ready_message(expected: str) -> None:
    """Send what's in the message box, only if it is exactly the text that was approved."""
    w = _window()
    compose = _compose_box(w) if w else None
    if compose is None:
        raise WhatsAppError("The WhatsApp chat isn't open any more, so nothing was sent.")
    if _box_text(compose) != expected.strip():
        raise WhatsAppError("The message box no longer matches what you approved, so I didn't send it.")
    sends = _bfs(w, lambda e: e.element_info.control_type == "Button" and _name(e).lower() == "send")
    if sends:
        _safe_click(w, sends[0])
    else:  # no visible Send button: Enter in the message box, once WhatsApp is verified in front
        _bring_to_front(w)
        if not _is_front(w):
            raise WhatsAppError("WhatsApp wasn't in front, so nothing was sent.")
        compose.set_focus()
        U32.keybd_event(0x0D, 0, 0, 0)
        U32.keybd_event(0x0D, 0, 2, 0)
    time.sleep(1.0)
    if _box_text(compose):
        raise WhatsAppError("WhatsApp didn't confirm it sent; please check the chat.")
    log.info("whatsapp: message sent")


def clear_ready_message() -> None:
    w = _window()
    compose = _compose_box(w) if w else None
    if compose is not None:
        _clear(w, compose)


@tool("Prepare a WhatsApp message to a contact (shown and read back; sent only after the user confirms)",
      contact="Contact or chat name as it appears in WhatsApp", message="The message text")
async def whatsapp_message(contact: str, message: str) -> Result:
    import asyncio

    try:
        chat = await asyncio.wait_for(asyncio.to_thread(open_chat_and_type, contact, message), timeout=40)
    except WhatsAppError as e:
        return Result(str(e), ok=False, icon="whatsapp")
    except Exception as e:
        log.exception("whatsapp failed")
        return Result(f"WhatsApp didn't cooperate: {str(e)[:80]}.", ok=False, icon="whatsapp")
    return Result(f"I've opened your chat with {chat} and typed: {message}. Shall I send it?", icon="whatsapp",
                  card={"kind": "whatsapp_draft", "id": f"wa-{int(time.time())}", "chat": chat, "text": message},
                  pending={"kind": "wa_send", "chat": chat, "text": message})
