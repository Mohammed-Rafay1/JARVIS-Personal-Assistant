"""10 Nov, research on ChatGPT. 15:01: ChatGPT read the clipboard late and the user's copied skills list was
sent instead of the question. 15:36: the paste showed so late that it was pasted again (landed twice) and then
nothing was sent. Now the question is typed (no clipboard) in checked pieces, and sending is confirmed."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.tools import ai_chat  # noqa: E402

QUESTION = ai_chat.research_prompt("adolf hitler")
SKILLS = "• Large Language Models (LLMs) • Agentic AI • AI Product Development"
PH = ("ask chatgpt",)


@pytest.mark.parametrize("have,state", [
    (QUESTION, "ok"), ("Ask ChatGPT", "empty"), ("", "empty"), (SKILLS, "wrong"), (None, "unknown"),
    (QUESTION[:40], "partial"), (QUESTION + QUESTION, "wrong"), (QUESTION.replace('"', "“"), "ok"),
])
def test_what_is_in_the_box(have, state):
    assert ai_chat.pasted(have, QUESTION, PH) == state


def test_the_question_is_typed_on_one_line():
    assert ai_chat.one_line("a\nb  c\n") == "a b c"     # a typed newline would be Enter: half a question sent


class _Box:
    """ChatGPT's box: shows its placeholder when empty; `drop` = how many typing calls lose their keys."""

    def __init__(self, value="", drop=()):
        self.value, self.drop, self.calls, self.focused = value, set(drop), 0, True

    def set_focus(self):
        self.focused = True

    @property
    def iface_value(self):
        return types.SimpleNamespace(CurrentValue=self.value or "Ask ChatGPT")


@pytest.fixture
def chat(monkeypatch):
    box = _Box()
    sent = []

    def typed(text):
        box.calls += 1
        if box.calls not in box.drop:
            box.value += text

    def keys(*vks):
        if vks == (0x2E,):
            box.value = ""                      # Ctrl+A then Delete
        if vks == (0x0D,):
            sent.append(box.value)
            box.value = ""
    monkeypatch.setattr(ai_chat, "_type", typed)
    monkeypatch.setattr(ai_chat, "_keys", keys)
    monkeypatch.setattr(ai_chat, "_bring_to_front", lambda *a, **k: None)
    monkeypatch.setattr(ai_chat, "_is_front", lambda w: True)
    monkeypatch.setattr(ai_chat, "_has_focus", lambda e: e.focused)
    monkeypatch.setattr(ai_chat.time, "sleep", lambda s: None)
    clock = iter(range(0, 10 ** 7))
    monkeypatch.setattr(ai_chat.time, "monotonic", lambda: next(clock) * 0.1)
    return box, sent


def test_typed_once_exactly(chat):
    box, _ = chat
    ai_chat._paste(None, box, QUESTION, PH)
    assert box.value == QUESTION


def test_dropped_keys_are_typed_again_without_doubling(chat):
    box, _ = chat
    box.drop = {2, 5}                           # the burst after the first letter, and a later piece, are lost
    ai_chat._paste(None, box, QUESTION, PH)
    assert box.value == QUESTION


def test_a_leftover_draft_is_cleared_first(chat):
    box, _ = chat
    box.value = QUESTION + QUESTION             # what 15:36 left behind
    ai_chat._paste(None, box, QUESTION, PH)
    assert box.value == QUESTION


def test_never_types_into_another_window(chat, monkeypatch):
    box, _ = chat
    monkeypatch.setattr(ai_chat, "_is_front", lambda w: False)
    with pytest.raises(ai_chat.AppError):
        ai_chat._paste(None, box, QUESTION, PH)
    assert box.value == ""


def test_a_box_that_takes_nothing_is_given_up_on(chat):
    box, _ = chat
    box.drop = set(range(1, 100))
    with pytest.raises(ai_chat.AppError):
        ai_chat._paste(None, box, QUESTION, PH)
    assert box.value == ""


def test_send_is_confirmed_and_pressed_again_if_ignored(chat, monkeypatch):
    box, sent = chat
    box.value = QUESTION
    presses = []
    monkeypatch.setattr(ai_chat, "_send_buttons", lambda w: [object()])

    def press(b):                               # the first press is ignored (app still busy), the second sends
        presses.append(b)
        if len(presses) == 2:
            sent.append(box.value)
            box.value = ""
    monkeypatch.setattr(ai_chat, "_press", press)
    ai_chat._send(None, box, QUESTION, PH)
    assert len(presses) == 2 and sent == [QUESTION] and box.value == ""


def test_send_that_never_happens_is_reported(chat, monkeypatch):
    box, _ = chat
    box.value = QUESTION
    monkeypatch.setattr(ai_chat, "_send_buttons", lambda w: [object()])
    monkeypatch.setattr(ai_chat, "_press", lambda b: None)
    with pytest.raises(ai_chat.AppError):
        ai_chat._send(None, box, QUESTION, PH)
