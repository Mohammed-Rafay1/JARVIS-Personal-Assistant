"""Process priority for responsiveness."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

log = logging.getLogger(__name__)

_OLLAMA = ("ollama.exe", "llama-server.exe", "ollama_llama_server.exe")


class _ThrottleState(ctypes.Structure):
    _fields_ = [("Version", wintypes.ULONG), ("ControlMask", wintypes.ULONG), ("StateMask", wintypes.ULONG)]


def _full_speed(pid: int) -> bool:
    """Opt a process out of Windows 11's power throttling ("efficiency mode", EcoQoS). Windows applies it to
    background processes without a visible window, like Jarvis and Ollama, and runs them on slower cores and clocks.
    This asks for normal speed only; it changes no system setting and takes no CPU from other apps."""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x0200 | 0x1000, False, pid)   # PROCESS_SET_INFORMATION | QUERY_LIMITED_INFORMATION
    if not h:
        return False
    try:
        st = _ThrottleState(1, 0x1, 0x0)   # control EXECUTION_SPEED, state off = not throttled
        return bool(k32.SetProcessInformation(h, 4, ctypes.byref(st), ctypes.sizeof(st)))   # ProcessPowerThrottling
    except Exception:
        return False
    finally:
        k32.CloseHandle(h)


def boost_priority() -> None:
    """Above-normal CPU priority and no power throttling for Jarvis and Ollama's model runner: when a build, a data
    job or Docker is eating the CPU, your voice command still gets processed first (it's idle otherwise). Run again
    from time to time: Ollama starts a fresh runner process whenever it loads the model."""
    import psutil

    try:
        me = psutil.Process()
        me.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        _full_speed(me.pid)
        parent = me.parent()   # the supervisor that restarts Jarvis after a crash
        if parent is not None and "python" in parent.name().lower():
            _full_speed(parent.pid)
    except Exception as e:
        log.debug("priority: %s", e)
    for p in psutil.process_iter(["name"]):
        if (p.info["name"] or "").lower() in _OLLAMA:
            try:
                if p.nice() == psutil.NORMAL_PRIORITY_CLASS:
                    p.nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
                _full_speed(p.pid)
            except Exception:
                pass
