"""Play a song in the Spotify desktop app. Works on the Free plan, no developer account.

Opens Spotify's search for the query, then presses the top result's Play button through
Windows UI Automation (Spotify is a Chromium app, so its buttons carry accessible names).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from ..config import WINDOWS
from .base import Result, tool

log = logging.getLogger(__name__)
SPOTIFY_EXE = Path(os.environ.get("APPDATA", "")) / "Spotify" / "Spotify.exe"


def _spotify_pids() -> list[int]:
    import psutil

    return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "spotify.exe"]


def _main_window():
    from pywinauto import Desktop

    pids = set(_spotify_pids())
    for w in Desktop(backend="uia").windows(class_name="Chrome_WidgetWin_1"):
        try:
            if w.process_id() in pids and w.window_text():
                return w
        except Exception:
            continue
    return None


def now_playing() -> str | None:
    """Spotify's window title is 'Artist - Song' while playing, 'Spotify ...' when paused."""
    if not WINDOWS:
        return None
    w = _main_window()
    if w is None:
        return None
    title = w.window_text()
    return None if title.lower().startswith("spotify") else title


def _find_play_button(win, query: str):
    """Search-page buttons are named 'Play <title>' in page order (top result first).
    Pick the earliest one whose title best matches what was asked for."""
    from rapidfuzz import fuzz

    q = clean_query(query).lower()
    best, best_score = None, -1.0
    candidates = [b for b in win.descendants(control_type="Button")
                  if (b.window_text() or "").strip().lower().startswith("play ")
                  and (b.window_text() or "").strip().lower() != "play next"]
    # an exact title among the first few results beats Spotify's "top result" ("Dilruba" over "Dilruba Na Raazi")
    for b in candidates[:6]:
        if re.sub(r"\s*\(.*?\)|\s+by .*$", "", b.window_text()[5:]).strip().lower() == q:
            return b
    for b in candidates:
        name = b.window_text().strip()
        score = fuzz.token_set_ratio(q, name[5:].lower())
        if score > best_score + 5:  # earlier buttons win ties
            best, best_score = b, score
    # a weak match usually means the previous page is still on screen; keep polling
    return best if best_score >= 60 else None


def _ensure_running() -> None:
    if _spotify_pids():
        return
    if not SPOTIFY_EXE.exists():
        raise RuntimeError("Spotify is not installed")
    os.startfile(str(SPOTIFY_EXE))
    for _ in range(30):
        time.sleep(0.5)
        if _main_window() is not None:
            break
    time.sleep(2.0)


def _open_and_press(uri: str, pick) -> str | None:
    """Open a spotify: URI, wait for the page, press the button `pick(window)` returns.
    Returns the now-playing title, "" if playing but unknown, None if no button was found."""
    if not WINDOWS:  # pressing Play needs Windows UI Automation: open the page and let the user press it
        from ..linux import open_spotify

        open_spotify(uri)
        return None
    import pythoncom

    pythoncom.CoInitialize()  # UI Automation is COM; this runs on a worker thread
    _ensure_running()
    before = now_playing()
    os.startfile(uri)
    deadline = time.monotonic() + 12
    focused = False
    while time.monotonic() < deadline:
        time.sleep(0.8)
        win = _main_window()
        if win is None:
            continue
        if not focused:  # a hidden/covered Chromium window doesn't render its search results
            focused = True
            try:
                if win.is_minimized():
                    win.restore()
                win.set_focus()
            except Exception as e:
                log.debug("spotify focus: %s", e)
        btn = pick(win)
        if btn is None:
            continue
        log.info("spotify: pressing %r", btn.window_text())
        try:
            btn.invoke()
        except Exception:
            btn.click_input()
        for _ in range(10):
            time.sleep(0.4)
            title = now_playing()
            if title and title != before:
                return title
        return now_playing() or ""
    return None


def _play(query: str) -> str | None:
    return _open_and_press(f"spotify:search:{quote(clean_query(query))}", lambda w: _find_play_button(w, query))


def _pick_liked(win, first_track: bool):
    buttons = [b for b in win.descendants(control_type="Button")
               if (b.window_text() or "").startswith("Play ")]
    if first_track:  # rows are named "Play <title> by <artist>", top row first
        return next((b for b in buttons if " by " in b.window_text()), None)
    return next((b for b in buttons if b.window_text() == "Play Liked Songs"), None)


def _spoken_title(title: str) -> tuple[str, dict | None]:
    artist, _, song = title.partition(" - ")
    spoken = f"{song} by {artist}" if song else title
    return spoken, {"kind": "media", "service": "spotify", "title": song or title, "subtitle": artist if song else ""}


@tool("Play the user's Liked Songs on Spotify, from the top or just the first song",
      which="'all' (default) or 'first' for only the top song")
async def play_spotify_liked(which: str = "all") -> Result:
    first = which.lower() in ("first", "top", "1", "one")
    try:
        title = await asyncio.wait_for(asyncio.to_thread(
            _open_and_press, "spotify:collection:tracks", lambda w: _pick_liked(w, first)), timeout=40)
    except Exception as e:
        log.warning("spotify liked failed: %s", e)
        title = None
    if title is None:
        return Result("I opened your Liked Songs, but couldn't start playback. Press play there.", ok=False, icon="spotify")
    if not title:
        return Result("Playing your Liked Songs.", icon="spotify")
    spoken, card = _spoken_title(title)
    return Result(f"Playing your Liked Songs, starting with {spoken}.", icon="spotify", card=card)


@tool("Play a song, artist or playlist on Spotify", query="What to play, e.g. 'Blinding Lights'")
async def play_spotify(query: str) -> Result:
    try:
        title = await asyncio.wait_for(asyncio.to_thread(_play, query), timeout=40)
    except Exception as e:
        log.warning("spotify failed: %r", e)
        title = None
    log.info("spotify result for %r: %r", query, title)
    if title is None:
        return Result(f"I opened Spotify's search for {query}, but couldn't start it. Press play on the top result.",
                      ok=False, icon="spotify")
    if not title:
        return Result(f"Playing {query} on Spotify.", icon="spotify")
    spoken, card = _spoken_title(title)
    return Result(f"Playing {spoken}.", icon="spotify", card=card)


def clean_query(q: str) -> str:
    return re.sub(r"\b(the )?(song|track|music)\b", "", q).strip() or q
