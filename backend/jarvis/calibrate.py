"""`python -m jarvis --calibrate`: tune the wake word and mic level to your voice (about a minute).

Records a few seconds of quiet, then you saying "Hey Jarvis" four times, then one command.
Picks a mic gain and wake threshold from the measurements and saves them to config.yaml.
"""
from __future__ import annotations

import re
import statistics
import time

import numpy as np
import sounddevice as sd

from .config import ROOT

SR = 16000


def _record(seconds: float) -> np.ndarray:
    a = sd.rec(int(SR * seconds), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    return a[:, 0].astype(np.float32) / 32768.0


def _wake_score(model, name: str, audio: np.ndarray, gain: float) -> float:
    model.reset()
    x = np.concatenate([np.zeros(SR // 2, np.float32), audio * gain])
    pcm = (np.clip(x, -1, 1) * 32767).astype(np.int16)
    best = 0.0
    for i in range(0, len(pcm) - 1280, 1280):
        best = max(best, model.predict(pcm[i:i + 1280])[name])
    return best


def _bar(v: float, width: int = 24) -> str:
    n = int(round(min(1.0, v) * width))
    return "█" * n + "░" * (width - n)


def _save(gain: float, threshold: float, near_peak: float) -> None:
    path = ROOT / "config.yaml"
    s = path.read_text(encoding="utf-8")
    s = re.sub(r"(?m)^(  near_peak:\s*)[\d.]+", rf"\g<1>{near_peak:.2f}", s, count=1)
    s = re.sub(r"(?m)^(  gain:\s*)[\d.]+", rf"\g<1>{gain:.1f}", s, count=1)
    s = re.sub(r"(?m)^(  threshold:\s*)[\d.]+", rf"\g<1>{threshold:.2f}", s, count=1)
    path.write_text(s, encoding="utf-8")


def run(cfg: dict) -> int:
    from openwakeword.model import Model

    from .audio.stt import STT

    print("\n  JARVIS - wake word calibration\n  " + "-" * 44)
    print("  Sit where you normally do and speak at your normal volume.\n")
    model = Model(wakeword_models=[cfg["wake_word"]["model"]], inference_framework="onnx")
    name = next(iter(model.models))
    print("  Loading speech recognition…")
    stt = STT(cfg["stt"]["model"], cfg["stt"]["compute_type"], cfg["stt"]["threads"])
    stt.set_vocabulary(["Spotify", "YouTube", "Chrome", "Excel"])

    input("\n  Step 1/3 - stay quiet for 5 seconds. Press Enter to start…")
    quiet = _record(5)
    quiet_rms = float(np.sqrt(np.mean(quiet ** 2)))
    print(f"  background noise: {_bar(quiet_rms * 10)} {quiet_rms:.4f}")

    print("\n  Step 2/3 - say \"Hey Jarvis\" four times (one per prompt).")
    takes = []
    for i in range(4):
        input(f"  [{i + 1}/4] Press Enter, then say \"Hey Jarvis\"…")
        print("        listening…", end="", flush=True)
        a = _record(2.5)
        peak = float(np.max(np.abs(a)))
        score = _wake_score(model, name, a, 1.0)
        takes.append(a)
        print(f"\r        level {_bar(peak)} {peak:.2f}   wake score {score:.2f}")

    input("\n  Step 3/3 - press Enter, then say \"What time is it?\"…")
    print("        listening…", end="", flush=True)
    cmd = _record(3.5)
    print("\r                    ", end="")

    peaks = [float(np.max(np.abs(a))) for a in takes]
    med_peak = statistics.median(peaks)
    gain = 1.0
    if med_peak < 0.3:  # quiet mic array: boost so the voice peaks around half scale
        gain = float(min(6.0, max(1.0, 0.5 / max(med_peak, 0.02))))
    scores = [_wake_score(model, name, a, gain) for a in takes]
    noise = _wake_score(model, name, quiet, gain)
    heard = stt.transcribe(np.clip(cmd * gain, -1, 1))
    from .brain.intents import wake_phrase

    texts = [stt.quick(np.clip(a * gain, -1, 1)) for a in takes]
    by_whisper = sum(wake_phrase(t) is not None for t in texts)
    near_peak = round(max(0.12, min(0.5, med_peak * gain * 0.4)), 2)

    floor = max(0.15, noise * 1.5 + 0.05)  # stay clear of what silence/background scores
    good = sorted(scores)[1]  # second-weakest take, so one fluffed attempt doesn't drag it down
    threshold = round(min(0.5, max(floor, good * 0.7)), 2)
    hits = sum(s >= threshold for s in scores)

    print("\n  Results\n  " + "-" * 44)
    print(f"  your voice level   {med_peak:.2f}  ->  mic gain {gain:.1f}x")
    print(f"  wake scores        {', '.join(f'{s:.2f}' for s in scores)} (background {noise:.2f})")
    print(f"  wake threshold     {threshold:.2f}  ({hits}/4 of your takes would wake Jarvis)")
    print(f"  Whisper heard      {heard!r}")
    print(f"  backup wake check  {by_whisper}/4 caught by speech recognition: {', '.join(repr(t) for t in texts)}")
    either = sum((sc >= threshold) or (wake_phrase(t) is not None) for sc, t in zip(scores, texts))
    print(f"  overall            {either}/4 of your \"Hey Jarvis\" takes would wake Jarvis")
    if max(scores) < 0.15:
        print("\n  ! The wake word model doesn't recognise your \"Hey Jarvis\" well on this mic.")
        print("    Ctrl+Alt+J and clicking the orb always work. A headset mic usually fixes it.")
    if not heard:
        print("\n  ! Whisper didn't understand the command; check the mic in Windows sound settings.")

    _save(gain, threshold, near_peak)
    print(f"\n  Saved to config.yaml. Restart Jarvis (start_jarvis.bat) to use the new settings.\n")
    time.sleep(0.5)
    return 0
