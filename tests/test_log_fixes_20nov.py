"""From the 20 Nov voice test: goodbyes that were missed or taken as an edit to an email draft, a misheard "close
Spotify", a draft with "[Sender's First Name]" and its subject line, and "dropped an email for me"."""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis import app as app_mod  # noqa: E402
from jarvis.brain.intents import is_goodbye, normalize, parse  # noqa: E402
from jarvis.tools.apps import sounds_like_recent  # noqa: E402
from jarvis.tools.gmail import clean_reply  # noqa: E402


@pytest.mark.parametrize("said", ["Okay, by James.", "Bye, Jairis.", "Bye, Jairvis.", "Okay bye Jarvis.", "Goodbye."])
def test_misheard_goodbyes_end_the_call(said):
    t = normalize(said)
    assert is_goodbye(t)
    assert parse(said).tool == "stop"


@pytest.mark.parametrize("said", ["send it", "yes", "say I'm free on Monday instead", "by tomorrow please"])
def test_answers_are_not_goodbyes(said):
    assert not is_goodbye(normalize(said))


def test_dropped_an_email_is_draft_an_email():
    assert parse("dropped an email for me.").tool == "email_ask_content"


def test_a_misheard_close_finds_the_app_jarvis_just_opened():
    assert sounds_like_recent("portified", ["spotify", "youtube"]) == "spotify"
    assert sounds_like_recent("spotty first", ["chrome", "spotify"]) == "spotify"
    assert sounds_like_recent("excel", ["spotify", "youtube"]) is None
    assert sounds_like_recent("notepad", []) is None


def test_reply_draft_loses_placeholders_and_subject_line():
    body = ("Subject: Re: Urgent: Convocation Fee Voucher - Same Day Payment Required\n\n"
            "Dear [Sender's First Name],\n\nThank you for the prompt email. I'll confirm by [date].\n\n"
            "Best regards,\n[Your Name]")
    out = clean_reply(body, "Kiran")
    assert out.startswith("Dear Kiran,")
    assert "[" not in out and "Subject" not in out
    assert "I'll confirm shortly." in out
    assert clean_reply("Dear [Name],\nThanks.", "") == "Hi,\nThanks."
    assert clean_reply("Hi Kiran,\nThanks for your note.", "Kiran") == "Hi Kiran,\nThanks for your note."


class _UI:
    def send(self, m):
        pass


def test_a_goodbye_while_a_reply_waits_is_not_an_edit_to_it():
    j = app_mod.Jarvis({"address_as": "sir", "music": {"default_service": "youtube"}, "apps": {"aliases": {}},
                        "websites": {}, "llm": {"use_tools": False}, "listening": {}}, _UI())
    j.state, j._speech_q = "idle", asyncio.Queue()
    said, answered = [], []

    async def say(text, then_idle=True, follow=False):
        said.append(text)

    async def nothing():
        pass

    class Replies:
        _asking = True

        async def answer(self, t, raw):
            answered.append(t)
            return True

    class Player:
        busy = False

        def play(self, *a):
            pass

        def stop(self):
            pass

    j.say, j._wait_speech, j._go_idle, j.player = say, nothing, lambda: None, Player()
    j.board = type("B", (), {"replies": Replies()})()
    j.pending = {"kind": "reply_board", "id": "m1"}
    asyncio.run(j.handle_text("Bye, Jairis.", typed=True))
    assert answered == [] and j.pending is None and j.board.replies._asking is False
