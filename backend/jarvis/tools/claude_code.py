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


_PROJECT_FILLER = re.compile(
    r"\b(?:tell me|let me know|give me|show me|what is|what's|how is|how's|i said|i mean|please|about|the|status|"
    r"state|progress|update|updates|of|on|for|with|my|project|projects|in|claude|code|going|doing|latest|one|is|"
    r"it|and|that|this|named|called)\b")


def project_query(text: str) -> str:
    """"tell me about Blonnie Mona" -> "blonnie mona": only the words that could be the name."""
    t = re.sub(r"[^\w\s]", " ", re.sub(r"[-_]+", " ", text.lower()))
    return re.sub(r"\s+", " ", _PROJECT_FILLER.sub(" ", t)).strip()


def _sound(text: str) -> str:
    """A rough sound of a name: the consonants, with the ones Whisper swaps merged ("blonnie mona" and
    "polani mono" both -> "pln mn")."""
    t = re.sub(r"[^a-z ]", "", text.lower())
    for a, b in (("ph", "f"), ("ck", "k"), ("b", "p"), ("d", "t"), ("g", "k"), ("v", "f"), ("z", "s"), ("q", "k"),
                 ("c", "k"), ("w", ""), ("h", ""), ("y", "")):
        t = t.replace(a, b)
    words = [re.sub(r"(.)\1+", r"\1", re.sub(r"[aeiou]", "", w)) for w in t.split()]
    return " ".join(w for w in words if w)


def name_score(said: str, name: str) -> float:
    """How close a heard name is to a project's name, by spelling or by sound (0-100)."""
    said, name = said.lower().strip(), re.sub(r"[-_\s]+", " ", name.lower()).strip()
    s = fuzz.ratio(said, name)
    a, b = _sound(said), _sound(name)
    if len(a.replace(" ", "")) >= 3 and len(b.replace(" ", "")) >= 3:
        s = max(s, fuzz.ratio(a, b) - 5)  # sound alone is a little weaker evidence than spelling
    return s


def find_project(name: str) -> Project | None:
    projects = list_projects(12)
    if not projects:
        return None
    if re.search(r"[\\/]", name):  # a folder path from the project list
        hit = next((p for p in projects if p.cwd.lower() == name.lower()), None)
        if hit:
            return hit
    q = project_query(name) or name.lower()
    # the name as heard: close to a project's folder name ("pulani mono", "hulani mono" -> Polani mono)
    score, best = max(((name_score(q, p.name), p) for p in projects), key=lambda x: x[0])
    if score >= 70:
        return best
    # or in its session title or folder path, so "hackathon" finds the project living in Desktop\Hackathon\...
    labels = {f"{p.name} {p.title} {p.cwd}".lower().replace("-", " "): p for p in projects}
    hit = process.extractOne(q, list(labels), scorer=fuzz.partial_ratio, score_cutoff=75)
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
        # a yes/no question, not "what next?": the next thing said was once taken as a task even when it was
        # a new command ("open ChatGPT" became a prompt for Claude Code, 4 Nov)
        return Result(f"{head} Would you like to give Claude Code a task in it?", icon="code", card=card,
                      pending={"kind": "claude_task_offer", "cwd": p.cwd, "name": p.name,
                               "session": p.session.stem})
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


def _exe() -> str | None:
    return str(CLAUDE_EXE) if CLAUDE_EXE.exists() else shutil.which("claude")


def background_enabled() -> bool:
    return bool(CTX.cfg.get("claude_code", {}).get("background", True))


_signed_in_at = 0.0
NOT_SIGNED_IN = ("Claude Code on this laptop isn't signed in, so I can't hand it work yet. At the laptop, open a "
                 "terminal, run claude auth login, and sign in once in the browser. The desktop app's sign-in "
                 "isn't shared with it.")


def signed_in() -> bool:
    """Whether the command-line Claude Code can work (the desktop app signs in separately: on 5 Nov the
    command line wasn't, and every task would have stopped at "Not logged in")."""
    global _signed_in_at
    if time.monotonic() - _signed_in_at < 600:
        return True
    exe = _exe()
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, timeout=20,
                             creationflags=0x08000000).stdout
        ok = bool(json.loads(out[out.index("{"):]).get("loggedIn"))
    except Exception as e:
        log.info("claude auth status unreadable (%s); trying anyway", e.__class__.__name__)
        return True
    if ok:
        _signed_in_at = time.monotonic()
    return ok


