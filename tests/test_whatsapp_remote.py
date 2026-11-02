import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.services.whatsapp_remote import card_text, decode_voice_note, encode_voice_note  # noqa: E402


def test_voice_note_round_trip(tmp_path):
    sr = 24000
    tone = (np.sin(np.arange(sr) * 2 * np.pi * 220 / sr) * 0.3).astype(np.float32)
    path = tmp_path / "note.ogg"
    seconds = encode_voice_note(tone, sr, path)
    audio = decode_voice_note(path)
    assert abs(seconds - 1.0) < 0.01
    assert abs(audio.size / 16000 - 1.0) < 0.1 and 0.2 < np.abs(audio).max() < 0.4


def test_choices_card_becomes_a_numbered_list():
    text = card_text({"kind": "choices", "title": "Which file?", "items": ["a.pdf", "b.pdf"],
                      "subs": ["Downloads", ""]})
    assert "1. a.pdf (Downloads)" in text and "2. b.pdf" in text and "number" in text


def test_drafts_ask_for_confirmation():
    assert "send it" in card_text({"kind": "email_draft", "to": "a@example.com", "subject": "Hi", "body": "Hello"})
    assert "send it" in card_text({"kind": "whatsapp_draft", "chat": "Ali", "text": "late"})


def test_unknown_card_is_skipped():
    assert card_text({"kind": "meter", "value": 40}) == ""


class _UI:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


def _jarvis():
    from jarvis.app import Jarvis

    j = Jarvis({"address_as": "sir"}, _UI())
    j._speech_q = asyncio.Queue()
    return j


def test_remote_command_collects_the_reply_and_stays_silent():
    from jarvis.tools.base import Result

    async def run():
        j = _jarvis()
        seen = {}

        async def fake_handle(text, typed=False):
            seen["aloud"], seen["typed"] = j.speak_aloud, typed
            j._remote_cards.append(Result("x", card={"kind": "choices", "title": "T", "items": ["one"]}).card)
            j._queue_speech("Opening Chrome.")
        j.handle_text = fake_handle
        said, cards = await j.handle_remote("open chrome", voice=True, conf=-0.2)
        return j, seen, said, cards

    j, seen, said, cards = asyncio.run(run())
    assert said == ["Opening Chrome."] and "1. one" in cards[0]
    assert seen == {"aloud": False, "typed": False}
    assert j._remote_said is None and j.speak_aloud is True   # back to normal for the next spoken command


def test_remote_waits_for_a_call_on_the_laptop():
    async def run():
        j = _jarvis()
        j.in_call = True

        async def fake_handle(text, typed=False):
            j._queue_speech("done")
        j.handle_text = fake_handle

        async def end_call():
            await asyncio.sleep(0.6)
            j.in_call = False
        asyncio.get_running_loop().create_task(end_call())
        return await j.handle_remote("hi", voice=False)

    said, _ = asyncio.run(run())
    assert said == ["done"]
