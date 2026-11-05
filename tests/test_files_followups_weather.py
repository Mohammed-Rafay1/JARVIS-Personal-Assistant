"""5 Nov: finding files, ChatGPT research wording, the Claude Code question that took over other requests,
weather, and "yes, search it". Every phrase marked "log" is what the user really said."""
import asyncio
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.tools.base import Result  # noqa: E402


# ---------------------------------------------------------------- understanding file requests
@pytest.mark.parametrize("said,tool,args", [
    ("Tell me where is the flyer data.rar file in my laptop", "find_file",                       # log
     {"query": "flyer data.rar", "kind": "", "where": ""}),
    ("Open flyer data.rar file from my downloads", "open_file",                                  # log
     {"query": "flyer data.rar", "kind": "", "where": "downloads"}),
    ("Open folder solvix on desktop", "open_file", {"query": "solvix", "kind": "folder", "where": "desktop"}),  # log
    ("Check if there is a project Solvix on my desktop", "find_file",                            # log
     {"query": "solvix", "kind": "folder", "where": "desktop"}),
    ("open the solvix folder", "open_file", {"query": "solvix", "kind": "folder", "where": ""}),
    ("open my downloads folder", "open_folder", {"name": "downloads"}),
    ("do I have a file called budget", "find_file", {"query": "budget", "kind": "", "where": ""}),
    ("locate my CV", "find_file", {"query": "cv", "kind": "", "where": ""}),
    ("open report dot pdf", "open_file", {"query": "report.pdf", "kind": "", "where": ""}),
    ("search for flyer data.rar", "open_file", {"query": "flyer data.rar", "kind": "", "where": ""}),
])
def test_file_requests(said, tool, args):
    got = parse(said)
    assert got is not None and got.tool == tool and got.args == args, got


@pytest.mark.parametrize("said", ["where is lahore", "where is the nearest pharmacy"])
def test_where_is_a_place_is_not_a_file(said):
    got = parse(said)
    assert got is None or got.tool != "find_file"


# ---------------------------------------------------------------- research wording
@pytest.mark.parametrize("said,query,services", [
    ("Research on Hitler and tell me summary on chatgpt in new chat", "hitler", "chatgpt"),  # log: went to both
    ("Search on Hitler on chatgpt", "hitler", "chatgpt"),                                     # log: was "on hitler"
    ("Search about the Hitler on ChatGPT", "the hitler", "chatgpt"),
    ("who was hitler on chatgpt", "who was hitler", "chatgpt"),
    ("Jarvis open chatgpt and research about Hitler", "hitler", "chatgpt"),
])
def test_research_wording(said, query, services):
    got = parse(said)
    assert got.tool == "ai_research" and got.args == {"query": query, "services": services}, got


# ---------------------------------------------------------------- weather, "search it", corrections
@pytest.mark.parametrize("said,city,when", [
    ("Jarvis tell me about today's weather in Lahore", "lahore", "today"),  # log: the model made up 22°C
    ("will it rain tomorrow", "", "tomorrow"),
    ("what's the temperature", "", "today"),
    ("is it hot in dubai", "dubai", "today"),
])
def test_weather(said, city, when):
    got = parse(said)
    assert got.tool == "weather" and got.args == {"city": city, "when": when}, got


@pytest.mark.parametrize("said", ["google weather in Lahore", "play hot songs"])
def test_not_weather(said):
    assert parse(said).tool != "weather"


@pytest.mark.parametrize("said", ["Yes search it", "google it", "look it up", "search that online"])  # log: "Yes search it"
def test_search_it(said):
    assert parse(said).tool == "search_last"


def test_correction_then_command():
    assert parse("No Jarvis, Open Spotify.").tool in ("open_any", "open_app")  # log: "No Jarvis, Open ChatGPT app."
    assert parse("no") is None or parse("no").tool != "open_any"


def test_weather_spoken():
    from jarvis.tools.weather import spoken

    data = {"current": {"temperature_2m": 27.2, "apparent_temperature": 27, "weather_code": 0},
            "daily": {"temperature_2m_max": [28.4, 30], "temperature_2m_min": [18, 19],
                      "precipitation_probability_max": [0, 40], "weather_code": [0, 61]}}
    assert spoken("Lahore", "today", data) == ("It's 27 degrees in Lahore right now with clear skies. "
                                                "Today's high is 28 and the low 18. Chance of rain 0 percent.")
    assert spoken("Lahore", "tomorrow", data).startswith("Tomorrow in Lahore: light rain, a high of 30")


# ---------------------------------------------------------------- the file search itself
def test_query_terms_split_the_extension():
    from jarvis.tools.files import query_terms

    assert query_terms("flyer data.rar") == (["flyer", "data"], [".rar"])   # once searched "datarar"
    assert query_terms("Solvix") == (["solvix"], [])


