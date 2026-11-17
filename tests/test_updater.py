"""A new commit is offered as an update: "my new update is ready, should I restart?" (17 Nov)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.services import updater  # noqa: E402

A = "a" * 40
B = "b" * 40


def _repo(tmp_path, commit=A, packed=False):
    g = tmp_path / ".git"
    (g / "refs" / "heads").mkdir(parents=True)
    (g / "logs").mkdir()
    (g / "HEAD").write_text("ref: refs/heads/rafay\n", encoding="utf-8")
    if packed:
        (g / "packed-refs").write_text(f"# pack-refs\n{commit} refs/heads/rafay\n", encoding="utf-8")
    else:
        (g / "refs" / "heads" / "rafay").write_text(commit + "\n", encoding="utf-8")
    (g / "logs" / "HEAD").write_text(
        f"{'0' * 40} {A} Me <m@x> 1 +0500\tcommit: First\n"
        f"{A} {B} Me <m@x> 2 +0500\tcommit: \"Close board\" closes it, however it's heard\n", encoding="utf-8")
    return tmp_path


def test_head_commit_from_branch_file_and_packed_refs(tmp_path):
    assert updater.head_commit(_repo(tmp_path)) == A
    assert updater.head_commit(_repo(tmp_path / "p", B, packed=True)) == B
    assert updater.head_commit(tmp_path / "nothing") == ""


def test_commit_subject_is_speakable(tmp_path):
    root = _repo(tmp_path)
    s = updater.commit_subject(root, B)
    assert s == "\"Close board\" closes it, however it's heard"
    assert updater.spoken_subject(s) == "Close board closes it, however it's heard"
    assert updater.commit_subject(root, "c" * 40) == ""


class _J:
    """Just enough of Jarvis for the restart answer."""
    def __init__(self):
        from jarvis.app import Jarvis
        self.said, self.restarted = [], 0
        self._pending, self._pending_at = {"kind": "restart_update"}, 0.0
        self.restart_cb = lambda: setattr(self, "restarted", self.restarted + 1)
        self._restart_answer = Jarvis._restart_answer.__get__(self)

    pending = property(lambda s: s._pending, lambda s, v: setattr(s, "_pending", v))

    async def say(self, text, then_idle=True, follow=False):
        self.said.append(text)


def test_yes_restarts_and_no_waits():
    j = _J()
    assert asyncio.run(j._restart_answer("yes")) and j.restarted == 1 and j.pending is None
    j = _J()
    assert asyncio.run(j._restart_answer("not now")) and j.restarted == 0 and "restart" in j.said[0].lower()
    j = _J()
    assert asyncio.run(j._restart_answer("open spotify")) is False and j.restarted == 0  # a new command instead


def test_player_release_waits_while_speaking():
    from jarvis.audio.player import Player
    p = Player(lambda *a: None)
    assert p.release() is True            # idle: the output can move to the headset now
    p._idle.clear()
    assert p.release() is False           # mid-sentence: the switch waits


def test_default_devices_reads_windows_names():
    import comtypes
    from jarvis.audio.mic import default_devices
    comtypes.CoInitialize()
    names = default_devices()
    assert names is None or (isinstance(names[0], str) and isinstance(names[1], str))


def test_wake_as_heard_on_the_headset():
    from jarvis.brain.intents import wake_phrase
    assert wake_phrase("Hey, Jadvis.") == ""
    assert wake_phrase("Hey, dear, this.") == ""
    assert wake_phrase("Jadvis, open the board") == "open the board"
    assert wake_phrase("hey dear this is my friend") is None   # ordinary speech stays ordinary


def test_short_greetings_get_a_second_listen():
    from jarvis.app import _MAYBE_WAKE
    for heard in ("Hey, did we see you?", "Edge, Database,", "Edge, Address,", "Hey, dear, this."):
        assert _MAYBE_WAKE.match(heard), heard
    for heard in ("Can you listen to me?", "Hey, what are you doing tonight at the party with everyone?"):
        assert not _MAYBE_WAKE.match(heard), heard


def test_show_it_on_my_screen():
    from jarvis.app import _SHOW_IT
    for said in ("the youtube you opened in background i want to see the youtube page on my screen",
                 "you have just opened the youtube in the background", "bring it to the front",
                 "show it on my screen", "i can't see it"):
        assert _SHOW_IT.search(said), said
    for said in ("open youtube", "what's on my calendar", "show me my emails"):
        assert not _SHOW_IT.search(said), said


def test_front_words_for_tools():
    from jarvis.tools.front import words_for
    assert words_for("open_website", {"site": "youtube"}) == (["youtube"], True)
    assert words_for("open_folder", {"name": "downloads"}) == (["downloads"], False)
    assert words_for("close_app", {"name": "x"}) is None


def test_made_up_actions_are_caught():
    from jarvis.app import _FALSE_ACTION
    for said in ('Got it, sir. I\'ll open the "work" MSWord file from your Downloads folder.',
                 "I apologize for that oversight, sir. Let me check and ensure Spotify is closed.",
                 "I'll close it for you now.", 'Understood, sir. I\'ll open the "to work" file in Microsoft Word for you now.'):
        assert _FALSE_ACTION.search(said), said
    for said in ("Paris is the capital of France.", "You can open it from the Start menu.", "I'd start with Python."):
        assert not _FALSE_ACTION.search(said), said


def test_open_a_named_file_in_one_breath():
    from jarvis.brain.intents import parse
    i = parse("Jarvis, there is a microsoft world file in my laptop named to work. Please open that.")
    assert (i.tool, i.args.get("app"), i.args.get("query")) == ("office_open", "word", "to work")
    i = parse("I'm saying there is a file in my Downloads folder. The name of the file is work. "
              "You have to open that MSWord file for me.")
    assert (i.tool, i.args.get("app"), i.args.get("query")) == ("office_open", "word", "work")


def test_draft_reply_phrases():
    from jarvis.app import _DRAFT_REPLY, _YES_LEAD
    for said in ("yes please make a drop for their email", "make a drop for that email",
                 "i mean the email that needs a reply i want you to make a draft reply for that email",
                 "make a draft and i'll check the draft and then tell you", "reply to it",
                 "make a drop trip life for my email that needs my reply"):
        assert _DRAFT_REPLY.search(said), said
    assert _YES_LEAD.match("yes please make a drop for their email")
    assert not _DRAFT_REPLY.search("open spotify")
