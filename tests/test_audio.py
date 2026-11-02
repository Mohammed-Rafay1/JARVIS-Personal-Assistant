import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.audio.vad import Endpointer  # noqa: E402

FRAME = np.zeros(1280, dtype=np.int16)


def _run(ep: Endpointer, pattern: str) -> list[str]:
    """pattern: one char per 80 ms frame, 's' = speech, '.' = silence."""
    out = []
    for ch in pattern:
        ep._is_speech = lambda f, s=(ch == "s"): s
        out.append(ep.feed(FRAME))
    return out


def test_short_word_then_pause_keeps_listening():
    # "Jarvis," (0.4 s) then a 0.7 s pause: not the end of the sentence yet
    ep = Endpointer(silence_ms=600, short_silence_ms=1000)
    got = _run(ep, "sssss" + "." * 9)
    assert "done" not in got


def test_short_word_ends_after_long_pause():
    ep = Endpointer(silence_ms=600, short_silence_ms=1000)
    got = _run(ep, "sssss" + "." * 13)
    assert got[-1] == "done"


def test_full_sentence_ends_after_normal_pause():
    ep = Endpointer(silence_ms=600, short_silence_ms=1000)
    got = _run(ep, "s" * 20 + "." * 8)
    assert got[-1] == "done"


def test_short_silence_off_by_default():
    ep = Endpointer(silence_ms=600)
    got = _run(ep, "sssss" + "." * 8)
    assert got[-1] == "done"
