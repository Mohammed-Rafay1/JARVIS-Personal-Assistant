"""Paths, settings and logging."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
MODELS = DATA / "models"
LOGS = DATA / "logs"
SECRETS = ROOT / "secrets"

DEFAULTS: dict[str, Any] = {
    "address_as": "sir",
    "wake_word": {"enabled": True, "model": "hey_jarvis", "threshold": 0.45,
                  "threshold_while_speaking": 0.75, "whisper_backup": True, "near_peak": 0.3},
    "hotkey": "Control+Alt+J",
    "mic": {"gain": 1.0},
    "call": {"first_timeout": 8, "idle_timeout": 90, "greeting": True, "barge_in": True, "barge_in_ratio": 2.5,
             "rethink_min_rms": 1800},
    "listening": {"silence_ms": 600, "short_silence_ms": 1000, "max_seconds": 12, "no_speech_timeout": 5,
                  "follow_up_seconds": 6, "follow_up_after_chat": False,
                  "vad_aggressiveness": 2},
    "stt": {"model": "base.en", "accurate_model": "small.en", "retry_below": -0.6, "retry_max_seconds": 6, "compute_type": "int8", "threads": 4},
    "tts": {"engine": "auto", "piper_voice": "en_GB-alan-medium", "voice": "bm_george", "speed": 1.05},
    "speak_typed_replies": False,
    "greet_on_start": True,
    "llm": {"enabled": True, "model": "qwen2.5:3b", "host": "http://127.0.0.1:11434",
            "keep_alive": "30m"},
    "music": {"default_service": "youtube"},
    "ducking": {"enabled": True, "level": 0.25},
    "claude_code": {"allow_tasks": False},
    "files": {"search_roots": ["~"]},
    "email": {"enabled": True, "poll_seconds": 60, "announce": True, "only_primary": True,
              "vip": [], "quiet_hours": ["22:00", "07:30"], "hold_during_calls": True,
              "signature": ""},
    "apps": {"aliases": {}},
    "websites": {},
    "ui": {"idle_dock": True},
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load_config() -> dict[str, Any]:
    path = ROOT / "config.yaml"
    user = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    return _merge(DEFAULTS, user or {})


def load_contacts() -> dict[str, str]:
    path = ROOT / "contacts.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return {str(k).lower(): str(v) for k, v in data.items()}


def setup_logging(verbose: bool = False) -> None:
    for d in (DATA, MODELS, LOGS):
        d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fh = logging.handlers.RotatingFileHandler(LOGS / "jarvis.log", maxBytes=2_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.handlers[:] = [fh, sh]
    for noisy in ("httpx", "httpcore", "websockets", "comtypes", "googleapiclient", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
