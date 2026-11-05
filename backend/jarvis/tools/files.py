"""Open known folders and find files through the Windows Search index."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from rapidfuzz import fuzz

from . import CTX
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


# app data, build output and code-library folders: full of files nobody means when they ask for one
_JUNK = re.compile(r"[\\/](?:appdata|node_modules|\.git|\.venv|venv|env|site-packages|__pycache__|\$recycle\.bin|"
                   r"\.next|\.nuxt|dist|build|\.cache|\.npm|\.nuget|\.vscode|\.idea|\.claude|\.gradle|target|"
                   r"bower_components|vendor|\.turbo|coverage)(?:[\\/]|$)", re.I)
_KNOWN_EXTS = {e for exts in KIND_EXT.values() for e in exts} | {
    ".rar", ".7z", ".exe", ".msi", ".iso", ".mp3", ".wav", ".gif", ".json", ".html", ".htm", ".psd", ".py", ".js",
    ".md", ".txt", ".apk", ".svg", ".webp", ".ai", ".log", ".xml", ".ini", ".bat", ".ps1"}
_FILLER_WORDS = {"the", "my", "a", "an", "file", "files", "folder", "called", "named", "of", "this", "that"}


def query_terms(query: str) -> tuple[list[str], list[str]]:
    """"Flyer Data.rar" -> (["flyer", "data"], [".rar"]): an extension said with the name narrows the type
    instead of being searched as text (it once searched "datarar" and found nothing)."""
    q = query.lower().strip()
    exts = []
    for m in re.finditer(r"\.([a-z0-9]{1,4})\b", q):
        if "." + m.group(1) in _KNOWN_EXTS:
            exts.append("." + m.group(1))
    q = re.sub(r"\.(" + "|".join(e[1:] for e in exts) + r")\b", " ", q) if exts else q
    words = [w for w in re.split(r"[\s_\-.,]+", q) if w and w not in _FILLER_WORDS]
    return words, exts


def _roots(where: str = "") -> list[Path]:
    if where:
        p = match_folder(where)
        if p is not None and p.exists():
            return [p]
    return [Path(os.path.expanduser(r)) for r in CTX.cfg.get("files", {}).get("search_roots", ["~"])]


def _search_index(words: list[str], exts: list[str], roots: list[Path], folders: bool | None,
                  limit: int = 200) -> list[tuple[str, float]]:
    """folders: True = folders only, False = files only, None = both."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    conn = win32com.client.Dispatch("ADODB.Connection")
    conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
    try:
        clean = [re.sub(r"[^\w]", "", w).replace("'", "") for w in words]
        conds = [f"System.FileName LIKE '%{w}%'" for w in clean if w]
        if exts:
            conds.append("(" + " OR ".join(f"System.FileExtension = '{e}'" for e in exts) + ")")
        if folders is True:
            conds.append("System.ItemType = 'Directory'")
        elif folders is False:
            conds.append("System.ItemType <> 'Directory'")
        scope = " OR ".join(f"SCOPE='file:{r.as_posix()}'" for r in roots)
        sql = (f"SELECT TOP {limit} System.ItemPathDisplay, System.DateModified FROM SYSTEMINDEX "
               f"WHERE ({scope}) AND " + " AND ".join(conds) + " ORDER BY System.DateModified DESC")
        rs, _ = conn.Execute(sql)
        out = []
        while not rs.EOF:
            path = rs.Fields.Item("System.ItemPathDisplay").Value
            mod = rs.Fields.Item("System.DateModified").Value
            ts = mod.timestamp() if hasattr(mod, "timestamp") else time.time()
            if not _JUNK.search(path):
                out.append((path, ts))
            rs.MoveNext()
        return out
    finally:
        conn.Close()


def _names_match(words: list[str], exts: list[str], name: str, is_dir: bool, folders: bool | None) -> bool:
    if (folders is True and not is_dir) or (folders is False and is_dir):
        return False
    stem, ext = (name.lower(), "") if is_dir else os.path.splitext(name.lower())
    if exts and ext not in exts:
        return False
    return all(w in stem for w in words)


