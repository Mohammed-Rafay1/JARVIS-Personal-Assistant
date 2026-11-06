"""A "Hey Jarvis" verifier trained on your own voice (made by `--calibrate`).

openWakeWord's stock "hey_jarvis" model scores many voices low (yours: 0.15-0.36 where 0.5 is its default).
A verifier is a small classifier on the model's audio features, trained on your takes of "Hey Jarvis" (low,
normal and loud; quiet room and noise) against silence, background noise and your ordinary speech. Once the
stock model's score passes a small trigger, openWakeWord replaces it with the verifier's "this is you saying
Hey Jarvis" probability. (openWakeWord's own trainer only learns from moments that already score above 0.5,
which a quiet voice never reaches, so the features are gathered here around each take's best moment.)
"""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np

SR = 16000
STEP = 1280  # 80 ms, the model's frame


def _pcm(audio: np.ndarray, gain: float, lead: int = 0) -> np.ndarray:
    x = np.concatenate([np.zeros(SR // 2 + lead, np.float32), audio.astype(np.float32) * gain,
                        np.zeros(SR // 4, np.float32)])
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def scan(model, name: str, audio: np.ndarray, gain: float = 1.0, lead: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Base score and audio features for every frame of a clip: (scores (n,), features (n, 16, 96))."""
    model.reset()
    pcm = _pcm(audio, gain, lead)
    scores, feats = [], []
    for i in range(0, len(pcm) - STEP + 1, STEP):
        scores.append(float(model.predict(pcm[i:i + STEP])[name]))
        feats.append(model.preprocessor.get_features(model.model_inputs[name])[0])
    return np.array(scores), np.array(feats)


def best_score(model, name: str, audio: np.ndarray, gain: float = 1.0) -> float:
    s, _ = scan(model, name, audio, gain)
    return float(s.max()) if s.size else 0.0


class Clip:
    """One recording, analysed once: its frame scores and features (for checking) and the features to
    train on (the moments around the best score for a take; every other frame for background)."""

    def __init__(self, model, name: str, audio: np.ndarray, gain: float, positive: bool) -> None:
        self.scores, self.feats = scan(model, name, audio, gain)
        if not positive:
            self.train = self.feats[::2]
            return
        out = []
        for lead in (0, 320, 640, 960):  # slightly shifted starts, for a little variety
            s, f = (self.scores, self.feats) if lead == 0 else scan(model, name, audio, gain, lead)
            if not s.size:
                continue
            peak = int(s.argmax())
            floor = max(0.02, 0.35 * float(s[peak]))
            out += [f[j] for j in range(max(0, peak - 2), min(len(s), peak + 3)) if s[j] >= floor]
        self.train = np.array(out) if out else np.empty((0, 16, 96))

    def probability(self, verifier, trigger: float) -> float:
        """What the verifier would say at runtime: its best over frames where the stock score passes the
        trigger (0 if it never does)."""
        idx = np.where(self.scores >= trigger)[0]
        return float(verifier.predict_proba(self.feats[idx])[:, -1].max()) if idx.size else 0.0


def _stack(clips: list[Clip]) -> np.ndarray:
    parts = [c.train for c in clips if len(c.train)]
    return np.vstack(parts) if parts else np.empty((0, 16, 96))


def positive_features(model, name: str, clips: list[np.ndarray], gain: float) -> np.ndarray:
    """The frames around the best moment of each take."""
    return _stack([Clip(model, name, c, gain, True) for c in clips])


def negative_features(model, name: str, clips: list[np.ndarray], gain: float) -> np.ndarray:
    """Every other frame of silence, noise and speech that isn't "Hey Jarvis"."""
    return _stack([Clip(model, name, c, gain, False) for c in clips])


def train(pos: np.ndarray, neg: np.ndarray):
    """A scikit-learn pipeline whose predict_proba(features (1,16,96))[0][-1] is "this is you"."""
    from openwakeword.custom_verifier_model import flatten_features
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import FunctionTransformer, StandardScaler

    x = np.vstack([pos, neg])
    y = np.array([1] * len(pos) + [0] * len(neg))
    clf = LogisticRegression(random_state=0, max_iter=3000, C=0.01, class_weight="balanced")
    return make_pipeline(FunctionTransformer(flatten_features), StandardScaler(), clf).fit(x, y)


def clip_probability(model, name: str, verifier, clip: np.ndarray, gain: float, trigger: float) -> float:
    """What the verifier would say for this recording at runtime."""
    return Clip(model, name, clip, gain, False).probability(verifier, trigger)


def cross_check(positives: list[Clip], negatives: list[Clip], trigger: float,
                folds: int = 4) -> tuple[list[float], list[float]]:
    """Held-out verifier probabilities: each take and each background piece scored by a verifier that never
    saw it. (positive probabilities, negative probabilities)"""
    pos_p, neg_p = [], []
    for k in range(folds):
        tp = [c for i, c in enumerate(positives) if i % folds != k]
        tn = [c for i, c in enumerate(negatives) if i % folds != k]
        if not tp or not tn or not len(_stack(tp)):
            continue
        v = train(_stack(tp), _stack(tn))
        # a take the stock model never reacts to can't wake Jarvis this way whatever the verifier says (the
        # speech-recognition check covers those), so it doesn't count for or against the verifier
        pos_p += [c.probability(v, trigger) for i, c in enumerate(positives)
                  if i % folds == k and c.scores.size and c.scores.max() >= trigger]
        neg_p += [c.probability(v, trigger) for i, c in enumerate(negatives) if i % folds == k]
    return pos_p, neg_p


def train_clips(positives: list[Clip], negatives: list[Clip]):
    return train(_stack(positives), _stack(negatives))


def save(verifier, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(verifier, f)


def choose(pos_p: list[float], neg_p: list[float]) -> tuple[bool, float]:
    """Whether the verifier separates you from the background well enough to use, and its threshold."""
    if len(pos_p) < 6 or not neg_p:
        return False, 0.5
    pos_low = float(np.percentile(pos_p, 20))   # 4 in 5 of your held-out takes pass
    neg_high = float(max(neg_p))
    if pos_low <= neg_high + 0.1:               # not clearly apart: don't let it block you
        return False, 0.5
    return True, round(float(np.clip((pos_low + neg_high) / 2, 0.3, 0.8)), 2)
