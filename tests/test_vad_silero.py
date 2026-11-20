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
        v = _load(p)
        v = v / max(float(np.percentile(np.abs(v), 99.5)), 1e-3) * 0.35   # stored quiet; live you peak ~0.35-0.6
        a = np.concatenate([np.zeros(4000, np.float32), v, np.zeros(16000, np.float32)])
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
def test_a_voice_across_the_room_is_not_you():
    """19 Nov: Silero took the room's noise for speech and listened 9 s to it. Speech must also be louder than the
    room (1.8x its usual level), as it was with WebRTC."""
    rng = np.random.default_rng(0)
    t = np.arange(16000 * 6) / 16000
    # a voice-like murmur (pitch harmonics, syllable rhythm) at the room's own level
    murmur = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 560), 1))
    murmur = murmur * (0.5 + 0.5 * np.sin(2 * np.pi * 4 * t)) + rng.normal(0, 0.05, t.size)
    murmur = (murmur / np.sqrt(np.mean(murmur ** 2)) * 0.09).astype(np.float32)   # ~3000 int16 rms, as at 14:42
    ep = Endpointer(2, 600, 40, 3, 1000, neural=SileroGate(MODEL))
    ep.noise_floor = 3000.0   # learned while the murmur was already going
    states = [ep.feed(f) for f in _frames(murmur)]
    assert "timeout" in states or ep.audio().size / 16000 < 1.5


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
