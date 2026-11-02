"""Lower other apps' volume while you talk to Jarvis, then put it back."""
from __future__ import annotations

import logging
import os
import threading

log = logging.getLogger(__name__)


class Ducker:
    def __init__(self, level: float = 0.25, enabled: bool = True) -> None:
        self.level = level
        self.enabled = enabled
        self._saved: dict[int, float] = {}
        self._lock = threading.Lock()

    def _sessions(self):
        import comtypes
        from pycaw.pycaw import AudioUtilities

        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        me = os.getpid()
        for s in AudioUtilities.GetAllSessions():
            if s.Process is None or s.ProcessId == me:
                continue
            yield s

    def duck(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._saved:
                return
            try:
                for s in self._sessions():
                    vol = s.SimpleAudioVolume
                    cur = vol.GetMasterVolume()
                    self._saved[s.ProcessId] = cur
                    vol.SetMasterVolume(cur * self.level, None)
            except Exception as e:
                log.debug("duck failed: %s", e)

    def restore(self) -> None:
        with self._lock:
            if not self._saved:
                return
            try:
                for s in self._sessions():
                    if s.ProcessId in self._saved:
                        s.SimpleAudioVolume.SetMasterVolume(self._saved[s.ProcessId], None)
            except Exception as e:
                log.debug("restore failed: %s", e)
            self._saved.clear()
