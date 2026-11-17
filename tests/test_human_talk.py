"""'Let me think' while a request takes a while (17 Nov). The "hmm" while you talk and the answer to "how long?"
while it worked were tried the same day and taken out again: they didn't sound right."""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.app import _NUDGES, Jarvis  # noqa: E402


def _j(**kw):
    j = Jarvis.__new__(Jarvis)
    j.cfg = {"address_as": "sir", "call": {"think_aloud": True}, "listening": {}}
    j.in_call, j.speak_aloud, j.state = True, True, "thinking"
    j._answering, j._remote_said, j._recognising = False, None, False
    j._nudges, j._turn_t0, j._last_spoke, j._doing = 0, time.monotonic() - 4, 0.0, "thinking about your question"
    j.player = type("P", (), {"busy": False})()
    j._speech_q = asyncio.Queue()
    j.task = type("T", (), {"done": lambda s: False})()
    for k, v in kw.items():
        setattr(j, k, v)
    return j


def test_let_me_think_comes_after_a_few_quiet_seconds():
    now = time.monotonic()
    assert _j()._narration_due(now)                                    # 4 s of silence while thinking
    assert not _j(_turn_t0=now - 2)._narration_due(now)                # too soon
    assert not _j(_answering=True)._narration_due(now)                 # the answer has started
    assert not _j(_recognising=True)._narration_due(now)               # still hearing you: wait longer
    assert _j(_recognising=True, _turn_t0=now - 7)._narration_due(now)
    assert not _j(in_call=False)._narration_due(now)
    assert not _j(_nudges=1, _turn_t0=now - 20, _last_spoke=now - 5)._narration_due(now)  # the next one waits 8 s
    assert _j(_nudges=1, _turn_t0=now - 20, _last_spoke=now - 9)._narration_due(now)


def test_no_hmm_in_the_lines():
    assert all(lines for lines in _NUDGES)
    assert not any(line.lower().startswith(("hmm", "mm", "uh")) for lines in _NUDGES for line in lines)
