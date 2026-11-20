"""Speech to text with faster-whisper (CPU, int8)."""
from __future__ import annotations

import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable

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


class _Parakeet:
    """NVIDIA Parakeet TDT 0.6B v2 (int8, via sherpa-onnx). Measured 18 Nov on the user's own recordings mixed
    with their room's background: 0.5% / 2.4% of words wrong at mild / moderate noise, against Whisper's 3.4% /
    16.8%, and 1.0-1.4 s per sentence against 1.7-4.2 s (the Whisper re-check is no longer needed)."""

    # its confidence (mean token log-probability) is ~0 for clear speech and -0.5 to -0.8 for noise and mumbles;
    # scaled so the rest of Jarvis can keep Whisper's yardstick ("unsure" below -0.7)
    SCALE = 1.4
    DROP_BELOW = -0.75   # mean log-prob under this is background chatter or noise: heard as nothing

    def __init__(self, folder: Path, threads: int) -> None:
        import sherpa_onnx

        f = Path(folder)
        self.rec = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(f / "encoder.int8.onnx"), decoder=str(f / "decoder.int8.onnx"),
            joiner=str(f / "joiner.int8.onnx"), tokens=str(f / "tokens.txt"), model_type="nemo_transducer",
            num_threads=threads, decoding_method="greedy_search")

    def run(self, audio: np.ndarray) -> tuple[str, float]:
        """(text, mean token log-probability)."""
        s = self.rec.create_stream()
        s.accept_waveform(16000, np.ascontiguousarray(audio, dtype=np.float32))
        self.rec.decode_stream(s)
        lp = list(s.result.ys_log_probs or [])
        return s.result.text.strip(), (float(np.mean(lp)) if lp else 0.0)


