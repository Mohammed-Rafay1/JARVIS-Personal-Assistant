"""From the 6 Nov 14:21-15:17 log: "Start it" opened Antigravity IDE, a clipped "Hey Jarvis" was thrown away,
"past 24 hours" was reported as "today", and Spotify gave up while it was still loading."""
import asyncio
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.brain.intents import parse  # noqa: E402
from jarvis.tools.base import Result  # noqa: E402


# ------------------------------------------------------------------ what you say
@pytest.mark.parametrize("said,tool", [
    ("Start it", "play_it"), ("play it", "play_it"), ("resume it", "play_it"), ("press play", "play_it"),
    ("start the song", "play_it"), ("start playing", "play_it"),
    ("play that song", "ask_song"),          # nothing named: still asks which song
    ("open it", "say"),                       # no idea what "it" is: asks, never guesses an app
    ("open spotify", "open_any"), ("start chrome", "open_any"), ("resume", "media_control"),
])
def test_start_it_is_never_an_app(said, tool):
    assert parse(said).tool == tool


@pytest.mark.parametrize("said,period", [
    ("Jarvis, can you tell me in past 24 hours which email I have received?", "24h"),   # 15:08:39
    ("emails in the last 24 hours", "24h"), ("emails from yesterday", "yesterday"),
    ("any new emails", "unread"), ("which emails did I get this week", "recent"), ("check my inbox", "today"),
])
def test_email_periods(said, period):
    i = parse(said)
    assert i.tool == "check_email" and i.args["period"] == period


def test_emails_from_a_person_are_still_about_that_person():
    assert parse("read the email from ali").tool == "read_email"


def test_pronouns_never_match_an_app():
    from jarvis.tools.apps import AppIndex

    idx = AppIndex.__new__(AppIndex)
    idx.apps = {"Antigravity IDE": "x", "Spotify": "y"}
    assert idx.find("it") is None and idx.find("this") is None
    assert idx.find("spotify") == ("Spotify", "y")


# ------------------------------------------------------------------ the inbox says which period it read
def test_check_email_says_the_period(monkeypatch):
    from jarvis.tools import CTX, gmail

    asked = []

    class G:
        def search(self, q, n):
            asked.append(q)
            return [{"from_name": "Spotify", "subject": "x", "id": "1"}]
    monkeypatch.setattr(CTX, "gmail", G())
    CTX.cfg = {}
    assert asyncio.run(gmail.check_email("24h")).say == "1 email in the last 24 hours, from Spotify."
    assert asyncio.run(gmail.check_email("yesterday")).say == "1 email yesterday, from Spotify."
    assert "newer_than:2d older_than:1d" in asked[-1]


# ------------------------------------------------------------------ Jarvis itself
class _UI:
    def send(self, m):
        pass


@pytest.fixture
def jarvis(monkeypatch):
    from jarvis import app as app_mod
    from jarvis.tools import CTX

    CTX.cfg = {}
    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "wake_word": {}}, _UI())
    j.state = "idle"
    said, tools, woke = [], [], []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def run_tool(name, args):
        tools.append((name, args))
        return Result(f"ran {name}")
    j.say, j.run_tool = say, run_tool
    j._wake = lambda source, *a, **k: woke.append(source)
    return j, said, tools, woke


def test_start_it_retries_the_song_that_did_not_start(jarvis):
    import time

    j, said, tools, _ = jarvis
    j._last_play = {"tool": "play_spotify", "args": {"query": "amplifier"}, "ok": False, "at": time.monotonic()}
    asyncio.run(j.handle_text("Start it", typed=True))
    assert tools == [("play_spotify", {"query": "amplifier"})]


def test_start_it_with_nothing_failed_just_presses_play(jarvis):
    j, said, tools, _ = jarvis
    asyncio.run(j.handle_text("Start it", typed=True))
    assert tools == [("media_control", {"action": "play_pause"})]


class _STT:
    def __init__(self, heard):
        self.heard = heard

    def quick(self, audio, wait=0.0):
        return self.heard


@pytest.mark.parametrize("heard,score,wakes", [
    ("", 0.36, True),                     # 14:41:20: a clipped "Hey Jarvis", nothing recognisable: accept
    ("", 0.18, False),                    # weak and nothing heard: still refused
    ("Thanks for watching!", 0.36, False),  # a video's words: refused
    ("Hey Jarvis!", 0.16, True),          # heard it: accepted however weak
])
def test_weak_wake_check(jarvis, heard, score, wakes):
    j, _, _, woke = jarvis
    j.stt, j.muted, j.in_call = _STT(heard), False, False
    asyncio.run(j._verify_wake(np.zeros(16000, dtype=np.float32), score))
    assert (woke == ["voice"]) == wakes