@pytest.fixture
def home(monkeypatch, tmp_path):
    from jarvis.tools import CTX, files

    for d in ("Desktop/Solvix/src", "Desktop/Solvix-backup-2026", "Downloads/flyer data", "Desktop/app/node_modules/x"):
        (tmp_path / d).mkdir(parents=True)
    (tmp_path / "Downloads/flyer data.rar").write_text("x")
    (tmp_path / "Desktop/app/node_modules/x/flyer data.rar").write_text("x")
    (tmp_path / "Desktop/Party Plan.docx").write_text("x")
    monkeypatch.setattr(files, "known_folders", lambda: {"desktop": tmp_path / "Desktop",
                                                         "downloads": tmp_path / "Downloads", "home": tmp_path})
    monkeypatch.setattr(files, "_search_index", lambda *a, **k: [])  # this test folder isn't indexed
    CTX.cfg = {"files": {"search_roots": [str(tmp_path)]}}
    return tmp_path


def test_finds_a_file_with_its_extension_where_you_said(home):
    from jarvis.tools.files import find_files

    assert find_files("flyer data.rar", "", "downloads") == [str(home / "Downloads/flyer data.rar")]
    assert find_files("flyer data.rar")[0] == str(home / "Downloads/flyer data.rar")  # not the node_modules copy


def test_finds_the_folder_by_its_exact_name_first(home):
    from jarvis.tools.files import _clear_winner, find_files

    got = find_files("solvix", "folder", "desktop")
    assert got[0] == str(home / "Desktop/Solvix") and _clear_winner("solvix", got)


def test_misheard_name_still_found(home):
    from jarvis.tools.files import find_files

    assert find_files("partyplan")[0] == str(home / "Desktop/Party Plan.docx")


def test_find_file_says_where_and_offers_to_open(home):
    from jarvis.tools import files

    files.HOME = home
    r = asyncio.run(files.find_file("flyer data.rar"))
    assert r.say == "flyer data.rar is in your Downloads folder. Shall I open it, or show it in its folder?"
    assert r.pending == {"kind": "choose_file", "paths": [str(home / "Downloads/flyer data.rar")]}


def test_not_in_the_folder_you_said_but_found_elsewhere(home):
    from jarvis.tools import files

    r = asyncio.run(files.find_file("party plan", "", "downloads"))
    assert r.say.startswith("It isn't in Downloads, but I found it.")


# ---------------------------------------------------------------- follow-up questions
class _UI:
    def send(self, msg):
        pass


class _P:
    def __init__(self, cwd, name, session="s", title="", updated=0):
        self.cwd, self.name, self.session, self.title, self.updated = cwd, name, Path(f"{session}.jsonl"), title, updated


@pytest.fixture
def jarvis(monkeypatch, tmp_path):
    from jarvis import app as app_mod
    from jarvis.tools import CTX
    from jarvis.tools import claude_code as cc

    projects = [_P(r"C:\x\Polani mono", "Polani mono", "p1"), _P(r"C:\x\Solvix", "Solvix", "s1")]
    monkeypatch.setattr(cc, "list_projects", lambda n=6: projects)
    CTX.cfg = {"claude_code": {"allow_tasks": True, "new_project_dir": str(tmp_path)}}

    class Apps:
        apps = {"ChatGPT": "id", "Spotify": "id"}

        def find(self, name):
            return ("ChatGPT", 100) if "chatgpt" in name.replace(" ", "") else \
                ("Spotify", 100) if "spotify" in name else None
    CTX.apps = Apps()
    j = app_mod.Jarvis({"address_as": "sir", "claude_code": CTX.cfg["claude_code"],
                        "music": {"default_service": "youtube"}, "apps": {"aliases": {}}, "websites": {}}, _UI())
    j.state = "idle"
    said, tools, chats = [], [], []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def run_tool(name, args):
        tools.append((name, args))
        return Result(f"ran {name}")

    async def chat(text):
        chats.append(text)
    j.say, j.run_tool, j._chat = say, run_tool, chat
    return j, said, tools, chats


def _turn(j, text):
    asyncio.run(j.handle_text(text, typed=True))


def _send_pending():
    return {"kind": "claude_send", "cwd": r"C:\x\Polani mono", "name": "Polani mono", "task": "x", "session": "p1"}


def test_a_new_command_ends_the_claude_question(jarvis):  # log, 4 Nov 11:08
    j, said, tools, _ = jarvis
    j.pending = _send_pending()
    _turn(j, "No Jarvis, Open ChatGPT app.")
    assert tools == [("open_app", {"name": "chatgpt"})] and j.pending is None


def test_unclear_words_drop_the_task_instead_of_asking_again(jarvis):
    j, said, tools, chats = jarvis
    j.pending = _send_pending()
    _turn(j, "There will be Open Chat GPT.")
    assert j.pending is None and not any("Shall I send it" in s for s in said)


def test_reworded_task_is_read_back(jarvis):
    j, said, _, _ = jarvis
    j.pending = _send_pending()
    _turn(j, "change it to add a signup page")
    assert said[-1] == "I'll ask Claude Code in Polani mono to: add a signup page. Shall I send it?"


