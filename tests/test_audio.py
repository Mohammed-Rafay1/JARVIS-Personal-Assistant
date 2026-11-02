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


class _Seg:
    def __init__(self, text, logprob):
        self.text, self.avg_logprob, self.no_speech_prob, self.compression_ratio = text, logprob, 0.1, 1.2


def _stt(fast, accurate):
    import threading

    from jarvis.audio.stt import STT

    stt = STT.__new__(STT)
    stt.name, stt.prompt, stt.retry_below, stt.retry_max_seconds = "base.en", "", -0.6, 6.0
    stt.last_conf, stt._busy = 0.0, threading.Lock()
    stt.model, stt.accurate, stt.calls = "fast", "accurate", []

    def run(model, audio):
        stt.calls.append(model)
        seg = fast if model == "fast" else accurate
        return [seg], seg.avg_logprob
    stt._run = run
    return stt


def _speech(seconds):
    return (np.sin(np.arange(int(16000 * seconds)) / 5) * 0.5).astype(np.float32)


def test_unsure_short_clip_is_rechecked():
    stt = _stt(_Seg("play pullygood", -0.8), _Seg("play Pulli Gud", -0.4))
    assert stt.transcribe(_speech(2)) == "play Pulli Gud"
    assert stt.calls == ["fast", "accurate"]


def test_clear_command_skips_recheck():
    stt = _stt(_Seg("open spotify", -0.8), _Seg("x", -0.1))
    assert stt.transcribe(_speech(2), good_enough=lambda t: True) == "open spotify"
    assert stt.calls == ["fast"]


def test_long_clip_skips_recheck():
    stt = _stt(_Seg("a long request", -0.8), _Seg("x", -0.1))
    stt.transcribe(_speech(7))
    assert stt.calls == ["fast"]


def test_cancelled_request_does_no_work():
    stt = _stt(_Seg("hi", -0.8), _Seg("x", -0.1))
    assert stt.transcribe(_speech(2), cancelled=lambda: True) == ""
    assert stt.calls == []


def test_idle_wake_check_yields_to_a_real_request():
    stt = _stt(_Seg("hey jarvis", -0.2), _Seg("x", -0.1))
    with stt._busy:
        assert stt.quick(_speech(1)) == ""
    assert stt.quick(_speech(1)) == "hey jarvis"


def test_carry_keeps_the_start_of_the_sentence():
    ep = Endpointer(silence_ms=600)
    first = [np.full(1280, 7, dtype=np.int16)] * 3
    ep.reset(8, carry=first)
    ep._is_speech = lambda f: True
    ep.feed(np.full(1280, 9, dtype=np.int16))
    audio = ep.audio()
    assert audio.size == 4 * 1280 and audio[0] == 7 / 32768.0
