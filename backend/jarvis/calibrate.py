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
# for your voiceprint: varied sounds, said the way you talk to Jarvis
VOICEPRINT_LINES = [
    "Open my email and tell me who wrote to me this morning.",
    "Play some relaxing music on Spotify while I finish this report.",
    "What does my schedule look like for tomorrow afternoon?",
    "Remind me at five o'clock to call Ali about the project.",
    "Research the latest news about artificial intelligence on ChatGPT.",
    "Turn the volume down a little and close the browser, please.",
    "Show me my board and tell me which emails need a reply.",
    "Quick brown foxes jump over lazy dogs near the river bank.",
]
VOICEPRINT = "data/voice/voiceprint.npz"     # relative to the Jarvis folder, as written to config.yaml
# WeSpeaker ResNet34. (The CAM++ model tried first gave a different voiceprint for the same recording at different
# lengths under sherpa-onnx 2.0.1 - 0.16 similarity between 2.0 s and 2.5 s cuts - so it couldn't tell anyone apart.)
SPEAKER_MODEL = "data/models/speaker/wespeaker_en_voxceleb_resnet34.onnx"


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

    def sentences(self, n: int) -> None:
        """Your voiceprint: a few ordinary sentences in your normal voice."""
        self.lines: list[np.ndarray] = getattr(self, "lines", [])
        print(f"\n  Read each sentence aloud in your normal voice, as you'd talk to Jarvis ({n} of them).")
        for i, line in enumerate(VOICEPRINT_LINES[:n]):
            for attempt in range(2):
                input(f"\n   [{i + 1}/{n}] Enter, then read:  \"{line}\"\n   ")
                print("        recording…", end="", flush=True)
                a = _record(5.0)
                voiced = voiced_part(a)
                peak = float(np.max(np.abs(a)))
                if voiced.size >= SR or attempt:   # at least a second of voice
                    break
                print("\r        I barely heard that - once more, a little closer or louder.")
            print(f"\r        level {_bar(peak)} {peak:.2f}   voice {voiced.size / SR:.1f} s   ")
            self.lines.append(a)

    def others(self, seconds: float) -> None:
        """Other voices, so Jarvis can tell them from you."""
        print("\n  OTHER VOICES - so Jarvis learns to ignore them.")
        print("  Let someone talk near the laptop, or play a video of people talking (a podcast, the news,")
        print("  any YouTube talk) at a normal volume. Don't speak yourself during this step.")
        got = input(f"  Press Enter to start ({seconds:.0f} s), or type s and Enter to skip… ").strip().lower()
        if got.startswith("s"):
            self.other_voices = None
            return
        print("        recording…", end="", flush=True)
        a = _record(seconds)
        rms = float(np.sqrt(np.mean(a ** 2)))
        print(f"\r        level {_bar(rms * 10)} {rms:.4f}")
        self.other_voices = a


def voiced_part(a: np.ndarray, frame: int = 1280) -> np.ndarray:
    """Only the parts of a recording with a voice in them (the silence before and after is dropped)."""
    frames = [a[i:i + frame] for i in range(0, len(a) - frame + 1, frame)]
    if not frames:
        return a
    rms = np.array([float(np.sqrt(np.mean(f ** 2))) for f in frames])
    floor = max(float(np.percentile(rms, 20)) * 2.5, 0.004)
    keep = [f for f, r in zip(frames, rms) if r > floor]
    return np.concatenate(keep) if keep else np.zeros(0, np.float32)


def choose_speech_gain(voiced: list[np.ndarray], loud_peaks: list[float]) -> float:
    """Boost so your normal speech sits around -20 dBFS (RMS 0.1), without clipping a loud voice."""
    levels = [float(np.sqrt(np.mean(v ** 2))) for v in voiced if v.size]
    if not levels:
        return 1.0
    gain = 0.1 / max(statistics.median(levels), 0.002)
    if loud_peaks:
        gain = min(gain, 0.95 / max(max(loud_peaks), 1e-3))
    return round(float(np.clip(gain, 1.0, 12.0)), 1)


def choose_match_threshold(yours: list[float], others: list[float]) -> float:
    """Where "you" starts on the voiceprint score: between other voices and your weakest takes, nearer the
    others (missing you is worse than an occasional stranger, which the other checks still catch)."""
    low_you = float(np.percentile(yours, 10)) if yours else 0.8
    high_other = float(np.percentile(others, 98)) if others else 0.42
    if low_you <= high_other:      # too alike to separate: lean on letting you in
        return round(float(np.clip(low_you - 0.05, 0.35, 0.6)), 2)
    t = high_other + (low_you - high_other) * 0.4
    return round(float(np.clip(t, 0.42, 0.7)), 2)


