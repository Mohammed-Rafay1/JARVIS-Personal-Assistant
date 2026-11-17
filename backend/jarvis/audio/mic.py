"""Microphone input: one 16 kHz mono stream shared by wake word, VAD and the meter.

Level: every microphone gets its own boost (data/voice/mic_levels.json, set by calibration), so switching
between the laptop's built-in mic and a headset or external mic doesn't leave one drowned and the other
distorted (15 Nov: the 6x boost tuned for the built-in array was applied to the external mic too). On top of
that a slow automatic level control keeps your voice at a steady level: it learns only from speech (never
from silence, and never while Jarvis itself is talking), moves a little at a time, and backs off at once if
your voice starts to clip. Windows' own mic settings are left alone.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms, the frame size openWakeWord expects
TARGET_RMS = 3300.0   # your voice after the boost (int16 RMS, about -20 dBFS): clear for the models, far from clipping
MIN_GAIN, MAX_GAIN = 0.6, 12.0


def load_levels(path: Path | None) -> dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()} if path else {}
    except (OSError, ValueError):
        return {}


def save_levels(path: Path, levels: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: round(v, 2) for k, v in levels.items()}, indent=1), encoding="utf-8")


class AutoGain:
    """Slow level control from speech only. Pure numbers, so it can be tested without a mic."""

    def __init__(self, gain: float) -> None:
        self.gain = gain
        self.floor = 100.0                       # the room's noise, raw (before the boost)
        self._speech: deque[float] = deque(maxlen=40)
        self._n = 0
        self._last_clip = 0.0

    def update(self, raw: np.ndarray, hold: bool = False) -> float:
        """raw: int16 frame before the boost. Returns the gain to use."""
        x = raw.astype(np.float32)
        rms = float(np.sqrt(np.mean(x * x)))
        if not hold and np.count_nonzero(np.abs(x) * self.gain >= 32000) > len(x) * 0.005:
            now = time.monotonic()
            if now - self._last_clip > 0.4:     # clipping: back off straight away
                self._last_clip = now
                self.gain = max(MIN_GAIN, self.gain * 0.85)
            return self.gain
        if rms < max(self.floor * 3.0, 120.0):   # relative to the room, so a quiet mic still counts as speech
            self.floor = 0.97 * self.floor + 0.03 * min(rms, 2000)   # quiet: learn the room, don't touch the gain
            return self.gain
        if hold:  # Jarvis is talking: that's its own voice in the mic, not yours
            return self.gain
        self._speech.append(rms)
        self._n += 1
        if self._n >= 25 and len(self._speech) >= 20:   # every ~2 s of speech
            self._n = 0
            level = float(np.percentile(self._speech, 80)) * self.gain
            step = float(np.clip((TARGET_RMS / max(level, 1.0)) ** 0.5, 0.88, 1.12))
            # never boost so far that the room's noise becomes as loud as speech
            ceiling = min(MAX_GAIN, 1800.0 / max(self.floor, 1.0))
            self.gain = float(np.clip(self.gain * step, MIN_GAIN, max(MIN_GAIN, ceiling)))
        return self.gain


def default_devices() -> tuple[str, str] | None:
    """Windows' default recording and playback devices by name (they change when a headset is plugged in).
    None when they can't be read; the caller's thread must have COM initialised."""
    try:
        from pycaw.pycaw import AudioUtilities

        mic = AudioUtilities.CreateDevice(AudioUtilities.GetMicrophone()).FriendlyName
        out = AudioUtilities.GetSpeakers()
        out = getattr(out, "FriendlyName", None) or AudioUtilities.CreateDevice(out).FriendlyName
        return str(mic or ""), str(out or "")
    except Exception as e:
        log.debug("default audio devices unreadable: %s", e)
        return None


class Mic:
    """Delivers int16 frames of FRAME samples to `frames`. Reopens itself if the device disappears."""

    def __init__(self, gain: float = 1.0, levels_path: Path | None = None, auto: bool = True) -> None:
        self.default_gain = gain                 # for a mic that was never calibrated
        self.gain = gain
        self.levels_path = levels_path
        self.levels = load_levels(levels_path)   # {device name: boost}
        self.auto = auto
        self.agc: AutoGain | None = None
        self.hold = False                        # set while Jarvis is speaking (the app does this)
        self.device = ""
        self._saved_gain = gain
        self._saved_at = 0.0
        self.frames: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self._stream: sd.InputStream | None = None
        self._stop = threading.Event()
        self._last_frame = 0.0
        self._watchdog: threading.Thread | None = None
        # The app sets this to the player's release(): when Windows switches devices (a headset plugged in),
        # the output has to let go before PortAudio is re-initialised. False = busy, try again shortly.
        self.before_switch = None
        self._defaults: tuple[str, str] | None = None   # Windows' default (input, output) names
        self._checked = 0.0

    def _callback(self, indata, _frames, _time, status) -> None:
        if status and status.input_overflow:
            log.debug("mic overflow")
        self._last_frame = time.monotonic()
        try:
            raw = indata[:, 0].copy()
            if self.agc is not None:
                self.gain = self.agc.update(raw, self.hold)
            frame = raw if self.gain == 1.0 else \
                np.clip(raw.astype(np.float32) * self.gain, -32768, 32767).astype(np.int16)
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
        dev = sd.query_devices(kind="input")
        self.device = str(dev["name"])
        self.gain = self.levels.get(self.device, self.default_gain)
        self._saved_gain = self.gain
        self.agc = AutoGain(self.gain) if self.auto else None
        self._stream.start()
        self._last_frame = time.monotonic()
        known = "its own level" if self.device in self.levels else "no saved level yet"
        log.info("mic open: %s (boost %.1fx, %s%s)", self.device, self.gain, known,
                 ", auto level on" if self.auto else "")

    def start(self) -> None:
        self._open()
        self._watchdog = threading.Thread(target=self._watch, daemon=True, name="mic-watchdog")
        self._watchdog.start()

    def _remember(self) -> None:
        """Keep a level the auto control has settled on, for this mic, for next time."""
        if not (self.levels_path and self.device) or time.monotonic() - self._saved_at < 120:
            return
        if abs(self.gain - self._saved_gain) / max(self._saved_gain, 0.1) < 0.1:
            return
        self._saved_at, self._saved_gain = time.monotonic(), self.gain
        self.levels[self.device] = self.gain
        try:
            save_levels(self.levels_path, self.levels)
            log.info("mic level for %s now %.1fx", self.device, self.gain)
        except OSError as e:
            log.debug("couldn't save mic level: %s", e)

    def _watch(self) -> None:
        # survives headset unplug / sleep-resume: if frames stop for 3 s, reopen the default device. And
        # follows Windows' default devices: plugging in headphones switches Jarvis to their mic and earpieces
        # (the laptop mic kept working, so the stall check alone never moved it, 17 Nov).
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while not self._stop.wait(1.0):
            if self.agc is not None:
                self._remember()
            if time.monotonic() - self._checked >= 2.0:
                self._checked = time.monotonic()
                names = default_devices()
                if names and self._defaults is None:
                    self._defaults = names
                elif names and names != self._defaults:
                    if self.before_switch is not None and not self.before_switch():
                        continue  # Jarvis is speaking; switch when it's done
                    log.info("audio devices changed: input %r, output %r (was %r, %r)",
                             names[0], names[1], *self._defaults)
                    self._defaults = names
                    self._reopen()
                    continue
            if time.monotonic() - self._last_frame < 3.0:
                continue
            log.warning("mic stalled, reopening")
            if self.before_switch is not None:
                self.before_switch()
            self._reopen()

    def _reopen(self) -> None:
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
