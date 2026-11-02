"""Audio output with level metering (drives the orb while Jarvis talks) and instant stop."""
from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

import numpy as np
import sounddevice as sd

from .meter import levels
from .tts import SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK = 800  # 33 ms at 24 kHz -> ~30 level updates per second


class Player:
    def __init__(self, on_level: Callable[[float, list[float]], None]) -> None:
        self.on_level = on_level
        self._q: queue.Queue[np.ndarray] = queue.Queue()
        self._buf = np.zeros(0, dtype=np.float32)
        self._lock = threading.Lock()
        self._idle = threading.Event()
        self._idle.set()
        self._stream: sd.OutputStream | None = None

    def _ensure_stream(self) -> None:
        if self._stream is None or not self._stream.active:
            try:
                if self._stream is not None:
                    self._stream.close()
            except Exception:
                pass
            self._stream = sd.OutputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                           blocksize=BLOCK, callback=self._callback)
            self._stream.start()

    def _callback(self, outdata, frames, _time, _status) -> None:
        with self._lock:
            while self._buf.size < frames:
                try:
                    self._buf = np.concatenate([self._buf, self._q.get_nowait()])
                except queue.Empty:
                    break
            chunk = self._buf[:frames]
            self._buf = self._buf[frames:]
        out = np.zeros(frames, dtype=np.float32)
        out[:chunk.size] = chunk
        outdata[:, 0] = out
        # an exception here would make PortAudio stop the stream and leave Jarvis "speaking" forever
        try:
            if chunk.size:
                rms, bands = levels(chunk, SAMPLE_RATE)
                self.on_level(rms, bands)
            elif not self._idle.is_set():
                self.on_level(0.0, [0.0] * 32)
        except Exception:
            log.exception("level meter failed")
        if not chunk.size:
            self._idle.set()

    def play(self, audio: np.ndarray) -> None:
        """Queue audio; returns immediately."""
        if audio.size == 0:
            return
        for attempt in range(2):
            try:
                self._ensure_stream()
                break
            except Exception as e:
                # After sleep/resume or a headset change the mic watchdog re-initialises PortAudio,
                # which invalidates this stream. Forget it and open a fresh one.
                self._stream = None
                if attempt:
                    log.error("audio output unavailable: %s", e)
                    return
        self._idle.clear()
        self._q.put(audio.astype(np.float32))

    def wait(self, timeout: float | None = None) -> bool:
        return self._idle.wait(timeout)

    @property
    def busy(self) -> bool:
        try:
            lost = self._stream is not None and not self._stream.active  # device lost mid-clip
        except Exception:
            lost, self._stream = True, None
        if lost:
            self._idle.set()
        return not self._idle.is_set()

    def stop(self) -> None:
        with self._lock:
            self._buf = np.zeros(0, dtype=np.float32)
            while True:
                try:
                    self._q.get_nowait()
                except queue.Empty:
                    break
