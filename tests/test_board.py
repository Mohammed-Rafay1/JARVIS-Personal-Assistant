"""The board: today's plan from what you say, Jarvis doing its share, ticking things off, reminders."""
import asyncio
import datetime as dt
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.services import board as B  # noqa: E402
from jarvis.services.briefing import Board  # noqa: E402
from jarvis.tools import CTX  # noqa: E402
from jarvis.tools.base import Result  # noqa: E402


# ------------------------------------------------------------------------------------------- words
@pytest.mark.parametrize("said,items", [
    ("call Ali, finish the report and email Sara about the meeting",
     ["Call Ali", "Finish the report", "Email Sara about the meeting"]),
    ("Okay so today I need to finish the Agreeofy summary, then call the bank. Also send a WhatsApp to Ahmed that "
     "I'm late", ["Finish the Agreeofy summary", "Call the bank", "Send a WhatsApp to Ahmed that I'm late"]),
    ("1. Prepare slides 2. Meeting with Usman at 3 3. Pay electricity bill",
     ["Prepare slides", "Meeting with Usman at 3", "Pay electricity bill"]),
    ("I have to submit my CV and I want to research portfolio sites on chatgpt and buy groceries",
     ["Submit my CV", "Research portfolio sites on chatgpt", "Buy groceries"]),
])
def test_a_plan_becomes_items(said, items):
    assert B.split_plan(said) == items


def test_jarvis_takes_what_it_can_do():
    p = lambda s: parse(s)  # noqa: E731
    assert B.classify("Research AI agents on ChatGPT", p)[:3] == ("jarvis", "auto", "ai_research")
    assert B.classify("Email Sara about the meeting", p)[:3] == ("jarvis", "guided", "draft_email")
    assert B.classify("Send a WhatsApp to Ahmed that I'm late", p)[:2] == ("jarvis", "guided")
    assert B.classify("Call Ali", p)[0] == "you"
    assert B.classify("Go to the gym", p)[0] == "you"


@pytest.mark.parametrize("said,what,when", [
    ("remind me at 5 to call ali", "call ali", (13, 17, 0)),
    ("remind me to stretch in 20 minutes", "stretch", (13, 10, 20)),
    ("remind me tomorrow at 9 am to pay the bill", "pay the bill", (14, 9, 0)),
    ("remind me to pay the bill tomorrow at 9", "pay the bill", (14, 9, 0)),
    ("remind me at 7 this evening to call mom", "call mom", (13, 19, 0)),
    ("remind me in an hour to drink water", "drink water", (13, 11, 0)),
    ("set a reminder for 4 pm to leave for the airport", "leave for the airport", (13, 16, 0)),
    ("remind me at noon to eat", "eat", (13, 12, 0)),
])
def test_reminder_times(said, what, when):
    got = B.parse_reminder(said, dt.datetime(2026, 11, 13, 10, 0))
    assert got and got[0] == what and (got[1].day, got[1].hour, got[1].minute) == when


def test_no_time_is_not_a_reminder():
    assert B.parse_reminder("remind me to call ali", dt.datetime(2026, 11, 13, 10, 0)) is None


ITEMS = [{"text": "Call Ali"}, {"text": "Finish the report"}, {"text": "Go to the gym"},
         {"text": "Email Sara about the meeting"}]


@pytest.mark.parametrize("said,meant", [
    ("i called ali", "Call Ali"), ("finished the report", "Finish the report"), ("i went to the gym", "Go to the gym"),
    ("sent sara the email", "Email Sara about the meeting"), ("2", "Finish the report"), ("the last one", None),
])
def test_what_you_did_matches_the_item(said, meant):
    it, score, clear = B.best_match(said, ITEMS)
    if meant is None:
        return
    assert it["text"] == meant and score >= 74 and clear


@pytest.mark.parametrize("said,done", [
    ("i called ali", True), ("i've sent the email to sara", True), ("just finished the slides", True),
    ("the report is done", True), ("i think we should go", False), ("what's the weather", False),
])
def test_done_statements(said, done):
    assert B.done_statement(said) == done


