"""Entry point: python -m jarvis [--selftest] [--no-ui] [--verbose]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
import subprocess
import sys

from .config import ROOT, load_config, setup_logging
from .priority import boost_priority

log = logging.getLogger("jarvis")
_EXIT = {"code": 0}
ELECTRON = ROOT / "ui" / "node_modules" / "electron" / "dist" / "electron.exe"


def _single_instance() -> object | None:
    import win32api
    import win32event
    import winerror

    handle = win32event.CreateMutex(None, False, "Global\\JarvisAssistantBackend")
    if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
        return None
    return handle


def _spawn_ui(port: int, token: str, hotkey: str) -> subprocess.Popen | None:
    if not ELECTRON.exists():
        log.error("overlay not installed: run `npm install` in %s", ROOT / "ui")
        return None
    return subprocess.Popen([str(ELECTRON), str(ROOT / "ui"), f"--jarvis-port={port}", f"--jarvis-token={token}",
                             f"--jarvis-hotkey={hotkey}"], cwd=str(ROOT / "ui"))


async def _run(cfg: dict, with_ui: bool, port: int = 0, token: str = "") -> None:
    from .app import Jarvis
    from .ui_bridge import UIBridge

    token = token or secrets.token_urlsafe(16)
    jarvis: Jarvis | None = None

    async def on_message(msg: dict) -> None:
        if jarvis is not None:
            await jarvis.on_ui(msg)

    bridge = UIBridge(token, on_message)
    port = await bridge.start(port)
    ui = _spawn_ui(port, token, cfg["hotkey"]) if with_ui else None
    jarvis = Jarvis(cfg, bridge)

    async def watch_ui() -> None:
        while ui is not None:
            await asyncio.sleep(1)
            code = ui.poll()
            if code is not None:
                # 0 = user quit from the tray; anything else is a crash the supervisor should recover from
                log.info("overlay exited (code %s); shutting down", code)
                _EXIT["code"] = 0 if code == 0 else 3
                if jarvis.ducker:
                    jarvis.ducker.restore()
                asyncio.get_running_loop().stop()
                return

    asyncio.create_task(watch_ui())
    try:
        await jarvis.start()
    except Exception:
        if ui:
            ui.terminate()
        raise
    if not with_ui:  # console mode: type commands
        asyncio.create_task(_console(jarvis))
    await asyncio.Event().wait()


async def _console(jarvis) -> None:
    print("\nType a command (or just say 'Hey Jarvis'). Ctrl+C to quit.\n")
    while True:
        line = await asyncio.to_thread(sys.stdin.readline)
        if not line:
            return
        if line.strip():
            jarvis.speak_aloud = True
            await jarvis.handle_text(line.strip())


def main() -> None:
    p = argparse.ArgumentParser(prog="jarvis")
    p.add_argument("--selftest", action="store_true", help="check every component and exit")
    p.add_argument("--calibrate", action="store_true", help="tune the wake word and mic level to your voice")
    p.add_argument("--no-ui", action="store_true", help="run without the overlay (type commands in the console)")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)   # dev: fixed UI bridge port
    p.add_argument("--token", default="", help=argparse.SUPPRESS)
    p.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)  # run under the supervisor
    args = p.parse_args()
    setup_logging(args.verbose)
    cfg = load_config()
    if args.selftest:
        from .selftest import run

        sys.exit(run(cfg))
    if args.calibrate:
        from .calibrate import run

        sys.exit(run(cfg))
    if not args.worker:
        lock = _single_instance()
        if lock is None:
            print("Jarvis is already running (look for the orb in the bottom-right corner).")
            sys.exit(0)
        if not args.no_ui:
            sys.exit(_supervise(sys.argv[1:]))
    boost_priority()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main_task = loop.create_task(_run(cfg, not args.no_ui, args.port, args.token))
    main_task.add_done_callback(lambda _t: loop.stop())  # _run only returns on failure
    failed = False
    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if main_task.done() and not main_task.cancelled() and main_task.exception():
            log.error("fatal: %s", main_task.exception())
            failed = True
        log.info("bye")
    sys.exit(1 if failed else _EXIT["code"])


def _supervise(argv: list[str]) -> int:
    """Run the backend as a child and restart it after a crash (not after a normal quit)."""
    import time

    crashes: list[float] = []
    while True:
        code = subprocess.call([sys.executable, "-m", "jarvis", "--worker", *argv])
        if code == 0:
            return 0
        now = time.monotonic()
        crashes = [t for t in crashes if now - t < 600] + [now]
        log.error("backend exited with code %s (crash %d in 10 min)", code, len(crashes))
        if len(crashes) >= 5:
            log.error("giving up after repeated crashes; see this log")
            return code
        time.sleep(2)


if __name__ == "__main__":
    main()
