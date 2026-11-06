"""`python -m jarvis --calibrate`: tune "Hey Jarvis" and the mic to your voice (about 6-8 minutes).

Two settings, because Jarvis has to work in both: a quiet room, then with background noise (music, a video,
people talking). In each you say "Hey Jarvis" in a low, a normal and a loud voice, and also talk without it,
so Jarvis learns what is you calling it and what isn't. From the recordings it picks the mic boost, the
wake thresholds and the level for the speech-recognition wake check, and trains a small model of your own
"Hey Jarvis" (audio/voiceprint.py). Everything stays on this laptop (data/voice); nothing is uploaded.
"""
from __future__ import annotations

import re
import statistics
import time
import wave

import numpy as np
import sounddevice as sd

from .config import DATA, ROOT

SR = 16000
VOICE = DATA / "voice"
VERIFIER = "data/voice/wake_verifier.pkl"  # relative to the Jarvis folder, as written to config.yaml

READ_QUIET = ("Please open the budget spreadsheet and check the totals for last month, "
              "then send a short summary to the team before lunch.")
READ_NOISE = "I think we should meet on Thursday afternoon to go through the new designs together."


# ------------------------------------------------------------------ recording
def _record(seconds: float) -> np.ndarray:
    a = sd.rec(int(SR * seconds), samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    return a[:, 0].astype(np.float32) / 32768.0


def _bar(v: float, width: int = 24) -> str:
    n = int(round(min(1.0, max(0.0, v)) * width))
    return "█" * n + "░" * (width - n)


def _save_wav(path, audio: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())


class Session:
    def __init__(self, model, name: str) -> None:
        self.model, self.name = model, name
        self.takes: list[tuple[str, np.ndarray]] = []   # (condition, audio) of "Hey Jarvis"
        self.background: list[tuple[str, np.ndarray]] = []  # silence, noise, ordinary speech
        self.commands: list[np.ndarray] = []

    def wake_takes(self, cond: str, how: str, n: int) -> None:
        from .audio.voiceprint import best_score

        print(f"\n  Say \"Hey Jarvis\" {how}, {n} times (press Enter before each one).")
        for i in range(n):
            for attempt in range(2):
                input(f"   [{i + 1}/{n}] Enter, then say \"Hey Jarvis\" {how}… ")
                a = _record(2.5)
                peak = float(np.max(np.abs(a)))
                if peak >= 0.015 or attempt:
                    break
                print("        I didn't hear anything - once more, a little closer or louder.")
            score = best_score(self.model, self.name, a)
            print(f"        level {_bar(peak)} {peak:.2f}   wake score {score:.2f}")
            self.takes.append((cond, a))

    def listen(self, cond: str, seconds: float, prompt: str) -> np.ndarray:
        input(f"\n  {prompt}\n  Press Enter to start ({seconds:.0f} s)… ")
        print("        recording…", end="", flush=True)
        a = _record(seconds)
        rms = float(np.sqrt(np.mean(a ** 2)))
        print(f"\r        level {_bar(rms * 10)} {rms:.4f}")
        self.background.append((cond, a))
        return a

    def command(self, prompt: str) -> None:
        input(f"\n  {prompt}\n  Press Enter, then say it… ")
        self.commands.append(_record(4.0))


# ------------------------------------------------------------------ choosing the settings
def choose_gain(normal_peaks: list[float], loud_peaks: list[float]) -> float:
    """Boost a quiet mic so your normal voice peaks around half scale, without clipping a loud voice."""
    med = statistics.median(normal_peaks) if normal_peaks else 0.5
    gain = 1.0 if med >= 0.3 else min(6.0, 0.5 / max(med, 0.02))
    if loud_peaks:
        gain = min(gain, 0.98 / max(max(loud_peaks), 1e-3))
    return round(max(1.0, gain), 1)


def choose_base_threshold(take_scores: list[float], background_scores: list[float]) -> float:
    """Without a voice model: low enough for most takes (low voice included), above what background scores."""
    floor = max(0.1, (max(background_scores) if background_scores else 0.0) * 1.2 + 0.03)
    target = float(np.percentile(take_scores, 25)) * 0.8 if take_scores else 0.15
    return round(float(np.clip(max(floor, target), 0.1, 0.5)), 2)


def choose_trigger(take_scores: list[float]) -> float:
    """The stock score at which your voice model takes over: below your weakest takes."""
    weakest = float(np.percentile(take_scores, 10)) if take_scores else 0.05
    return round(float(np.clip(weakest * 0.6, 0.03, 0.1)), 2)


def choose_near_peak(low_peaks: list[float], normal_peaks: list[float], noise_peaks: list[float],
                     gain: float) -> tuple[float, bool]:
    """How loud a short phrase must be for the speech-recognition wake check: catches your low voice, but
    stays above background noise. (value, whether your low voice is quieter than the noise)"""
    low = statistics.median(low_peaks) if low_peaks else 0.2
    normal = statistics.median(normal_peaks) if normal_peaks else 0.4
    near = min(low * gain * 0.7, normal * gain * 0.4)
    noisy = max(noise_peaks) * gain if noise_peaks else 0.0
    drowned = near <= noisy
    if drowned:
        near = noisy * 1.05
    return round(float(np.clip(near, 0.08, 0.5)), 2), drowned


def save_config(values: dict) -> None:
    """Write wake_word / mic settings into config.yaml, keeping its comments; missing keys are added."""
    path = ROOT / "config.yaml"
    s = path.read_text(encoding="utf-8")
    notes = {"verifier": "# your own \"Hey Jarvis\" model from calibrate_wake.bat (\"\" = the standard one)",
             "verifier_trigger": "# stock score at which your voice model takes over"}
    for key, val in values.items():
        if key == "gain":
            s = re.sub(r"(?m)^(  gain:\s*)[\d.]+", rf"\g<1>{val:.1f}", s, count=1)
            continue
        text = f'"{val}"' if isinstance(val, str) else f"{val:.2f}"
        pat = rf"(?m)^(  {key}:)(\s*)(?:\"[^\"]*\"|[\d.]+)([ \t]*)(#.*)?$"
        if re.search(pat, s):
            def put(m, text=text):  # the new value, with the comment kept in its column
                head = f"{m.group(1)} {text}"
                if not m.group(4):
                    return head
                width = len(m.group(0)) - len(m.group(4))
                return head.ljust(width) if len(head) < width else head + " "
            s = re.sub(pat, lambda m: put(m) + (m.group(4) or ""), s, count=1)
        else:  # a new setting: add it to the wake_word section
            line = f"  {key}: {text}".ljust(29) + notes.get(key, "")
            s = re.sub(r"(?m)^(  threshold_while_speaking:.*)$", lambda m: m.group(1) + "\n" + line, s, count=1)
    path.write_text(s, encoding="utf-8")


# ------------------------------------------------------------------ the session
def run(cfg: dict) -> int:
    from openwakeword.model import Model

    from .audio import voiceprint as vp
    from .audio.stt import STT
    from .brain.intents import wake_phrase

    print("\n  JARVIS - voice calibration\n  " + "=" * 52)
    print("  About 6-8 minutes. Sit where you usually do, about as far from the laptop as usual.")
    print("  Part 1 is in a quiet room, part 2 with background noise. Each step says what to do.")
    model = Model(wakeword_models=[cfg["wake_word"]["model"]], inference_framework="onnx")
    name = next(iter(model.models))
    print("  Loading speech recognition…")
    stt = STT(cfg["stt"]["model"], cfg["stt"]["compute_type"], cfg["stt"]["threads"])
    stt.set_vocabulary(["Spotify", "YouTube", "Chrome", "Excel"])
    sess = Session(model, name)

    print("\n  PART 1 of 2 - QUIET ROOM\n  " + "-" * 52)
    print("  Turn off music and videos. Close the door if it's noisy outside.")
    sess.listen("silence", 8, "Step 1 - stay completely quiet.")
    sess.wake_takes("quiet-normal", "in your normal voice", 4)
    sess.wake_takes("quiet-low", "quietly, almost a whisper, as if someone is asleep nearby", 4)
    sess.wake_takes("quiet-loud", "loudly, as if calling from across the room", 3)
    sess.listen("speech", 12, f"Read this aloud at your normal voice (no \"Jarvis\"):\n    \"{READ_QUIET}\"")
    sess.command("Say in one breath: \"Hey Jarvis, what time is it?\"")

    print("\n  PART 2 of 2 - WITH BACKGROUND NOISE\n  " + "-" * 52)
    print("  Play music or a video at a normal volume (on this laptop or nearby), or turn on a fan,")
    print("  or let the room be as busy as it usually gets. Keep it playing until the end.")
    input("  Press Enter once the noise is on… ")
    sess.listen("noise", 10, "Stay quiet and let the noise play.")
    sess.wake_takes("noise-normal", "in your normal voice", 4)
    sess.wake_takes("noise-loud", "loudly, over the noise", 3)
    sess.wake_takes("noise-low", "quietly", 3)
    sess.listen("speech-noise", 10, f"Read this aloud over the noise (no \"Jarvis\"):\n    \"{READ_NOISE}\"")
    sess.command("Say in one breath: \"Hey Jarvis, open Chrome.\"")
    print("\n  You can stop the noise now. Working it out (this takes a minute or two)…")

    # ---- the mic boost, from your voice levels
    peaks = {c: [float(np.max(np.abs(a))) for cc, a in sess.takes if cc == c] for c, _ in sess.takes}
    normal_peaks = peaks.get("quiet-normal", []) + peaks.get("noise-normal", [])
    loud_peaks = peaks.get("quiet-loud", []) + peaks.get("noise-loud", [])
    low_peaks = peaks.get("quiet-low", []) + peaks.get("noise-low", [])
    gain = choose_gain(peaks.get("quiet-normal", normal_peaks), loud_peaks)

    # ---- what the stock model gives you, and the background
    take_scores = [vp.best_score(model, name, a, gain) for _, a in sess.takes]
    bg_scores = [vp.best_score(model, name, a, gain) for _, a in sess.background]
    noise_peaks = [float(np.max(np.abs(a))) for c, a in sess.background if c in ("noise",)]

    # ---- your own voice model, checked on takes it never saw
    trigger = choose_trigger(take_scores)
    positives = [a for _, a in sess.takes]
    # longer background clips are cut in 2 s pieces so the check sees many separate moments
    negatives = []
    for _, a in sess.background:
        negatives += [a[i:i + 2 * SR] for i in range(0, max(1, len(a) - SR), 2 * SR) if len(a[i:i + 2 * SR]) > SR // 2]
    print("  Checking how well a model of your voice tells you apart from the background…")
    pos_clips = [vp.Clip(model, name, a, gain, True) for a in positives]
    neg_clips = [vp.Clip(model, name, a, gain, False) for a in negatives]
    pos_p, neg_p = vp.cross_check(pos_clips, neg_clips, trigger)
    use_verifier, v_threshold = vp.choose(pos_p, neg_p)
    verifier = None
    if use_verifier:
        verifier = vp.train_clips(pos_clips, neg_clips)
        vp.save(verifier, ROOT / VERIFIER)
        threshold = v_threshold
        verify_below = round(min(0.9, threshold + 0.2), 2)
        accept_unheard = threshold
    else:
        threshold = choose_base_threshold(take_scores, bg_scores)
        verify_below = 0.5
        accept_unheard = round(max(threshold + 0.07, 0.2), 2)

    near_peak, drowned = choose_near_peak(low_peaks, normal_peaks, noise_peaks, gain)

    # ---- the speech-recognition wake check and commands
    texts = [stt.quick(np.clip(a * gain, -1, 1)) for _, a in sess.takes]
    heard_cmds = [stt.transcribe(np.clip(a * gain, -1, 1)) for a in sess.commands]

    # ---- keep the recordings for next time (local only)
    for i, (c, a) in enumerate(sess.takes):
        _save_wav(VOICE / "takes" / f"{c}-{i:02d}.wav", a)
    for i, (c, a) in enumerate(sess.background):
        _save_wav(VOICE / "background" / f"{c}-{i:02d}.wav", a)

    # ---- results, per setting
    print("\n  RESULTS\n  " + "=" * 52)
    print(f"  mic boost            {gain:.1f}x   (your normal voice {statistics.median(normal_peaks):.2f}, "
          f"low {statistics.median(low_peaks):.2f}, loud {max(loud_peaks):.2f})")
    print(f"  background           silence/noise/talk score up to {max(bg_scores):.2f} on the standard model")
    if use_verifier:
        print(f"  your voice model     ON  - held-out takes {np.median(pos_p):.2f} vs background up to "
              f"{max(neg_p):.2f}; wakes at {threshold:.2f}")
    else:
        why = "not enough clear takes" if len(pos_p) < 6 else "your takes and the background were too alike"
        print(f"  your voice model     off ({why}); standard model threshold {threshold:.2f}")
    print("\n  Would wake Jarvis, by setting:")
    conds = ["quiet-normal", "quiet-low", "quiet-loud", "noise-normal", "noise-low", "noise-loud"]
    wakes = []
    for (c, a), clip, sc, text in zip(sess.takes, pos_clips, take_scores, texts):
        by_model = (clip.probability(verifier, trigger) if verifier is not None else sc) >= threshold
        wakes.append((c, by_model or wake_phrase(text or "") is not None))
    for c in conds:
        got = [w for cc, w in wakes if cc == c]
        if got:
            label = c.replace("-", ", ").replace("quiet,", "quiet room,").replace("noise,", "with noise,")
            print(f"    {label:<22} {sum(got)}/{len(got)} {_bar(sum(got) / len(got), 12)}")
    total = sum(w for _, w in wakes)
    print(f"    {'overall':<22} {total}/{len(wakes)}")
    for cmd, heard in zip(("\"Hey Jarvis, what time is it?\"", "\"Hey Jarvis, open Chrome.\""), heard_cmds):
        print(f"  you said {cmd:<32} heard: {heard!r}")
    if drowned:
        print("\n  ! In that noise your quiet voice is softer than the room. Speak up a little in noisy places,")
        print("    or use Ctrl+Alt+J / click the orb.")
    if max(take_scores) < 0.1 and not use_verifier:
        print("\n  ! The wake word model barely reacts to this mic. A headset mic usually fixes it;")
        print("    Ctrl+Alt+J and clicking the orb always work.")

    save_config({"gain": gain, "threshold": threshold, "near_peak": near_peak, "verify_below": verify_below,
                 "accept_unheard_above": accept_unheard, "verifier": VERIFIER if use_verifier else "",
                 "verifier_trigger": trigger})
    print("\n  Saved to config.yaml (recordings kept in data\\voice on this laptop only).")
    print("  Jarvis restarts with the new settings when you close this.\n")
    time.sleep(0.5)
    return 0