@pytest.mark.parametrize("said,tool", [
    ("show my board", "board_show"), ("what's my status", "board_show"), ("close the board", "board_hide"),
    ("brief me", "briefing"), ("good morning jarvis", "briefing"), ("what's on my to do list", "todo_list"),
    ("what do I have to do today", "todo_list"), ("plan my day", "plan_day"),
    ("today I need to call Ali and email Sara", "plan_items"), ("add buy milk to my list", "todo_add"),
    ("mark call ali as done", "todo_done"), ("I've finished the report", "todo_done"),
    ("remove gym from my list", "todo_remove"), ("start your tasks", "board_run"),
    ("remind me at 5 to email Sara", "reminder"), ("what are my reminders", "reminder_list"),
    ("start a focus session for 45 minutes", "focus_start"), ("stop focus mode", "focus_stop"),
    # unchanged
    ("check my email", "check_email"), ("what's the status of my projects", "claude_projects"), ("undo", "office_undo"),
    ("send an email to sara about the meeting", "draft_email"), ("is the task done", "claude_task_status"),
])
def test_board_commands(said, tool):
    i = parse(said)
    assert i is not None and i.tool == tool


# ------------------------------------------------------------------------------------------- the store
def test_unfinished_items_roll_over_to_the_next_day(tmp_path):
    day = [dt.date(2026, 11, 12)]
    s = B.Store(tmp_path / "b.json", today=lambda: day[0])
    s.add("Call Ali")
    done = s.add("Gym")
    s.set(done, status="done")
    day[0] = dt.date(2026, 11, 13)
    s2 = B.Store(tmp_path / "b.json", today=lambda: day[0])
    assert [(i["text"], i["carried"]) for i in s2.items] == [("Call Ali", True)]


def test_the_same_item_said_twice_is_one(tmp_path):
    s = B.Store(tmp_path / "b.json")
    s.add("Call Ali")
    s.add("call ali.")
    assert len(s.items) == 1


# ------------------------------------------------------------------------------------------- with Jarvis
class _UI:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


@pytest.fixture
def jarvis(monkeypatch, tmp_path):
    from jarvis import app as app_mod

    monkeypatch.setattr(CTX, "gmail", None)
    CTX.cfg = {"email": {"enabled": False}}
    ui = _UI()
    j = app_mod.Jarvis({"address_as": "sir", "email": {"enabled": False}, "music": {"default_service": "youtube"},
                        "apps": {"aliases": {}}, "websites": {}, "board": {"enabled": True}}, ui)
    b = Board(j, store=B.Store(tmp_path / "board.json"))

    async def nothing(*a, **k):
        return []

    async def no_weather():
        return None
    monkeypatch.setattr(b, "_projects", nothing)
    monkeypatch.setattr(b, "_weather", no_weather)
    j.board = b
    j.state = "idle"
    j.player = type("P", (), {"play": lambda self, *a: None, "stop": lambda self: None})()
    from jarvis.tools import whatsapp

    monkeypatch.setattr(whatsapp, "screen_locked", lambda: False)
    said = []

    async def say(text, then_idle=True, follow=False):
        said.append(text)
    j.say = say
    return j, b, said, ui


def test_morning_answer_becomes_the_list_and_jarvis_offers_its_part(jarvis):
    j, b, said, ui = jarvis
    j.pending = {"kind": "plan_today"}
    asyncio.run(j.handle_text("Call Ali, finish the report and research AI agents on ChatGPT", typed=True))
    texts = [(i["text"], i["owner"]) for i in b.store.items]
    assert texts == [("Call Ali", "you"), ("Finish the report", "you"), ("Research AI agents on ChatGPT", "jarvis")]
    assert "research AI agents on ChatGPT myself" in said[-1] and j.pending["kind"] == "board_start"
    assert any(m.get("type") == "board" and m.get("action") == "show" for m in ui.sent)  # the board opened


