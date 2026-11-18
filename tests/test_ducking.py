"""Other apps' volume (18 Nov: Chrome ended at 0%, Spotify 2%): lowered during a call, always put back, never
compounding, and recovered after Jarvis was stopped mid-call."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.audio.ducking import Ducker  # noqa: E402


class Vol:
    def __init__(self, v):
        self.v = v

    def GetMasterVolume(self):
        return self.v

    def SetMasterVolume(self, v, _ctx):
        self.v = v


class Sess:
    def __init__(self, name, pid, v):
        self.ProcessId = pid
        self.Process = type("P", (), {"name": lambda s, n=name: n})()
        self.SimpleAudioVolume = Vol(v)


class Mixer:
    """Windows' per-app volumes; it remembers them by app, as Windows does."""
    def __init__(self, **apps):
        self.apps = {n: v for n, v in apps.items()}
        self.pid = 100

    def sessions(self):
        self.pid += 1
        out = [Sess(n, self.pid, v) for n, v in self.apps.items()]
        self._live = out
        return out

    def sync(self):
        for s in self._live:
            self.apps[s.Process.name()] = s.SimpleAudioVolume.v


def _ducker(mixer, path, level=0.25):
    d = Ducker.__new__(Ducker)
    d.level, d.enabled, d.state_path, d._saved = level, True, path, {}
    import threading
    d._lock = threading.Lock()
    d._sessions = lambda: iter(mixer.sessions())
    return d


def test_lowered_during_a_call_and_put_back(tmp_path):
    m = Mixer(**{"chrome.exe": 1.0, "spotify.exe": 0.8})
    d = _ducker(m, tmp_path / "d.json")
    d.duck()
    m.sync()
    assert m.apps == {"chrome.exe": 0.25, "spotify.exe": 0.2} and (tmp_path / "d.json").is_file()
    d.restore()
    m.sync()
    assert m.apps == {"chrome.exe": 1.0, "spotify.exe": 0.8} and not (tmp_path / "d.json").exists()


def test_stopped_mid_call_is_recovered_on_the_next_start_and_never_compounds(tmp_path):
    m = Mixer(**{"chrome.exe": 1.0})
    path = tmp_path / "d.json"
    d = _ducker(m, path)
    d.duck()
    m.sync()                       # Jarvis is force-stopped here: no restore
    assert m.apps["chrome.exe"] == 0.25
    d2 = _ducker(m, path)          # the next start
    d2.recover()
    m.sync()
    assert m.apps["chrome.exe"] == 1.0 and not path.exists()
    for _ in range(5):             # many calls later, still a quarter of the real volume
        d2.duck()
        m.sync()
        assert m.apps["chrome.exe"] == 0.25
        d2.restore()
        m.sync()
    assert m.apps["chrome.exe"] == 1.0


def test_app_restarted_mid_call_still_gets_its_volume_back(tmp_path):
    m = Mixer(**{"chrome.exe": 0.9})
    d = _ducker(m, tmp_path / "d.json")
    d.duck()
    m.sync()
    d.restore()                    # new session objects and process ids: matched by app name
    m.sync()
    assert m.apps["chrome.exe"] == 0.9