def _search_walk(words: list[str], exts: list[str], roots: list[Path], folders: bool | None,
                 fuzzy: bool = False, seconds: float = 4.0, max_depth: int = 99) -> list[tuple[str, float]]:
    """Scan the folders directly (no index, or nothing indexed). fuzzy: names that sound alike count too,
    for misheard words ("partify" for "party")."""
    out = []
    deadline = time.monotonic() + seconds
    target = " ".join(words)
    for root in roots:
        base = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            depth = len(Path(dirpath).parts) - base
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and not _JUNK.search("\\" + d + "\\")
                           and depth < max_depth]
            for name, is_dir in [(d, True) for d in dirnames] + [(f, False) for f in filenames]:
                ok = _names_match(words, exts, name, is_dir, folders)
                if not ok and fuzzy and target and not ((folders is True and not is_dir) or (folders is False and is_dir)):
                    stem = name.lower() if is_dir else os.path.splitext(name.lower())[0]
                    ok = (not exts or os.path.splitext(name.lower())[1] in exts) and \
                        fuzz.token_set_ratio(target, re.sub(r"[_\-.]+", " ", stem)) >= 84
                if ok:
                    p = os.path.join(dirpath, name)
                    try:
                        out.append((p, os.path.getmtime(p)))
                    except OSError:
                        pass
            if time.monotonic() > deadline or len(out) > 200:
                break
    return out


def find_files(query: str, kind: str | None = None, where: str = "") -> list[str]:
    """Best matches first (at most five): files and folders whose names contain the words."""
    words, exts = query_terms(query)
    kind = (kind or "").lower()
    folders = True if kind == "folder" else (False if kind and kind in KIND_EXT or exts else None)
    exts = exts or KIND_EXT.get(kind, [])
    roots = _roots(where)
    if not words:
        return []
    try:
        hits = _search_index(words, exts, roots, folders)
    except Exception as e:
        log.warning("Windows Search failed (%s); scanning folders", e)
        hits = []
    # The index skips some folders (Desktop\Solvix wasn't in it), so always look at the top of the usual
    # places too: that's where most things people ask for by name live
    near = roots if where else [p for k, p in known_folders().items()
                                if k in ("desktop", "downloads", "documents", "pictures", "videos", "music")]
    hits += _search_walk(words, exts, near, folders, seconds=1.0, max_depth=1)
    if not hits:
        hits = _search_walk(words, exts, roots, folders, seconds=2.5)
    if not hits:  # a misheard name: look for one that sounds alike in the usual places
        hits = _search_walk(words, exts, near, folders, fuzzy=True, seconds=2.0, max_depth=3)
    now = time.time()
    target = " ".join(words)

    def score(item: tuple[str, float]) -> float:
        path = item[0]
        is_dir = os.path.isdir(path)
        name = os.path.basename(path).lower()
        stem = name if is_dir else os.path.splitext(name)[0]
        stem = re.sub(r"[_\-.]+", " ", stem).strip()
        age_days = max(0.0, (now - item[1]) / 86400)
        depth = len(Path(path).parts) - len(Path.home().parts)
        s = fuzz.token_set_ratio(target, stem) + fuzz.ratio(target, stem) / 4
        s += 25 if stem == target else 0                      # exactly the name you said
        s += max(0, 10 - age_days / 15)                       # recent beats old
        s -= 3 * max(0, depth - 2)                            # Desktop\Solvix beats Desktop\x\y\z\Solvix
        s += 8 if (kind == "folder") == is_dir else 0
        return s

    ranked = sorted(dict(hits).items(), key=score, reverse=True)
    return [p for p, _ in ranked[:5]]


def where_phrase(path: str) -> str:
    """'C:\\Users\\me\\Downloads\\x.rar' -> 'in your Downloads folder'; deeper -> 'in Desktop, Solvix, docs'."""
    parent = Path(path).parent
    try:
        rel = parent.relative_to(HOME)
    except ValueError:
        return f"in {parent}"
    parts = [p for p in rel.parts if p]
    if not parts:
        return "in your user folder"
    if len(parts) == 1:
        return f"in your {parts[0]} folder"
    return "in " + ", ".join(parts[-3:]) if len(parts) > 3 else "in " + ", ".join(parts)


@tool("Open a common folder: desktop, documents, downloads, pictures, music, videos, onedrive",
      name="Folder name")
async def open_folder(name: str) -> Result:
    path = match_folder(name)
    if path is None or not path.exists():
        return await open_file(name)
    os.startfile(str(path))
    return Result(f"Opening {path.name}.", icon="folder")


