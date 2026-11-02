"""Claude Code projects: what you're working on, where each one stands, and handing it the next task.

Reads Claude Code's own local session records (~/.claude/projects/*/<session>.jsonl), tail only since
they can be ~100 MB. Summaries come from the local LLM, so nothing leaves the laptop. Sending a task
opens a visible terminal running Claude Code in that project, forked from its latest session so it has
the context but never collides with a session open in the desktop app. Jarvis always reads the task
back and waits for "send it" first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process

from . import CTX
from .base import Result, tool

log = logging.getLogger(__name__)
PROJECTS = Path.home() / ".claude" / "projects"
CLAUDE_EXE = Path.home() / ".local" / "bin" / "claude.exe"
_SKIP = ("scratch-workspaces", "-AppData-", "-Temp-")


@dataclass
class Project:
    name: str
    cwd: str
    session: Path
    updated: float
    title: str = ""
    last_prompt: str = ""
    updates: list[str] = field(default_factory=list)   # Claude's most recent messages, oldest first


def _tail_records(path: Path, nbytes: int) -> list[dict]:
    size = path.stat().st_size
    with open(path, "rb") as f:
        f.seek(max(0, size - nbytes))
        chunk = f.read().decode("utf-8", "ignore")
    lines = chunk.splitlines()[1 if size > nbytes else 0:]  # first line may be cut in half
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _head_cwds(path: Path) -> list[str]:
    with open(path, "rb") as f:
        head = f.read(200_000).decode("utf-8", "ignore")
    out = []
    for line in head.splitlines():
        try:
            cwd = json.loads(line).get("cwd")
        except ValueError:
            continue
        if cwd and cwd not in out:
            out.append(cwd)
    return out


def _texts(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text")


def _read(dir_: Path, deep: bool) -> Project | None:
    sessions = sorted(dir_.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not sessions:
        return None
    s = sessions[0]
    records = _tail_records(s, 2_500_000 if deep else 400_000)
    # Claude Code names this folder after the project path (non-alphanumerics -> "-"); of the working
    # folders the session visited, the one that encodes to that name is the project itself
    seen = _head_cwds(s) + [r["cwd"] for r in records if r.get("cwd")]
    cwd = next((c for c in seen if re.sub(r"[^A-Za-z0-9]", "-", c) == dir_.name), seen[-1] if seen else "")
    if not cwd:
        return None
    p = Project(name=Path(cwd).name, cwd=cwd, session=s, updated=s.stat().st_mtime)
    for r in records:
        t = r.get("type")
        if t == "custom-title" and r.get("customTitle"):
            p.title = r["customTitle"]
        elif t == "last-prompt" and r.get("lastPrompt"):
            p.last_prompt = r["lastPrompt"]
        elif deep and t == "assistant":
            txt = _texts(r.get("message", {}).get("content")).strip()
            if len(txt) > 40:
                p.updates.append(txt)
    p.updates = p.updates[-6:]
    return p


def list_projects(limit: int = 6) -> list[Project]:
    found: dict[str, Project] = {}
    for d in PROJECTS.iterdir() if PROJECTS.exists() else []:
        if not d.is_dir() or any(k in d.name for k in _SKIP):
            continue
        try:
            p = _read(d, deep=False)
        except Exception as e:
            log.debug("skip %s: %s", d.name, e)
            continue
        if p and Path(p.cwd).exists() and (p.cwd not in found or found[p.cwd].updated < p.updated):
            found[p.cwd] = p
    return sorted(found.values(), key=lambda p: p.updated, reverse=True)[:limit]


def find_project(name: str) -> Project | None:
    projects = list_projects(12)
    if not projects:
        return None
    # name, session title and folder path, so "hackathon" finds the project living in Desktop\Hackathon\...
    labels = {f"{p.name} {p.title} {p.cwd}".lower().replace("-", " "): p for p in projects}
    hit = process.extractOne(name.lower(), list(labels), scorer=fuzz.partial_ratio, score_cutoff=70)
    return labels[hit[0]] if hit else None


def _ago(ts: float) -> str:
    m = int((time.time() - ts) / 60)
    if m < 60:
        return f"{max(m, 1)} min ago"
    if m < 60 * 24:
        return f"{m // 60} h ago"
    return f"{m // 1440} d ago"


def _spoken_name(p: Project) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", p.name)).strip()


def _speakable(text: str) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)    # [label](link) -> label
    text = re.sub(r"`[^`]*`", "", text)                       # inline code
    text = re.sub(r"https?://\S+|[A-Za-z]:\\\S+", "", text)    # links and paths
    return re.sub(r"\s+", " ", re.sub(r"[*_#>|]", "", text)).strip(" .,:;")


def _brief(p: Project) -> str:
    """Instant spoken briefing from the project's own notes. (A local-AI summary takes over a minute on
    this laptop's CPU, so it's off unless claude_code.ai_summary is true.)"""
    parts = []
    if p.title:
        parts.append(f"You're on {p.title}.")
    latest = [s for s in (_speakable(_first_sentence(u)) for u in p.updates[-2:]) if len(s) > 15]
    if latest:
        parts.append("Latest: " + ". ".join(latest) + ".")
    if p.last_prompt:
        ask = _speakable(p.last_prompt)
        if len(ask) > 140:  # cut on a word boundary
            ask = ask[:140].rsplit(" ", 1)[0] + "…"
        parts.append(f"Your last request was: {ask}.")
    return " ".join(parts) or "I couldn't find any recent notes for it."


async def _summarise(p: Project) -> str:
    deep = await asyncio.to_thread(_read, p.session.parent, True)
    p.updates, p.title, p.last_prompt = deep.updates, deep.title or p.title, deep.last_prompt or p.last_prompt
    if not CTX.cfg.get("claude_code", {}).get("ai_summary", False):
        return _brief(p)
    context = (f"Session title: {p.title}\nThe user's last request: {p.last_prompt}\n\n"
               "Claude Code's latest messages, oldest first:\n" +
               "\n---\n".join(u[:700] for u in p.updates[-4:]))
    if CTX.llm and await CTX.llm.available():
        try:
            return await asyncio.wait_for(CTX.llm.complete(
                "You brief a developer by voice on one of their software projects. From the notes, say in at most "
                "three short spoken sentences: what the project is, what was done most recently, and what is "
                "pending or where it stands now. No lists, no file paths, no code.", context, max_tokens=140),
                timeout=40)
        except Exception as e:
            log.warning("project summary failed: %s", e)
    return _brief(p)


@tool("List the user's Claude Code projects (what they are working on) and ask which one to discuss")
async def claude_projects() -> Result:
    projects = await asyncio.to_thread(list_projects, 6)
    if not projects:
        return Result("I couldn't find any Claude Code projects on this laptop.", ok=False, icon="code")
    names = [_spoken_name(p) for p in projects]
    spoken = ", ".join(names[:-1]) + f" and {names[-1]}" if len(names) > 1 else names[0]
    card = {"kind": "choices", "title": "Claude Code projects", "items": [p.name for p in projects],
            "subs": [f"{p.title or 'session'} · {_ago(p.updated)}" for p in projects]}
    return Result(f"You have {len(projects)} active projects in Claude Code: {spoken}. Which one would you like?",
                  icon="code", card=card, pending={"kind": "choose_project", "projects": [p.cwd for p in projects]})


def tasks_allowed() -> bool:
    """Read-only by default: Jarvis reports on projects but only hands out tasks when switched on."""
    return bool(CTX.cfg.get("claude_code", {}).get("allow_tasks", False))


def _first_sentence(text: str) -> str:
    text = re.sub(r"[`*#>|]", "", text).strip()
    return re.split(r"(?<=[.!?])\s", text)[0][:180]


@tool("Tell where a Claude Code project stands and what was done most recently",
      project="Project name, e.g. Solvix or Jarvis")
async def claude_project_status(project: str) -> Result:
    p = await asyncio.to_thread(find_project, project)
    if p is None:
        return Result(f"I couldn't find a Claude Code project called {project}.", ok=False, icon="code")
    summary = await _summarise(p)
    card = {"kind": "project", "name": p.name, "title": p.title, "ago": _ago(p.updated), "summary": summary,
            "last_prompt": p.last_prompt, "recent": [_first_sentence(u) for u in p.updates[-3:]][::-1]}
    head = f"{_spoken_name(p)}, last active {_ago(p.updated)}. {summary}"
    if tasks_allowed():
        return Result(f"{head} I'm standing by. What would you like Claude Code to do next?", icon="code", card=card,
                      pending={"kind": "claude_task", "cwd": p.cwd, "name": p.name, "session": p.session.stem})
    # read-only: offer the other projects instead of a task
    others = [q.cwd for q in await asyncio.to_thread(list_projects, 6)]
    return Result(f"{head} Would you like the status of another project?", icon="code", card=card,
                  pending={"kind": "choose_project", "projects": others})


def prepare_task(cwd: str, name: str, task: str, session: str = "") -> Result:
    """Read the task back and wait for confirmation (never sent straight from speech)."""
    return Result(f"I'll ask Claude Code in {re.sub(r'[-_]+', ' ', name)} to: {task}. Shall I send it?",
                  icon="code", card={"kind": "prompt_draft", "id": f"cc-{int(time.time())}", "project": name,
                                     "text": task},
                  pending={"kind": "claude_send", "cwd": cwd, "name": name, "task": task, "session": session})


def launch(cwd: str, name: str, task: str, session: str = "") -> Result:
    """Open a terminal with Claude Code in the project, continuing (forked from) its latest session."""
    exe = str(CLAUDE_EXE) if CLAUDE_EXE.exists() else shutil.which("claude")
    if not exe:
        return Result("Claude Code's command-line tool isn't installed, so I can't hand it a task.", ok=False)
    args = [exe]
    if session:
        args += ["--resume", session, "--fork-session"]
    args += ["-n", f"Jarvis: {name}", task.replace(";", ",")]  # ";" separates commands in Windows Terminal
    if shutil.which("wt.exe"):
        subprocess.Popen(["wt.exe", "-w", "new", "-d", cwd, "--title", f"Claude Code - {name}", *args])
    else:
        subprocess.Popen(["cmd", "/c", "start", "", *args], cwd=cwd)
    log.info("claude code task sent in %s (session %s): %r", cwd, session, task)
    return Result(f"Sent. Claude Code is working on it in {re.sub(r'[-_]+', ' ', name)}; "
                  "the terminal will ask before it changes anything.", icon="code")


@tool("Give Claude Code a task in one of the user's projects (read back for confirmation first)",
      project="Project name", task="What Claude Code should do")
async def claude_code_task(project: str, task: str) -> Result:
    if not tasks_allowed():
        return Result("I'm in read-only mode for Claude Code, sir. I can brief you on any project, "
                      "but I won't send it work until you switch that on.", icon="code")
    p = await asyncio.to_thread(find_project, project)
    if p is None:
        return Result(f"I couldn't find a Claude Code project called {project}.", ok=False, icon="code")
    return prepare_task(p.cwd, p.name, task, p.session.stem)
