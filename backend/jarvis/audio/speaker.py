"""Who is speaking: your voiceprint, so only you can give Jarvis commands.

A speaker-embedding model (WeSpeaker ResNet34, trained on thousands of voices; data/models/speaker) turns speech
into a 256-number "voiceprint". Calibration stores yours (data/voice/voiceprint.npz, from the sentences you read).
At run time a spoken command is compared with it. Measured on this laptop (15 Nov): your whole commands 0.87+,
your "Hey Jarvis" 0.74-0.81, another speaker 0.72 at most. (A CAM++ model was tried first; under sherpa-onnx
2.0.1 it gave a different voiceprint for the same recording at different lengths, so it was dropped.)
Everything runs on the laptop.

Used in app.py: a spoken command that isn't yours gets "Sorry, you're not my boss" and nothing happens;
"Hey Jarvis" in a clearly different voice gets the same; a clearly different voice can't cut in while
Jarvis speaks.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)
SR = 16000


class SpeakerID:
    """Scores audio against your voiceprint (cosine similarity, -1..1)."""

    def __init__(self, model_path: str | Path, profile_path: str | Path | None = None, threshold: float = 0.55) -> None:
        import sherpa_onnx

        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(
            sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(model_path), num_threads=1))
        self._lock = threading.Lock()  # the extractor is used from the audio thread and the checker thread
        self.threshold = threshold               # a whole command (2 s of voice or more)
        self.short_threshold = threshold - 0.04  # a short command
        self.wake_reject = threshold - 0.12      # "Hey Jarvis": below this it's clearly someone else
        self.profile: np.ndarray | None = None
        self.takes: np.ndarray | None = None
        self.live: np.ndarray | None = None      # how you've sounded lately (see adapt)
        self.live_path: Path | None = None
        self._learnt = 0
        if profile_path and Path(profile_path).exists():
            self.load(profile_path)
            self.live_path = Path(profile_path).with_name("voiceprint_live.npy")
            try:
                live = np.load(str(self.live_path))
                if live.shape == self.profile.shape and float(live @ self.profile) > 0.6:  # still recognisably you
                    self.live = live.astype(np.float32)
            except (OSError, ValueError):
                pass

    @property
    def ready(self) -> bool:
        return self.profile is not None

    def embed(self, audio: np.ndarray) -> np.ndarray:
        """audio: float32 mono 16 kHz in [-1, 1]. A unit-length voiceprint."""
        with self._lock:
            s = self._ex.create_stream()
            s.accept_waveform(SR, np.ascontiguousarray(audio, dtype=np.float32))
            s.input_finished()
            e = np.asarray(self._ex.compute(s), dtype=np.float32)
        n = float(np.linalg.norm(e))
        return e / n if n else e

    def score(self, audio: np.ndarray) -> float:
        """How much this sounds like you: similarity to your voiceprint. (Matching your nearest single clip
        instead let room noise match a clip that had the same noise in it, 15 Nov.)"""
        if self.profile is None or len(audio) < SR // 3:
            return 0.0
        return self.score_embedding(self.embed(audio))

    def score_embedding(self, e: np.ndarray) -> float:
        return float(e @ self.current) if self.profile is not None else 0.0

    # ------------------------------------------------------------------ learning how you sound day to day
    @property
    def current(self) -> np.ndarray:
        """The voiceprint in use: your calibrated one, nudged towards how you've sounded lately. The calibrated
        part always keeps at least 60% of the weight, so it can't drift away from you."""
        if self.live is None:
            return self.profile
        v = 0.6 * self.profile + 0.4 * self.live
        return v / (np.linalg.norm(v) or 1)

    def adapt(self, e: np.ndarray) -> None:
        """A command that was clearly yours (high score, plenty of voice): learn from it, a little at a time."""
        self.live = e.copy() if self.live is None else 0.9 * self.live + 0.1 * e
        self.live /= np.linalg.norm(self.live) or 1
        self._learnt += 1
        if self.live_path and self._learnt % 5 == 0:
            try:
                np.save(str(self.live_path), self.live)
            except OSError as err:
                log.debug("couldn't save the live voiceprint: %s", err)

    def is_me(self, audio: np.ndarray, margin: float = 0.0) -> tuple[bool, float]:
        s = self.score(audio)
        return s >= self.threshold - margin, s

    # ------------------------------------------------------------------ the profile
    @staticmethod
    def build(embeddings: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
        takes = np.stack([e / (np.linalg.norm(e) or 1) for e in embeddings]).astype(np.float32)
        mean = takes.mean(axis=0)
        return (mean / (np.linalg.norm(mean) or 1)).astype(np.float32), takes

    def set_profile(self, embeddings: list[np.ndarray]) -> None:
        self.profile, self.takes = self.build(embeddings)
        self.live = None

    def save(self, path: str | Path, **info) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        np.savez(str(path), profile=self.profile, takes=self.takes, threshold=np.float32(self.threshold),
                 **{k: np.asarray(v) for k, v in info.items()})

    def load(self, path: str | Path) -> None:
        d = np.load(str(path))
        self.profile = d["profile"].astype(np.float32)
        self.takes = d["takes"].astype(np.float32) if "takes" in d else None
        log.info("voiceprint loaded (%d takes)", 0 if self.takes is None else len(self.takes))


class Tracker:
    """Keeps an eye on who is talking right now, without slowing the audio thread: the last ~1.5 s of audio (as
    it is, pauses included: the model needs natural, continuous speech; stitching only the voiced bits together
    wrecked its scores, 15 Nov) is scored by a background thread every ~0.3 s of speech. me() answers from the
    latest score: True (you), False (someone else), or None (not enough speech heard yet to tell)."""

    WINDOW = 19        # frames (80 ms each): ~1.5 s of continuous audio
    MIN = 8            # at least ~0.65 s of speech in it before deciding
    EVERY = 4          # score again after this many new speech frames (~0.3 s)
    STALE = 1.2        # seconds a verdict stays valid after the speech it came from

    def __init__(self, sid: SpeakerID, margin: float = 0.0) -> None:
        self.sid, self.margin = sid, margin
        self._frames: deque[np.ndarray] = deque(maxlen=self.WINDOW)
        self._speech: deque[bool] = deque(maxlen=self.WINDOW)
        self._since = 0
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self.last_score = 0.0
        self._verdict: bool | None = None
        self._at = 0.0
        threading.Thread(target=self._work, daemon=True, name="speaker-check").start()

    def reset(self) -> None:
        self._frames.clear()
        self._speech.clear()
        self._since = 0
        self._verdict, self._at = None, 0.0

    def feed(self, frame: np.ndarray, speech: bool) -> None:
        self._frames.append(frame)
        self._speech.append(bool(speech))
        if not speech:
            return
        self._since += 1
        if sum(self._speech) >= self.MIN and self._since >= self.EVERY:
            self._since = 0
            clip = np.concatenate(list(self._frames)).astype(np.float32) / 32768.0
            try:
                self._q.put_nowait(clip)
            except queue.Full:  # still busy with the last one: skip, a newer one comes soon
                pass

    def me(self) -> bool | None:
        if self._verdict is None or time.monotonic() - self._at > self.STALE:
            return None
        return self._verdict

    def _work(self) -> None:
        while True:
            clip = self._q.get()
            try:
                s = self.sid.score(clip)
            except Exception as e:
                log.debug("speaker check failed: %s", e)
                continue
            self.last_score = s
            self._verdict = s >= self.sid.threshold - self.margin
            self._at = time.monotonic()
