"""Parakeet as Jarvis's recogniser (18 Nov): on the user's own recordings, through the same STT class Jarvis uses."""
import glob
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from jarvis.audio import stt as S  # noqa: E402
from jarvis.brain.intents import normalize, wake_phrase  # noqa: E402

PK = ROOT / "data" / "models" / "sherpa-onnx-nemo-parakeet-tdt-0.6b-v2-int8"
need = pytest.mark.skipif(not (PK / "encoder.int8.onnx").is_file(), reason="Parakeet model not downloaded")


def _load(p):
    w = wave.open(str(p))
    return np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768


@pytest.fixture(scope="module")
def stt():
    return S.STT(engine="parakeet", parakeet_dir=PK)


@need
def test_hears_the_users_sentences(stt):
    from jarvis.calibrate import VOICEPRINT_LINES
    lines = sorted((ROOT / "data" / "voice" / "lines").glob("*.wav"))
    if not lines:
        pytest.skip("no recordings")
    assert stt.name == "parakeet" and stt.model is None
    for p, said in zip(lines, VOICEPRINT_LINES):
        heard = stt.transcribe(_load(p))
        assert normalize(heard).replace("chat gpt", "chatgpt") == normalize(said).replace("5", "five"), heard
        assert stt.last_conf > -0.7      # sure of it: never "Sorry, I didn't catch that"


@need
def test_hey_jarvis_takes_wake_it(stt):
    takes = sorted((ROOT / "data" / "voice" / "takes").glob("*.wav"))
    if not takes:
        pytest.skip("no recordings")
    hits = sum(wake_phrase(stt.quick(_load(p))) is not None for p in takes)
    assert hits >= len(takes) * 0.65     # 17/24 measured, as Whisper's quick check (18/24)


@need
def test_silence_and_noise_are_nothing(stt):
    assert stt.transcribe(np.zeros(16000 * 2, np.float32)) == ""
    noise = np.random.default_rng(1).normal(0, 0.05, 16000 * 2).astype(np.float32)
    assert stt.transcribe(noise) == ""
    text, conf = stt.transcribe_careful(np.zeros(16000, np.float32))
    assert text == "" and conf == -9.0
    assert stt.wake_recheck(noise) == ""   # Whisper-only second listen: nothing to add with Parakeet


def test_falls_back_to_whisper_when_parakeet_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "_load", lambda name, ct, th: f"whisper:{name}")
    s = S.STT("base.en", engine="parakeet", parakeet_dir=tmp_path / "nothing-here")
    assert s.parakeet is None and s.model == "whisper:base.en" and s.name == "base.en"
