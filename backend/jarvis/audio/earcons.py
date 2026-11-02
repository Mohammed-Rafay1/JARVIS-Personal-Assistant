"""Short UI sounds, synthesised so there are no files to ship."""
from __future__ import annotations

import numpy as np

from .tts import SAMPLE_RATE


def _tone(freqs: list[float], dur: float, vol: float = 0.25, decay: float = 6.0) -> np.ndarray:
    t = np.arange(int(SAMPLE_RATE * dur)) / SAMPLE_RATE
    env = np.minimum(1, t / 0.008) * np.exp(-decay * t)
    sig = sum(np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t) for f in freqs)
    return (vol * env * sig / len(freqs)).astype(np.float32)


def _seq(*parts: np.ndarray, gap: float = 0.0) -> np.ndarray:
    silence = np.zeros(int(SAMPLE_RATE * gap), dtype=np.float32)
    out = []
    for p in parts:
        out += [p, silence]
    return np.concatenate(out)


WAKE = _seq(_tone([880], 0.09, 0.18), _tone([1318.5], 0.18, 0.2), gap=0.0)   # rising two-note chime
HEARD = _tone([660], 0.08, 0.12, decay=20)                                     # soft tick: got it
DONE = _seq(_tone([1046.5], 0.06, 0.12), _tone([1568], 0.12, 0.12))
ERROR = _seq(_tone([392], 0.12, 0.18), _tone([311], 0.2, 0.18))
END = _seq(_tone([1046.5], 0.08, 0.12), _tone([659.3], 0.2, 0.12))      # falling: call over
NOTIFY = _seq(_tone([987.8, 1318.5], 0.15, 0.18), _tone([1318.5, 1760], 0.25, 0.16), gap=0.03)
