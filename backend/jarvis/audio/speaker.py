"""Who is speaking: your voiceprint, so Jarvis listens to you and not to the people (or videos) around you.

A speaker-embedding model (CAM++, trained on thousands of voices; data/models/speaker) turns ~1 s of speech into
a 512-number "voiceprint". Calibration stores yours (data/voice/voiceprint.npz, made from the sentences you
read). At run time a stretch of speech is compared with it: about 0.8-1.0 for you on any mic level, and well
under 0.5 for anybody else (measured on this laptop's recordings, 15 Nov). Everything runs on the laptop.

Used three ways (app.py): only your voice wakes Jarvis; other voices don't start or stretch your sentence;
only your voice cuts in while Jarvis is speaking.
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
        self.threshold = threshold
        self.profile: np.ndarray | None = None
        self.takes: np.ndarray | None = None
        if profile_path and Path(profile_path).exists():
            self.load(profile_path)

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
        """How much this sounds like you: similarity to your average voiceprint. (Matching your nearest single
        clip instead let room noise match a clip that had the same noise in it, 15 Nov.)"""
        if self.profile is None or len(audio) < SR // 3:
            return 0.0
        return float(self.embed(audio) @ self.profile)

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
    """Keeps an eye on who is talking right now, without slowing the audio thread: speech frames go into a
    rolling 1.5 s window and a background thread scores it every ~0.4 s. me() answers from the latest score:
    True (you), False (someone else), or None (not enough speech heard yet to tell)."""

    WINDOW = 15        # frames (80 ms each): ~1.2 s, long enough to be sure, short enough to notice a new voice
    MIN = 9            # at least ~0.7 s of speech before deciding
    EVERY = 4          # score again after this many new speech frames (~0.3 s)
    STALE = 1.2        # seconds a verdict stays valid after the speech it came from

    def __init__(self, sid: SpeakerID, margin: float = 0.0) -> None:
        self.sid, self.margin = sid, margin
        self._frames: deque[np.ndarray] = deque(maxlen=self.WINDOW)
        self._since = 0
        self._q: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
        self.last_score = 0.0
        self._verdict: bool | None = None
        self._at = 0.0
        threading.Thread(target=self._work, daemon=True, name="speaker-check").start()

    def reset(self) -> None:
        self._frames.clear()
        self._since = 0
        self._verdict, self._at = None, 0.0

    def feed(self, frame: np.ndarray, speech: bool) -> None:
        if not speech:
            return
        self._frames.append(frame)
        self._since += 1
        if len(self._frames) >= self.MIN and self._since >= self.EVERY:
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
