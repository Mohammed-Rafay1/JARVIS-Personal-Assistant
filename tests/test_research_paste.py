"""10 Nov 15:01, "Research on Adolf Hitler on ChatGPT": ChatGPT read the clipboard late, after Jarvis had already put
the user's own clipboard back, and the user's copied skills list was sent instead of the question."""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.tools import ai_chat  # noqa: E402

QUESTION = ai_chat.research_prompt("adolf hitler")
SKILLS = "• Large Language Models (LLMs) • Agentic AI • AI Product Development"


@pytest.mark.parametrize("have,state", [
    (QUESTION, "ok"), ("Ask ChatGPT", "empty"), ("", "empty"), (SKILLS, "wrong"), (None, "unknown"),
])
def test_what_is_in_the_box(have, state):
    assert ai_chat.pasted(have, QUESTION, ("ask chatgpt",)) == state


class _Clipboard(types.ModuleType):
    CF_UNICODETEXT = 13

    def __init__(self, text):
        super().__init__("win32clipboard")
        self.data = text

    def OpenClipboard(self):
        pass

    def CloseClipboard(self):
        pass

    def EmptyClipboard(self):
        self.data = None

    def IsClipboardFormatAvailable(self, fmt):
        return self.data is not None

    def GetClipboardData(self, fmt):
        return self.data

    def SetClipboardData(self, fmt, text):
        self.data = text


class _SlowBox:
    """Like ChatGPT right after a new chat: a Ctrl+V is only handled a moment later, with whatever the
    clipboard holds by then."""

    def __init__(self, cb, slow_reads=3):
        self.cb, self.value, self.queued, self.slow = cb, "Ask ChatGPT", False, slow_reads

    def set_focus(self):
        pass

    @property
    def iface_value(self):
        if self.queued:
            self.slow -= 1
            if self.slow <= 0:
                self.value, self.queued = (self.value if self.value != "Ask ChatGPT" else "") + self.cb.data, False
        return types.SimpleNamespace(CurrentValue=self.value)


@pytest.fixture
def setup(monkeypatch):
    cb = _Clipboard(SKILLS)                       # the user had copied the skills list
    monkeypatch.setitem(sys.modules, "win32clipboard", cb)
    box = _SlowBox(cb)
    pressed = []

    def keys(*vks):
        pressed.append(vks)
        if vks == (0x11, 0x56):
            box.queued = True
        if vks == (0x2E,):
            box.value = "Ask ChatGPT"
    monkeypatch.setattr(ai_chat, "_keys", keys)
    monkeypatch.setattr(ai_chat, "_bring_to_front", lambda *a, **k: None)
    monkeypatch.setattr(ai_chat, "_is_front", lambda w: True)
    monkeypatch.setattr(ai_chat, "_has_focus", lambda e: True)
    monkeypatch.setattr(ai_chat.time, "sleep", lambda s: None)
    return cb, box, pressed


def test_a_slow_paste_still_gets_the_question_and_the_clipboard_comes_back(setup):
    cb, box, _ = setup
    ai_chat._paste(None, box, QUESTION, ("ask chatgpt",))
    assert box.value == QUESTION                  # not the skills list
    assert cb.data == SKILLS                      # the user's clipboard is put back afterwards


def test_wrong_text_is_cleared_and_never_left_to_send(setup, monkeypatch):
    cb, box, pressed = setup
    box.value = SKILLS                            # something else got into the box
    ai_chat._paste(None, box, QUESTION, ("ask chatgpt",))
    assert (0x11, 0x41) in pressed and box.value == QUESTION


def test_if_it_never_pastes_nothing_is_sent(setup, monkeypatch):
    cb, box, _ = setup
    box.slow = 10 ** 9                            # the paste never lands
    monkeypatch.setattr(ai_chat.time, "monotonic", iter(range(0, 10 ** 6, 2)).__next__)
    with pytest.raises(ai_chat.AppError):
        ai_chat._paste(None, box, QUESTION, ("ask chatgpt",))
    assert cb.data == SKILLS