# ---------------------------------------------------------------- tasks run in the background
@dataclass
class Task:
    """One task handed to Claude Code, running without a window; Jarvis reports when it finishes."""
    name: str
    cwd: str
    task: str
    from_phone: bool = False
    started: float = field(default_factory=time.time)
    status: str = "running"          # running | done | failed | stopped | timed_out
    result: str = ""                 # Claude Code's own closing message
    files: list[str] = field(default_factory=list)    # files it created or edited
    denied: list[str] = field(default_factory=list)   # commands it wanted to run but wasn't allowed to
    session: str = ""
    turns: int = 0
    error: str = ""
    proc: object = None
    log_path: Path | None = None


TASKS: list[Task] = []
_EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def running(cwd: str = "") -> list[Task]:
    return [t for t in TASKS if t.status == "running" and (not cwd or t.cwd.lower() == cwd.lower())]


def background_args(exe: str, task: str, name: str, session: str, cfg: dict) -> list[str]:
    """Claude Code without a window. It may edit files in the project (acceptEdits); anything it would normally
    ask about first (running commands, the web) is refused instead of waiting for an answer nobody can give,
    apart from the commands listed in claude_code.allowed_tools. The session is saved under the name
    "Jarvis: <project>", so it can be opened and continued in Claude Code afterwards."""
    args = [exe, "-p", task, "--output-format", "stream-json", "--verbose",
            "--permission-mode", cfg.get("permission_mode", "acceptEdits"), "--permission-prompts", "none",
            "-n", f"Jarvis: {name}"]
    if session:
        args += ["--resume", session, "--fork-session"]
    if cfg.get("max_budget_usd"):
        args += ["--max-budget-usd", str(cfg["max_budget_usd"])]
    allowed = cfg.get("allowed_tools") or []
    if allowed:  # last: the option takes every value after it
        args += ["--allowedTools", ",".join(allowed)]
    return args


def take_event(t: Task, ev: dict) -> None:
    """Fold one line of Claude Code's stream into the task: its session, the files it edits, its result."""
    kind = ev.get("type")
    if kind == "system" and ev.get("session_id"):
        t.session = ev["session_id"]
    elif kind == "assistant":
        for b in (ev.get("message") or {}).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in _EDIT_TOOLS:
                path = (b.get("input") or {}).get("file_path") or (b.get("input") or {}).get("notebook_path")
                if path and path not in t.files:
                    t.files.append(path)
    elif kind == "result":
        t.result = str(ev.get("result") or "")
        t.turns = int(ev.get("num_turns") or 0)
        t.session = ev.get("session_id") or t.session
        if ev.get("is_error") or ev.get("subtype", "success") != "success":
            t.status, t.error = "failed", t.result or str(ev.get("subtype", "error"))
        for d in ev.get("permission_denials") or []:
            inp = d.get("tool_input") or {}
            what = inp.get("command") or inp.get("url") or inp.get("file_path") or d.get("tool_name", "")
            if what and what not in t.denied:
                t.denied.append(str(what)[:120])


def _short_files(t: Task) -> list[str]:
    out = []
    for f in t.files:
        try:
            out.append(str(Path(f).resolve().relative_to(Path(t.cwd).resolve())))
        except ValueError:
            out.append(Path(f).name)
    return out


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + f" and {items[-1]}"


def report(t: Task, spoken: bool = True) -> str:
    """What happened, for the ear (short) or for WhatsApp (with Claude Code's own words and every file)."""
    name = re.sub(r"[-_]+", " ", t.name)
    mins = max(1, round((time.time() - t.started) / 60))
    if t.status == "stopped":
        return f"I stopped the Claude Code task in {name}."
    if t.status == "timed_out":
        head = f"The Claude Code task in {name} ran over {mins} minutes, so I stopped it."
    elif t.status == "failed" and re.search(r"not logged in|/login|authenticat", t.error + t.result, re.I):
        return NOT_SIGNED_IN
    elif t.status == "failed":
        head = f"Claude Code ran into a problem in {name}: {_first_sentence(_speakable(t.error or t.result)) or 'no details'}."
    else:
        head = f"Claude Code finished in {name} after {mins} minute{'s' if mins != 1 else ''}."
    files = _short_files(t)
    if files:
        shown = files if not spoken else [Path(f).name for f in files[:3]]
        more = f" and {len(files) - 3} more" if spoken and len(files) > 3 else ""
        head += f" It changed {len(files)} file{'s' if len(files) != 1 else ''}: {_list(shown)}{more}."
    elif t.status == "done":
        head += " It didn't change any files."
    if t.denied:
        head += (f" It wanted to run {len(t.denied)} command{'s' if len(t.denied) != 1 else ''} I don't allow "
                 f"without you, like {t.denied[0][:60]}; open the Jarvis: {t.name} session to approve "
                 f"{'them' if len(t.denied) != 1 else 'it'}.")
    said = _speakable(t.result)
    if spoken:
        sentences = re.split(r"(?<=[.!?])\s+", said)
        return f"{head} {' '.join(sentences[:2])[:300]}".strip()
    return f"{head}\n\n{t.result[:1800]}".strip()


