"""Bring what Jarvis just opened to the front of the screen.

Windows only lets the program you last typed into decide what's in front, so a browser or folder opened by
Jarvis often lands behind your current window ("the YouTube you opened in background", 17 Nov). After opening
something, Jarvis looks for its window by title and pulls it forward.
"""
from __future__ import annotations

import ctypes
import logging
import re
import time

log = logging.getLogger(__name__)
U32 = ctypes.windll.user32

_BROWSERS = re.compile(r"(?:google chrome|microsoft.? edge|mozilla firefox|brave|opera)\s*$", re.I)


def _windows() -> list[tuple[int, str]]:
    import win32gui

    out: list[tuple[int, str]] = []

    def each(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                out.append((hwnd, title))
        return True

    win32gui.EnumWindows(each, None)
    return out  # top of the screen first


def find_window(words: list[str], browser: bool = False) -> int | None:
    """The topmost visible window whose title has one of `words` (for a site: a browser window)."""
    words = [w.lower() for w in words if w and len(w) >= 3]
    for hwnd, title in _windows():
        t = title.lower()
        if browser and not _BROWSERS.search(title):
            continue
        if any(w in t for w in words):
            return hwnd
    return None


def bring_to_front(hwnd: int) -> bool:
    if U32.GetForegroundWindow() == hwnd:
        return True
    if U32.IsIconic(hwnd):
        U32.ShowWindow(hwnd, 9)  # SW_RESTORE
    k32 = ctypes.windll.kernel32
    for i in range(3):
        U32.keybd_event(0x12, 0, 0, 0)   # a tap of Alt: Windows then allows the foreground to change
        U32.keybd_event(0x12, 0, 2, 0)
        if i == 0:
            U32.SetForegroundWindow(hwnd)
        else:
            fg_thread = U32.GetWindowThreadProcessId(U32.GetForegroundWindow(), None)
            me = k32.GetCurrentThreadId()
            attached = fg_thread and fg_thread != me and U32.AttachThreadInput(me, fg_thread, True)
            try:
                U32.ShowWindow(hwnd, 5)
                U32.BringWindowToTop(hwnd)
                U32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    U32.AttachThreadInput(me, fg_thread, False)
        time.sleep(0.2)
        if U32.GetForegroundWindow() == hwnd:
            return True
    return False


def surface(words: list[str], browser: bool = False, timeout: float = 6.0) -> bool:
    """Wait for the window (a page takes a moment to get its title) and bring it forward."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            hwnd = find_window(words, browser)
            if hwnd:
                ok = bring_to_front(hwnd)
                log.info("brought to front: %s (%s)", words, "ok" if ok else "refused")
                return ok
        except Exception as e:
            log.debug("bring to front: %s", e)
        time.sleep(0.4)
    log.info("bring to front: no window titled %s", words)
    return False


def words_for(tool: str, args: dict) -> tuple[list[str], bool] | None:
    """What the window opened by this tool is called, and whether it's a browser tab."""
    if tool == "open_website":
        site = re.sub(r"^https?://(?:www\.)?|\.(?:com|org|net|io|ai|dev|pk|co|uk)\b.*$", "", str(args.get("site", "")))
        return [site.strip()], True
    if tool in ("play_youtube",):
        return ["youtube"], True
    if tool == "web_search":
        return [str(args.get("query", ""))[:40], "google search"], True
    if tool in ("open_folder",):
        return [str(args.get("name", ""))], False
    if tool in ("open_app",):
        return [str(args.get("name", "")), str(args.get("name", "")).replace(" ", "")], False
    if tool in ("play_spotify", "play_spotify_liked"):
        return ["spotify"], False
    return None