class STT:
    """Speech to text. With engine "parakeet" (the default since 18 Nov), one pass of NVIDIA Parakeet.
    Otherwise Whisper in two passes: the fast model answers clear speech in ~1.5 s; when it is unsure (low
    confidence, which on this laptop usually means an accent it mishears, like "Jarvis" -> "Derivi"), the
    accurate model re-checks the same audio. Measured on the user's own clips."""

    parakeet: _Parakeet | None = None   # set in __init__ when Parakeet is the engine

    def __init__(self, model: str = "base.en", compute_type: str = "int8", threads: int = 4,
                 accurate_model: str = "", retry_below: float = -0.6, retry_max_seconds: float = 6.0,
                 engine: str = "whisper", parakeet_dir: str | Path = "") -> None:
        t = time.perf_counter()
        self.prompt = ""
        self.accurate = None
        self.parakeet: _Parakeet | None = None
        self.retry_below = retry_below
        # The re-check costs ~3x the fast pass and grows with length (8-20 s measured on long clips),
        # while long sentences carry enough context for the fast model. Only short ones are re-checked.
        self.retry_max_seconds = retry_max_seconds
        self.last_conf = 0.0
        # One decode at a time: two at once on this 4-core CPU made both 3-5x slower (13 s for "Jarvis,")
        self._busy = threading.Lock()
        if engine == "parakeet" and parakeet_dir and (Path(parakeet_dir) / "encoder.int8.onnx").is_file():
            try:
                self.parakeet = _Parakeet(Path(parakeet_dir), threads)
                self.model, self.name = None, "parakeet"
                log.info("STT ready: parakeet (%.1fs)", time.perf_counter() - t)
                return
            except Exception as e:
                log.warning("Parakeet unavailable (%s); using Whisper", e)
        elif engine == "parakeet":
            log.warning("Parakeet model not found in %s; using Whisper", parakeet_dir)
        self.model = _load(model, compute_type, threads)
        self.name = model
        log.info("STT ready: %s (%.1fs)", model, time.perf_counter() - t)
        if accurate_model and accurate_model != model:
            def load_accurate():  # in the background so startup stays fast
                t2 = time.perf_counter()
                try:
                    self.accurate = _load(accurate_model, compute_type, threads)
                    log.info("STT re-check model ready: %s (%.1fs)", accurate_model, time.perf_counter() - t2)
                except Exception as e:
                    log.warning("STT re-check model unavailable: %s", e)
            threading.Thread(target=load_accurate, daemon=True).start()

    def quick(self, audio: np.ndarray, wait: float = 0.0) -> str:
        """Fast pass only, no re-check or debug clips: used to spot "Hey Jarvis" in idle speech.
        wait: seconds to wait for another recognition to finish instead of giving up."""
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 0.01:
            return ""
        if not (self._busy.acquire(timeout=wait) if wait else self._busy.acquire(blocking=False)):
            if wait:
                log.info("quick check skipped: recogniser busy for %.0fs", wait)
            return ""  # a real request is being recognised; the idle wake check can skip this phrase
        try:
            if self.parakeet is not None:
                return self.parakeet.run(_trim(audio / peak * 0.9))[0]
            segments, _ = self._run(self.model, _trim(audio / peak * 0.9))
        finally:
            self._busy.release()
        return " ".join(s.text for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.5).strip()

    def _parakeet_text(self, audio: np.ndarray, raw: np.ndarray, peak: float, label: str) -> tuple[str, float]:
        """One Parakeet pass with Jarvis's usual clean-up: noise dropped, confidence on Whisper's scale."""
        t = time.perf_counter()
        text, lp = self.parakeet.run(audio)
        conf = lp * _Parakeet.SCALE if text else -9.0
        if text and lp < _Parakeet.DROP_BELOW:
            log.info("STT dropped as noise (%.2f): %r", lp, text)
            text, conf = "", -9.0
        self.last_conf = conf
        log.info("%s %.2fs (parakeet, %.2f): %r", label, time.perf_counter() - t, lp, text)
        if not text:
            log.info("STT empty: %.1fs audio, peak %.3f", raw.size / 16000, peak)
            _save_debug(raw, "empty")
        if re.sub(r"[^a-z ]", "", text.lower()).strip() in _HALLUCINATIONS:
            return "", -9.0
        return text, conf

    def wake_recheck(self, audio: np.ndarray) -> str:
        """A second listen to a short "Hey ..." the fast pass couldn't place ("Hey, did we see you?", "Edge,
        Database," on the headset, 17 Nov): the accurate model, told to expect "Hey Jarvis". (Whisper only:
        Parakeet takes no hint, so a second listen would hear the same.)"""
        if self.parakeet is not None:
            return ""
        peak = float(np.max(np.abs(audio))) if audio.size else 0.0
        if peak < 0.01 or not self._busy.acquire(timeout=1.0):
            return ""
        try:
            t = time.perf_counter()
            model = self.accurate if self.accurate is not None else self.model
            segments = list(model.transcribe(
                _trim(audio / peak * 0.9), language="en", beam_size=3, best_of=3, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=True,
                initial_prompt="Hey Jarvis. Hey Jarvis, open the board.", hotwords="Jarvis",
            )[0])
            text = " ".join(s.text for s in segments if s.no_speech_prob < 0.6).strip()
            log.info("wake re-check %.2fs: %r", time.perf_counter() - t, text)
            return text
        finally:
            self._busy.release()

    def _run(self, model, audio: np.ndarray) -> tuple[list, float]:
        segments = list(model.transcribe(
            audio, language="en", beam_size=1, best_of=1, temperature=0.0,
            condition_on_previous_text=False, without_timestamps=True,
            initial_prompt=self.prompt or None, hotwords="Jarvis",
        )[0])
        speech = [s for s in segments if s.no_speech_prob < 0.6]
        conf = min((s.avg_logprob for s in speech), default=-9.0)
        return segments, conf

    # what you say to Jarvis, so a phone recording of it is heard as these words ("Claude Gaut", "Get Wisped,
    # can you give me a moderning update", "gave me a bit when I was away" on 16 Nov)
    COMMAND_WORDS = ("Jarvis, give me the morning update. Claude Code projects, my emails, my to-do list, the board, "
                     "what did I miss while I was away, does anything need me, mark them done, remind me at 3 PM, "
                     "send an email, WhatsApp, Spotify, ChatGPT.")

    def transcribe_careful(self, audio: np.ndarray) -> tuple[str, float]:
        """A recording that isn't waiting on a live reply (a WhatsApp voice note): the accurate model, a wider
        beam and the command vocabulary. A few seconds slower, much closer to what was said."""
        with self._busy:
            self.last_conf = 0.0
            if audio.size < 16000 * 0.3:
                return "", -9.0
            peak = float(np.max(np.abs(audio)))
            if peak < 0.005:
                return "", -9.0
            raw = audio
            audio = _trim(audio / max(peak, 1e-3) * 0.9)
            if self.parakeet is not None:   # already the most accurate recogniser here
                return self._parakeet_text(audio, raw, peak, "STT careful")
            model, used = (self.accurate, "accurate") if self.accurate is not None else (self.model, self.name)
            prompt = self.COMMAND_WORDS + (" " + self.prompt if self.prompt else "")
            t = time.perf_counter()
            segments = list(model.transcribe(
                audio, language="en", beam_size=5, best_of=5, temperature=0.0,
                condition_on_previous_text=False, without_timestamps=True, vad_filter=True,
                initial_prompt=prompt[:800], hotwords="Jarvis Claude Code",
            )[0])
            kept = [s for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.2
                    and s.compression_ratio < 2.4]
            text = " ".join(s.text for s in kept).strip()
            conf = min((s.avg_logprob for s in kept), default=-9.0)
            self.last_conf = conf
            log.info("STT careful %.2fs (%s): %r", time.perf_counter() - t, used, text)
            if re.sub(r"[^a-z ]", "", text.lower()).strip() in _HALLUCINATIONS:
                return "", -9.0
            return text, conf

    def set_vocabulary(self, words: list[str]) -> None:
        # names Whisper would otherwise misspell; keep the prompt short, it costs decode time
        self.prompt = "Jarvis, " + ", ".join(words[:60]) + "."

    def transcribe(self, audio: np.ndarray, good_enough: Callable[[str], bool] | None = None,
                   cancelled: Callable[[], bool] | None = None, retry_max_seconds: float | None = None) -> str:
        """good_enough(text): True when the fast pass is already usable (a clear command), so the slow
        re-check is skipped. cancelled(): True once the user has moved on (interrupted, call closed)."""
        with self._busy:
            if cancelled and cancelled():
                return ""
            return self._transcribe(audio, good_enough, cancelled, retry_max_seconds or self.retry_max_seconds)

    def _transcribe(self, audio: np.ndarray, good_enough, cancelled, retry_max: float) -> str:
        raw = audio
        self.last_conf = 0.0
        if audio.size < 16000 * 0.3:
            return ""
        peak = float(np.max(np.abs(audio)))
        if peak < 0.01:
            log.info("STT skipped: too quiet (peak %.4f) - mic level may be too low", peak)
            _save_debug(raw, "quiet")
            return ""
        audio = _trim(audio / max(peak, 1e-3) * 0.9)
        if self.parakeet is not None:
            return self._parakeet_text(audio, raw, peak, "STT")[0]
        t = time.perf_counter()
        # no vad_filter: the endpointer already cut the utterance, and Silero would add ~0.4 s
        segments, conf = self._run(self.model, audio)
        used = self.name
        # re-check only unsure *speech*; if the fast pass found no speech at all it's noise, move on
        fast_text = " ".join(s.text for s in segments).strip()
        recheck = self.accurate is not None and -9.0 < conf < self.retry_below
        if recheck and audio.size / 16000 > retry_max:
            recheck = False
            log.info("STT re-check skipped: %.1fs clip", audio.size / 16000)
        elif recheck and cancelled and cancelled():
            recheck = False
        elif recheck and good_enough and good_enough(fast_text):
            recheck = False
            log.info("STT re-check skipped: clear command %r", fast_text)
        if recheck:
            seg2, conf2 = self._run(self.accurate, audio)
            log.info("STT re-check: fast %r (%.2f) vs accurate %r (%.2f)",
                     " ".join(s.text for s in segments).strip(), conf, " ".join(s.text for s in seg2).strip(), conf2)
            if conf2 > conf:
                segments, used = seg2, "accurate"
        # drop low-confidence segments: usually background chatter or a video playing nearby
        kept = [s for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0
                and s.compression_ratio < 2.4]
        text = " ".join(s.text for s in kept).strip()
        self.last_conf = min((s.avg_logprob for s in kept), default=-9.0)
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
    for old in sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime)[:-4]:   # by age: names are times of day
        old.unlink(missing_ok=True)
    with wave.open(str(d / f"{time.strftime('%H%M%S')}_{tag}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
