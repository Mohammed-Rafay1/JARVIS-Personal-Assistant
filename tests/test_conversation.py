"""Jarvis holds a conversation: questions reach the model, "What?" repeats, a friendly persona (5 Nov)."""
import asyncio
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis import app as app_mod  # noqa: E402
from jarvis.brain.intents import normalize  # noqa: E402
from jarvis.brain.llm import system_prompt  # noqa: E402


@pytest.mark.parametrize("said", [
    "How can you open any app on my system?",                     # the questions from the 5 Nov test call
    "No, I am asking if you are just a particular intelligence and don't have any hands or body, how will you open an app?",
    "Should I start learning Python or JavaScript first?",
    "What do you think about my project?",
    "Can you explain how you open files?",
    "I need some advice on my interview tomorrow",
    "Tell me how you play music",
])
def test_questions_about_actions_are_conversation(said):
    assert app_mod._is_conversation(said, normalize(said))


@pytest.mark.parametrize("said", ["open the spotty file", "Can you open the report?", "please play something",
                                  "launch the thing"])
def test_short_requests_are_still_commands(said):
    assert not app_mod._is_conversation(said, normalize(said))


class _UI:
    def __init__(self):
        self.sent = []

    def send(self, m):
        self.sent.append(m)


class _LLM:
    reply = "I don't need hands, sir. I'm software, so I ask Windows to open apps for you, just like a click would."

    def __init__(self):
        self.calls = []

    async def available(self):
        return True

    async def loaded(self):
        return True

    async def chat(self, history, tools, max_tokens=200):
        self.calls.append({"history": list(history), "tools": tools, "max_tokens": max_tokens})
        for word in self.reply.split(" "):
            yield "text", word + " "


@pytest.fixture
def jarvis():
    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "llm": {"use_tools": False}, "listening": {}}, _UI())
    j.state, j.llm, j._speech_q = "idle", _LLM(), asyncio.Queue()
    said = []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def nothing():
        pass

    class Player:
        busy = False

        def play(self, *a):
            pass
    j.say, j._wait_speech, j._go_idle, j.player = say, nothing, lambda: None, Player()
    return j, said


def _spoken(j):
    return " ".join(m["text"] for m in j.ui.sent if m.get("type") == "reply" and m.get("text"))


def test_a_question_about_opening_apps_gets_a_real_answer(jarvis):
    j, said = jarvis
    asyncio.run(j.handle_text("How can you open any app on my system?", typed=True))
    assert len(j.llm.calls) == 1
    call = j.llm.calls[0]
    assert call["tools"] == []                  # a question, so no tool list to confuse the model
    assert call["max_tokens"] >= 300            # room for a proper answer
    assert "I ask Windows to open apps" in _spoken(j)
    assert not any("didn't get that command" in s for s in said)


def test_the_conversation_is_remembered(jarvis):
    j, _ = jarvis
    asyncio.run(j.handle_text("Are you alive?", typed=True))
    asyncio.run(j.handle_text("So how are you able to speak?", typed=True))
    history = j.llm.calls[-1]["history"]
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[0]["content"] == "Are you alive?"


def test_a_misheard_short_command_still_asks_again(jarvis):
    j, said = jarvis
    asyncio.run(j.handle_text("the file thing on the left", typed=True))
    assert j.llm.calls == [] and "didn't get that command" in said[-1]


@pytest.mark.parametrize("again", ["What?", "Pardon?", "Say that again.", "Can you say that again?",
                                   "I didn't hear you."])
def test_what_repeats_the_last_reply(jarvis, again):
    j, said = jarvis
    j._queue_speech("One moment.", filler=True)
    j._queue_speech("Paris is the capital of France.")
    asyncio.run(j.handle_text(again, typed=True))
    assert said[-1] == "Paris is the capital of France."   # the filler isn't repeated
    assert j.llm.calls == []


def test_what_with_nothing_said_yet_goes_on_as_normal(jarvis):
    j, said = jarvis
    asyncio.run(j.handle_text("What?", typed=True))
    assert j.llm.calls  # nothing to repeat: an ordinary (if short) question


def test_persona_is_friendly_knows_itself_and_stays_cacheable():
    p = system_prompt("sir")
    for phrase in ("warm, friendly", "advice", "Remember what was said", "You don't need hands",
                   "never recite them", "Never make up live facts"):
        assert phrase in p
    assert p == system_prompt("sir")  # identical on every call, so Ollama reuses the cached prefix


def test_lists_are_spoken_as_sentences_and_long_answers_stop(jarvis):
    j, _ = jarvis
    j.llm.reply = ("Here are some tips:\n\n1. **Practice**: Rehearse it twice. 2. **Sleep**: Go to bed early. "
                   "3. **Breathe**: Slow breaths help. 4. **Arrive early**: Ten minutes is plenty. "
                   "5. **Smile**: It relaxes you. 6. **Extra**: Far too much.")
    asyncio.run(j.handle_text("I have a big presentation tomorrow and I'm nervous. Any advice?", typed=True))
    out = _spoken(j)
    assert out.startswith("Here are some tips: Practice: Rehearse it twice. Sleep: Go to bed early.")
    assert "**" not in out and "2." not in out
    assert "Smile" not in out  # five sentences, then Jarvis stops


def test_filler_offers_are_not_said(jarvis):
    j, _ = jarvis
    j.llm.reply = "Paris is the capital of France. How can I assist you today, sir?"
    asyncio.run(j.handle_text("What's the capital of France?", typed=True))
    assert _spoken(j) == "Paris is the capital of France."


def test_old_exchanges_are_dropped_in_one_go(jarvis):
    j, _ = jarvis
    for i in range(6):
        asyncio.run(j.handle_text(f"Question number {i} about life?", typed=True))
    sizes = [len(c["history"]) for c in j.llm.calls]
    assert sizes == [1, 3, 5, 7, 9, 5]   # grows, then trimmed to the last two exchanges at once


def test_examples_teach_the_style_and_never_change():
    from jarvis.brain.llm import style_examples

    ex = style_examples("sir")
    assert [m["role"] for m in ex[:2]] == ["user", "assistant"]
    assert any("ask Windows to launch the app" in m["content"] for m in ex)
    assert not any(re.search(r"assist you|\n\s*1\.", m["content"]) for m in ex)
    assert ex == style_examples("sir")