async def _locate(query: str, kind: str, where: str) -> tuple[list[str], str]:
    """Search where you said; if it isn't there, everywhere (and say so)."""
    paths = await asyncio.to_thread(find_files, query, kind or None, where)
    note = ""
    if not paths and where:
        paths = await asyncio.to_thread(find_files, query, kind or None, "")
        if paths:
            note = f"It isn't in {where.title()}, but I found it. "
    return paths, note


def _label(path: str) -> str:
    p = Path(path)
    return f"the {p.name} folder" if p.is_dir() else p.name


def _not_found(query: str, kind: str, where: str) -> Result:
    what = "a folder" if kind == "folder" else "a file"
    place = f" in {where.title()}" if where else " on this laptop"
    return Result(f"I couldn't find {what} called {query}{place}. Try another word from its name.",
                  ok=False, icon="folder")


@tool("Find and open a file or folder on this computer by (part of) its name",
      query="Words from the file or folder name", kind="Optional: folder, pdf, excel, word, presentation, image, video",
      where="Optional: desktop, downloads, documents, pictures, music, videos")
async def open_file(query: str, kind: str = "", where: str = "") -> Result:
    paths, note = await _locate(query, kind, where)
    if not paths:
        return _not_found(query, kind, where)
    if len(paths) == 1 or _clear_winner(query, paths):
        os.startfile(paths[0])
        return Result(f"{note}Opening {_label(paths[0])}.", icon="folder")
    top = paths[:3]
    names = [Path(p).name for p in top]
    folders = [_short_folder(p) for p in top]
    same_name = len({Path(p).stem.lower() for p in top}) < len(top)
    # when the names match, the folder is what tells them apart
    spoken = ", ".join(f"{i}: {Path(p).stem}" + (f" in {Path(p).parent.name}" if same_name else "")
                       for i, p in enumerate(top, 1))
    return Result(f"{note}I found {len(top)}. {spoken}. Which one?", icon="folder",
                  card={"kind": "choices", "title": "Which one?", "items": names, "subs": folders},
                  pending={"kind": "choose_file", "paths": top})


@tool("Tell where a file or folder is on this computer (without opening it)",
      query="Words from the file or folder name", kind="Optional: folder, pdf, excel, word, presentation, image",
      where="Optional: desktop, downloads, documents, pictures, music, videos")
async def find_file(query: str, kind: str = "", where: str = "") -> Result:
    paths, note = await _locate(query, kind, where)
    if not paths:
        return _not_found(query, kind, where)
    top = paths[:3] if not _clear_winner(query, paths) else paths[:1]
    card = {"kind": "choices", "title": "Found", "items": [Path(p).name for p in top],
            "subs": [_short_folder(p) for p in top]}
    if len(top) == 1:
        p = top[0]
        return Result(f"{note}{Path(p).name} is {where_phrase(p)}. Shall I open it, or show it in its folder?",
                      icon="folder", card=card, pending={"kind": "choose_file", "paths": top})
    spoken = "; ".join(f"{i}: {Path(p).name}, {where_phrase(p)}" for i, p in enumerate(top, 1))
    return Result(f"{note}I found {len(top)}. {spoken}. Which one should I open?", icon="folder", card=card,
                  pending={"kind": "choose_file", "paths": top})


def reveal_path(path: str) -> Result:
    """Open the folder that holds it, with it selected."""
    import subprocess

    subprocess.Popen(["explorer", "/select,", path])
    return Result(f"Here it is, {where_phrase(path)}.", icon="folder")


def _short_folder(path: str) -> str:
    parent = Path(path).parent
    try:
        return "~\\" + str(parent.relative_to(HOME))
    except ValueError:
        return str(parent)


def _clear_winner(query: str, paths: list[str]) -> bool:
    """The first result is plainly the one you named: same name, and the next one isn't."""
    words, exts = query_terms(query)
    target = " ".join(words)

    def name(p: str) -> str:
        n = Path(p).name.lower() if Path(p).is_dir() else Path(p).stem.lower()
        return re.sub(r"[_\-.]+", " ", n).strip()
    first = name(paths[0])
    if len(paths) > 1 and first == name(paths[1]):
        return False  # two with the same name: ask which folder
    if first == target:
        return True
    s = [fuzz.token_set_ratio(target, name(p)) for p in paths[:2]]
    return s[0] >= 90 and s[0] - s[1] >= 15


def open_path(path: str) -> Result:
    os.startfile(path)
    return Result(f"Opening {_label(path)}.", icon="folder")
