"""Ubuntu (GNOME on X11) versions of the Windows-only pieces. The Windows modules call in here only when
not running on Windows, so the Windows code paths are unchanged.

Programs opened for the user (apps, files, browsers, terminals) start in their own session with JARVIS's
markers and caches removed, so ./stop_jarvis.sh, which stops only processes carrying JARVIS_ROOT, never
closes them.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

from .config import RUNTIME

log = logging.getLogger(__name__)

# set by start_jarvis.sh for JARVIS's own processes only; the user's originals are kept as JARVIS_ORIG_<name>
_OVERRIDDEN = ("PATH", "XDG_CACHE_HOME", "HF_HOME", "TMPDIR", "npm_config_cache", "electron_config_cache",
               "PIP_CACHE_DIR", "PIP_REQUIRE_VIRTUALENV")
DUCKED = RUNTIME / "ducked.json"


# ---------------------------------------------------------------------- starting things for the user
def clean_env() -> dict[str, str]:
    env = dict(os.environ)
    if "JARVIS_ROOT" in env:
        for k in _OVERRIDDEN:
            orig = env.pop(f"JARVIS_ORIG_{k}", None)
            if orig is not None:
                env[k] = orig
            elif k != "PATH":
                env.pop(k, None)
    for k in [k for k in env if k.startswith("JARVIS_")]:
        del env[k]
    return env


def spawn(argv: list[str], cwd: str | None = None) -> None:
    p = subprocess.Popen(argv, cwd=cwd, env=clean_env(), start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    threading.Thread(target=p.wait, daemon=True).start()  # reap it; the program itself lives on


def open_path(target: str) -> None:
    """Linux os.startfile: a file, folder or URL in its default app."""
    spawn(["xdg-open", str(target)])


_BROWSERS = {"chrome": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"],
             "edge": ["microsoft-edge", "microsoft-edge-stable"], "firefox": ["firefox"],
             "brave": ["brave-browser", "brave"]}
_BROWSERS["google chrome"], _BROWSERS["microsoft edge"] = _BROWSERS["chrome"], _BROWSERS["edge"]


def open_url(url: str, browser: str = "") -> None:
    wanted = browser.lower().strip()
    for exe in _BROWSERS.get(wanted, []):
        path = shutil.which(exe)
        if path:
            spawn([path, url])
            return
    if wanted:
        log.warning("%s not installed; using the default browser", browser)
    open_path(url)


def open_terminal(cwd: str, args: list[str]) -> None:
    if shutil.which("gnome-terminal"):
        spawn(["gnome-terminal", f"--working-directory={cwd}", "--", *args])
    elif shutil.which("x-terminal-emulator"):
        spawn(["x-terminal-emulator", "-e", *args], cwd=cwd)
    else:
        raise RuntimeError("no terminal app found")


def open_spotify(uri: str) -> None:
    """The Spotify app if installed, otherwise the same page in the web player."""
    if shutil.which("spotify") or Path("/snap/bin/spotify").exists():
        open_path(uri)
        return
    kind, _, rest = uri.removeprefix("spotify:").partition(":")
    page = {"search": f"search/{rest}", "collection": "collection/tracks"}.get(kind, "")
    open_path(f"https://open.spotify.com/{page}")


def lock_screen() -> None:
    attempts = (["loginctl", "lock-session"],
                ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path",
                 "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.Lock"],
                ["xdg-screensaver", "lock"])
    for argv in attempts:
        if shutil.which(argv[0]):
            try:
                if subprocess.run(argv, env=clean_env(), capture_output=True, timeout=5).returncode == 0:
                    return
            except (OSError, subprocess.TimeoutExpired):
                continue
    raise RuntimeError("the screen locker didn't respond")


# ---------------------------------------------------------------------- installed apps (.desktop files)
def _desktop_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home / "applications"] + [Path(d) / "applications" for d in dirs.split(":") if d]


def _entry(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    inside = False
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if line.startswith("["):
            inside = line == "[Desktop Entry]"
        elif inside and "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out


def desktop_apps() -> list[dict[str, str]]:
    """Apps shown in the GNOME app grid, in the shape Get-StartApps gives on Windows."""
    desktops = {d.lower() for d in os.environ.get("XDG_CURRENT_DESKTOP", "").split(":") if d}
    seen: set[str] = set()
    out = []
    for base in _desktop_dirs():
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.desktop")):
            app_id = str(f.relative_to(base)).replace("/", "-")
            if app_id in seen:  # the first directory wins, as in the XDG spec
                continue
            seen.add(app_id)
            e = _entry(f)
            hidden = "true" in (e.get("NoDisplay", "").lower(), e.get("Hidden", "").lower())
            if e.get("Type") != "Application" or hidden:
                continue
            only = {d.lower() for d in e.get("OnlyShowIn", "").split(";") if d}
            never = {d.lower() for d in e.get("NotShowIn", "").split(";") if d}
            if (only and not only & desktops) or never & desktops:
                continue
            if e.get("Name"):
                out.append({"Name": e["Name"], "AppID": str(f)})
    return out


def launch_app(desktop_file: str) -> None:
    spawn(["gio", "launch", desktop_file])


# ---------------------------------------------------------------------- windows (X11)
_SKIP_PROCS = {"python", "python3", "electron", "jarvis", "gnome-shell", "Xorg"}
CLOSE_ALIASES = {"fileexplorer": "nautilus", "explorer": "nautilus", "files": "nautilus",
                 "taskmanager": "gnome-system-monitor", "systemmonitor": "gnome-system-monitor",
                 "calculator": "gnome-calculator", "settings": "gnome-control-center",
                 "terminal": "gnome-terminal-server", "texteditor": "gnome-text-editor",
                 "notepad": "gnome-text-editor", "googlechrome": "google-chrome", "chrome": "google-chrome",
                 "vscode": "code", "visualstudiocode": "code"}


def _desktop_names(desktop_file: str) -> set[str]:
    """Process and window-class names an app is likely to show up under."""
    if not desktop_file.endswith(".desktop"):
        return set()
    e = _entry(Path(desktop_file))
    names = {Path(desktop_file).stem}
    exe = [t for t in (e.get("Exec") or "").split() if t != "env" and "=" not in t]  # snaps: "env VAR=x /snap/bin/code"
    if exe:
        names.add(Path(exe[0]).name)
    if e.get("StartupWMClass"):
        names.add(e["StartupWMClass"])
    return {n.lower().replace(" ", "") for n in names if n}


def close_windows(targets: set[str], desktop_file: str = "") -> list[str]:
    """Politely close matching windows (_NET_CLOSE_WINDOW, the same as clicking X: the app may ask to save)."""
    import psutil
    from rapidfuzz import fuzz
    from Xlib import X, display, protocol

    targets = set(targets) | _desktop_names(desktop_file)
    targets |= {CLOSE_ALIASES[t] for t in list(targets) if t in CLOSE_ALIASES}
    d = display.Display()
    closed: set[str] = set()
    try:
        root = d.screen().root
        atom = d.intern_atom
        normal = {atom("_NET_WM_WINDOW_TYPE_NORMAL"), atom("_NET_WM_WINDOW_TYPE_DIALOG")}
        clients = root.get_full_property(atom("_NET_CLIENT_LIST"), X.AnyPropertyType)
        for wid in clients.value if clients else []:
            w = d.create_resource_object("window", wid)
            try:
                wtype = w.get_full_property(atom("_NET_WM_WINDOW_TYPE"), X.AnyPropertyType)
                if wtype and not normal & set(wtype.value):
                    continue
                pid_prop = w.get_full_property(atom("_NET_WM_PID"), X.AnyPropertyType)
                pid = int(pid_prop.value[0]) if pid_prop else 0
                proc = psutil.Process(pid).name() if pid else ""
                wm_class = w.get_wm_class() or ()
            except Exception:
                continue
            if proc in _SKIP_PROCS or pid == os.getpid():
                continue
            keys = {k.lower().replace(" ", "") for k in (*wm_class, proc) if k}
            if keys & targets or any(t and fuzz.ratio(t, k) >= 85 for t in targets for k in keys):
                ev = protocol.event.ClientMessage(window=w, client_type=atom("_NET_CLOSE_WINDOW"),
                                                  data=(32, [X.CurrentTime, 2, 0, 0, 0]))
                root.send_event(ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
                closed.add(proc or wm_class[-1])
        d.flush()
    finally:
        d.close()
    return sorted(closed)


# Windows virtual-key codes from tools/media.py -> X keysyms GNOME routes to the active media player
_MEDIA_KEYSYMS = {0xB3: 0x1008FF14, 0xB2: 0x1008FF15, 0xB1: 0x1008FF16, 0xB0: 0x1008FF17, 0xAD: 0x1008FF12}


def media_key(vk: int) -> None:
    from Xlib import X, display
    from Xlib.ext import xtest

    d = display.Display()
    try:
        code = d.keysym_to_keycode(_MEDIA_KEYSYMS[vk])
        if not code:
            raise RuntimeError("this keyboard layout has no media keys")
        xtest.fake_input(d, X.KeyPress, code)
        xtest.fake_input(d, X.KeyRelease, code)
        d.sync()
    finally:
        d.close()


# ---------------------------------------------------------------------- sound (PipeWire via its PulseAudio API)
@contextlib.contextmanager
def _pulse():
    """A short-lived connection per call: nothing goes stale across sleep/wake or a PipeWire restart."""
    import pulsectl

    p = pulsectl.Pulse("jarvis", connect=False)
    try:
        p.connect(timeout=2)
        yield p
    finally:
        p.close()


def _client_pids() -> dict[str, int]:
    """PipeWire client id -> process id. Apps using ALSA (JARVIS itself, via PortAudio) carry their process id
    only on the client, which PipeWire's PulseAudio interface doesn't show."""
    try:
        objects = json.loads(subprocess.run(["pw-dump"], capture_output=True, timeout=3, check=True).stdout)
    except Exception as e:
        log.debug("pw-dump: %s", e)
        return {}
    out = {}
    for o in objects:
        pid = ((o.get("info") or {}).get("props") or {}).get("application.process.id")
        if o.get("type") == "PipeWire:Interface:Client" and pid:
            out[str(o.get("id"))] = int(pid)
    return out


def _pid(stream, clients: dict[str, int]) -> int:
    try:
        pid = int(stream.proplist.get("application.process.id", 0))
    except (TypeError, ValueError):
        pid = 0
    return pid or clients.get(str(stream.proplist.get("client.id", "")), 0)


class Endpoint:
    """The default speaker, with the pycaw methods tools/media.py uses."""

    def _call(self, fn):
        try:
            with _pulse() as p:
                return fn(p, p.get_sink_by_name(p.server_info().default_sink_name))
        except Exception as e:
            log.warning("sound system: %s", e)
            raise RuntimeError("I couldn't reach the sound system") from None

    def GetMasterVolumeLevelScalar(self) -> float:
        return self._call(lambda p, s: p.volume_get_all_chans(s))

    def SetMasterVolumeLevelScalar(self, value: float, _ctx=None) -> None:
        self._call(lambda p, s: p.volume_set_all_chans(s, max(0.0, min(1.0, float(value)))))

    def SetMute(self, on, _ctx=None) -> None:
        self._call(lambda p, s: p.mute(s, bool(on)))


class _StreamVolume:
    def __init__(self, index: int, volume: float) -> None:
        self.index, self.volume = index, volume

    def GetMasterVolume(self) -> float:
        return self.volume

    def SetMasterVolume(self, value: float, _ctx=None) -> None:
        try:
            with _pulse() as p:
                p.volume_set_all_chans(p.sink_input_info(self.index), max(0.0, float(value)))
        except Exception as e:
            log.debug("stream volume: %s", e)


class _Session:
    def __init__(self, pid: int, index: int, volume: float) -> None:
        self.Process = self.ProcessId = pid
        self.SimpleAudioVolume = _StreamVolume(index, volume)


def audio_sessions() -> list[_Session]:
    """Other apps' playback streams, shaped like pycaw sessions for audio/ducking.py. Empty if unreachable."""
    try:
        with _pulse() as p:
            streams = p.sink_input_list()
    except Exception as e:
        log.debug("ducking: sound system unavailable: %s", e)
        return []
    me, clients = os.getpid(), _client_pids()
    pids = {s.index: _pid(s, clients) for s in streams}
    return [_Session(pids[s.index], s.index, s.volume.value_flat) for s in streams if pids[s.index] not in (0, me)]


