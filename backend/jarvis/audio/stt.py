"""Speech to text with faster-whisper (CPU, int8)."""
from __future__ import annotations

import logging
import re
import time

import numpy as np

from ..config import MODELS

log = logging.getLogger(__name__)

# Whisper invents these on silence or noise
_HALLUCINATIONS = {"", "you", "thank you", "thanks for watching", "thank you for watching",
                   "bye", "so", "okay", "the", "uh", "um", "hmm", "subtitles by the amaraorg community"}


def _trim(audio: np.ndarray, sr: int = 16000) -> np.ndarray:
    """Cut leading/trailing quiet (keeps 150 ms margins) so Whisper sees mostly speech."""
    win = sr // 50
    n = len(audio) // win
    if n < 3:
        return audio
    e = np.sqrt(np.mean(audio[:n * win].reshape(n, win) ** 2, axis=1))
    loud = np.where(e > max(0.02, e.max() * 0.08))[0]
    if loud.size == 0:
        return audio
    a = max(0, (loud[0] - 8) * win)
    b = min(len(audio), (loud[-1] + 8) * win)
    return audio[a:b]


def _load(name: str, compute_type: str, threads: int):
    from faster_whisper import WhisperModel

    kw = dict(device="cpu", compute_type=compute_type, cpu_threads=threads, download_root=str(MODELS / "whisper"))
    try:  # skip the Hugging Face network check when the model is already on disk
        return WhisperModel(name, local_files_only=True, **kw)
    except Exception:
        return WhisperModel(name, **kw)


class STT:
    """Two-pass recognition. The fast model answers clear speech in ~1.5 s; when it is unsure
    (low confidence, which on this laptop usually means an accent it mishears, like "Jarvis" ->
    "Derivi"), the accurate model re-checks the same audio. Measured on the user's own clips."""

    def __init__(self, model: str = "base.en", compute_type: str = "int8", threads: int = 4,
                 accurate_model: str = "", retry_below: float = -0.6) -> None:
        t = time.perf_counter()
        self.model = _load(model, compute_type, threads)
        self.name = model
        self.prompt = ""
        self.accurate = None
        self.retry_below = retry_below
        log.info("STT ready: %s (%.1fs)", model, time.perf_counter() - t)
        if accurate_model and accurate_model != model:
            def load_accurate():  # in the background so startup stays fast
                t2 = time.perf_counter()
                try:
                    self.accurate = _load(accurate_model, compute_type, threads)
                    log.info("STT re-check model ready: %s (%.1fs)", accurate_model, time.perf_counter() - t2)
                except Exception as e:
                    log.warning("STT re-check model unavailable: %s", e)
            import threading
            threading.Thread(target=load_accurate, daemon=True).start()

    def quick(self, audio: np.ndarray) -> str:
        """Fast pass only, no re-check or debug clips: used to spot "Hey Jarvis" in idle speech."""
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 0.01:
            return ""
        segments, _ = self._run(self.model, _trim(audio / peak * 0.9))
        return " ".join(s.text for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.5).strip()

    def _run(self, model, audio: np.ndarray) -> tuple[list, float]:
        segments = list(model.transcribe(
            audio, language="en", beam_size=1, best_of=1, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=self.prompt or None, hotwords="Jarvis",
        )[0])
        speech = [s for s in segments if s.no_speech_prob < 0.6]
        conf = min((s.avg_logprob for s in speech), default=-9.0)
        return segments, conf

    def set_vocabulary(self, words: list[str]) -> None:
        # names Whisper would otherwise misspell; keep the prompt short, it costs decode time
        self.prompt = "Jarvis, " + ", ".join(words[:60]) + "."

    def transcribe(self, audio: np.ndarray) -> str:
        raw = audio
        if audio.size < 16000 * 0.3:
            return ""
        peak = float(np.max(np.abs(audio)))
        if peak < 0.01:
            log.info("STT skipped: too quiet (peak %.4f) - mic level may be too low", peak)
            _save_debug(raw, "quiet")
            return ""
        audio = _trim(audio / max(peak, 1e-3) * 0.9)
        t = time.perf_counter()
        # no vad_filter: the endpointer already cut the utterance, and Silero would add ~0.4 s
        segments, conf = self._run(self.model, audio)
        used = self.name
        # re-check only unsure *speech*; if the fast pass found no speech at all it's noise, move on
        if self.accurate is not None and -9.0 < conf < self.retry_below:
            seg2, conf2 = self._run(self.accurate, audio)
            log.info("STT re-check: fast %r (%.2f) vs accurate %r (%.2f)",
                     " ".join(s.text for s in segments).strip(), conf, " ".join(s.text for s in seg2).strip(), conf2)
            if conf2 > conf:
                segments, used = seg2, "accurate"
        # drop low-confidence segments: usually background chatter or a video playing nearby
        kept = [s for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0
                and s.compression_ratio < 2.4]
        text = " ".join(s.text for s in kept).strip()
        log.info("STT %.2fs (%s): %r", time.perf_counter() - t, used, text)
        if not text:
            log.info("STT empty: %.1fs audio, peak %.3f, segments %s", raw.size / 16000, peak,
                     [(s.text, round(s.no_speech_prob, 2), round(s.avg_logprob, 2)) for s in segments])
            _save_debug(raw, "empty")
        if re.sub(r"[^a-z ]", "", text.lower()).strip() in _HALLUCINATIONS:
            return ""
        return text


def _save_debug(audio: np.ndarray, tag: str) -> None:
    """Keep the last few unrecognised utterances (data/debug) so problems can be diagnosed. Local only."""
    import wave

    from ..config import DATA

    d = DATA / "debug"
    d.mkdir(parents=True, exist_ok=True)
    for old in sorted(d.glob("*.wav"))[:-4]:
        old.unlink(missing_ok=True)
    with wave.open(str(d / f"{time.strftime('%H%M%S')}_{tag}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
