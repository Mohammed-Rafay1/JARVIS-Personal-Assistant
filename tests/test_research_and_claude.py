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


# ---------------------------------------------------------------- better prompts, and reading answers back
from jarvis.tools.ai_chat import in_short, research_prompt  # noqa: E402
from jarvis.tools.web_research import parse_results, search_query  # noqa: E402


def test_topic_becomes_a_research_prompt():
    p = research_prompt("hitler")                     # sent as just "hitler" on 3 Nov
    assert p.startswith("Research hitler and give me a clear, well-structured overview")
    assert 'End with one line starting "In short:"' in p


def test_question_stays_the_question():
    assert research_prompt("what is quantum computing").startswith("What is quantum computing? Answer clearly")


def test_reader_finds_the_answer_when_the_topic_repeats_in_it():
    p = research_prompt("hitler")
    texts = ["Hitler overview", "You said:", p, "Adolf Hitler was the leader...", "Hitler rose to power in 1933.",
             "In short: Hitler led Nazi Germany. He started World War II."]
    got = answer_after(texts, p)
    assert got.startswith("Adolf Hitler was the leader") and in_short(got).startswith("Hitler led Nazi Germany.")


def test_the_answers_own_summary_is_used_without_the_model():
    from jarvis.tools import CTX

    CTX.llm = None
    text = asyncio.run(summarise("q", [Answer("chatgpt", "Long answer.\nIn short: Tokyo. It is the capital.")]))
    assert text == "Tokyo. It is the capital."


@pytest.mark.parametrize("said,query", [
    ("the history of lahore", "history of lahore"),
    ("about some information on odoo 18", "odoo 18"),
    ("flights to dubai please", "flights to dubai"),
])
def test_search_query_cleanup(said, query):
    assert search_query(said) == query


def test_parse_duckduckgo_results():
    page = ('<a class="result__a" href="x">History of <b>Lahore</b></a> <a class="result__snippet" href="x">'
            'Lahore is an ancient city &amp; capital.</a><a class="result__a" href="y">Empty</a>'
            '<a class="result__snippet" href="y"></a>')
    assert parse_results(page) == [("History of Lahore", "Lahore is an ancient city & capital.")]


@pytest.mark.parametrize("said,query", [
    ("research the history of Lahore on google", "the history of lahore"),
    ("research about Hitler on google and tell me summary", "hitler"),
    ("search google for odoo 18 features and tell me the summary", "odoo 18 features"),
    ("research flights to dubai online", "flights to dubai"),
])
def test_google_research_requests(said, query):
    got = parse(said)
    assert got.tool == "web_research" and got.args["query"] == query


def test_plain_google_search_still_just_opens_google():
    assert parse("Search Google for Odoo release notes").tool == "web_search"


def test_room_wake_does_not_cancel_a_whatsapp_command(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    j._remote_said = []                       # a WhatsApp command is running
    called = []
    j._interrupt = lambda: called.append("interrupt")
    j._wake("voice")
    j._wake("barge-in")
    assert called == []


def test_snippet_made_speakable():
    from jarvis.tools.web_research import speakable

    s = ("The recorded history of Lahore (Punjabi: لاہور (Shahmukhi); romanized: "
         "Lāhaur) refers to the city's past (founded 1000 AD). Today it is the capital. More ...")
    assert speakable(s) == "The recorded history of Lahore refers to the city's past (founded 1000 AD). Today it is the capital."


def test_mute_in_a_call_ends_with_the_call(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    j.player = type("P", (), {"play": lambda self, x: None, "stop": lambda self: None})()
    j._idle, j.state = (lambda: None), "idle"
    j.in_call = True
    asyncio.run(j.on_ui({"type": "mute", "value": True}))
    assert j.muted
    j._end_call("closed")
    assert not j.muted                         # "Hey Jarvis" works again after the call


def test_mute_outside_a_call_stays(monkeypatch, tmp_path):
    j, said = _jarvis(monkeypatch, tmp_path)
    j.player = type("P", (), {"play": lambda self, x: None, "stop": lambda self: None})()
    j._idle, j.state = (lambda: None), "idle"
    j.in_call = False
    asyncio.run(j.on_ui({"type": "mute", "value": True}))
    j.in_call = True
    j._end_call("closed")
    assert j.muted
