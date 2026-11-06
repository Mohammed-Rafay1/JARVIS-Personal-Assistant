"""'Hey Jarvis' detection with openWakeWord (ONNX, offline)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np


log = logging.getLogger(__name__)


class WakeWord:
    def __init__(self, model_name: str = "hey_jarvis", threshold: float = 0.5, verifier: str = "",
                 verifier_trigger: float = 0.05) -> None:
        """verifier: a model of your own "Hey Jarvis" made by calibrate_wake.bat (see audio/voiceprint.py).
        With it, once the stock score passes verifier_trigger the score becomes "this is you" (0-1)."""
        import openwakeword
        from openwakeword.model import Model

        extra = {}
        if verifier and Path(verifier).exists():
            extra = {"custom_verifier_models": {model_name: str(verifier)}, "custom_verifier_threshold": verifier_trigger}
        try:
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx", **extra)
        except FileNotFoundError:
            openwakeword.utils.download_models([model_name])
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx", **extra)
        except Exception:
            if not extra:
                raise
            log.exception("your voice model couldn't be loaded; using the standard wake word")
            extra = {}
            self.model = Model(wakeword_models=[model_name], inference_framework="onnx")
        self.name = next(iter(self.model.models))
        self.threshold = threshold
        self.personal = bool(extra)
        self._cooldown_until = 0.0
        self._near_logged = 0.0
        self.last_score = 0.0
        log.info("wake word ready: %s (threshold %.2f%s)", self.name, threshold,
                 f", tuned to your voice, trigger {verifier_trigger:.2f}" if extra else "")

    def process(self, frame: np.ndarray, threshold: float | None = None) -> bool:
        """frame: int16, 1280 samples. True when the wake word fires."""
        score = self.model.predict(frame)[self.name]
        now = time.monotonic()
        thr = threshold or self.threshold
        if 0.08 <= score < thr and now - self._near_logged > 1.5:
            # near misses tell us whether the threshold suits this voice and microphone
            log.info("wake near-miss (score %.2f, threshold %.2f)", score, thr)
            self._near_logged = now
        if score >= (threshold or self.threshold) and now >= self._cooldown_until:
            log.info("wake word (score %.2f)", score)
            self._cooldown_until = now + 2.0
            self.last_score = float(score)
            self.model.reset()
            return True
        return False

    def reset(self) -> None:
        self.model.reset()
