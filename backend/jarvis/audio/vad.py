"""End-of-speech detection: WebRTC VAD gated by an adaptive noise floor."""
from __future__ import annotations

import numpy as np
import webrtcvad

from .mic import FRAME, SAMPLE_RATE

SUB = 320  # 20 ms sub-frames for webrtcvad
FRAME_MS = FRAME * 1000 // SAMPLE_RATE


class Endpointer:
    """Feed 80 ms frames; reports when an utterance has started and ended."""

    def __init__(self, aggressiveness: int = 2, silence_ms: int = 700, max_seconds: float = 12,
                 no_speech_timeout: float = 5, short_silence_ms: int = 0) -> None:
        self.vad = webrtcvad.Vad(aggressiveness)
        self.silence_frames = max(1, silence_ms // FRAME_MS)
        # People pause after "Jarvis," or "Send a text to..." before the rest. While only a word or two
        # has been said, wait longer before deciding the sentence is over.
        self.short_silence_frames = max(self.silence_frames, short_silence_ms // FRAME_MS)
        self.short_speech_frames = 1000 // FRAME_MS
        self.max_frames = int(max_seconds * 1000 / FRAME_MS)
        self.no_speech_frames = int(no_speech_timeout * 1000 / FRAME_MS)
        self.noise_floor = 300.0  # int16 RMS; updated while idle
        self.reset()

    def reset(self, no_speech_timeout: float | None = None, carry: list[np.ndarray] | None = None) -> None:
        if no_speech_timeout is not None:
            self.no_speech_frames = int(no_speech_timeout * 1000 / FRAME_MS)
        self.carry = list(carry or [])  # the start of a sentence heard before a pause; kept in front of it
        self.frames: list[np.ndarray] = []
        self.started = False
        self.speech_frames = 0
        self.silent_run = 0

    def observe_idle(self, frame: np.ndarray) -> None:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        self.noise_floor = 0.98 * self.noise_floor + 0.02 * min(rms, 3000)

    def vad_votes(self, frame: np.ndarray) -> int:
        """How many of the frame's four 20 ms slices WebRTC VAD calls speech (0-4)."""
        pcm = frame.tobytes()
        return sum(self.vad.is_speech(pcm[i * SUB * 2:(i + 1) * SUB * 2], SAMPLE_RATE) for i in range(FRAME // SUB))

    def _is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        if rms < max(150.0, self.noise_floor * 1.8):
            return False
        pcm = frame.tobytes()
        votes = sum(self.vad.is_speech(pcm[i * SUB * 2:(i + 1) * SUB * 2], SAMPLE_RATE)
                    for i in range(FRAME // SUB))
        return votes >= 2

    def feed(self, frame: np.ndarray) -> str:
        """Returns 'waiting', 'speaking', 'done' (utterance ready) or 'timeout' (nothing said)."""
        self.frames.append(frame)
        speech = self._is_speech(frame)
        if speech:
            self.speech_frames += 1
            self.silent_run = 0
            if self.speech_frames >= 2:
                self.started = True
        else:
            self.silent_run += 1
            if not self.started and len(self.frames) > 2:
                self.observe_idle(frame)  # the room's noise, between turns of a call
        if not self.started:
            if len(self.frames) >= self.no_speech_frames:
                return "timeout"
            # keep a short pre-roll only
            if len(self.frames) > 8 and self.speech_frames == 0:
                self.frames = self.frames[-8:]
            return "waiting"
        need = self.short_silence_frames if self.speech_frames < self.short_speech_frames else self.silence_frames
        if self.silent_run >= need or len(self.frames) >= self.max_frames:
            return "done"
        return "speaking"

    def audio(self) -> np.ndarray:
        """The captured utterance as float32 in [-1, 1]."""
        frames = self.carry + self.frames
        if not frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(frames).astype(np.float32) / 32768.0
