"""Microphone input: one 16 kHz mono stream shared by wake word, VAD and the meter."""
from __future__ import annotations

import logging
import queue
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects


class Mic:
    """Delivers int16 frames of FRAME samples to `frames`. Reopens itself if the device disappears."""

    def __init__(self, gain: float = 1.0) -> None:
        self.gain = gain  # software boost for quiet laptop mic arrays (set by --calibrate)
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream: sd.InputStream | None = None
        self._stop = threading.Event()
        self._last_frame = 0.0
        self._watchdog: threading.Thread | None = None

    def _callback(self, indata, _frames, _time, status) -> None:
        if status and status.input_overflow:
            log.debug("mic overflow")
        self._last_frame = time.monotonic()
        try:
            frame = indata[:, 0].copy()
            if self.gain != 1.0:
                frame = np.clip(frame.astype(np.float32) * self.gain, -32768, 32767).astype(np.int16)
            self.frames.put_nowait(frame)
        except queue.Full:  # consumer stalled; drop the oldest frame and keep the newest
            try:
                self.frames.get_nowait()
                self.frames.put_nowait(frame)
            except (queue.Empty, queue.Full):
                pass

    def _open(self) -> None:
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                                      blocksize=FRAME, callback=self._callback)
        self._stream.start()
        self._last_frame = time.monotonic()
        dev = sd.query_devices(kind="input")
        log.info("mic open: %s", dev["name"])

    def start(self) -> None:
        self._open()
        self._watchdog = threading.Thread(target=self._watch, daemon=True, name="mic-watchdog")
        self._watchdog.start()

    def _watch(self) -> None:
        # survives headset unplug / sleep-resume: if frames stop for 3 s, reopen the default device
        while not self._stop.wait(1.0):
            if time.monotonic() - self._last_frame < 3.0:
                continue
            log.warning("mic stalled, reopening")
            try:
                if self._stream:
                    self._stream.close()
            except Exception:
                pass
            try:
                sd._terminate()
                sd._initialize()
                self._open()
            except Exception as e:
                log.error("mic reopen failed: %s", e)
                self._last_frame = time.monotonic()  # retry in 3 s

    def stop(self) -> None:
        self._stop.set()
        if self._stream:
            self._stream.close()
