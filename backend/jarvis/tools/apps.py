"""Open and close installed applications (Win32 and Store apps)."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time

from rapidfuzz import fuzz, process

from ..config import DATA
from . import CTX
from .base import Result, tool

log = logging.getLogger(__name__)
CACHE = DATA / "apps.json"
_JUNK = re.compile(r"uninstall|readme|help|documentation|release notes|license|website|manual|"
                   r"what's new|support|feedback|recovery|debug|\(x86\)|setup|repair", re.I)
NO_WINDOW = 0x08000000


class AppIndex:
    def __init__(self) -> None:
        self.apps: dict[str, str] = {}  # display name -> AppID
        if CACHE.exists():
            try:
                self.apps = json.loads(CACHE.read_text(encoding="utf-8"))
            except Exception:
                pass

    def refresh(self) -> None:
        t = time.perf_counter()
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"],
            capture_output=True, text=True, encoding="utf-8", creationflags=NO_WINDOW, timeout=30)
        items = json.loads(out.stdout or "[]")
        if isinstance(items, dict):
            items = [items]
        apps = {i["Name"]: i["AppID"] for i in items if i.get("Name") and not _JUNK.search(i["Name"])}
        if apps:
            self.apps = apps
            CACHE.write_text(json.dumps(apps, indent=1), encoding="utf-8")
        log.info("app index: %d apps (%.1fs)", len(self.apps), time.perf_counter() - t)

    def names(self) -> list[str]:
        return list(self.apps)

    def find(self, query: str) -> tuple[str, str] | None:
        q = query.lower().strip()
        if len(q) < 3 or q in {"it", "this", "that", "them", "one", "the", "app", "song", "music", "file"}:
            return None  # 6 Nov: "start it" fuzzy-matched "Antigravity IDE"
        aliases = {k.lower(): v for k, v in CTX.cfg.get("apps", {}).get("aliases", {}).items()}
        if q in aliases:
            target = aliases[q]
            for name in self.apps:
                if name.lower() == target.lower():
                    return name, self.apps[name]
            q = target.lower()
        for name in self.apps:  # exact match first
            if name.lower() == q:
                return name, self.apps[name]
        names = list(self.apps)
        hit = process.extractOne(q, names, scorer=fuzz.WRatio, processor=str.lower, score_cutoff=82)
        if hit is None:
            return None
        # WRatio rewards short partial hits ("note" -> "OneNote"); require the spoken words to be present
        name = hit[0]
        if fuzz.partial_ratio(q, name.lower()) < 80:
            return None
        return name, self.apps[name]

    @staticmethod
    def launch(app_id: str) -> None:
        if re.match(r"^[a-zA-Z]:\\", app_id):
            os.startfile(app_id)
        else:  # Store app IDs, AUMIDs and {KnownFolder}\x.exe paths all launch through AppsFolder
            subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=NO_WINDOW)


@tool("Open an installed application by name, e.g. Excel, Chrome, VS Code, Spotify, Teams",
      name="Application name as the user said it")
async def open_app(name: str) -> Result:
    hit = CTX.apps.find(name)
    if hit is None:
        return Result(f"I couldn't find an app called {name}.", ok=False, icon="app")
    display, app_id = hit
    await asyncio.to_thread(AppIndex.launch, app_id)
    return Result(f"Opening {display}.", icon="app")


def _close_windows(name: str) -> list[str]:
    import psutil
    import win32con
    import win32gui
    import win32process

    q = name.lower().replace(" ", "")
    hit = CTX.apps.find(name) if CTX.apps else None
    targets = {q}
    if hit:
        targets.add(hit[0].lower().replace(" ", ""))
        if hit[1].lower().endswith(".exe"):
            targets.add(os.path.basename(hit[1]).lower()[:-4])
    exe_alias = {"excel": "excel", "word": "winword", "powerpoint": "powerpnt", "vscode": "code",
                 "visualstudiocode": "code", "teams": "ms-teams", "microsoftteams": "ms-teams",
                 "googlechrome": "chrome", "microsoftedge": "msedge", "edge": "msedge", "fileexplorer": "explorer"}
    targets |= {exe_alias[t] for t in list(targets) if t in exe_alias}
    closed: set[str] = set()

    def visit(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if not win32gui.IsWindowVisible(hwnd) or not title:
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            exe = psutil.Process(pid).name().lower().removesuffix(".exe")
        except Exception:
            return True
        if exe in {"python", "pythonw", "electron", "jarvis"}:
            return True
        if exe == "explorer" and "explorer" not in targets:
            return True
        # Store apps (Calculator, Settings...) live inside ApplicationFrameHost: match on the title instead
        key = title.lower().replace(" ", "") if exe == "applicationframehost" else exe
        # "WhatsApp.Root.exe": match on the part before the dot too, or on a window titled exactly "WhatsApp"
        keys = {key, key.split(".")[0], title.lower().replace(" ", "")} if exe != "explorer" else {key}
        if keys & targets or any(t and fuzz.ratio(t, key) >= 85 for t in targets):
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)  # lets the app ask "save changes?"
            closed.add(exe)
        return True

    win32gui.EnumWindows(visit, None)
    log.info("close %r: targets=%s closed=%s", name, sorted(targets), sorted(closed))
    return sorted(closed)


_FOLDER_WORDS = re.compile(r"\b(?:the|my|this|that|these|those|all|open|folders?|windows?|file explorer|explorer)\b")


def folder_query(name: str) -> str:
    """'the Design folder' -> 'design'; 'the folder' -> '' (the last one opened)."""
    return re.sub(r"\s+", " ", _FOLDER_WORDS.sub(" ", name.lower())).strip()


def pick_folders(name: str, open_folders: list[str]) -> list[int]:
    """Which open folder windows (by folder name, oldest first) "close <name>" means."""
    q = folder_query(name)
    if not q:
        if not open_folders:
            return []
        return list(range(len(open_folders))) if re.search(r"\ball\b|\bfolders\b", name.lower()) \
            else [len(open_folders) - 1]
    hits = [i for i, f in enumerate(open_folders)
            if f.lower() == q or fuzz.ratio(q, f.lower()) >= 80 or (len(q) >= 4 and q in f.lower())]
    return hits


def _close_folders(name: str) -> list[str]:
    """Close File Explorer windows showing the folder you named (5 Nov: "close Design folder" did nothing,
    because Explorer windows were never matched)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        windows = []
        for w in win32com.client.Dispatch("Shell.Application").Windows():
            try:
                if str(w.FullName).lower().endswith("explorer.exe"):
                    windows.append((w, str(w.LocationName)))
            except Exception:
                continue
        closed = []
        for i in pick_folders(name, [loc for _, loc in windows]):
            w, loc = windows[i]
            try:
                w.Quit()
                closed.append(loc)
            except Exception:
                log.warning("could not close folder window %s", loc)
        log.info("close folder %r: open=%s closed=%s", name, [loc for _, loc in windows], closed)
        return closed
    finally:
        pythoncom.CoUninitialize()


def _folders_said(closed: list[str]) -> str:
    return closed[0] if len(closed) == 1 else f"{len(closed)} folders"


@tool("Close an open application window or folder window by name (the app may ask to save work)",
      name="Application or folder name")
async def close_app(name: str) -> Result:
    if re.search(r"\bfolders?\b|\bexplorer\b", name.lower()):
        closed = await asyncio.to_thread(_close_folders, name)
        if closed:
            return Result(f"Closed {_folders_said(closed)}.", icon="folder")
        q = folder_query(name)
        return Result(f"I don't see the {q} folder open." if q else "I don't see a folder open.", ok=False,
                      icon="folder")
    closed = await asyncio.to_thread(_close_windows, name)
    if not closed:
        folders = await asyncio.to_thread(_close_folders, name)  # "close Solvix": an open folder of that name
        if folders:
            return Result(f"Closed {_folders_said(folders)}.", icon="folder")
        return Result(f"I don't see {name} open.", ok=False, icon="app")
    return Result(f"Closing {name}.", icon="app")
