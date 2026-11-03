"""Text to speech.

Engines, fastest first on this laptop (measured, i5-10310U under normal load):
  piper  - British 'Alan' voice, ~0.2x real time  -> default, keeps replies snappy
  kokoro - most natural voice, ~1.3x real time    -> set tts.engine: kokoro on a faster machine
  sapi   - built-in Windows voice, always available as a last resort
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict

import numpy as np

from ..config import MODELS, WINDOWS

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
KOKORO_MODEL = MODELS / "kokoro" / "kokoro-v1.0.int8.onnx"
KOKORO_VOICES = MODELS / "kokoro" / "voices-v1.0.bin"
PIPER_DIR = MODELS / "piper"


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?;])\s+|\n+", text.strip())  # not ":" -- "1: README" must stay one phrase
    return [p.strip() for p in parts if p.strip()]


def clean_for_speech(text: str) -> str:
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_#`>|]", "", text)
    text = text.replace("&", " and ").replace("%", " percent")
    return re.sub(r"\s+", " ", text).strip()


def _resample(audio: np.ndarray, sr: int) -> np.ndarray:
    if sr == SAMPLE_RATE:
        return audio
    n = int(len(audio) * SAMPLE_RATE / sr)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)


class _Piper:
    name = "piper"

    def __init__(self, voice: str, speed: float) -> None:
        from piper import PiperVoice, SynthesisConfig

        path = PIPER_DIR / f"{voice}.onnx"
        if not path.exists():
            found = sorted(PIPER_DIR.glob("*.onnx"))
            if not found:
                raise FileNotFoundError("no Piper voice downloaded")
            path = found[0]
        self.v = PiperVoice.load(str(path))
        self.cfg = SynthesisConfig(length_scale=1.0 / speed)
        self.name = f"piper ({path.stem})"

    def synth(self, text: str) -> np.ndarray:
        chunks = list(self.v.synthesize(text, syn_config=self.cfg))
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        audio = np.concatenate([c.audio_float_array for c in chunks]).astype(np.float32)
        return _resample(audio, chunks[0].sample_rate)


class _Kokoro:
    name = "kokoro"

    def __init__(self, voice: str, speed: float) -> None:
        from kokoro_onnx import Kokoro

        self.k = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        self.voice, self.speed = voice, speed
        self.name = f"kokoro ({voice})"

    def synth(self, text: str) -> np.ndarray:
        audio, sr = self.k.create(text, voice=self.voice, speed=self.speed, lang="en-gb")
        return _resample(np.asarray(audio, dtype=np.float32), sr)


class _Sapi:
    """Windows SAPI rendered to memory so the orb can still follow the voice."""
    name = "windows"

    def __init__(self, speed: float) -> None:
        self.speed = speed
        self._local = threading.local()

    def _voice(self):
        if not hasattr(self._local, "v"):
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            v = win32com.client.Dispatch("SAPI.SpVoice")
            for tok in v.GetVoices():  # prefer a British voice when installed
                desc = tok.GetDescription()
                if "George" in desc or "Ryan" in desc or "Hazel" in desc:
                    v.Voice = tok
                    break
            v.Rate = int(round((self.speed - 1.0) * 10))
            self._local.v = v
        return self._local.v

    def synth(self, text: str) -> np.ndarray:
        import win32com.client

        v = self._voice()
        stream = win32com.client.Dispatch("SAPI.SpMemoryStream")
        fmt = win32com.client.Dispatch("SAPI.SpAudioFormat")
        fmt.Type = 26  # SAFT24kHz16BitMono
        stream.Format = fmt
        v.AudioOutputStream = stream
        v.Speak(text)
        return np.frombuffer(bytes(stream.GetData()), dtype=np.int16).astype(np.float32) / 32768.0


class TTS:
    def __init__(self, engine: str = "auto", voice: str = "", speed: float = 1.0,
                 piper_voice: str = "en_GB-alan-medium") -> None:
        self.engine = None
        order = {"auto": ["piper", "kokoro"], "piper": ["piper", "kokoro"], "kokoro": ["kokoro", "piper"],
                 "sapi": []}.get(engine, ["piper", "kokoro"])
        for name in order:
            try:
                t = time.perf_counter()
                self.engine = _Piper(piper_voice, speed) if name == "piper" else _Kokoro(voice or "bm_george", speed)
                self.engine.synth("Ready.")  # warm up
                log.info("TTS: %s (%.1fs)", self.engine.name, time.perf_counter() - t)
                break
            except Exception as e:
                log.warning("TTS %s unavailable: %s", name, e)
        if self.engine is None:
            if not WINDOWS:  # no built-in voice to fall back on
                raise RuntimeError("no voice model found: run scripts/setup_linux.sh")
            self.engine = _Sapi(speed)
            log.info("TTS: Windows voice (run scripts\\download_models.py for a better voice)")
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()  # repeated phrases are instant

    @property
    def name(self) -> str:
        return self.engine.name

    def synth(self, text: str) -> np.ndarray:
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, dtype=np.float32)
        with self._lock:
            if text in self._cache:
                self._cache.move_to_end(text)
                return self._cache[text]
            audio = self.engine.synth(text)
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 0:
                audio = audio / peak * 0.85
            audio = np.concatenate([audio, np.zeros(int(SAMPLE_RATE * 0.08), dtype=np.float32)])
            if len(text) < 80:
                self._cache[text] = audio
                if len(self._cache) > 150:
                    self._cache.popitem(last=False)
            return audio
