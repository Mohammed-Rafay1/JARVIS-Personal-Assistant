"""Claude Code tasks said the way people say them on WhatsApp (5 Nov 17:25 test), and tidy remote replies."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.tools.base import Result  # noqa: E402

POLANI = r"C:\x\Polani mono"


@pytest.mark.parametrize("said,project,task,here", [
    ("Can u give prompt in Polani mono", "polani mono", "", False),
    ("Give prompt there to check whether v119 is live on both app and admin ?", "",
     "check whether v119 is live on both app and admin", True),
    ('Enter this as a prompt in my project polani mono in Claude code "check whether v119 is live on both app and admin"',
     "polani mono", "check whether v119 is live on both app and admin", False),
    ("Give prompt", "", "", False),
    ("Do it yourself in my claude", "", "", True),
    ("give prompt in polani mono to check the build", "polani mono", "check the build", False),
    ("send a prompt to claude code in solvix: fix the login", "solvix", "fix the login", False),
    ("prompt claude in solvix to add tests", "solvix", "add tests", False),
])
def test_ways_of_giving_claude_code_a_prompt(said, project, task, here):
    i = parse(said)
    assert i.tool == "claude_code_task"
    assert (i.args["project"], i.args["task"], bool(i.args.get("here"))) == (project, task, here)


@pytest.mark.parametrize("said,tool", [("send a message to Ali", "whatsapp_ask"),
                                       ("status of solvix", "claude_project_status"),
                                       ("how many projects im currently working in claude code", "claude_projects")])
def test_other_commands_unchanged(said, tool):
    assert parse(said).tool == tool


class _P:
    def __init__(self, cwd, name, session="s", title="", updated=0):
        self.cwd, self.name, self.session, self.title, self.updated = cwd, name, Path(f"{session}.jsonl"), title, updated


class _UI:
    def send(self, m):
        pass


@pytest.fixture
def jarvis(monkeypatch):
    from jarvis import app as app_mod
    from jarvis.tools import CTX
    from jarvis.tools import claude_code as cc

    projects = [_P(POLANI, "Polani mono", "p1", "mono"), _P(r"C:\x\Solvix", "Solvix", "s1")]
    monkeypatch.setattr(cc, "list_projects", lambda n=6: projects)
    CTX.cfg = {"claude_code": {"allow_tasks": True}}
    j = app_mod.Jarvis({"address_as": "sir", "claude_code": CTX.cfg["claude_code"],
                        "music": {"default_service": "youtube"}, "apps": {"aliases": {}}, "websites": {}}, _UI())
    j.state = "idle"
    said, tools, chats = [], [], []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def run_tool(name, args):
        tools.append((name, args))
        if name == "claude_project_status":  # as the real tool does: offer a task in that project
            return Result("Polani mono, last active 3 h ago. Would you like to give Claude Code a task in it?",
                          pending={"kind": "claude_task_offer", "cwd": POLANI, "name": "Polani mono", "session": "p1"})
        return Result(f"ran {name}")

    async def chat(text):
        chats.append(text)
    j.say, j.run_tool, j._chat = say, run_tool, chat
    return j, said, tools, chats


def _turn(j, text):
    asyncio.run(j.handle_text(text, typed=True))


def test_give_prompt_while_choosing_a_project_asks_for_the_task(jarvis):  # 17:25:58
    j, said, tools, chats = jarvis
    j.pending = {"kind": "choose_project", "projects": [POLANI, r"C:\x\Solvix"]}
    _turn(j, "Can u give prompt in Polani mono")
    assert tools == [] and chats == []                       # not the project status any more
    assert said[-1] == "What should Claude Code do in Polani mono?"
    _turn(j, "check whether v119 is live on both app and admin")
    assert said[-1] == "I'll ask Claude Code in Polani mono to: check whether v119 is live on both app and admin. Shall I send it?"
    assert j.pending["kind"] == "claude_send" and j.pending["cwd"] == POLANI and j.pending["session"] == "p1"


def test_give_prompt_there_after_the_status_uses_that_project(jarvis):  # 17:26:34
    j, said, tools, chats = jarvis
    _turn(j, "status of polani mono")
    _turn(j, "Give prompt there to check whether v119 is live on both app and admin ?")
    assert chats == []                                        # not a made-up answer from the chat model
    assert said[-1].startswith("I'll ask Claude Code in Polani mono to: check whether v119 is live")
    assert j.pending["kind"] == "claude_send"


def test_the_task_itself_answers_the_offer(jarvis):
    j, said, _, chats = jarvis
    _turn(j, "status of polani mono")
    _turn(j, "Check whether V119 is live on both app and admin")
    assert chats == [] and "to: Check whether V119 is live on both app and admin." in said[-1]


def test_enter_this_as_a_prompt_with_the_project_and_task(jarvis):  # 17:28:21
    j, said, _, chats = jarvis
    _turn(j, 'Enter this as a prompt in my project polani mono in Claude code "check whether v119 is live on both app '
             'and admin"')
    assert chats == []
    assert said[-1].startswith("In Polani mono, shall I continue your latest chat")   # then which chat, as always
    assert j.pending["kind"] == "cc_chat" and j.pending["task"] == "check whether v119 is live on both app and admin"


def test_give_prompt_or_do_it_yourself_brings_back_the_unsent_task(jarvis):  # 17:28:47, 17:29:06
    j, said, _, chats = jarvis
    _turn(j, "status of polani mono")
    _turn(j, "Give prompt there to check whether v119 is live on both app and admin")
    _turn(j, "hmm let me think")         # the read-back is dropped for something else
    for again in ("Give prompt", "Do it yourself in my claude"):
        _turn(j, again)
        assert said[-1].startswith("I'll ask Claude Code in Polani mono to: check whether v119 is live")
        assert j.pending["kind"] == "claude_send"
        j.pending = None


def test_give_prompt_with_nothing_known_asks_for_the_task_then_the_project(jarvis):
    j, said, _, _ = jarvis
    _turn(j, "Give prompt")
    assert said[-1] == "What would you like Claude Code to do?" and j.pending["kind"] == "claude_task_new"
    _turn(j, "add a dark mode")
    assert said[-1].startswith("Which project should Claude Code work in, sir?")
    assert j.pending["kind"] == "cc_project" and j.pending["task"] == "add a dark mode"


def test_whatsapp_replies_carry_no_fillers(jarvis):  # 17:25:06 "Just a moment, sir. I'm just a program..."
    j, _, _, _ = jarvis
    j._speech_q, j._remote_said = asyncio.Queue(), []
    j._queue_speech("Just a moment, sir.", filler=True)
    j._queue_speech("Very well, thank you.")
    assert j._remote_said == ["Very well, thank you."]


def test_hi_on_whatsapp_is_a_greeting(jarvis):  # 17:24:44 "Loud and clear, sir. Go ahead."
    j, said, _, _ = jarvis
    _turn(j, "Hi")
    assert said[-1] == "Hello, sir. What can I do for you?"
    _turn(j, "can you hear me")
    assert said[-1].startswith("Loud and clear")


@pytest.mark.parametrize("said,tool", [("Listen to my plays.", "ack"), ("Listen to me please", "ack"),
                                       ("listen to arijit singh", "play_youtube"),
                                       ("listen to my liked songs", "play_spotify_liked")])
def test_misheard_listen_to_me_is_not_a_song(said, tool):  # 17:35:31 played "my plays" on YouTube
    assert parse(said).tool == tool
