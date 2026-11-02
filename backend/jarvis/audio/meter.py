"""Loudness and spectrum for the orb."""
from __future__ import annotations

import numpy as np

N_BANDS = 32


def _band_edges(n_fft: int, sr: int) -> np.ndarray:
    # log-spaced bands between 80 Hz and 8 kHz (voice range)
    freqs = np.geomspace(80, min(8000, sr / 2 - 1), N_BANDS + 1)
    return np.clip((freqs / (sr / 2) * (n_fft // 2)).astype(int), 1, n_fft // 2)


_EDGES: dict[tuple[int, int], np.ndarray] = {}


def levels(x: np.ndarray, sr: int) -> tuple[float, list[float]]:
    """x: float32 mono in [-1, 1]. Returns (rms 0..1, 32 bands 0..1)."""
    if x.size == 0:
        return 0.0, [0.0] * N_BANDS
    rms = float(np.sqrt(np.mean(x * x)))
    # perceptual-ish scaling: -50 dBFS -> 0, -10 dBFS -> 1
    db = 20 * np.log10(rms + 1e-9)
    level = float(np.clip((db + 50) / 40, 0, 1))
    n = 512 if x.size >= 512 else int(2 ** np.floor(np.log2(max(x.size, 32))))
    seg = x[-n:]
    if seg.size < n:  # tail of a clip shorter than the window
        seg = np.pad(seg, (n - seg.size, 0))
    seg = seg * np.hanning(n)
    spec = np.abs(np.fft.rfft(seg))
    key = (n, sr)
    if key not in _EDGES:
        _EDGES[key] = _band_edges(n, sr)
    e = _EDGES[key]
    bands = [float(spec[e[i]:max(e[i + 1], e[i] + 1)].mean()) for i in range(N_BANDS)]
    bands_db = 20 * np.log10(np.asarray(bands) + 1e-9)
    bands = np.clip((bands_db + 10) / 50, 0, 1)
    return round(level, 3), [round(float(b), 3) for b in bands]
