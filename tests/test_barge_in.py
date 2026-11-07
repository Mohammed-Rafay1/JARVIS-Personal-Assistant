"""Talking over Jarvis (8 Nov): with the mic boosted 6x, Jarvis's own voice through the speakers was taken for
the user cutting in ("barge-in (mic 1228 vs echo 255)" twice, mid-answer) and its answers got interrupted."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


class _UI:
    def send(self, m):
        pass


class _Player:
    def __init__(self):
        self.now = 0.0      # raw RMS of what is being played, 0-1

    def reference(self, window=0.4):
        return self.now

    busy = True


class _Endpointer:
    noise_floor = 60.0

    def vad_votes(self, frame):
        return 3 if np.abs(frame).max() > 200 else 0   # speech-like whenever there's sound


@pytest.fixture
def jarvis():
    from jarvis import app as app_mod

    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "call": {"barge_in_ratio": 2.5}}, _UI())
    j.player, j.endpointer = _Player(), _Endpointer()
    return j


def frame(rms):
    """An 80 ms frame with this RMS (int16 units)."""
    rng = np.random.default_rng(int(rms))
    x = rng.normal(0, 1, 1280)
    return (x / np.sqrt(np.mean(x ** 2)) * rms).astype(np.int16)


def speak(j, played, heard):
    """Jarvis plays at `played` (0-1) and the mic hears `heard` (int16 RMS): did that count as a barge-in?"""
    j.player.now = played
    return any(j._barge_feed(frame(heard)) for _ in range(6))


def learn(j, k=0.37):
    """Jarvis's greeting: the mic hears k times what it plays."""
    for p in (0.02, 0.05, 0.1, 0.08, 0.04, 0.1, 0.12, 0.06, 0.09, 0.03, 0.07, 0.11, 0.05, 0.1):
        j.player.now = p
        j._echo_learn(k * p * 32768)


def test_a_loud_word_in_jarvis_own_answer_is_not_you(jarvis):
    learn(jarvis)
    assert not speak(jarvis, 0.01, 200)        # the quiet start of a sentence...
    assert not speak(jarvis, 0.1, 1228)        # ...then a loud word: 1228 is just its echo (the 8 Nov case)


def test_you_cutting_in_between_its_words_is_heard(jarvis):
    learn(jarvis)
    assert speak(jarvis, 0.0, 1200)            # Jarvis pausing, you talking at your normal level


def test_you_talking_clearly_over_it_is_heard(jarvis):
    learn(jarvis)
    assert speak(jarvis, 0.03, 4000)


def test_before_it_has_learnt_its_echo_it_works_as_before(jarvis):
    assert not speak(jarvis, 0.1, 300)         # measuring the first frames
    assert speak(jarvis, 0.1, 3000)            # well above that echo


def test_the_greeting_teaches_it_outside_a_call(jarvis):
    assert jarvis._echo_k() is None
    learn(jarvis, k=0.5)
    assert jarvis._echo_k() == pytest.approx(0.5, rel=0.05)


def test_quiet_moments_of_its_voice_are_not_learnt(jarvis):
    jarvis.player.now = 0.005                  # 164 in mic units: too quiet to tell echo from room noise
    jarvis._echo_learn(500)
    assert len(jarvis._echo_ratios) == 0


def test_the_player_reports_what_it_just_played():
    import time

    from jarvis.audio.player import Player

    p = Player(lambda *a: None)
    p._played.extend([(time.monotonic() - 1.0, 0.5), (time.monotonic() - 0.1, 0.2), (time.monotonic(), 0.05)])
    assert p.reference() == 0.2                # the loudest of the last 0.4 s, not older