def save_voice_settings(threshold: float, gain: float, device: str, enabled: bool = True,
                        short: float | None = None, wake: float | None = None) -> None:
    """The voiceprint thresholds (and whether to use them) into config.yaml (voice_id section, added if missing)
    and this mic's level into data/voice/mic_levels.json (each microphone keeps its own)."""
    from .audio.mic import load_levels, save_levels

    path = ROOT / "config.yaml"
    s = path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^voice_id:", s):
        s = s.rstrip("\n") + "\n\n" + VOICE_ID_SECTION.format(threshold=threshold) + "\n"
    s = re.sub(r"(?m)^(  match_threshold:\s*)[\d.]+", rf"\g<1>{threshold:.2f}", s, count=1)
    s = re.sub(r'(?m)^(  model:\s*)"data/models/speaker/[^"]*"', rf'\g<1>"{SPEAKER_MODEL}"', s, count=1)
    for key, val, note in (("short_threshold", short, "a short command (about a second of voice)"),
                           ("wake_reject", wake, "\"Hey Jarvis\" below this is clearly someone else")):
        if val is None:
            continue
        if re.search(rf"(?m)^  {key}:", s):
            s = re.sub(rf"(?m)^(  {key}:\s*)[\d.]+", rf"\g<1>{val:.2f}", s, count=1)
        else:  # add it under match_threshold
            line = f"  {key}: {val:.2f}".ljust(29) + f"# {note}"
            s = re.sub(r"(?m)^(  match_threshold:.*)$", lambda m: m.group(1) + "\n" + line, s, count=1)
    if re.search(r"(?m)^  keep_off:\s*true", s):  # you switched voice recognition off: recalibrating keeps it off
        enabled = False
    s = re.sub(r"(?ms)^(voice_id:.*?^  enabled:\s*)(?:true|false)", rf"\g<1>{'true' if enabled else 'false'}", s,
               count=1)
    path.write_text(s, encoding="utf-8")
    levels = load_levels(VOICE / "mic_levels.json")
    if device:
        levels[device] = gain
        save_levels(VOICE / "mic_levels.json", levels)


VOICE_ID_SECTION = """voice_id:                    # recognise your voice (calibrate_wake.bat makes your voiceprint)
  enabled: true
  model: "data/models/speaker/wespeaker_en_voxceleb_resnet34.onnx"
  profile: "data/voice/voiceprint.npz"
  match_threshold: {threshold:.2f}       # a whole spoken command must score this to count as you
  lock_wake: true            # "Hey Jarvis" from a clearly different voice: "Sorry, you're not my boss"
  lock_commands: true        # a spoken command that isn't yours is refused the same way
  lock_barge_in: true        # a clearly different voice can't cut in while Jarvis speaks
  lock_listening: false      # mid-sentence checks on 1.5 s of audio (too little voice to be reliable)
  stranger_reply: "Sorry, you're not my boss.\""""


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
def run(cfg: dict, quick: bool = False) -> int:
    from openwakeword.model import Model

    from .audio import voiceprint as vp
    from .audio.stt import STT
    from .brain.intents import wake_phrase

    print("\n  JARVIS - voice calibration" + (" (quick)" if quick else "") + "\n  " + "=" * 52)
    device = str(sd.query_devices(kind="input")["name"])
    print(f"  Microphone: {device}")
    print("  (Use the mic you normally use with Jarvis. Each microphone gets its own settings.)")
    if quick:
        print("  About 3 minutes. Sit where you usually do and speak as you normally would to Jarvis.")
    else:
        print("  About 8-10 minutes. Sit where you usually do, about as far from the laptop as usual.")
        print("  Part 1 is in a quiet room, part 2 with background noise. Each step says what to do.")
    model = Model(wakeword_models=[cfg["wake_word"]["model"]], inference_framework="onnx")
    name = next(iter(model.models))
    print("  Loading speech recognition…")
    stt = STT(cfg["stt"]["model"], cfg["stt"]["compute_type"], cfg["stt"]["threads"])
    stt.set_vocabulary(["Spotify", "YouTube", "Chrome", "Excel"])
    sess = Session(model, name)
    sess.device = device

    if quick:  # one pass: quiet, your voiceprint, your voice at three levels, other voices, one command
        sess.listen("silence", 5, "Step 1 - stay quiet.")
        sess.sentences(5)
        sess.wake_takes("quiet-normal", "in your normal voice", 3)
        sess.wake_takes("quiet-low", "quietly", 2)
        sess.wake_takes("quiet-loud", "loudly", 2)
        sess.others(12)
        sess.command("Say in one breath: \"Hey Jarvis, what time is it?\"")
        print("\n  Working it out (under a minute)…")
        return _finish(cfg, model, name, stt, sess, vp, wake_phrase)

    print("\n  PART 1 of 2 - QUIET ROOM\n  " + "-" * 52)
    print("  Turn off music and videos. Close the door if it's noisy outside.")
    sess.listen("silence", 8, "Step 1 - stay completely quiet.")
    sess.sentences(8)
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
    print("\n  You can stop the noise now.")
    sess.others(15)
    print("\n  Working it out (this takes a minute or two)…")
    return _finish(cfg, model, name, stt, sess, vp, wake_phrase)


