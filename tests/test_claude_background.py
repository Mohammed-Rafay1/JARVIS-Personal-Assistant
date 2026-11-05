"""Claude Code tasks run in the background and are reported back (5 Nov)."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.tools import claude_code as cc  # noqa: E402
from jarvis.tools.base import Result  # noqa: E402


def test_background_command_line():
    args = cc.background_args("claude.exe", "add tests", "Solvix", "s1",
                              {"permission_mode": "acceptEdits", "allowed_tools": ["Bash(git diff:*)", "Bash(pytest:*)"]})
    assert args[:3] == ["claude.exe", "-p", "add tests"]                     # the prompt right after -p
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert args[args.index("--permission-prompts") + 1] == "none"            # never waits for an answer
    assert args[args.index("--resume") + 1] == "s1" and "--fork-session" in args
    assert args[-2:] == ["--allowedTools", "Bash(git diff:*),Bash(pytest:*)"]  # last: it takes every value after it


def test_new_chat_has_no_resume():
    assert "--resume" not in cc.background_args("c", "x", "n", "", {})


def _task():
    return cc.Task(name="Solvix", cwd=r"C:\p\Solvix", task="add a login page")


def test_events_give_session_files_and_refusals():
    t = _task()
    cc.take_event(t, {"type": "system", "subtype": "init", "session_id": "abc"})
    cc.take_event(t, {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Write", "input": {"file_path": r"C:\p\Solvix\src\login.tsx"}},
        {"type": "tool_use", "name": "Read", "input": {"file_path": r"C:\p\Solvix\README.md"}},
        {"type": "text", "text": "Done."}]}})
    cc.take_event(t, {"type": "result", "subtype": "success", "is_error": False, "num_turns": 7,
                      "result": "I added a login page with email and password fields. Tests pass.",
                      "permission_denials": [{"tool_name": "Bash", "tool_input": {"command": "npm install zod"}}]})
    assert t.session == "abc" and t.files == [r"C:\p\Solvix\src\login.tsx"] and t.denied == ["npm install zod"]
    t.status = "done"
    said = cc.report(t)
    assert said.startswith("Claude Code finished in Solvix after 1 minute. It changed 1 file: login.tsx.")
    assert "npm install zod" in said and "I added a login page" in said
    assert "src\\login.tsx" in cc.report(t, spoken=False)  # WhatsApp gets the full path in the project


def test_not_signed_in_is_explained():
    t = _task()
    cc.take_event(t, {"type": "result", "subtype": "success", "is_error": True, "result": "Not logged in · Please run /login"})
    assert t.status == "failed" and cc.report(t) == cc.NOT_SIGNED_IN


def test_signed_in_reads_auth_status(monkeypatch):
    class R:
        stdout = '{\n "loggedIn": false,\n "authMethod": "none"\n}'
    monkeypatch.setattr(cc, "_exe", lambda: "claude.exe")
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: R())
    monkeypatch.setattr(cc, "_signed_in_at", 0.0)
    assert cc.signed_in() is False
    R.stdout = '{"loggedIn": true}'
    assert cc.signed_in() is True


@pytest.mark.parametrize("said,tool", [
    ("Is Claude Code done?", "claude_task_status"), ("what is claude code doing", "claude_task_status"),
    ("did claude finish", "claude_task_status"), ("is it done yet", "claude_task_status"),
    ("stop claude code", "claude_task_stop"), ("cancel the claude code task", "claude_task_stop"),
])
def test_task_commands(said, tool):
    assert parse(said).tool == tool


def test_plain_stop_still_ends_the_call():
    assert parse("stop").tool == "stop"


# ---------------------------------------------------------------- the flow in Jarvis
class _UI:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


@pytest.fixture
def jarvis(monkeypatch):
    from jarvis import app as app_mod
    from jarvis.tools import CTX

    CTX.cfg = {"claude_code": {"allow_tasks": True, "background": True}}
    j = app_mod.Jarvis({"address_as": "sir", "claude_code": CTX.cfg["claude_code"],
                        "music": {"default_service": "youtube"}, "apps": {"aliases": {}}, "websites": {}}, _UI())
    j.state = "idle"
    said = []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    class Player:
        def play(self, *a):
            pass
    j.say, j.player = say, Player()
    cc.TASKS.clear()
    return j, said


def _send(j):
    j.pending = {"kind": "claude_send", "cwd": r"C:\p\Solvix", "name": "Solvix", "task": "add tests", "session": "s1"}
    asyncio.run(j.handle_text("send it", typed=True))


def test_send_it_starts_in_the_background(jarvis, monkeypatch):
    j, said = jarvis
    started = []

    async def start(cwd, name, task, session="", from_phone=False, on_done=None):
        started.append((cwd, task, session, from_phone))
    monkeypatch.setattr(cc, "signed_in", lambda: True)
    monkeypatch.setattr(cc, "start_background", start)
    _send(j)
    assert started == [(r"C:\p\Solvix", "add tests", "s1", False)]
    assert said[-1].startswith("Sent. Claude Code is working on it in Solvix in the background. I'll tell you here")


def test_not_signed_in_says_how_to_fix(jarvis, monkeypatch):
    j, said = jarvis
    monkeypatch.setattr(cc, "signed_in", lambda: False)
    _send(j)
    assert said[-1] == cc.NOT_SIGNED_IN


def test_one_task_per_project_at_a_time(jarvis, monkeypatch):
    j, said = jarvis
    monkeypatch.setattr(cc, "signed_in", lambda: True)
    cc.TASKS.append(cc.Task(name="Solvix", cwd=r"C:\p\Solvix", task="earlier"))
    _send(j)
    assert said[-1].startswith("Claude Code is still working on the last task in Solvix.")


def test_finished_task_from_the_phone_is_reported_on_whatsapp(jarvis, monkeypatch):
    j, said = jarvis
    sent = []

    class Remote:
        linked = True

        def notify(self, text):
            sent.append(text)
    j.remote = Remote()
    t = _task()
    t.from_phone, t.status, t.result = True, "done", "Added it."
    asyncio.run(j._claude_done(t))
    assert sent and sent[0].startswith("Claude Code finished in Solvix") and said == []


def test_finished_task_from_the_laptop_is_spoken(jarvis, monkeypatch):
    from jarvis.tools import whatsapp

    j, said = jarvis
    monkeypatch.setattr(whatsapp, "screen_locked", lambda: False)

    class Player:
        def play(self, *a):
            pass
    j.player = Player()
    t = _task()
    t.status, t.result = "done", "Added it."
    asyncio.run(j._claude_done(t))
    assert said and said[0].startswith("Claude Code finished in Solvix")


def test_status_and_stop(monkeypatch):
    cc.TASKS.clear()
    assert asyncio.run(cc.claude_task_status()).say == "I haven't given Claude Code any tasks since I started."
    t = _task()
    t.files = [r"C:\p\Solvix\a.py"]
    cc.TASKS.append(t)
    assert "has been working in Solvix" in asyncio.run(cc.claude_task_status()).say
    monkeypatch.setattr(cc, "_kill", lambda t: None)
    assert asyncio.run(cc.claude_task_stop()).say.startswith("Stopped the Claude Code task in Solvix.")
    assert t.status == "stopped"
    cc.TASKS.clear()
