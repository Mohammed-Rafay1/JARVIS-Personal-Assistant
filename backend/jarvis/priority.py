"""Process priority for responsiveness."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def boost_priority() -> None:
    """Above-normal CPU priority for Jarvis and Ollama's model runner: when a build, a data job or
    Docker is eating the CPU, your voice command still gets processed first (it's idle otherwise)."""
    import psutil

    try:
        psutil.Process().nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
    except Exception as e:
        log.debug("priority: %s", e)
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() in ("ollama.exe", "llama-server.exe"):
            try:
                if p.nice() == psutil.NORMAL_PRIORITY_CLASS:
                    p.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
            except Exception:
                pass
