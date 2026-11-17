"""Talking like a person (17 Nov): "hmm" while you talk, "let me think" while it works, "how long?" answered
without dropping the work."""
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.app import _NUDGES, _STATUS_Q, Jarvis  # noqa: E402
from jarvis.audio.vad import Endpointer  # noqa: E402


def test_status_questions():
    for said in ("how long", "how much longer", "how much time will it take", "are you still there",
                 "are you still thinking", "what's taking so long", "any update", "is it done", "hello",
                 "what are you doing", "jarvis", "still thinking"):
        assert _STATUS_Q.search(said), said
    for said in ("open spotify", "what's the weather in lahore", "send an email to sara", "play music"):
        assert not _STATUS_Q.search(said), said


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
    assert all(lines for lines in _NUDGES)


def test_progress_line_says_what_and_how_long():
    j = _j(_turn_t0=time.monotonic() - 30, _doing="researching that")
    line = j._progress_line()
    assert "researching that" in line and "30 seconds" in line and "minute" in line
    for _ in range(10):  # the wording is picked at random: every variant must fit
        short = _j(_turn_t0=time.monotonic() - 3)._progress_line()
        assert "few more seconds" in short or "Nearly" in short


def _run_while_working(text):
    async def go():
        j = _j()
        said, spoken = [], []
        j.ui = type("UI", (), {"send": lambda s, m: None})()
        j._queue_speech = lambda s, filler=False: spoken.append(s)

        async def wait():
            return None

        async def say(text, then_idle=True, follow=False):
            said.append(text)

        j._wait_speech, j.say, j.mode = wait, say, "capture"
        j._set_state = lambda v, **k: setattr(j, "state", v)
        bg = asyncio.create_task(asyncio.sleep(5))
        handled = await j._while_working(bg, text)
        await asyncio.sleep(0)
        out = (handled, bg.cancelled() or bg.done(), spoken, said, j.task is bg)
        bg.cancel()
        return out
    return asyncio.run(go())


def test_how_long_keeps_working():
    handled, stopped, spoken, said, resumed = _run_while_working("How much longer?")
    assert handled and not stopped and resumed and spoken and "sir" in spoken[0]


def test_stop_drops_the_work():
    handled, stopped, spoken, said, _ = _run_while_working("never mind")
    assert handled and stopped and "stopped" in said[0]


def test_a_new_request_replaces_the_work():
    handled, stopped, spoken, said, _ = _run_while_working("open spotify")
    assert not handled and stopped


def test_endpointer_hold_freezes_the_pause():
    ep = Endpointer(silence_ms=600)
    loud = (np.sin(np.arange(1280) / 3) * 8000).astype(np.int16)
    for _ in range(40):
        ep.feed(loud)
    for _ in range(4):
        ep.feed(np.zeros(1280, np.int16))
    run = ep.silent_run
    for _ in range(10):        # Jarvis's "uh-huh" playing: kept, not judged
        ep.hold(np.zeros(1280, np.int16))
    assert ep.silent_run == run and ep.feed(np.zeros(1280, np.int16)) == "speaking"
