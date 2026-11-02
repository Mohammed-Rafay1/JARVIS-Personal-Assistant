"""Is the user in a call? (another app is using the microphone right now)"""
from __future__ import annotations

import sys
import winreg

_BASE = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


def _in_use(key) -> bool:
    try:
        stop, _ = winreg.QueryValueEx(key, "LastUsedTimeStop")
        start, _ = winreg.QueryValueEx(key, "LastUsedTimeStart")
        return start > 0 and stop == 0
    except OSError:
        return False


def mic_in_use_by_others() -> bool:
    me = sys.executable.lower().replace("\\", "#")
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BASE)
    except OSError:
        return False
    with root:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(root, name) as k:
                if name == "NonPackaged":
                    j = 0
                    while True:
                        try:
                            app = winreg.EnumKey(k, j)
                        except OSError:
                            break
                        j += 1
                        low = app.lower()
                        if low == me or "python" in low or "electron" in low:
                            continue
                        with winreg.OpenKey(k, app) as ak:
                            if _in_use(ak):
                                return True
                elif _in_use(k):
                    return True
    return False