def _voiceprint(sess: Session, gain: float) -> dict | None:
    """Build your voiceprint from the sentences and "Hey Jarvis" takes, and measure how well it tells you from
    other voices: each of your clips is scored against a voiceprint made without it (an honest test)."""
    from .audio.speaker import SpeakerID

    model = ROOT / SPEAKER_MODEL
    if not model.is_file():
        print("  ! The voice-recognition model is missing (data/models/speaker); skipping the voiceprint.")
        return None
    sid = SpeakerID(model)
    # The voiceprint comes from whole sentences, as recorded (stitching voiced bits together wrecks a speaker
    # model). Recordings with under a second of voice are left out.
    lines = [np.clip(a * gain, -1, 1) for a in getattr(sess, "lines", []) if voiced_part(a).size >= SR]
    if len(lines) < 3:
        print("  ! Too little of your voice was recorded for a voiceprint; skipping it.")
        return None
    embs = [sid.embed(v) for v in lines]
    held_out = []  # each sentence against a voiceprint made from the others (an honest test)
    for i in range(len(embs)):
        sid.set_profile(embs[:i] + embs[i + 1:])
        held_out.append(sid.score(lines[i]))
    sid.set_profile(embs)
    cut = lambda a, secs: [a[i:i + int(secs * SR)] for i in range(0, max(1, len(a) - int(secs * SR)) + 1, SR // 2)]  # noqa: E731
    # what Jarvis judges at run time: whole commands (2.5 s and up) and short ones / "Hey Jarvis" (about 1 s of voice)
    long_you = held_out + [sid.score(p) for a in lines for p in cut(a, 2.5)]
    short_you = [sid.score(np.clip(a * gain, -1, 1)) for _, a in sess.takes if voiced_part(a).size >= SR // 2]
    ov = getattr(sess, "other_voices", None)
    long_other, short_other = [], []
    if ov is not None:
        o = np.clip(ov * gain, -1, 1)
        long_other = [sid.score(p) for secs in (2.5, 4.0) for p in cut(o, secs)]
        short_other = [sid.score(p) for secs in (1.0, 1.5) for p in cut(o, secs)]
    room = [sid.score(p) for c, a in sess.background if c in ("silence", "noise")
            for p in cut(np.clip(a * gain, -1, 1), 2.0)]
    t = choose_voice_thresholds(long_you, short_you, long_other, short_other)
    sid.threshold = t["command"]
    sid.save(ROOT / VOICEPRINT, held_out=np.array(held_out), others=np.array(long_other or [0.0]))
    (ROOT / VOICEPRINT).with_name("voiceprint_live.npy").unlink(missing_ok=True)  # a new voiceprint starts fresh
    return {"yours": long_you, "short_you": short_you, "others": long_other, "short_other": short_other,
            "room": room, "threshold": t["command"], "short_threshold": t["short"], "wake_reject": t["wake"],
            "reliable": t["reliable"], "sid": sid}


def choose_voice_thresholds(long_you: list[float], short_you: list[float], long_other: list[float],
                            short_other: list[float]) -> dict:
    """Where "you" starts, for a whole command, a short one, and "Hey Jarvis" (where only a clearly different
    voice is turned away). It's only switched on when other voices were recorded and sit clearly below you."""
    lo_you = float(np.percentile(long_you, 5)) if long_you else 0.85
    sh_you = min(short_you) if short_you else lo_you - 0.1
    hi_other = max(long_other) if long_other else None
    sh_other = max(short_other) if short_other else None
    if hi_other is None:
        return {"command": round(lo_you - 0.08, 2), "short": round(sh_you - 0.05, 2), "wake": round(sh_you - 0.1, 2),
                "reliable": False}
    # nearer the other voices than to you: live conditions (mic level, distance) pull your scores down a little,
    # and refusing you is the worse mistake (15 Nov: a long sentence of the user's scored 0.74 against 0.78)
    command = hi_other + (lo_you - hi_other) * 0.25
    short = (sh_you + sh_other) / 2 - 0.01 if sh_other is not None else sh_you - 0.05
    reliable = lo_you - hi_other >= 0.05 and (sh_other is None or sh_you > sh_other)
    return {"command": round(float(command), 2), "short": round(float(short), 2),
            "wake": round(float(min(sh_you - 0.1, short - 0.04)), 2), "reliable": bool(reliable)}


def _finish(cfg: dict, model, name: str, stt, sess: Session, vp, wake_phrase) -> int:
    """Everything after the recordings: choose the settings, check them, show the results, save."""
    # ---- the mic boost, from your voice levels
    peaks = {c: [float(np.max(np.abs(a))) for cc, a in sess.takes if cc == c] for c, _ in sess.takes}
    normal_peaks = peaks.get("quiet-normal", []) + peaks.get("noise-normal", [])
    loud_peaks = peaks.get("quiet-loud", []) + peaks.get("noise-loud", [])
    low_peaks = peaks.get("quiet-low", []) + peaks.get("noise-low", [])
    gain = choose_gain(peaks.get("quiet-normal", normal_peaks), loud_peaks)
    lines = [voiced_part(a) for a in getattr(sess, "lines", [])]
    if lines:  # ordinary sentences say more about your speaking level than a two-word wake phrase
        gain = choose_speech_gain(lines, loud_peaks)

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

    # ---- your voiceprint: recognise you, ignore other voices
    print("  Building your voiceprint…")
    vprint = _voiceprint(sess, gain)

    # ---- keep the recordings for next time (local only)
    for i, (c, a) in enumerate(sess.takes):
        _save_wav(VOICE / "takes" / f"{c}-{i:02d}.wav", a)
    for i, (c, a) in enumerate(sess.background):
        _save_wav(VOICE / "background" / f"{c}-{i:02d}.wav", a)
    for i, a in enumerate(getattr(sess, "lines", [])):
        _save_wav(VOICE / "lines" / f"line-{i:02d}.wav", a)
    if getattr(sess, "other_voices", None) is not None:
        _save_wav(VOICE / "background" / "other-voices.wav", sess.other_voices)

    # ---- results, per setting
    print("\n  RESULTS\n  " + "=" * 52)
    print(f"  microphone           {getattr(sess, 'device', '') or 'default'}")
    print(f"  mic boost            {gain:.1f}x   (your normal voice {statistics.median(normal_peaks):.2f}, "
          f"low {statistics.median(low_peaks):.2f}, loud {max(loud_peaks):.2f}); Jarvis fine-tunes it as you talk")
    if vprint:
        ys, os_ = vprint["yours"], vprint["others"]
        sy, so = vprint["short_you"] or [0.0], vprint["short_other"]
        print(f"  your voiceprint      {'ON ' if vprint['reliable'] else 'OFF'}")
        print(f"    whole commands     you {np.percentile(ys, 5):.2f}-{np.median(ys):.2f}"
              + (f" vs other voices up to {max(os_):.2f}" if os_ else " (other voices not recorded)")
              + f" -> yours above {vprint['threshold']:.2f}")
        print(f"    \"Hey Jarvis\"       you {min(sy):.2f}-{np.median(sy):.2f}"
              + (f" vs other voices up to {max(so):.2f}" if so else "")
              + f" -> \"not my boss\" below {vprint['wake_reject']:.2f}")
        if not vprint["reliable"]:
            print("  ! Jarvis can't safely tell you apart from other voices yet, so it won't refuse anyone (it would")
            print("    risk refusing you). Run this again: sit close to the mic or use a headset, speak at your")
            print("    normal level, and in the other-voices step let someone else talk (not a quiet video).")
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
        heard = by_model or wake_phrase(text or "") is not None
        if heard and vprint and vprint["reliable"]:  # and the voiceprint must not turn you away, as at run time
            heard = voiced_part(a).size < SR // 2 or \
                vprint["sid"].score(np.clip(a * gain, -1, 1)) >= vprint["wake_reject"]
        wakes.append((c, heard))
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
    save_voice_settings(vprint["threshold"] if vprint else 0.75, gain, getattr(sess, "device", ""),
                        enabled=bool(vprint and vprint["reliable"]),
                        short=vprint["short_threshold"] if vprint else None,
                        wake=vprint["wake_reject"] if vprint else None)
    print("\n  Saved to config.yaml (recordings kept in data\\voice on this laptop only).")
    print("  Jarvis restarts with the new settings when you close this.\n")
    time.sleep(0.5)
    return 0
