"""Open known folders and find files through the Windows Search index."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from rapidfuzz import fuzz

from ..config import WINDOWS
from . import CTX

if WINDOWS:
    _startfile = os.startfile
else:
    from ..linux import open_path as _startfile
from .base import Result, tool

log = logging.getLogger(__name__)
HOME = Path.home()

KIND_EXT = {
    "pdf": [".pdf"],
    "excel": [".xlsx", ".xls", ".xlsm", ".csv"], "spreadsheet": [".xlsx", ".xls", ".xlsm", ".csv"],
    "sheet": [".xlsx", ".xls", ".csv"], "csv": [".csv"],
    "word": [".docx", ".doc"], "document": [".docx", ".doc", ".pdf"], "doc": [".docx", ".doc"],
    "presentation": [".pptx", ".ppt"], "powerpoint": [".pptx", ".ppt"], "ppt": [".pptx", ".ppt"],
    "slides": [".pptx", ".ppt"], "deck": [".pptx", ".ppt", ".pdf"],
    "image": [".png", ".jpg", ".jpeg"], "photo": [".png", ".jpg", ".jpeg", ".heic"],
    "picture": [".png", ".jpg", ".jpeg"], "screenshot": [".png", ".jpg"],
    "video": [".mp4", ".mov", ".mkv"], "zip": [".zip", ".rar", ".7z"], "text": [".txt", ".md"],
}


def _onedrive() -> Path | None:
    for var in ("OneDriveCommercial", "OneDrive"):
        if os.environ.get(var):
            return Path(os.environ[var])
    return None


def known_folders() -> dict[str, Path]:
    od = _onedrive()
    out = {
        "desktop": HOME / "Desktop", "documents": HOME / "Documents", "downloads": HOME / "Downloads",
        "pictures": HOME / "Pictures", "music": HOME / "Music", "videos": HOME / "Videos",
        "home": HOME, "user": HOME,
    }
    if od:
        out["onedrive"] = od
        for k in ("desktop", "documents", "pictures"):  # OneDrive folder backup moves these
            if (od / k.title()).exists() and not out[k].exists():
                out[k] = od / k.title()
    return out


def match_folder(name: str) -> Path | None:
    n = re.sub(r"\b(my|the|folder|directory)\b", "", name.lower()).strip()
    n = {"download": "downloads", "document": "documents", "picture": "pictures", "photos": "pictures",
         "video": "videos", "one drive": "onedrive"}.get(n, n)
    return known_folders().get(n)


def _search_index(words: list[str], exts: list[str], roots: list[Path], limit: int = 25) -> list[tuple[str, float]]:
    if not WINDOWS:  # no Windows Search index: find_files scans the folders instead
        return []
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    try:
        clean = [re.sub(r"[^\w\-]", "", w) for w in words]
        clean = [w for w in clean if w]
        conds = [f"System.FileName LIKE '%{w}%'" for w in clean]
        if exts:
            conds.append("(" + " OR ".join(f"System.FileExtension = '{e}'" for e in exts) + ")")
        scope = " OR ".join(f"SCOPE='file:{r.as_posix()}'" for r in roots)
        sql = (f"SELECT TOP {limit} System.ItemPathDisplay, System.DateModified FROM SYSTEMINDEX "
               f"WHERE ({scope}) AND System.ItemType <> 'Directory' AND "
               + " AND ".join(conds) + " ORDER BY System.DateModified DESC")
        rs, _ = conn.Execute(sql)
        out = []
        while not rs.EOF:
            path = rs.Fields.Item("System.ItemPathDisplay").Value
            mod = rs.Fields.Item("System.DateModified").Value
            ts = mod.timestamp() if hasattr(mod, "timestamp") else time.time()
            out.append((path, ts))
            rs.MoveNext()
        return out
    finally:
        conn.Close()


def _search_walk(words: list[str], exts: list[str], roots: list[Path], limit: int = 25) -> list[tuple[str, float]]:
    out = []
    skip = {"appdata", "node_modules", ".git", ".venv", "venv", "$recycle.bin", "__pycache__"}
    deadline = time.monotonic() + 4
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip and not d.startswith(".")]
            for f in filenames:
                fl = f.lower()
                if all(w in fl for w in words) and (not exts or os.path.splitext(fl)[1] in exts):
                    p = os.path.join(dirpath, f)
                    try:
                        out.append((p, os.path.getmtime(p)))
                    except OSError:
                        pass
            if time.monotonic() > deadline or len(out) > limit * 4:
                break
    return sorted(out, key=lambda x: -x[1])[:limit]


def find_files(query: str, kind: str | None = None) -> list[str]:
    words = [w for w in re.split(r"[\s_\-]+", query.lower()) if w and w not in {"the", "my", "a", "file"}]
    exts = KIND_EXT.get((kind or "").lower(), [])
    roots = [Path(os.path.expanduser(r)) for r in CTX.cfg.get("files", {}).get("search_roots", ["~"])]
    if not words:
        return []
    try:
        hits = _search_index(words, exts, roots)
    except Exception as e:
        log.warning("Windows Search failed (%s); scanning folders", e)
        hits = []
    if not hits:
        hits = _search_walk(words, exts, roots)
    now = time.time()

    def score(item: tuple[str, float]) -> float:
        name = os.path.splitext(os.path.basename(item[0]))[0].lower()
        age_days = max(0.0, (now - item[1]) / 86400)
        return fuzz.token_set_ratio(" ".join(words), name) + max(0, 15 - age_days / 10)

    ranked = sorted(dict(hits).items(), key=score, reverse=True)
    return [p for p, _ in ranked[:5]]


@tool("Open a common folder: desktop, documents, downloads, pictures, music, videos, onedrive",
      name="Folder name")
async def open_folder(name: str) -> Result:
    path = match_folder(name)
    if path is None or not path.exists():
        return await open_file(name)
    _startfile(str(path))
    return Result(f"Opening {path.name}.", icon="folder")


@tool("Find and open a file on this computer by (part of) its name",
      query="Words from the file name", kind="Optional: pdf, excel, word, presentation, image, video")
async def open_file(query: str, kind: str = "") -> Result:
    paths = await asyncio.to_thread(find_files, query, kind or None)
    if not paths:
        return Result(f"I couldn't find a file matching {query}.", ok=False, icon="folder")
    if len(paths) == 1 or _clear_winner(query, paths):
        _startfile(paths[0])
        return Result(f"Opening {Path(paths[0]).stem}.", icon="folder")
    top = paths[:3]
    names = [Path(p).name for p in top]
    folders = [_short_folder(p) for p in top]
    same_name = len({Path(p).stem.lower() for p in top}) < len(top)
    # when the names match, the folder is what tells them apart
    spoken = ", ".join(f"{i}: {Path(p).stem}" + (f" in {Path(p).parent.name}" if same_name else "")
                       for i, p in enumerate(top, 1))
    return Result(f"I found {len(top)}. {spoken}. Which one?", icon="folder",
                  card={"kind": "choices", "title": "Which file?", "items": names, "subs": folders},
                  pending={"kind": "choose_file", "paths": top})


def _short_folder(path: str) -> str:
    parent = Path(path).parent
    try:
        return "~" + os.sep + str(parent.relative_to(HOME))
    except ValueError:
        return str(parent)


def _clear_winner(query: str, paths: list[str]) -> bool:
    s = [fuzz.token_set_ratio(query.lower(), Path(p).stem.lower()) for p in paths[:2]]
    return s[0] >= 90 and s[0] - s[1] >= 15


def open_path(path: str) -> Result:
    _startfile(path)
    return Result(f"Opening {Path(path).stem}.", icon="folder")
