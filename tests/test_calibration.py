"""The voice calibration's choices (6 Nov): mic boost, thresholds, the speech-check level, saving settings, and
whether to use a model of the user's own "Hey Jarvis"."""
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis import calibrate as cal  # noqa: E402
from jarvis.audio import voiceprint as vp  # noqa: E402


def test_gain_boosts_a_quiet_mic_without_clipping_a_loud_voice():
    assert cal.choose_gain([0.1, 0.12, 0.11], [0.3]) == 3.3          # 0.98 / 0.3: loud voice stays unclipped
    assert cal.choose_gain([0.1, 0.12, 0.11], [0.15]) == 4.5         # normal voice to ~half scale
    assert cal.choose_gain([0.5, 0.6], [0.9]) == 1.0                  # loud enough already
    assert cal.choose_gain([0.01], []) == 6.0                         # never more than 6x


def test_base_threshold_sits_above_the_background_and_below_your_voice():
    assert cal.choose_base_threshold([0.3, 0.35, 0.4, 0.5], [0.05, 0.02]) == 0.27
    assert cal.choose_base_threshold([0.3, 0.35, 0.4, 0.5], [0.3]) == 0.39   # a noisy room raises it
    assert cal.choose_base_threshold([0.05, 0.06], [0.0]) == 0.1             # never below 0.1


def test_trigger_is_below_the_weakest_takes():
    assert cal.choose_trigger([0.2, 0.25, 0.3, 0.4]) == 0.1
    assert cal.choose_trigger([0.03, 0.2, 0.3]) == 0.04


def test_speech_check_level_catches_a_low_voice_but_not_the_noise():
    near, drowned = cal.choose_near_peak([0.1, 0.12], [0.3], [0.05], 2.0)
    assert near == 0.15 and not drowned
    near, drowned = cal.choose_near_peak([0.05], [0.3], [0.2], 2.0)      # noise louder than the low voice
    assert drowned and near == 0.42


def test_verifier_used_only_when_it_clearly_separates():
    assert vp.choose([0.95, 0.99, 0.9, 1.0, 0.97, 0.98], [0.01, 0.05]) == (True, 0.5)
    assert vp.choose([0.4, 0.5, 0.45, 0.6, 0.5, 0.55], [0.45]) == (False, 0.5)   # too alike: don't block you
    assert vp.choose([0.99, 0.98], [0.0])[0] is False                              # too few takes


def test_settings_are_written_into_config_yaml(tmp_path, monkeypatch):
    src = Path(__file__).resolve().parents[1] / "config.yaml"
    shutil.copy(src, tmp_path / "config.yaml")
    monkeypatch.setattr(cal, "ROOT", tmp_path)
    cal.save_config({"gain": 3.3, "threshold": 0.48, "near_peak": 0.15, "verify_below": 0.68,
                     "accept_unheard_above": 0.48, "verifier": "data/voice/wake_verifier.pkl",
                     "verifier_trigger": 0.05})
    import yaml

    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    w = cfg["wake_word"]
    assert (w["threshold"], w["near_peak"], w["verify_below"], w["accept_unheard_above"]) == (0.48, 0.15, 0.68, 0.48)
    assert w["verifier"] == "data/voice/wake_verifier.pkl" and w["verifier_trigger"] == 0.05
    assert cfg["mic"]["gain"] == 3.3
    assert w["threshold_while_speaking"] == 0.75                      # other settings untouched
    # running it again replaces, never duplicates
    cal.save_config({"gain": 1.0, "threshold": 0.2, "verifier": "", "verifier_trigger": 0.03})
    text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert text.count("verifier:") == 1 and text.count("verifier_trigger:") == 1
    w = yaml.safe_load(text)["wake_word"]
    assert w["verifier"] == "" and w["threshold"] == 0.2


def test_a_voice_model_trains_and_loads_into_the_wake_word(tmp_path):
    """Real openWakeWord features: a verifier trained on two kinds of sound tells them apart and loads."""
    from openwakeword.model import Model

    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    rng = np.random.default_rng(1)
    t = np.arange(16000 * 2) / 16000
    tone = [(0.3 * np.sin(2 * np.pi * f * t)).astype(np.float32) for f in (300, 320, 340, 360)]
    hiss = [(rng.normal(0, 0.05, 32000)).astype(np.float32) for _ in range(4)]
    pos = [vp.Clip(model, "hey_jarvis", a, 1.0, True) for a in tone]
    neg = [vp.Clip(model, "hey_jarvis", a, 1.0, False) for a in hiss]
    for c in pos:  # the stock model barely reacts to a tone; train on all its frames for this check
        c.train = c.feats
    v = vp.train_clips(pos, neg)
    path = tmp_path / "v.pkl"
    vp.save(v, path)
    from jarvis.audio.wakeword import WakeWord

    w = WakeWord("hey_jarvis", 0.5, str(path), 0.05)
    assert w.personal
    assert not WakeWord("hey_jarvis", 0.5, str(tmp_path / "missing.pkl")).personal   # no file: the standard one
