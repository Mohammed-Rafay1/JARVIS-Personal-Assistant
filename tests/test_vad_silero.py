"""Silero VAD while Jarvis listens (19 Nov): far fewer mid-sentence cut-offs than WebRTC VAD on the user's voice."""
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.audio.vad import Endpointer, SileroGate  # noqa: E402

MODEL = ROOT / "data" / "models" / "vad" / "silero_vad.onnx"
need = pytest.mark.skipif(not MODEL.is_file(), reason="Silero model not downloaded")


def _frames(a):
    a = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    return [a[i:i + 1280] for i in range(0, len(a) - 1279, 1280)]


def _load(p):
    w = wave.open(str(p))
    return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


@need
def test_a_whole_recorded_sentence_is_one_utterance():
    lines = sorted((ROOT / "data" / "voice" / "lines").glob("*.wav"))
    if not lines:
        pytest.skip("no recordings")
    for p in lines:
        ep = Endpointer(2, 600, 40, 5, 1000, neural=SileroGate(MODEL))
        a = np.concatenate([np.zeros(4000, np.float32), _load(p), np.zeros(16000, np.float32)])
        states = [ep.feed(f) for f in _frames(a)]
        assert "done" in states, p.name
        done_at = states.index("done") * 0.08
        assert done_at >= 0.25 + 3.0, f"{p.name}: ended at {done_at:.1f}s, before the sentence did"


@need
def test_silence_and_hum_never_start_a_sentence():
    ep = Endpointer(2, 600, 40, 3, 1000, neural=SileroGate(MODEL))
    t = np.arange(16000 * 3) / 16000
    hum = (0.05 * np.sin(2 * np.pi * 100 * t)).astype(np.float32)
    states = [ep.feed(f) for f in _frames(hum)]
    assert "speaking" not in states and states[-1] == "timeout"


@need
def test_reset_starts_clean():
    gate = SileroGate(MODEL)
    ep = Endpointer(2, 600, 40, 5, 1000, neural=gate)
    for f in _frames(np.zeros(16000, np.float32)):
        ep.feed(f)
    ep.reset(5)
    assert ep.frames == [] and not ep.started


def test_without_silero_it_is_webrtc_as_before():
    ep = Endpointer(2, 600, 40, 5)
    assert ep.neural is None
    assert ep.feed(np.zeros(1280, np.int16)) == "waiting"