def progress(t: Task) -> str:
    name = re.sub(r"[-_]+", " ", t.name)
    mins = max(1, round((time.time() - t.started) / 60))
    files = [Path(f).name for f in t.files]
    so_far = f" So far it has changed {_list(files[-3:])}." if files else " It hasn't changed any files yet."
    return f"Claude Code has been working in {name} for {mins} minute{'s' if mins != 1 else ''}.{so_far}"


async def start_background(cwd: str, name: str, task: str, session: str = "", from_phone: bool = False,
                           on_done=None) -> Task:
    """Start the task and return at once; on_done(task) is awaited when it ends."""
    from ..config import DATA

    exe = _exe()
    if not exe:
        raise FileNotFoundError("Claude Code's command-line tool isn't installed")
    cfg = CTX.cfg.get("claude_code", {})
    t = Task(name=name, cwd=cwd, task=task, from_phone=from_phone)
    logs = DATA / "claude_tasks"
    logs.mkdir(parents=True, exist_ok=True)
    t.log_path = logs / f"{time.strftime('%Y%m%d-%H%M%S')}-{re.sub(r'[^A-Za-z0-9]+', '-', name)}.jsonl"
    t.proc = await asyncio.create_subprocess_exec(
        *background_args(exe, task, name, session, cfg), cwd=cwd, stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=0x08000000,
        limit=32 * 1024 * 1024)  # one stream line can hold a whole file Claude Code read
    TASKS.append(t)
    log.info("claude code task started in %s (from session %s, pid %s): %r", cwd, session or "new", t.proc.pid, task)
    asyncio.create_task(_watch(t, float(cfg.get("max_minutes", 30)) * 60, on_done))
    return t


async def _watch(t: Task, limit: float, on_done) -> None:
    errors: list[str] = []

    async def read() -> None:
        with open(t.log_path, "a", encoding="utf-8") as f:
            async for raw in t.proc.stdout:
                line = raw.decode("utf-8", "replace")
                f.write(line)
                try:
                    take_event(t, json.loads(line))
                except ValueError:
                    continue

    async def read_errors() -> None:
        async for raw in t.proc.stderr:
            errors.append(raw.decode("utf-8", "replace").strip())

    err_task = asyncio.create_task(read_errors())
    try:
        await asyncio.wait_for(read(), timeout=limit)
        await t.proc.wait()
    except asyncio.TimeoutError:
        t.status = "timed_out"
        _kill(t)
    except Exception as e:
        log.exception("claude code task watch failed")
        t.status, t.error = "failed", str(e)
    finally:
        err_task.cancel()
    if t.status == "running":
        code = t.proc.returncode
        if code == 0 or t.result:
            t.status = "done"
        else:
            t.status, t.error = "failed", t.error or " ".join(errors[-3:])[:300] or f"exit code {code}"
    log.info("claude code task %s in %s: %d files, %d refused, session %s", t.status, t.cwd, len(t.files),
             len(t.denied), t.session)
    if on_done:
        try:
            await on_done(t)
        except Exception:
            log.exception("claude code report failed")


def _kill(t: Task) -> None:
    """End the task and anything it started (a test run, a build)."""
    pid = getattr(t.proc, "pid", None)
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, creationflags=0x08000000)


def stop(cwd: str = "") -> list[Task]:
    stopped = running(cwd)
    for t in stopped:
        t.status = "stopped"
        _kill(t)
    return stopped


@tool("How the Claude Code task Jarvis started is going (or how the last one ended)")
async def claude_task_status() -> Result:
    now = running()
    if now:
        return Result(" ".join(progress(t) for t in now), icon="code")
    if TASKS:
        return Result(report(TASKS[-1]), icon="code")
    return Result("I haven't given Claude Code any tasks since I started.", icon="code")


@tool("Stop the Claude Code task Jarvis started")
async def claude_task_stop() -> Result:
    stopped = await asyncio.to_thread(stop)
    if not stopped:
        return Result("Claude Code isn't working on anything for me right now.", icon="code")
    names = _list([re.sub(r"[-_]+", " ", t.name) for t in stopped])
    return Result(f"Stopped the Claude Code task in {names}. Changes it already made are still in the files.",
                  icon="code")


def launch(cwd: str, name: str, task: str, session: str = "") -> Result:
    """Open a terminal with Claude Code in the project, continuing (forked from) its latest session."""
    exe = _exe()
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
