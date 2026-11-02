"""`python -m jarvis --selftest`: check every component and say how to fix what's broken."""
from __future__ import annotations

import asyncio
import time
import traceback
from typing import Callable

import numpy as np

from .config import ROOT


def _check(name: str, fn: Callable[[], str], fix: str, results: list) -> None:
    t = time.perf_counter()
    try:
        detail = fn()
        results.append((name, True, f"{detail} ({time.perf_counter() - t:.1f}s)", ""))
    except Exception as e:
        results.append((name, False, f"{e.__class__.__name__}: {str(e)[:90]}", fix))
        traceback.print_exc()


def run(cfg: dict) -> int:
    results: list = []
    shared: dict = {}

    def mic():
        import sounddevice as sd

        rec = sd.rec(int(16000 * 1.0), samplerate=16000, channels=1, dtype="float32")
        sd.wait()
        rms = float(np.sqrt(np.mean(rec ** 2)))
        return f"{sd.query_devices(kind='input')['name']}, level {rms:.4f}" + (" (silent?)" if rms < 1e-4 else "")

    def speaker():
        import sounddevice as sd

        return sd.query_devices(kind="output")["name"]

    def tts():
        from .audio.tts import SAMPLE_RATE, TTS

        engine = TTS(cfg["tts"]["engine"], cfg["tts"]["voice"], cfg["tts"]["speed"], cfg["tts"]["piper_voice"])
        t = time.perf_counter()
        audio = engine.synth("Open Excel.")
        shared["tts_audio"] = audio
        return f"{engine.name}, {len(audio) / SAMPLE_RATE:.1f}s of audio in {time.perf_counter() - t:.2f}s"

    def stt():
        from .audio.stt import STT

        model = STT(cfg["stt"]["model"], cfg["stt"]["compute_type"], cfg["stt"]["threads"])
        audio = shared.get("tts_audio")
        if audio is None:
            return "loaded"
        a16 = np.interp(np.arange(0, len(audio), 1.5), np.arange(len(audio)), audio).astype(np.float32)
        t = time.perf_counter()
        text = model.transcribe(a16)
        return f"heard {text!r} in {time.perf_counter() - t:.2f}s"

    def wake():
        from .audio.wakeword import WakeWord

        w = WakeWord(cfg["wake_word"]["model"])
        w.process(np.zeros(1280, dtype=np.int16))
        return w.name

    def llm():
        from .brain.llm import LLM

        async def go():
            m = LLM(cfg)
            if not await m.available():
                raise RuntimeError(f"Ollama not running or {m.model} not pulled")
            t = time.perf_counter()
            out = await m.complete("Answer with one word.", "Say ready.", 8)
            return f"{m.model} replied {out!r} in {time.perf_counter() - t:.1f}s"
        return asyncio.run(go())

    def apps():
        from . import tools
        from .tools import CTX
        from .tools.apps import AppIndex

        tools.load_all()
        CTX.cfg = cfg
        idx = AppIndex()
        idx.refresh()
        hit = idx.find("excel")
        return f"{len(idx.apps)} apps; 'excel' -> {hit[0] if hit else 'not found'}"

    def files():
        from .tools import CTX
        from .tools.files import _search_index

        CTX.cfg = cfg
        from pathlib import Path

        hits = _search_index(["jarvis"], [], [Path.home()], 3)
        return f"Windows Search OK ({len(hits)} hits for 'jarvis')"

    def gmail():
        from .tools.gmail import Gmail
        from .tools.mail_imap import ImapMail

        g = ImapMail.connect() or Gmail.connect()  # app password first, as Jarvis itself does
        if g is None:
            raise RuntimeError("not connected")
        return g.me

    def spotify():
        from .tools.spotify import SPOTIFY_EXE

        if not SPOTIFY_EXE.exists():
            raise RuntimeError("Spotify desktop app not found")
        return "desktop app installed"

    def overlay():
        exe = ROOT / "ui" / "node_modules" / "electron" / "dist" / "electron.exe"
        if not exe.exists():
            raise RuntimeError("electron missing")
        return "electron installed"

    _check("Microphone", mic, "Check Windows Settings > Privacy > Microphone, and the default input device.", results)
    _check("Speaker", speaker, "Set a default output device in Windows sound settings.", results)
    _check("Voice (TTS)", tts, "Run: .venv\\Scripts\\python scripts\\download_models.py", results)
    _check("Speech recognition", stt, "Run: .venv\\Scripts\\python scripts\\download_models.py", results)
    _check("Wake word", wake, "Run: .venv\\Scripts\\python scripts\\download_models.py", results)
    _check("Brain (Ollama)", llm, f"Install Ollama, then: ollama pull {cfg['llm']['model']}", results)
    _check("App index", apps, "PowerShell Get-StartApps failed; see log.", results)
    _check("File search", files, "Windows Search service may be off; Jarvis will fall back to scanning.", results)
    _check("Gmail", gmail, "Optional. Run setup_email.bat (see README).", results)
    _check("Spotify", spotify, "Optional. Install Spotify from spotify.com.", results)
    _check("Overlay", overlay, "Run: cd ui && npm install", results)

    print("\n JARVIS self-test\n " + "-" * 70)
    for name, ok, detail, fix in results:
        print(f" {'PASS' if ok else 'FAIL'}  {name:<20} {detail}")
        if not ok:
            print(f"       {'':<20} fix: {fix}")
    required = {"Microphone", "Speaker", "Voice (TTS)", "Speech recognition", "Overlay"}
    bad = [n for n, ok, _, _ in results if not ok and n in required]
    print(" " + "-" * 70)
    print(" Ready." if not bad else f" Not ready: {', '.join(bad)}")
    return 1 if bad else 0
