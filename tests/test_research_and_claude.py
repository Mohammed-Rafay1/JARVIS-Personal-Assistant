import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.tools.ai_chat import Answer, answer_after, clean, first_sentences, parse_services, summarise  # noqa: E402


# ---------------------------------------------------------------- understanding the request
@pytest.mark.parametrize("said,query,services", [
    ("Jarvis open chatgpt and research about Hitler", "hitler", "chatgpt"),        # from the log, 3 Nov
    ("Research about Hitler on chatgpt and tell me summary", "hitler", "chatgpt"),  # from the log, 3 Nov
    ("research the history of Lahore", "the history of lahore", ""),
    ("search about black holes", "black holes", ""),
    ("ask chatgpt what is quantum computing", "what is quantum computing", "chatgpt"),
    ("ask claude to explain recursion", "explain recursion", "claude"),
    ("search the population of Pakistan on claude", "the population of pakistan", "claude"),
    ("look up flights to Dubai", "flights to dubai", ""),
])
def test_research_requests(said, query, services):
    got = parse(said)
    assert got.tool == "ai_research" and got.args == {"query": query, "services": services}


@pytest.mark.parametrize("said,tool", [
    ("Search Google for Odoo release notes", "web_search"),
    ("search youtube for lofi beats", "play_youtube"),
    ("search for weather in lahore in edge", "web_search"),
    ("search for the budget spreadsheet", "open_file"),
    ("open chatgpt", "open_any"),
])
def test_other_searches_unchanged(said, tool):
    assert parse(said).tool == tool


@pytest.mark.parametrize("said,project,task", [
    ("ask claude code to build a login page", "", "build a login page"),      # was project "code"
    ("give this prompt to claude code: add dark mode", "", "add dark mode"),
    ("open claude code and add a logout button", "", "add a logout button"),
    ("claude code, write tests for the parser", "", "write tests for the parser"),
    ("ask claude code in solvix to fix the bug", "solvix", "fix the bug"),
])
def test_claude_code_prompts(said, project, task):
    got = parse(said)
    assert got.tool == "claude_code_task" and got.args == {"project": project, "task": task}


def test_claude_code_projects_still_lists():
    assert parse("claude code projects").tool == "claude_projects"


# ---------------------------------------------------------------- answers and summary
def test_services():
    assert parse_services("") == ["chatgpt", "claude"]
    assert parse_services("chatgpt") == ["chatgpt"]
    assert parse_services("claude") == ["claude"]


def test_clean_drops_buttons_and_markdown():
    assert clean("## Title\n**Bold** text\nCopy\n\n\n\nMore") == "Title\nBold text\nMore"


def test_answer_is_the_text_after_your_question():
    texts = ["Old chat title", "What is 2 plus 2? Answer briefly.", "2 plus 2 equals 4.", "Copy", "That's basic maths."]
    assert answer_after(texts, "What is 2 plus 2? Answer briefly.") == "2 plus 2 equals 4.\nThat's basic maths."
    assert answer_after(["nothing yet"], "What is 2 plus 2?") == ""


def test_summary_without_model_uses_the_first_answer():
    from jarvis.tools import CTX

    CTX.llm = None
    text = asyncio.run(summarise("q", [Answer("chatgpt", "One. Two. Three. Four."), Answer("claude", "")]))
    assert text == "One. Two. Three."
    assert first_sentences("A b. C d? E f!", 2) == "A b. C d?"


# ---------------------------------------------------------------- Claude Code: project, chat, read back
class _UI:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class _P:
    def __init__(self, cwd, name, session, title, updated=0):
        self.cwd, self.name, self.session, self.title, self.updated = cwd, name, Path(f"{session}.jsonl"), title, updated


def _jarvis(monkeypatch, tmp_path):
    from jarvis import app as app_mod
    from jarvis.tools import CTX
    from jarvis.tools import claude_code as cc

    projects = [_P(r"C:\x\Solvix", "Solvix", "s1", "Login page"), _P(r"C:\x\Jarvis", "Jarvis", "j1", "Voice fixes")]
    monkeypatch.setattr(cc, "list_projects", lambda n=6: projects)
    monkeypatch.setattr(cc, "find_project", lambda name: next((p for p in projects if p.name.lower() == name), None))
    CTX.cfg = {"claude_code": {"allow_tasks": True, "new_project_dir": str(tmp_path)}}
    j = app_mod.Jarvis({"address_as": "sir", "claude_code": CTX.cfg["claude_code"], "music": {"default_service": "youtube"},
                        "apps": {"aliases": {}}, "websites": {}}, _UI())
    said = []

    async def say(text, then_idle=True, follow=False):
        said.append(text)
    j.say = say
    return j, said


def _turn(j, text):
    asyncio.run(j.handle_text(text, typed=True))


def test_asks_project_then_chat_then_reads_back(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    _turn(j, "ask claude code to add dark mode")
    assert "Which project" in said[-1] and "1, Solvix" in said[-1] and j.pending["kind"] == "cc_project"
    _turn(j, "2")
    assert "continue your latest chat, Voice fixes," in said[-1] and j.pending["kind"] == "cc_chat"
    _turn(j, "new chat")
    assert said[-1].startswith("In a new chat. I'll ask Claude Code in Jarvis to: add dark mode. Shall I send it?")
    assert j.pending == {"kind": "claude_send", "cwd": r"C:\x\Jarvis", "name": "Jarvis", "task": "add dark mode",
                         "session": ""}


def test_named_project_skips_to_chat_and_continue_keeps_session(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    _turn(j, "ask claude code in solvix to fix the bug")
    assert j.pending["kind"] == "cc_chat"
    _turn(j, "continue")
    assert said[-1].startswith("Continuing your latest chat.") and j.pending["session"] == "s1"


def test_new_project_creates_its_folder(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    _turn(j, "give this prompt to claude code: set up a react app")
    _turn(j, "new project")
    assert "call the new project" in said[-1]
    _turn(j, "Recipe Box")
    assert (tmp_path / "Recipe-Box").is_dir()
    assert j.pending["kind"] == "claude_send" and j.pending["session"] == "" and j.pending["task"] == "set up a react app"


def test_no_cancels_and_nothing_is_sent(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    _turn(j, "ask claude code to delete the database")
    _turn(j, "no")
    assert said[-1] == "Okay, nothing sent to Claude Code." and j.pending is None


def test_read_only_mode_refuses(monkeypatch, tmp_path):
    from jarvis.tools import CTX

    j, said = _jarvis(monkeypatch, tmp_path)
    CTX.cfg = {"claude_code": {"allow_tasks": False}}
    _turn(j, "ask claude code to add dark mode")
    assert "read-only" in said[-1] and j.pending is None