def test_one_spoken_plan_is_not_run_as_commands(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    ran = []

    async def run_tool(name, args):
        ran.append(name)
        return Result("ran")
    monkeypatch.setattr(j, "run_tool", run_tool)
    asyncio.run(j.handle_text("Today I need to call Ali and email Sara about the budget", typed=True))
    assert ran == [] and [i["text"] for i in b.store.items] == ["Call Ali", "Email Sara about the budget"]


def test_telling_jarvis_it_is_done_ticks_it_off(jarvis):
    j, b, said, _ = jarvis
    b.store.add("Call Ali")
    b.store.add("Finish the report")
    asyncio.run(j.handle_text("I called Ali", typed=True))
    assert b.store.items[0]["status"] == "done" and "1 to go" in said[-1]
    asyncio.run(j.handle_text("the report is done", typed=True))
    assert b.store.items[1]["status"] == "done" and "everything" in said[-1]


def test_jarvis_runs_its_own_items_and_reports(jarvis, monkeypatch):
    j, b, said, ui = jarvis
    it = b._add("Research AI agents on ChatGPT")
    calls = []

    async def run_tool(name, args):
        calls.append((name, args))
        return Result("AI agents are programs that act on their own. They plan and use tools.")
    monkeypatch.setattr(j, "run_tool", run_tool)

    async def go():
        await j.board_run()
        await b._autos
    asyncio.run(go())
    assert calls == [("ai_research", {"query": "ai agents", "services": "chatgpt"})]
    assert b.store.get(it["id"])["status"] == "done" and "AI agents are programs" in b.store.get(it["id"])["result"]
    assert any("is done" in s for s in said)


def test_a_sent_email_ticks_off_the_item_and_offers_the_next(jarvis):
    j, b, said, _ = jarvis
    first = b._add("Email Sara about the meeting")
    nxt = b._add("WhatsApp Ahmed that I'm late")
    b.active = first["id"]
    line = asyncio.run(b.finished("email", "Sara"))
    assert b.store.get(first["id"])["status"] == "done"
    assert "whatsApp Ahmed" in line or "whatsapp ahmed" in line.lower()
    assert j.pending == {"kind": "board_guided", "id": nxt["id"]}


def test_reminders_fire_once(jarvis, monkeypatch):
    j, b, said, ui = jarvis
    from jarvis.tools import whatsapp

    monkeypatch.setattr(whatsapp, "screen_locked", lambda: False)
    announced = []

    async def announce(line, urgent=False):
        announced.append((line, urgent))
    monkeypatch.setattr(j, "board_announce", announce)
    b.cfg["enabled"] = False  # no morning briefing in this test
    b.store.remind("Call Ali", time.time() - 1)
    asyncio.run(b._tick_clock())
    asyncio.run(b._tick_clock())
    assert announced == [("Sir, a reminder: Call Ali.", True)]


def test_plan_question_lets_other_commands_through(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    j.pending = {"kind": "plan_today"}
    seen = []

    async def run_tool(name, args):
        seen.append(name)
        return Result("Opening Chrome.", icon="app")
    monkeypatch.setattr(j, "run_tool", run_tool)
    monkeypatch.setattr(j, "_resolve_open", lambda target, verb: __import__("jarvis.brain.intents", fromlist=["Intent"])
                        .Intent("open_app", {"name": target}))
    asyncio.run(j.handle_text("open chrome", typed=True))
    assert seen == ["open_app"] and b.store.items == [] and j.pending == {"kind": "plan_today"}


# ------------------------------------------------------------------------------------------- the walkthrough
def _walk_setup(jarvis, monkeypatch):
    j, b, said, ui = jarvis

    class Mail:
        me = "me@gmail.com"

        def search(self, q, n):
            return [{"id": "1", "from_name": "Murtaza Khalid", "subject": "Availability", "labels": ["UNREAD"],
                     "ts": 0, "thread": ""}]

        def needs_reply(self):
            return [{"id": "1", "from_name": "Murtaza Khalid", "subject": "Availability for a meeting"},
                    {"id": "2", "from_name": "Sara Ahmed", "subject": "Budget"}]
    monkeypatch.setattr(CTX, "gmail", Mail())
    CTX.cfg = {"email": {"enabled": True}, "claude_code": {"allow_tasks": True}}

    async def projects(*a, **k):
        return [{"name": "Solvix", "cwd": "C:/x/Solvix", "title": "Login page", "ago": "2 h ago",
                 "last": "make the sign-in button blue", "running": False, "task": ""}]
    monkeypatch.setattr(b, "_projects", projects)
    from jarvis.tools import claude_code as cc

    monkeypatch.setattr(cc, "find_project", lambda name: type("P", (), {"session": Path("abc.jsonl")})())
    return j, b, said, ui


def test_board_view_is_explained_and_offers_replies(jarvis, monkeypatch):
    j, b, said, ui = _walk_setup(jarvis, monkeypatch)
    asyncio.run(j.handle_text("show my board", typed=True))
    s = said[-1]
    assert "2 emails need your reply" in s and "Murtaza Khalid" in s and "Solvix" in s
    assert s.endswith("Shall I help you reply to them?") and j.pending["kind"] == "board_walk"


def test_no_to_replies_moves_on_to_the_claude_prompt(jarvis, monkeypatch):
    j, b, said, ui = _walk_setup(jarvis, monkeypatch)
    asyncio.run(j.handle_text("show my board", typed=True))
    asyncio.run(j.handle_text("no", typed=True))
    assert said[-1] == "Okay. Shall I give Claude Code the next prompt in Solvix?"
    asyncio.run(j.handle_text("yes", typed=True))
    assert said[-1] == "What should Claude Code do in Solvix?" and j.pending["kind"] == "claude_task"
    assert j.pending["session"] == "abc"


def test_yes_to_replies_asks_which_one(jarvis, monkeypatch):
    j, b, said, ui = _walk_setup(jarvis, monkeypatch)
    asyncio.run(j.handle_text("show my board", typed=True))
    asyncio.run(j.handle_text("yes", typed=True))
    assert said[-1].startswith("Which one first? 1, Murtaza Khalid") and j.pending["kind"] == "reply_list"


# ------------------------------------------------------------------------------------------- 14 Nov call log
@pytest.mark.parametrize("said,tool,text", [
    ("can add in to do list to deploy the new version of Polani mono project in my planner in jarvis overview board",
     "todo_add", "deploy the new version of polani mono project"),
    ("can you add call ali in my planner", "todo_add", "call ali"),
    ("show me in board", "board_show", None), ("show it on the board", "board_show", None),
    ("Please tell me my today's task", "todo_list", None), ("And, my task for tomorrow", "todo_ahead", None),
    ("what do I have planned for monday", "todo_ahead", None),
])
def test_call_log_phrases(said, tool, text):
    i = parse(said)
    assert i is not None and i.tool == tool and (text is None or i.args["text"] == text)


def test_items_can_be_for_tomorrow_and_arrive_on_the_day(tmp_path):
    day = [dt.date(2026, 11, 14)]
    s = B.Store(tmp_path / "b.json", today=lambda: day[0])
    s.add("Call Ali")
    when, text = B.when_day("deploy Polani mono tomorrow", day[0])
    s.add(text, on=when)
    assert [i["text"] for i in s.items] == ["Call Ali"]
    assert [(d.isoformat(), [i["text"] for i in its]) for d, its in s.ahead()] == [("2026-11-15", ["Deploy Polani mono"])]
    day[0] = dt.date(2026, 11, 15)
    s2 = B.Store(tmp_path / "b.json", today=lambda: day[0])
    assert sorted(i["text"] for i in s2.items) == ["Call Ali", "Deploy Polani mono"]   # planned + carried over


def test_adding_for_tomorrow_says_so(jarvis):
    j, b, said, _ = jarvis
    asyncio.run(j.handle_text("add deploy Polani mono for tomorrow to my list", typed=True))
    assert said[-1] == "Added Deploy Polani mono to the list for tomorrow." and b.store.items == []
    asyncio.run(j.handle_text("what's my plan for tomorrow", typed=True))
    assert said[-1] == "For tomorrow: deploy Polani mono."


def test_the_model_gets_the_planner_and_cannot_claim_it_added(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    b.store.add("Meeting with CCL's CTO 3pm", on=dt.date.today() + dt.timedelta(days=2))
    seen = {}

    class LLM:
        model = "fake"

        async def loaded(self):
            return True

        async def available(self):
            return True

        async def chat(self, history, tools, max_tokens=200, context=""):
            seen["context"] = context
            for chunk in ["Your meeting with CCL's CTO is at 3pm. ", "I've added it to your planner as well. ", "Done."]:
                yield "text", chunk
    j.llm = LLM()
    j.cfg["llm"] = {}
    spoken = []
    monkeypatch.setattr(j, "_queue_speech", lambda s, filler=False: spoken.append(s))

    async def wait():
        return None
    monkeypatch.setattr(j, "_wait_speech", wait)
    asyncio.run(j.handle_text("when is my meeting with CCL's CTO?", typed=True))
    assert "Meeting with CCL's CTO 3pm" in seen["context"]
    assert not any("added it" in s for s in spoken) and any("say add" in s for s in spoken)


def test_awesome_gets_a_reply_not_a_shrug(jarvis):
    j, b, said, _ = jarvis
    j._heard_conf = -0.78
    asyncio.run(j.handle_text("Awesome."))
    assert said[-1] == "Glad you like it, sir."


# ------------------------------------------------------------------------------------------- 16 Nov: jobs and updates
def test_a_time_in_a_request_makes_a_job():
    now = dt.datetime(2026, 11, 16, 10, 0)
    rest, when = B.split_schedule("send an email to sara at 3 pm about the invoice", now)
    assert rest == "send an email to sara about the invoice" and when.hour == 15
    assert B.split_schedule("research ai agents on chatgpt at 4", now)[1].hour == 16
    assert B.split_schedule("remind me at 5 to call ali", now) is None
    assert B.split_schedule("what's the weather at 5 pm", now) is None          # a question
    assert B.split_schedule("email sara that the meeting is at 3 pm", now) is None  # the time is the message


def test_a_scheduled_job_moves_to_the_next_day_once(tmp_path):
    day = [dt.date(2026, 11, 15)]
    s = B.Store(tmp_path / "b.json", today=lambda: day[0])
    at = dt.datetime(2026, 11, 15, 15, 0).timestamp()   # a fixed time on that day, not the real clock
    s.schedule("Email Sara: Invoice", at, "send_email", {"draft_id": "d1"})
    day[0] = dt.date(2026, 11, 16)
    s2 = B.Store(tmp_path / "b.json", today=lambda: day[0])
    assert [i["status"] for i in s2.items] == ["scheduled"]
    assert len(s2.due_jobs(time.time())) == 1  # never twice


def test_mark_all_done(jarvis):
    j, b, said, _ = jarvis
    b.store.add("Call Ali")
    b.store.add("Finish the report")
    j.pending = {"kind": "plan_today"}
    asyncio.run(j.handle_text("I've done all my tasks, mark them", typed=True))
    assert all(i["status"] == "done" for i in b.store.items) and "ticked off" in said[-1]


def test_does_anything_need_me_says_only_what_needs_you(jarvis):
    j, b, said, _ = jarvis
    b.store.add("Call the bank")
    b._add("Research AI agents on ChatGPT")
    asyncio.run(j.handle_text("is there my need?", typed=True))
    assert "call the bank" in said[-1].lower() and "I'll take care of the rest" in said[-1]
    assert "research AI agents" in said[-1]


def test_what_did_i_miss(jarvis):
    j, b, said, _ = jarvis
    b.away_since = time.time() - 3600
    b.store.note("Done: Research AI agents")
    asyncio.run(j.handle_text("give me the update from my absence", typed=True))
    assert "I finished research AI agents" in said[-1]


def test_a_research_with_a_time_is_scheduled_and_runs_then(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    later = (dt.datetime.now() + dt.timedelta(hours=2)).replace(minute=0)
    if later.date() != dt.date.today():
        pytest.skip("too close to midnight")
    h = later.strftime("%I").lstrip("0") + (" pm" if later.hour >= 12 else " am")
    asyncio.run(j.handle_text(f"research ai agents on chatgpt at {h}", typed=True))
    it = b.store.items[-1]
    assert it["status"] == "scheduled" and "Scheduled" in said[-1]
    calls = []

    async def run_tool(name, args):
        calls.append(name)
        return Result("Done researching.")
    monkeypatch.setattr(j, "run_tool", run_tool)

    async def go():
        await b._run_jobs(it["at"] + 5)
        await b._autos
    asyncio.run(go())
    assert calls == ["ai_research"] and it["status"] == "done"


def test_an_email_with_a_time_is_scheduled_after_you_confirm(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    sent = []

    class G:
        me = "me@x.com"

        def send_draft(self, d):
            sent.append(d)
    monkeypatch.setattr(CTX, "gmail", G())
    at = time.time() + 7200
    j._sched_at = (at, time.monotonic())
    asyncio.run(j._deliver([Result("Here's the draft to Sara, subject Invoice. Shall I send it?",
                                   pending={"kind": "send_email", "draft_id": "d1", "to": "sara@x.com",
                                            "to_name": "Sara Khan", "subject": "Invoice", "body": "Hi"})]))
    assert "Shall I schedule it?" in said[-1] and j.pending["at"] == at
    asyncio.run(j.handle_text("yes", typed=True))
    assert sent == [] and "Scheduled" in said[-1]
    it = b.store.items[-1]
    assert it["status"] == "scheduled" and it["args"]["draft_id"] == "d1"
    asyncio.run(b._run_jobs(at + 5))
    assert sent == ["d1"] and it["status"] == "done"


def test_a_job_the_laptop_slept_through_is_asked_about(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    sent = []
    monkeypatch.setattr(CTX, "gmail", type("G", (), {"send_draft": lambda self, d: sent.append(d)})())
    it = b.store.schedule("Email Sara: Invoice", time.time() - 7200, "send_email",
                          {"draft_id": "d1", "to": "s@x.com", "to_name": "Sara", "subject": "Invoice"})
    asyncio.run(b._run_jobs(time.time()))
    assert sent == [] and "Shall I do it now?" in said[-1] and j.pending["kind"] == "job_now"
    asyncio.run(j.handle_text("yes", typed=True))
    assert sent == ["d1"] and b.store.get(it["id"])["status"] == "done"


def test_the_morning_update_is_spoken_once_when_you_open_the_laptop(jarvis, monkeypatch):
    j, b, said, _ = jarvis
    from jarvis.tools import whatsapp

    locked = [True]
    monkeypatch.setattr(whatsapp, "screen_locked", lambda: locked[0])
    real = dt.datetime

    class Morning(real):
        @classmethod
        def now(cls, tz=None):
            return real.combine(real.today().date(), dt.time(9, 30))
    monkeypatch.setattr(dt, "datetime", Morning)
    asyncio.run(b._tick_clock())  # locked at 9:30: not spoken yet
    assert not said and b.store.day()["spoken_morning"] is False
    locked[0] = False
    j.pending = None
    asyncio.run(b._tick_clock())  # opened: spoken
    assert said and "Good morning" in said[-1]
    n = len(said)
    j.pending = None
    asyncio.run(b._tick_clock())
    assert len(said) == n  # once a day


@pytest.mark.parametrize("said,tool", [
    ("i've done all my yesterday's task so please mark them", "todo_done_all"),
    ("mark my tasks done", "todo_done_all"),
    ("is there my need", "needs_me"),
    ("does anything need me", "needs_me"),
    ("give me the update from my impresence", "away_update"),
    ("what did i miss", "away_update"),
    ("can you give me a morning update", "briefing"),
    ("what is today's update of my emails claude code and my to do tasks", "briefing"),
])
def test_new_board_phrases(said, tool):
    from jarvis.brain.intents import normalize

    i = parse(normalize(said))
    assert i is not None and i.tool == tool


def test_away_and_needs_in_one_request(jarvis):
    j, b, said, _ = jarvis
    from jarvis.brain.intents import normalize

    i = parse(normalize("Jarvis give me a bit when I was away and also tells me if anything needs me."))
    assert i.tool == "away_update" and i.args["needs"]
    b.away_since = time.time() - 600
    b.store.add("Call the bank")
    asyncio.run(j.handle_text("give me an update of when i was away and also tell me if anything needs me", typed=True))
    assert "Since" in said[-1] and "needs you" in said[-1] and said[-1].lower().count("call the bank") == 1


@pytest.mark.parametrize("said,tool", [
    ("I'm going on vacations can u handle everything?", "needs_me"),
    ("There's some going on applications, can you handle everything, my emails and my cloud code projects?",
     "needs_me"),
    ("Jarvis i wasn't there so give me update about my emails and claude code projects and my todo tasks",
     "away_update"),
    ("give me an update on my emails", "briefing"),
    ("tell me the status of polani mono", "claude_project_status"),
])
def test_phrases_from_16_nov_whatsapp(said, tool):
    from jarvis.brain.intents import normalize

    i = parse(normalize(said))
    assert i is not None and i.tool == tool


@pytest.mark.parametrize("said,tool", [
    ("Jarvis gave me an update about everything.", "briefing"),
    ("Jarvis gave me a bit when I was away and also tells me if anything needs me.", "away_update"),
])
def test_give_me_heard_as_gave_me(said, tool):
    from jarvis.brain.intents import normalize

    i = parse(normalize(said))
    assert i is not None and i.tool == tool