def remember_ducked(saved: dict[int, float]) -> None:
    """Keep the pre-duck volumes on disk while ducked, so ./stop_jarvis.sh can put them back after a crash."""
    try:
        if saved:
            RUNTIME.mkdir(parents=True, exist_ok=True)
            DUCKED.write_text(json.dumps({str(k): v for k, v in saved.items()}), encoding="utf-8")
        else:
            DUCKED.unlink(missing_ok=True)
    except OSError as e:
        log.debug("ducked state: %s", e)


def restore_ducked() -> None:
    try:
        saved = {int(k): float(v) for k, v in json.loads(DUCKED.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError):
        return
    for s in audio_sessions():
        if s.ProcessId in saved:
            s.SimpleAudioVolume.SetMasterVolume(saved[s.ProcessId])
    DUCKED.unlink(missing_ok=True)


def mic_in_use_by_others() -> bool:
    """Another app (Teams, Meet in a browser, Zoom...) is recording from a microphone right now."""
    try:
        with _pulse() as p:
            monitors = {s.index for s in p.source_list() if s.name.endswith(".monitor")}
            streams = p.source_output_list()
    except Exception as e:
        log.debug("mic check: sound system unavailable: %s", e)
        return False
    me, clients = os.getpid(), _client_pids()
    return any(s.source not in monitors and _pid(s, clients) != me for s in streams)


# ---------------------------------------------------------------------- one JARVIS at a time
def single_instance():
    """An exclusive lock the kernel drops when this process ends, so a crash can't leave it stuck."""
    import fcntl

    RUNTIME.mkdir(parents=True, exist_ok=True)
    f = open(RUNTIME / "backend.lock", "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f
