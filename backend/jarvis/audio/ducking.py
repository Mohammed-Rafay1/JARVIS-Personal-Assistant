"""Lower other apps' volume while you talk to Jarvis, then put it back.

18 Nov: Chrome had ended up at 0% and Spotify at 2%. Each call lowered apps to a quarter of their *current*
volume, and when Jarvis was stopped mid-call (a crash, a forced restart) they were never put back, so the next
call took a quarter of the lowered level (100 -> 25 -> 6 -> 1.5 -> 0), and Windows remembers each app's volume.
Now the volumes from before are written to disk first and put back on the next start if Jarvis didn't get to
do it; apps are matched by name (an app that restarts mid-call still gets its volume back).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)


def _state_file() -> Path:
    from ..config import DATA

    return DATA / "ducked.json"


class Ducker:
    def __init__(self, level: float = 0.25, enabled: bool = True, state_path: Path | None = None) -> None:
        self.level = level
        self.enabled = enabled
        self.state_path = Path(state_path) if state_path else _state_file()
        self._saved: dict[str, float] = {}    # app (exe name) -> its volume before Jarvis lowered it
        self._lock = threading.Lock()
        self.recover()

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

    @staticmethod
    def _name(s) -> str:
        try:
            return s.Process.name().lower()
        except Exception:
            return f"pid{s.ProcessId}"

    def _write(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._saved), encoding="utf-8")
        except OSError as e:
            log.warning("couldn't save app volumes: %s", e)

    def duck(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._saved:
                return
            try:
                sessions = list(self._sessions())
                for s in sessions:   # an app can have several sessions (Chrome): keep its loudest
                    n = self._name(s)
                    self._saved[n] = max(self._saved.get(n, 0.0), float(s.SimpleAudioVolume.GetMasterVolume()))
                self._write()        # on disk before anything is lowered
                for s in sessions:
                    vol = s.SimpleAudioVolume
                    vol.SetMasterVolume(min(float(vol.GetMasterVolume()), self._saved[self._name(s)] * self.level),
                                        None)
            except Exception as e:
                log.warning("couldn't lower other apps' volume: %s", e)

    def restore(self) -> None:
        with self._lock:
            if not self._saved:
                return
            ok = True
            try:
                for s in self._sessions():
                    n = self._name(s)
                    if n in self._saved:
                        try:
                            s.SimpleAudioVolume.SetMasterVolume(self._saved[n], None)
                        except Exception as e:
                            ok = False
                            log.warning("couldn't put back %s's volume: %s", n, e)
            except Exception as e:
                ok = False
                log.warning("couldn't put back app volumes: %s", e)
            if ok:
                self._saved.clear()
                try:
                    self.state_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def recover(self) -> None:
        """Volumes Jarvis lowered but never put back (it was stopped mid-call): put them back now."""
        try:
            saved = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.is_file() else {}
        except (OSError, ValueError):
            saved = {}
        if not saved:
            return
        log.info("putting back app volumes left lowered: %s",
                 ", ".join(f"{k} {round(v * 100)}%" for k, v in saved.items()))
        self._saved = {str(k): float(v) for k, v in saved.items()}
        self.restore()
