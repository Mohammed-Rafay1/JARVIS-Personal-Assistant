"""15 Nov: recognise the user's voice; other people talking mustn't wake Jarvis, start or stretch the user's
sentence, or cut in while Jarvis speaks; each microphone keeps its own level, held steady automatically."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis import calibrate  # noqa: E402
from jarvis.audio.mic import TARGET_RMS, AutoGain, load_levels, save_levels  # noqa: E402
from jarvis.audio.vad import Endpointer  # noqa: E402

rng = np.random.default_rng(1)


def voice(level=3000, n=1280):
    """A voiced frame: a buzzy tone (WebRTC VAD hears it as speech) at about this RMS."""
    t = np.arange(n) / 16000
    x = np.sin(2 * np.pi * 140 * t) + 0.5 * np.sin(2 * np.pi * 280 * t) + 0.3 * np.sin(2 * np.pi * 700 * t)
    x = x / np.sqrt(np.mean(x ** 2)) * level + rng.normal(0, level * 0.05, n)
    return np.clip(x, -32768, 32767).astype(np.int16)


def quiet(n=1280):
    return rng.normal(0, 60, n).astype(np.int16)


# ------------------------------------------------------------------------------------------- listening
def ep():
    e = Endpointer(2, 560, 12, 3, 560)
    e.require_me = True
    e.noise_floor = 100.0
    return e


def test_other_voices_neither_start_nor_keep_the_sentence():
    e = ep()
    statuses = [e.feed(voice(), me=False) for _ in range(30)]   # someone else talks for 2.4 s
    assert "speaking" not in statuses and e.others >= 28 and not e.started


def test_a_voice_not_yet_known_is_held_until_its_you():
    e = ep()
    for _ in range(6):
        assert e.feed(voice(), me=None) == "waiting"    # not sure yet: wait, keep the audio
    assert e.feed(voice(), me=True) == "speaking"
    assert len(e.frames) >= 7                            # the start of your sentence is still there


def test_your_sentence_ends_when_you_stop_even_if_others_carry_on():
    e = ep()
    for _ in range(10):
        e.feed(voice(), me=True)
    status = "speaking"
    for i in range(20):
        status = e.feed(voice(), me=False)                # they keep talking
        if status == "done":
            break
    assert status == "done" and i < 10
    assert len(e.audio()) <= (10 + 3) * 1280             # their words after yours are left out


def test_silence_times_out_now():
    e = ep()
    statuses = [e.feed(quiet()) for _ in range(40)]
    assert statuses[-1] == "timeout" or "timeout" in statuses   # 3 s of nothing (the old count never got there)


def test_if_the_check_never_answers_it_still_listens():
    e = ep()
    statuses = [e.feed(voice(), me=None) for _ in range(30)]
    assert e.started and statuses[-1] == "speaking"


# ------------------------------------------------------------------------------------------- mic level
def test_a_quiet_voice_is_brought_up_slowly():
    g = AutoGain(1.0)
    for _ in range(300):
        g.update(voice(600))
    assert 3.0 < g.gain < 8.0 and abs(600 * g.gain - TARGET_RMS) / TARGET_RMS < 0.4


def test_clipping_backs_off_at_once():
    g = AutoGain(10.0)
    g.update(voice(9000))
    assert g.gain < 10.0


def test_jarvis_talking_and_room_noise_dont_move_the_level():
    g = AutoGain(4.0)
    for _ in range(200):
        g.update(voice(400), hold=True)   # its own voice in the mic
        g.update(quiet())                  # the room
    assert g.gain == 4.0


def test_each_mic_keeps_its_level(tmp_path):
    p = tmp_path / "levels.json"
    save_levels(p, {"Microphone Array (Intel)": 6.0, "External Microphone (Realtek(R) Audio)": 1.8})
    assert load_levels(p)["External Microphone (Realtek(R) Audio)"] == 1.8
    assert load_levels(tmp_path / "missing.json") == {}


# ------------------------------------------------------------------------------------------- calibration
def test_threshold_sits_between_others_and_you():
    t = calibrate.choose_match_threshold([0.82, 0.9, 0.95, 0.88, 0.7], [0.2, 0.31, 0.38, 0.41, 0.25])
    assert 0.42 < t < 0.7


def test_threshold_leans_towards_you_when_they_overlap():
    assert calibrate.choose_match_threshold([0.5, 0.55, 0.6], [0.58, 0.62]) <= 0.5


def test_speech_gain_targets_a_steady_level():
    v = [np.full(16000, 0.02, np.float32)]
    assert calibrate.choose_speech_gain(v, [0.1]) == 5.0          # 0.02 -> 0.1
    assert calibrate.choose_speech_gain(v, [0.4]) == pytest.approx(2.4, abs=0.1)  # a loud voice limits it


def test_settings_are_saved(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("mic:\n  gain: 6.0\n", encoding="utf-8")
    monkeypatch.setattr(calibrate, "ROOT", tmp_path)
    monkeypatch.setattr(calibrate, "VOICE", tmp_path / "voice")
    calibrate.save_voice_settings(0.58, 2.4, "External Microphone")
    s = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "voice_id:" in s and "match_threshold: 0.58" in s
    calibrate.save_voice_settings(0.61, 2.0, "External Microphone", enabled=False)
    s = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "match_threshold: 0.61" in s and "voice_id:" in s and "  enabled: false" in s
    assert "gain: 6.0" in s                 # the other sections are untouched
    assert load_levels(tmp_path / "voice" / "mic_levels.json") == {"External Microphone": 2.0}


def test_recalibrating_keeps_voice_id_off_when_you_turned_it_off(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("voice_id:\n  enabled: false\n  keep_off: true\n  match_threshold: 0.75\n",
                                          encoding="utf-8")
    monkeypatch.setattr(calibrate, "ROOT", tmp_path)
    monkeypatch.setattr(calibrate, "VOICE", tmp_path / "voice")
    calibrate.save_voice_settings(0.70, 2.0, "Mic", enabled=True)
    s = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "  enabled: false" in s and "match_threshold: 0.70" in s


def test_voice_id_off_means_no_not_my_boss(monkeypatch):
    from jarvis.app import Jarvis
    j = Jarvis.__new__(Jarvis)
    j.cfg = {"voice_id": {"enabled": False, "model": "config.yaml", "lock_wake": True, "lock_commands": True}}
    j.speaker = None
    health = {}
    j._load_voiceprint(health)
    assert j.speaker is None and not j._voice_lock("wake") and not j._voice_lock("commands")


def test_voiced_part_drops_the_silence():
    a = np.concatenate([np.zeros(16000), np.sin(np.arange(16000) / 3) * 0.2, np.zeros(16000)]).astype(np.float32)
    assert 14000 <= calibrate.voiced_part(a).size <= 18000


# ------------------------------------------------------------------------------------------- in Jarvis
class _Speaker:
    ready, threshold, short_threshold, wake_reject = True, 0.78, 0.71, 0.66

    def __init__(self, score):
        self.s = score

    def score(self, audio):
        return self.s

    def embed(self, audio):
        return np.ones(4, np.float32) / 2

    def score_embedding(self, e):
        return self.s

    def adapt(self, e):
        self.adapted = getattr(self, "adapted", 0) + 1

    def is_me(self, audio, margin=0.0):
        return self.s >= self.threshold - margin, self.s


def test_someone_elses_hey_jarvis_is_ignored():
    from jarvis import app as app_mod

    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "voice_id": {"lock_wake": True}}, type("UI", (), {"send": lambda s, m: None})())
    j.endpointer = Endpointer()
    frames = [voice() for _ in range(15)]
    j.speaker = _Speaker(0.31)
    assert not j._is_you(frames, "wake")
    j.speaker = _Speaker(0.86)
    assert j._is_you(frames, "wake")
    j.speaker = _Speaker(0.31)
    j.cfg["voice_id"]["lock_wake"] = False
    assert j._is_you(frames, "wake")      # switched off in config.yaml: everyone may wake it


# ------------------------------------------------------------------------------------------- "not my boss"
def _jarvis():
    from jarvis import app as app_mod

    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "voice_id": {}}, type("UI", (), {"send": lambda s, m: None})())
    j.endpointer = Endpointer()
    j.endpointer.noise_floor = 60.0
    return j


def command(seconds):
    return np.concatenate([voice() for _ in range(int(seconds / 0.08))]).astype(np.float32) / 32768


def test_a_whole_command_needs_the_full_score_a_short_one_less():
    j = _jarvis()
    j.speaker = _Speaker(0.74)
    assert j._owner_check(command(1.2))            # short: 0.71 is enough
    assert not j._owner_check(command(3.5))        # whole command: needs 0.78
    j.speaker = _Speaker(0.80)
    assert j._owner_check(command(3.5))
    assert not getattr(j.speaker, "adapted", 0)    # 0.80 isn't sure enough to learn from
    j.speaker = _Speaker(0.90)
    assert j._owner_check(command(3.5)) and j.speaker.adapted == 1   # clearly you: learnt


def test_a_bare_hey_jarvis_never_gets_not_my_boss():
    import asyncio

    j = _jarvis()
    j.speaker = _Speaker(0.60)
    j.stt = type("STT", (), {"transcribe": lambda s, a, *x: "Hey, Jarvis.", "last_conf": 0.0})()
    j.player = type("P", (), {"play": lambda s, *a: None, "stop": lambda s: None})()
    said, handled = [], []

    async def say(t, then_idle=True, follow=False):
        said.append(t)

    async def handle(t, typed=False):
        handled.append(t)
    j.say, j.handle_text = say, handle
    asyncio.run(j._process_audio(command(0.8)))
    assert said == [] and handled == ["Hey, Jarvis."]


def test_the_live_voiceprint_stays_anchored(tmp_path):
    from jarvis.audio.speaker import SpeakerID

    sid = SpeakerID.__new__(SpeakerID)
    sid.profile = np.array([1.0, 0.0], np.float32)
    sid.live, sid.live_path, sid._learnt = None, tmp_path / "live.npy", 0
    for _ in range(200):                           # even if every "sure" match pulled the other way
        sid.adapt(np.array([0.0, 1.0], np.float32))
    assert float(sid.current @ sid.profile) > 0.8  # the calibrated voiceprint keeps most of the weight
    assert (tmp_path / "live.npy").exists()


def test_someone_elses_command_gets_not_my_boss(monkeypatch):
    import asyncio

    j = _jarvis()
    j.speaker = _Speaker(0.40)
    j.stt = type("STT", (), {"transcribe": lambda s, a, *x: "open chrome", "last_conf": 0.0})()
    j.player = type("P", (), {"play": lambda s, *a: None, "stop": lambda s: None})()
    said, handled = [], []

    async def say(t, then_idle=True, follow=False):
        said.append(t)

    async def handle(t, typed=False):
        handled.append(t)
    j.say, j.handle_text = say, handle
    asyncio.run(j._process_audio(command(2.0)))
    assert said == ["Sorry, you're not my boss."] and handled == []
    j.speaker = _Speaker(0.85)
    said.clear()
    asyncio.run(j._process_audio(command(2.0)))
    assert handled == ["open chrome"] and said == []


def test_thresholds_from_calibration():
    t = calibrate.choose_voice_thresholds([0.9, 0.93, 0.87, 0.91], [0.75, 0.78, 0.81], [0.6, 0.72, 0.65], [0.68, 0.6])
    assert t["reliable"] and 0.72 < t["command"] < 0.87 and 0.68 < t["short"] < 0.75 and t["wake"] < 0.75
    assert not calibrate.choose_voice_thresholds([0.9], [0.8], [], [])["reliable"]          # no other voices heard
    assert not calibrate.choose_voice_thresholds([0.8], [0.7], [0.82], [0.75])["reliable"]  # they sound alike