def test_status_offers_a_task_instead_of_taking_the_next_sentence(jarvis):
    j, said, tools, _ = jarvis
    j.pending = {"kind": "claude_task_offer", "cwd": r"C:\x\Solvix", "name": "Solvix", "session": "s1"}
    _turn(j, "search about the Hitler on ChatGPT")  # the complaint: this became a Claude Code task
    assert tools == [("ai_research", {"query": "the hitler", "services": "chatgpt"})] and j.pending is None


def test_yes_then_the_task(jarvis):
    j, said, _, _ = jarvis
    j.pending = {"kind": "claude_task_offer", "cwd": r"C:\x\Solvix", "name": "Solvix", "session": "s1"}
    _turn(j, "yes")
    assert said[-1] == "What should Claude Code do in Solvix?" and j.pending["kind"] == "claude_task"
    _turn(j, "add a logout button")
    assert said[-1] == "I'll ask Claude Code in Solvix to: add a logout button. Shall I send it?"


def test_yes_ask_it_to_in_one_go(jarvis):
    j, said, _, _ = jarvis
    j.pending = {"kind": "claude_task_offer", "cwd": r"C:\x\Solvix", "name": "Solvix", "session": "s1"}
    _turn(j, "yes, ask it to write tests")
    assert said[-1] == "I'll ask Claude Code in Solvix to: write tests. Shall I send it?"


def test_project_choice_understands_a_misheard_name(jarvis):  # log, 4 Nov 15:41
    j, said, tools, _ = jarvis
    j.pending = {"kind": "choose_project", "projects": [r"C:\x\Polani mono", r"C:\x\Solvix"]}
    _turn(j, "Tell me about Blonnie Mona.")
    assert tools == [("claude_project_status", {"project": r"C:\x\Polani mono"})]


def test_hello_is_not_a_project_name(jarvis):  # log: "hello" -> "no Claude Code project called hello"
    j, said, tools, _ = jarvis
    j.pending = {"kind": "choose_project", "projects": [r"C:\x\Polani mono", r"C:\x\Solvix"]}
    _turn(j, "hello")
    assert tools == [] and said[-1].startswith("Loud and clear") and j.pending is None


def test_which_project_is_asked_once_not_forever(jarvis):
    j, said, tools, _ = jarvis
    _turn(j, "ask claude code to add dark mode")
    _turn(j, "banana bread")
    assert said[-1].startswith("Which one?")
    _turn(j, "banana bread")
    assert said[-1] == "Okay, I've left the Claude Code prompt for now." and j.pending is None


def test_new_command_while_choosing_a_project(jarvis):
    j, said, tools, _ = jarvis
    _turn(j, "ask claude code to add dark mode")
    _turn(j, "what's the weather in Lahore")
    assert tools == [("weather", {"city": "lahore", "when": "today"})] and j.pending is None


def test_old_question_expires(jarvis):
    j, said, tools, _ = jarvis
    j.pending = {"kind": "choose_project", "projects": [r"C:\x\Polani mono"]}
    j._pending_at = time.monotonic() - 1000
    _turn(j, "polani mono")
    assert tools == [] and j.pending is None


def test_open_it_and_show_in_folder(jarvis, monkeypatch):
    from jarvis.tools import files

    j, said, _, _ = jarvis
    opened, shown = [], []
    monkeypatch.setattr(files, "open_path", lambda p: opened.append(p) or Result("Opening it."))
    monkeypatch.setattr(files, "reveal_path", lambda p: shown.append(p) or Result("Here it is."))
    j.pending = {"kind": "choose_file", "paths": [r"C:\d\flyer data.rar"]}
    _turn(j, "yes")
    assert opened == [r"C:\d\flyer data.rar"]
    j.pending = {"kind": "choose_file", "paths": [r"C:\d\flyer data.rar"]}
    _turn(j, "show it in its folder")
    assert shown == [r"C:\d\flyer data.rar"]


def test_yes_search_it_looks_up_the_last_question(jarvis):
    j, said, tools, _ = jarvis
    j._last_question = "Who won the 1992 cricket world cup?"
    _turn(j, "Yes search it")
    assert tools == [("web_research", {"query": "Who won the 1992 cricket world cup?"})]


# ---------------------------------------------------------------- research never comes back empty
def test_research_falls_back_to_the_web(monkeypatch):
    from jarvis.tools import CTX, ai_chat

    CTX.cfg = {"address_as": "sir"}

    async def ask_all(keys, prompt):
        return [ai_chat.Answer("chatgpt", problem="locked")]

    async def from_web(query):
        return "He led Nazi Germany from 1933 to 1945.", [("Adolf Hitler - Wikipedia", "...")]
    monkeypatch.setattr(ai_chat, "ask_all", ask_all)
    monkeypatch.setattr(ai_chat, "_from_web", from_web)
    r = asyncio.run(ai_chat.ai_research("hitler", "chatgpt"))
    assert r.ok and r.say == ("Your laptop is locked, so I couldn't use the apps. "
                              "Here's what the web says: He led Nazi Germany from 1933 to 1945.")
