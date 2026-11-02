"""The orchestrator: audio in, state machine, intents/LLM, speech out, UI events."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import threading
import time
from collections import deque
from typing import Any

import numpy as np

from . import tools
from .audio import earcons
from .audio.meter import levels
from .audio.mic import SAMPLE_RATE as MIC_SR
from .brain.intents import NO, STOP, YES, Intent, normalize, parse, parse_choice
from .config import load_contacts
from .tools import CTX
from .tools.base import REGISTRY, Result, llm_tools

log = logging.getLogger(__name__)

def help_text(sir: str, email: bool) -> str:
    """Spoken answer to "what can you do". Confident, but every claim is something Jarvis really does."""
    mail = " I read your inbox and draft and send your emails on your word." if email else ""
    return (f"At your service, {sir}. I command this laptop on your behalf. I open any application, file or folder, "
            "search the web, and play your music on YouTube or Spotify. I put Claude Code to work on your projects, "
            "building features and writing and fixing code, and I brief you on where each project stands."
            f"{mail} I handle the volume, the maths and your questions. "
            "The full list is on your screen. Just tell me what you need.")
# the on-screen version: every capability with phrases that work
CAPABILITIES = [
    ("Apps & windows", ["Open Excel / VS Code / Teams", "Close Spotify", "Open Claude Code", "Lock the screen"]),
    ("Files & folders", ["Open my Downloads folder", "Open the Q3 proposal PDF", "Find the budget spreadsheet"]),
    ("Web", ["Search Google for Odoo 18 release notes", "Open YouTube on Chrome", "Go to github.com"]),
    ("Music & media", ["Play Tum Hi Ho on YouTube", "Play Dilruba on Spotify", "Play my liked songs",
                       "Pause · Next song · Volume 40 · Mute"]),
    ("Build & code (via Claude Code)", ["What's the status of my projects?", "How is the Solvix project going?",
                                        "Ask Claude Code in Solvix to build a login page",
                                        "Ask Claude Code in Jarvis to fix the volume bug"]),
    ("WhatsApp", ["WhatsApp Ali that I'm running late", "Send a WhatsApp message to Sara", "Then: “send it” or “no”"]),
    ("Quick answers", ["What's 18% of 4,500?", "What time is it?", "Tell me something about space"]),
    ("During a call", ["Just start talking to interrupt me, even while I'm thinking",
                       "Say “bye” to end · Esc or ✕ to close",
                       "Ctrl+Alt+J to minimise"]),
]
_REVEALS = {"app", "web", "youtube", "spotify", "folder"}
_OPENS_WINDOW = {"open_app", "open_website", "web_search", "play_youtube", "play_spotify", "play_spotify_liked",
                 "open_folder", "open_file", "whatsapp_message"}
_ACTION_WORDS = re.compile(r"\b(?:open|launch|start|run|play|put on|search|google|look up|find|show|go to|visit|"
                           r"navigate|emails?|e-mails?|mails?|inbox|send|write to|draft|volume|louder|quieter|"
                           r"spotify|youtube|website|folder|file|app|project|projects|claude)\b")
# commands whose words come from a fixed list, so the fast recogniser's text is reliable enough
_CLOSED_INTENTS = {"stop", "help", "ack", "tell_time", "set_volume", "media_control", "lock_screen",
                   "claude_projects", "check_email", "emails_to_reply", "close_app", "open_any", "open_website",
                   "play_spotify_liked", "email_ask_content", "whatsapp_ask", "calculate", "ask_song"}
_THEN = re.compile(r"\s+(?:and then|then|and also|and)\s+(?=(?:open|launch|start|play|search|google|close|"
                   r"email|mute|unmute|set|turn|go to|lock|pause|check)\b)")


class Jarvis:
    def __init__(self, cfg: dict[str, Any], bridge) -> None:
        self.cfg = cfg
        self.ui = bridge
        self.loop: asyncio.AbstractEventLoop | None = None
        self.state = "booting"
        self.mode = "off"             # audio thread mode: off | wake | capture
        self.muted = False
        self.pending: dict | None = None
        self.history: deque[dict] = deque(maxlen=8)  # a shorter prompt answers faster on this CPU
        self._last_chat = 0.0
        self.follow_up = False
        self.speak_aloud = True
        self.task: asyncio.Task | None = None
        self.mic = self.wake = self.endpointer = self.stt = self.tts = self.player = None
        self.ducker = None
        self.llm = None
        self._preroll: deque[np.ndarray] = deque(maxlen=8)
        self._heard: list[np.ndarray] = []   # frames of the utterance being recognised right now
        self._recognising = False
        self._heard_conf = 0.0          # recogniser confidence of the last spoken request
        self._remote_said: list[str] | None = None   # replies collected for a WhatsApp command
        self._remote_cards: list[dict] = []
        self._remote_lock = asyncio.Lock()
        self._speech_q: asyncio.Queue | None = None
        self.notices: list[dict] = []
        self.in_call = False          # a "call" = continuous conversation after a wake, until stop/close
        self.call_turns = 0
        self.last_stt = 0.0
        self._wake_checking = False
        self._speech_gen = 0
        self._barge_echo: list[float] = []
        self._barge_run = 0
        self._rethink_run = 0

    # ------------------------------------------------------------------ startup
    def _status(self, msg: str, **extra) -> None:
        log.info(msg)
        self.ui.send({"type": "status", "text": msg, "muted": self.muted, **extra})

    def _load(self) -> dict[str, str]:
        """Heavy model loading, run in a worker thread. Returns component health."""
        from .audio.ducking import Ducker
        from .audio.mic import Mic
        from .audio.player import Player
        from .audio.stt import STT
        from .audio.tts import TTS
        from .audio.vad import Endpointer
        from .tools.apps import AppIndex

        health: dict[str, str] = {}
        c = self.cfg
        tools.load_all()
        CTX.cfg, CTX.contacts = c, load_contacts()
        CTX.apps = AppIndex()
        if not CTX.apps.apps:
            CTX.apps.refresh()  # first run only; later runs refresh in the background once ready
        health["apps"] = f"{len(CTX.apps.apps)} apps"

        # The voice takes ~10 s to load (mostly ONNX session setup); do it alongside the other models
        voice: dict = {}
        tts_thread = threading.Thread(target=lambda: voice.setdefault("tts", TTS(
            c["tts"]["engine"], c["tts"]["voice"], c["tts"]["speed"], c["tts"]["piper_voice"])), daemon=True)
        tts_thread.start()
        self.player = Player(self._on_tts_level)
        self.ducker = Ducker(c["ducking"]["level"], c["ducking"]["enabled"])

        self._status("Loading speech recognition…")
        self.stt = STT(c["stt"]["model"], c["stt"]["compute_type"], c["stt"]["threads"],
                       c["stt"].get("accurate_model", ""), float(c["stt"].get("retry_below", -0.6)),
                       float(c["stt"].get("retry_max_seconds", 6)))
        # Whisper's prompt is short, so put the words people actually say first (an alphabetical
        # app list left out "Spotify" and it heard "Supportify")
        installed = {a.lower(): a for a in CTX.apps.names()}
        key = ["Spotify", "YouTube", "Liked Songs", "Chrome", "Edge", "Excel", "Word", "PowerPoint", "Outlook",
               "Teams", "VS Code", "Gmail", "Notepad", "Calculator", "File Explorer", "Downloads", "WhatsApp"]
        extra = [a for a in installed.values() if len(a) < 20 and a not in key][:25]
        self.stt.set_vocabulary(key + list(CTX.contacts)[:15] + extra)
        health["speech"] = c["stt"]["model"]

        L = c["listening"]
        self.endpointer = Endpointer(L["vad_aggressiveness"], L["silence_ms"], L["max_seconds"],
                                     L["no_speech_timeout"], int(L.get("short_silence_ms", 0)))
        # short phrases heard while idle, for the Whisper "Hey Jarvis" backup
        self.wake_vad = Endpointer(L["vad_aggressiveness"], 450, 3.6, 10**6)
        if c["wake_word"]["enabled"]:
            self._status("Loading wake word…")
            try:
                from .audio.wakeword import WakeWord

                self.wake = WakeWord(c["wake_word"]["model"], c["wake_word"]["threshold"])
                health["wake"] = "hey jarvis"
            except Exception as e:
                log.exception("wake word unavailable")
                health["wake"] = f"off ({e.__class__.__name__})"
        self.mic = Mic(float(c.get("mic", {}).get("gain", 1.0)))
        self.mic.start()
        health["mic"] = "on"
        self._status("Loading voice…")
        tts_thread.join()
        self.tts = voice.get("tts") or TTS("sapi")
        health["voice"] = self.tts.name
        return health

    async def start(self) -> None:
        from .brain.llm import LLM

        self.loop = asyncio.get_running_loop()
        self._speech_q = asyncio.Queue()
        self.llm = CTX.llm = LLM(self.cfg)
        self._status("Starting up…")
        try:
            health = await asyncio.to_thread(self._load)
        except Exception as e:
            log.exception("startup failed")
            self._status(f"Startup failed: {e}", error=True)
            raise
        threading.Thread(target=self._audio_loop, daemon=True, name="audio").start()
        asyncio.create_task(self._speech_worker())
        asyncio.create_task(self._telemetry())

        health["brain"] = "ready" if await self.llm.ensure_running() else "offline (commands still work)"
        if health["brain"] == "ready":
            asyncio.create_task(self.llm.warm([]))  # questions are the common LLM path; prime that prefix
        health["email"] = await self._connect_email()
        if self.cfg.get("whatsapp_remote", {}).get("enabled"):
            from .services.whatsapp_remote import WhatsAppRemote

            asyncio.create_task(WhatsAppRemote(self.cfg, self).run())
            health["whatsapp"] = "remote on"
        self.mode = "wake"
        self._set_state("idle")
        self._status("Ready", ready=True, health=health)
        log.info("health: %s", health)
        self.loop.call_later(20, lambda: threading.Thread(target=self._safe(CTX.apps.refresh), daemon=True).start())
        # pre-render the call greeting so it plays instantly on "Hey Jarvis"
        sir = self.cfg["address_as"]
        from .audio.tts import split_sentences

        for line in split_sentences(_greeting_lines(sir)):
            await asyncio.to_thread(self.tts.synth, line)
        if not self.cfg.get("greet_on_start", True):
            return
        await self.say(f"{_good_time_of_day()}, {self.cfg['address_as']}. Systems are online.", then_idle=True)

    async def _connect_email(self) -> str:
        if not self.cfg["email"]["enabled"]:
            return "off"
        try:
            from .tools.gmail import Gmail
            from .tools.mail_imap import ImapMail

            # app password (no Google Cloud project) first, then the OAuth route if that was set up instead
            CTX.gmail = await asyncio.to_thread(ImapMail.connect) or await asyncio.to_thread(Gmail.connect)
        except Exception as e:
            log.warning("gmail connect failed: %s", e)
            CTX.gmail = None
            return "error (see log)"
        if CTX.gmail is None:
            return "not set up"
        threading.Thread(target=self._safe(CTX.gmail.load_contacts), daemon=True).start()
        from .services.gmail_watcher import GmailWatcher

        asyncio.create_task(GmailWatcher(CTX.gmail, self.cfg, self._announce_mail).run())
        return CTX.gmail.me

    @staticmethod
    def _safe(fn):
        def run():
            try:
                fn()
            except Exception:
                log.exception("background task failed")
        return run

    # ------------------------------------------------------------------ audio thread
    def _audio_loop(self) -> None:
        sent_levels = 0.0
        while True:
            frame = self.mic.frames.get()
            if self.muted:
                continue
            if self.mode == "off":
                # Jarvis is working out what you said. If you start talking again (it misheard, or you
                # changed your mind), drop that attempt and listen afresh.
                self._preroll.append(frame)
                if self.in_call and self.state == "thinking" and self.cfg["call"].get("barge_in", True) \
                        and self._rethink_feed(frame):
                    pre = list(self._preroll)[-6:]
                    if self._recognising:
                        # still working out the words: you paused mid-sentence and carried on. Keep what
                        # you said before the pause and hear the whole sentence together.
                        log.info("continuing the sentence after a pause")
                        self.loop.call_soon_threadsafe(
                            lambda p=pre, c=list(self._heard): self._wake("barge-in", p, carry=c))
                    else:
                        self.loop.call_soon_threadsafe(lambda p=pre: self._wake("barge-in", p))
                elif self.state != "thinking":
                    self._rethink_run = 0
                continue
            if self.mode == "wake":
                self._preroll.append(frame)
                speaking = self.player.busy
                if not speaking:  # Jarvis's own voice must not raise the "background noise" level
                    self.endpointer.observe_idle(frame)
                if speaking and self.in_call and self.cfg["call"].get("barge_in", True):
                    if self._barge_feed(frame):  # you started talking over Jarvis
                        pre = list(self._preroll)[-5:]
                        self.wake_vad.reset()
                        self.loop.call_soon_threadsafe(lambda p=pre: self._wake("barge-in", p))
                        continue
                elif not speaking:
                    self._barge_echo, self._barge_run = [], 0
                if self.wake is None:
                    continue
                thr = self.cfg["wake_word"]["threshold_while_speaking"] if speaking else None
                if self.wake.process(frame, thr):
                    pre = list(self._preroll)[-3:]
                    self.wake_vad.reset()
                    self.loop.call_soon_threadsafe(lambda p=pre: self._wake("voice", p))
                elif not speaking and not self.in_call and self.cfg["wake_word"].get("whisper_backup", True):
                    self._backup_wake_feed(frame)
            elif self.mode == "capture":
                status = self.endpointer.feed(frame)
                now = time.monotonic()
                if now - sent_levels > 0.06:
                    sent_levels = now
                    rms, bands = levels(frame.astype(np.float32) / 32768.0, MIC_SR)
                    self.ui.send({"type": "level", "source": "mic", "rms": rms, "bands": bands})
                if status in ("done", "timeout"):
                    self.mode = "off"
                    audio = self.endpointer.audio() if status == "done" else None
                    if audio is not None:
                        self._heard = self.endpointer.carry + self.endpointer.frames
                    self.loop.call_soon_threadsafe(self._utterance_ready, audio)

    def _barge_feed(self, frame: np.ndarray) -> bool:
        """True when you start talking over Jarvis. The mic also hears Jarvis through the speakers, so the
        first ~0.4 s of each reply measures that echo; after that, ~0.3 s of speech clearly louder than the
        echo counts as you cutting in."""
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        if len(self._barge_echo) < 5:
            self._barge_echo.append(rms)
            return False
        echo = float(np.median(self._barge_echo))
        need = max(echo * float(self.cfg["call"].get("barge_in_ratio", 2.5)), 900.0)
        if rms > need and self.endpointer.vad_votes(frame) >= 3:
            self._barge_run += 1
        else:
            self._barge_run = 0
            if rms < need:  # follow slow changes in Jarvis's own loudness
                self._barge_echo = self._barge_echo[-19:] + [rms]
        if self._barge_run >= 4:
            log.info("barge-in (mic %.0f vs echo %.0f)", rms, echo)
            self._barge_echo, self._barge_run = [], 0
            return True
        return False

    def _rethink_feed(self, frame: np.ndarray) -> bool:
        """True after ~0.4 s of clear, close speech while Jarvis is still thinking (nothing is playing
        then, so the bar is the room's normal noise rather than an echo). At 0.3 s and a lower level it
        fired 61 times in the log, mostly on other voices and videos, each time throwing the answer away."""
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        floor_rms = float(self.cfg["call"].get("rethink_min_rms", 1800))
        if rms > max(self.endpointer.noise_floor * 3.5, floor_rms) and self.endpointer.vad_votes(frame) >= 3:
            self._rethink_run += 1
        else:
            self._rethink_run = 0
        if self._rethink_run >= 5:
            self._rethink_run = 0
            log.info("interrupted while thinking (mic %.0f, floor %.0f)", rms, self.endpointer.noise_floor)
            return True
        return False

    def _backup_wake_feed(self, frame: np.ndarray) -> None:
        """The wake-word model misses some accents. Whisper doesn't: when someone close to the mic says a
        short phrase while Jarvis is idle, transcribe it and check whether it starts with "Hey Jarvis".
        Long speech (conversations, meetings) and distant/quiet voices are ignored. Nothing is stored."""
        self.wake_vad.observe_idle(frame)
        if self.wake_vad.feed(frame) != "done":
            return
        audio = self.wake_vad.audio()
        self.wake_vad.reset()
        dur, peak = audio.size / MIC_SR, float(np.max(np.abs(audio))) if audio.size else 0.0
        near = float(self.cfg["wake_word"].get("near_peak", 0.3))
        if 0.4 <= dur <= 3.4 and peak >= near and not self._wake_checking:
            self._wake_checking = True
            self.loop.call_soon_threadsafe(lambda a=audio: asyncio.ensure_future(self._wake_from_text(a)))

    async def _wake_from_text(self, audio: np.ndarray) -> None:
        from .brain.intents import wake_phrase

        try:
            text = await asyncio.to_thread(self.stt.quick, audio)
        finally:
            self._wake_checking = False
        rest = wake_phrase(text) if text else None
        if rest is None or self.in_call or self.state != "idle" or self.muted:
            log.debug("wake check: no (%r)", text)
            return
        log.info("wake (whisper): %r", text)
        # "Hey Jarvis, open Chrome" in one breath: skip the greeting and do it
        self._wake("voice", first_text=rest if len(rest.split()) >= 2 else None)

    def _on_tts_level(self, rms: float, bands: list[float]) -> None:
        self.ui.send({"type": "level", "source": "tts", "rms": rms, "bands": bands})

    # ------------------------------------------------------------------ state
    def _set_state(self, value: str, **extra) -> None:
        self.state = value
        self.ui.send({"type": "state", "value": value, **extra})

    def _start_capture(self, timeout: float | None, preroll: list | None = None,
                       carry: list | None = None) -> None:
        self.endpointer.reset(timeout, carry)
        for f in preroll or []:
            self.endpointer.frames.append(f)
        self.mode = "capture"
        self._set_state("listening", follow_up=self.follow_up)

    def _wake(self, source: str, preroll: list | None = None, first_text: str | None = None,
              carry: list | None = None) -> None:
        """'Hey Jarvis', the hotkey or a click: start a call, or (mid-call) cut in and listen."""
        if self.muted and source == "voice":
            return
        log.info("wake (%s)", source)
        self._interrupt()
        self.speak_aloud = True
        if not self.in_call:
            self.in_call = True
            self.call_turns = 0
            self.ui.send({"type": "call", "value": "start", "source": source})
            self.ducker and asyncio.get_running_loop().run_in_executor(None, self.ducker.duck)
            self.player.play(earcons.WAKE)
            asyncio.create_task(self._warm_llm())  # load the model while you're still talking
            if first_text:
                self.ui.send({"type": "transcript", "text": first_text, "final": True})
                self.task = asyncio.create_task(self.handle_text(first_text))
                return
            if self.cfg["call"].get("greeting", True):
                self.task = asyncio.create_task(self._greet())
                return
        self._start_capture(self._listen_timeout(), preroll, carry)

    async def _greet(self) -> None:
        """Opening line of every call; listening starts as soon as it finishes."""
        await self.say(_greeting_lines(self.cfg["address_as"]), then_idle=False)
        self._start_capture(self._listen_timeout())

    async def _telemetry(self) -> None:
        """Real numbers for the call screen's HUD, sent only while a call is open."""
        import psutil

        psutil.cpu_percent(None)
        while True:
            await asyncio.sleep(2)
            if not self.in_call:
                continue
            self.ui.send({"type": "stats", "cpu": round(psutil.cpu_percent(None)),
                          "mem": round(psutil.virtual_memory().percent), "stt": round(self.last_stt, 1),
                          "llm": self.llm.model if self.llm else "", "voice": self.tts.name if self.tts else "",
                          "muted": self.muted})

    def _listen_timeout(self) -> float:
        c = self.cfg["call"]
        # a short first window so an accidental wake-up ends by itself; then a long, patient one
        return c["first_timeout"] if self.call_turns == 0 else c["idle_timeout"]

    async def _warm_llm(self) -> None:
        from .priority import boost_priority

        if self.llm and await self.llm.available() and not await self.llm.loaded():
            await self.llm.warm([])
        await asyncio.to_thread(boost_priority)  # Ollama starts a fresh runner process per model load

    def _end_call(self, reason: str) -> None:
        log.info("call ended (%s)", reason)
        self._interrupt()
        self.in_call = False
        self.ui.send({"type": "call", "value": "end", "reason": reason})
        self.player.play(earcons.END)
        self._idle()

    def _interrupt(self) -> None:
        """Stop talking and abandon whatever is in progress."""
        if self.task and not self.task.done():
            self.task.cancel()
        self.player and self.player.stop()
        self._recognising = False
        self._speech_gen += 1  # a sentence still being synthesised is dropped instead of playing late
        if self._speech_q:
            while not self._speech_q.empty():
                self._speech_q.get_nowait()
                # without this, join() in _wait_speech never returns again: every later reply would
                # "never finish speaking" and Jarvis would stop listening
                self._speech_q.task_done()
        self.mode = "wake"

    def _go_idle(self) -> None:
        """A turn finished. In a call, listen again without the wake word; otherwise rest."""
        if self.in_call and not self.muted:
            self.call_turns += 1
            self.speak_aloud = True
            self._start_capture(self._listen_timeout())
            return
        self._idle()

    def _idle(self) -> None:
        if self.wake is not None and self.mode != "wake":
            self.wake.reset()  # forget the audio it heard before the call, so it starts clean
        self.mode = "wake"
        self.follow_up = False
        self._set_state("idle")
        if self.ducker:
            asyncio.get_running_loop().run_in_executor(None, self.ducker.restore)
        if self.notices:
            batch, self.notices = self.notices, []
            asyncio.create_task(self._announce_mail(batch))

    def _utterance_ready(self, audio: np.ndarray | None) -> None:
        self.task = asyncio.create_task(self._process_audio(audio))

    async def _process_audio(self, audio: np.ndarray | None) -> None:
        if audio is None:  # nothing said for the whole listening window
            log.info("no speech")
            if self.in_call:
                self._end_call("silence")
            else:
                self._idle()
            return
        self._set_state("thinking")
        self.player.play(earcons.HEARD)
        t0 = time.perf_counter()
        gen = self._speech_gen
        self._recognising = True
        try:
            text = await asyncio.to_thread(self.stt.transcribe, audio, self._clear_command,
                                           lambda: gen != self._speech_gen)
        finally:
            if gen == self._speech_gen:
                self._recognising = False
        self.last_stt = time.perf_counter() - t0
        self._heard_conf = self.stt.last_conf
        if not text:  # noise or background chatter: keep listening quietly
            if self.in_call and audio.size > MIC_SR * 0.8:
                self.ui.send({"type": "unclear"})  # a quiet hint on screen that you weren't understood
            self._go_idle()
            return
        self.ui.send({"type": "transcript", "text": text, "final": True})
        await self.handle_text(text)

    def _clear_command(self, text: str) -> bool:
        """The fast recogniser's text is a plain command, so the slow re-check can be skipped. Free text
        (song names, messages, email wording) and answers to "shall I send it?" are always re-checked."""
        if self.pending:
            return False
        t = normalize(text)
        if not t:
            return True
        intent = parse(t, self.cfg["music"]["default_service"])
        return intent is not None and intent.tool in _CLOSED_INTENTS

    # ------------------------------------------------------------------ understanding
    def _resolve_open(self, target: str, verb: str) -> Intent:
        from .tools.browser import resolve_site
        from .tools.files import match_folder

        if match_folder(target):
            return Intent("open_folder", {"name": target})
        aliases = {k.lower() for k in self.cfg["apps"]["aliases"]}
        sites = {k.lower() for k in self.cfg["websites"]}
        app = CTX.apps.find(target)
        exact_app = app and (target in aliases or app[0].lower() == target)
        if exact_app:
            return Intent("open_app", {"name": target})
        if target in sites:
            return Intent("open_website", {"site": target})
        if app:
            return Intent("open_app", {"name": target})
        if resolve_site(target):
            return Intent("open_website", {"site": target})
        return Intent("open_file", {"query": target})

    async def handle_text(self, text: str, typed: bool = False) -> None:
        if typed:  # in a call Jarvis answers out loud; a quick typed command outside one stays silent
            self.speak_aloud = self.in_call or bool(self.cfg.get("speak_typed_replies", False))
            self.ui.send({"type": "transcript", "text": text, "final": True})
        t = normalize(text)
        conf, self._heard_conf = self._heard_conf, 0.0
        log.info("user: %r -> %r", text, t)
        if not t:  # just "Hey Jarvis" (e.g. said again mid-call): acknowledge and keep listening
            await self.say("Yes?", then_idle=True)
            return
        if self.pending:
            handled = await self._handle_pending(t, text)
            if handled:
                return
        parts = _THEN.split(t) if " and " in t or " then " in t else [t]
        intents = [parse(p, self.cfg["music"]["default_service"], self._resolve_open) for p in parts]
        if len(parts) > 1 and not all(intents):
            intents = [parse(t, self.cfg["music"]["default_service"], self._resolve_open)]
        if intents == [None] and re.search(r"\b(?:tell me about|about|status|update|updates|how is|how's|progress|"
                                           r"what about|brief me on|what's happening)\b", t):
            # "tell me about Hackathon": a project name the patterns can't know, so check the real list
            from .tools.claude_code import list_projects

            projects = await asyncio.to_thread(list_projects, 12)
            named = next((p for p in projects if re.search(r"\b" + re.escape(_project_words(p.cwd)) + r"\b",
                                                           _project_words(t))), None)
            if named:
                intents = [Intent("claude_project_status", {"project": named.cwd})]
        if intents == [None] and not typed:
            tail = _embedded_command(t)
            if tail:  # "Take this, open WhatsApp": a misheard "Jarvis" in front of a clear command
                hit = parse(tail, self.cfg["music"]["default_service"], self._resolve_open)
                if hit is not None and hit.tool in _CLOSED_INTENTS | {"play_youtube", "play_spotify", "web_search"}:
                    log.info("command found after misheard words: %r", tail)
                    intents = [hit]
        if intents == [None]:
            if not typed and len(t.split()) <= 3 and conf < -0.7:
                # a mumbled fragment ("Yeah, this.") once kept the model busy for a minute; ask instead
                log.info("unsure fragment not sent to the model: %r (%.2f)", text, conf)
                await self.say(f"Sorry, {self.cfg['address_as']}, I didn't catch that.", then_idle=True)
                return
            await self._chat(text)
            return
        results = []
        for intent in intents:
            if intent.tool == "stop":
                self.pending = None
                self.ui.send({"type": "card", "kind": "clear"})
                if self.in_call:
                    self._end_call("user")
                else:
                    self._interrupt()
                    self._idle()
                return
            if intent.tool == "help":
                results.append(Result(help_text(self.cfg["address_as"], CTX.gmail is not None),
                                      card={"kind": "capabilities",
                                                  "groups": [{"title": t, "items": i} for t, i in CAPABILITIES]}))
                continue
            if intent.tool == "say":  # a fixed answer, e.g. something Jarvis can't do yet
                results.append(Result(intent.args["text"]))
                continue
            if intent.tool == "whatsapp_ask":  # who? -> what? -> typed in WhatsApp -> "send it"
                who = intent.args.get("contact", "")
                if who:
                    results.append(Result(f"Certainly. What should I say to {who} on WhatsApp?", icon="whatsapp",
                                          pending={"kind": "wa_content", "contact": who}))
                else:
                    self.ui.send({"type": "focus_input", "placeholder": "Contact name as it appears in WhatsApp…"})
                    results.append(Result("Certainly, sir. Who should I message on WhatsApp? Say or type their name "
                                          "as it appears in your chats.", icon="whatsapp",
                                          pending={"kind": "wa_recipient",
                                                   "message": intent.args.get("message", "")}))
                continue
            if intent.tool == "email_ask_content":
                if CTX.gmail is None:
                    from .tools.gmail import _not_connected

                    results.append(Result(_not_connected(), ok=False, icon="mail"))
                elif intent.args.get("to"):
                    to = intent.args["to"]
                    results.append(Result(f"Certainly. What should I say to {to}?", icon="mail",
                                          pending={"kind": "email_content", "to": to}))
                else:  # step 1 of the guided email: who? (typing is easiest for an address)
                    self.ui.send({"type": "focus_input", "placeholder": "Type the email address, then Enter…"})
                    results.append(Result("Certainly, sir. Who would you like to email? Type their address in the "
                                          "command box, or say their name.", icon="mail",
                                          pending={"kind": "email_recipient"}))
                continue
            if intent.tool == "ask_song":
                results.append(Result("Which song should I play?",
                                      pending={"kind": "song_query", "service": intent.args["service"]}))
                continue
            if intent.tool == "ack":
                results.append(Result(f"Loud and clear, {self.cfg['address_as']}. Go ahead."))
                continue
            results.append(await self.run_tool(intent.tool, intent.args))
        await self._deliver(results)

    async def run_tool(self, name: str, args: dict) -> Result:
        t = REGISTRY.get(name)
        if t is None:
            return Result("I can't do that yet.", ok=False)
        if t.risk != "safe":
            return Result("That needs your confirmation first.", ok=False)
        self._set_state("acting", icon=name.split("_")[-1])
        log.info("tool %s %s", name, args)
        if name == "claude_project_status":  # reading + summarising takes ~15 s on this CPU; don't go silent
            self._queue_speech("One moment, checking the project.")
        if self.in_call and name in _OPENS_WINDOW:
            # shrink the call first: a full-screen window on top stops Chromium apps (Spotify, browsers)
            # from rendering, so Spotify's search results never appear and Play can't be pressed
            self.ui.send({"type": "call", "value": "mini"})
            await asyncio.sleep(0.4)
        try:
            return await t.func(**{k: v for k, v in args.items() if k in t.params})
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("tool %s failed", name)
            return Result(f"That didn't work: {str(e)[:80]}.", ok=False)

    async def _deliver(self, results: list[Result]) -> None:
        for r in results:
            if r.card:
                self.ui.send({"type": "card", **r.card})
                if self._remote_said is not None:
                    self._remote_cards.append(r.card)
            if r.pending:
                self.pending = r.pending
        ok = all(r.ok for r in results)
        say = " ".join(r.say for r in results)
        # something opened on screen (app, site, video...): shrink the call so you can see it
        if self.in_call and any(r.ok and r.icon in _REVEALS for r in results):
            self.ui.send({"type": "call", "value": "mini"})
        if not ok:
            self._remote_said is None and self.player.play(earcons.ERROR)
            self._set_state("error")
        wants_answer = any(r.pending and r.pending.get("kind") != "email_list" for r in results)
        if self.in_call and not wants_answer and any(r.ok and r.icon in ("youtube", "spotify") for r in results):
            # music is playing: hang up so the volume comes back up and the mic doesn't hear the song
            # as commands. "Hey Jarvis" still works over music.
            await self.say(say, then_idle=False)
            self._end_call("music")
            return
        await self.say(say, then_idle=not wants_answer, follow=wants_answer)

    # ------------------------------------------------------------------ follow-ups
    async def _handle_pending(self, t: str, raw: str) -> bool:
        p = self.pending
        kind = p["kind"]
        if kind == "song_query":  # answer to "Which song should I play?"
            self.pending = None
            if NO.match(t):
                await self.say("Okay.", then_idle=True)
                return True
            other = parse(t)
            if other is not None and other.tool not in ("open_any", "open_file", "ack"):
                return False  # they gave a new command instead
            query = re.sub(r"^(?:play|the song|song)\s+", "", t).strip() or t
            await self._deliver([await self.run_tool(p["service"], {"query": query})])
            return True
        if kind == "choose_project":  # "Which one would you like?" -> a number or a name
            self.pending = None
            from .tools.claude_code import claude_project_status

            idx = parse_choice(t)
            # a listed project named anywhere in the sentence ("tell me the status of Hackathon")
            named = next((cwd for cwd in p["projects"]
                          if re.search(r"\b" + re.escape(_project_words(cwd)) + r"\b", _project_words(t))), None)
            if idx is not None and (idx == -1 or idx <= len(p["projects"])):
                target = p["projects"][idx - 1 if idx > 0 else -1]
            elif named:
                target = named
            elif NO.match(t):
                await self.say("Okay.", then_idle=True)
                return True
            elif parse(t) is not None and parse(t).tool not in ("open_any", "open_file", "ack"):
                return False
            else:
                target = re.sub(r"^(?:the |my )?(.+?)(?: project| one)?$", r"\1", t)
            self.ui.send({"type": "card", "kind": "clear"})
            await self._deliver([await self.run_tool("claude_project_status", {"project": target})])
            return True
        if kind == "claude_task":  # "What would you like Claude Code to do next?" -> the task, in your words
            self.pending = None
            if STOP.match(t):
                return False  # "bye" still ends the call
            if NO.match(t):
                await self.say("Understood. Standing by.", then_idle=True)
                return True
            other = parse(t)
            if other is not None and other.tool not in ("open_any", "open_file", "ack", "calculate"):
                return False  # a different command, not a task for Claude Code
            from .tools.claude_code import prepare_task

            await self._deliver([prepare_task(p["cwd"], p["name"], raw.strip().rstrip("."), p.get("session", ""))])
            return True
        if kind == "claude_send":  # read back; only "yes / send it" launches Claude Code
            if YES.match(t):
                await self._launch_claude(p)
                return True
            self.pending = None
            if NO.match(t):
                self.ui.send({"type": "card", "kind": "clear"})
                await self.say("Okay, not sent.", then_idle=True)
                return True
            if parse(t) is None:  # a reworded task
                from .tools.claude_code import prepare_task

                await self._deliver([prepare_task(p["cwd"], p["name"], raw.strip().rstrip("."), p.get("session", ""))])
                return True
            return False
        if kind == "send_email":
            if YES.match(t):
                await self._send_email(p)
                return True
            if NO.match(t):
                self.pending = None
                self.ui.send({"type": "card", "kind": "clear"})
                await self.say(_unsent_line(p["draft_id"]), then_idle=True)
                return True
            if parse(t) is None or parse(t).tool in ("open_any", "open_file"):
                await self._revise_email(p, raw)
                return True
            self.pending = None  # a new command; the draft stays in Gmail
            return False
        if kind == "wa_recipient":  # "Who should I message on WhatsApp?" -> a name
            self.pending = None
            if STOP.match(t):
                return False
            if NO.match(t):
                await self.say("Alright.", then_idle=True)
                return True
            who = re.sub(r"^(?:to |message |send it to )", "", raw.strip().rstrip(".?!"), flags=re.I)
            if p.get("message"):  # "send a text saying I'm late" -> "who?" -> "Ali": the message is known
                await self._deliver([await self.run_tool("whatsapp_message", {"contact": who,
                                                                              "message": p["message"]})])
                return True
            self.pending = {"kind": "wa_content", "contact": who}
            await self.say(f"What should I say to {who}?", follow=True, then_idle=False)
            return True
        if kind == "wa_content":  # "What should I say to Ali?" -> the message, in your words
            self.pending = None
            if STOP.match(t):
                return False
            if NO.match(t):
                await self.say("Alright, no message.", then_idle=True)
                return True
            await self._deliver([await self.run_tool("whatsapp_message", {"contact": p["contact"],
                                                                          "message": raw.strip()})])
            return True
        if kind == "wa_send":  # typed in WhatsApp and read back; only "yes / send it" sends
            if YES.match(t):
                await self._whatsapp_send(p)
                return True
            if NO.match(t):
                self.pending = None
                await asyncio.to_thread(_quietly, "whatsapp.clear")
                self.ui.send({"type": "card", "kind": "clear"})
                await self.say("Okay, not sent. I've cleared it from the chat.", then_idle=True)
                return True
            if parse(t) is None:  # a reworded message replaces the one in the box
                self.pending = None
                await asyncio.to_thread(_quietly, "whatsapp.clear")
                await self._deliver([await self.run_tool("whatsapp_message", {"contact": p["chat"],
                                                                              "message": raw.strip()})])
                return True
            self.pending = None
            return False
        if kind == "email_recipient":  # "Who would you like to email?" -> a typed address or a name
            if STOP.match(t):
                self.pending = None
                return False
            if NO.match(t):
                self.pending = None
                await self.say("Alright, no email.", then_idle=True)
                return True
            from .tools.gmail import resolve_recipient

            addr = _spoken_email(raw) or (raw.strip() if re.fullmatch(r"[^@\s]+@[^@\s]+\.\w+", raw.strip()) else "")
            matches = [(addr, addr)] if addr else resolve_recipient(re.sub(r"^(?:to |email |send it to )", "", t))
            if len(matches) == 1:
                name, addr = matches[0]
                self.pending = {"kind": "email_content", "to": addr, "to_name": name}
                self.ui.send({"type": "focus_input", "placeholder": "Say or type what the email should say…"})
                await self.say(f"Got it, {name}. What would you like the email to say?", follow=True, then_idle=False)
                return True
            if len(matches) > 1:
                self.pending = None
                await self._deliver([await self.run_tool("draft_email", {"to": t, "about": ""})])
                return True
            self.ui.send({"type": "focus_input", "placeholder": "Type the email address, then Enter…"})
            await self.say("I don't have an address for that name. Please type the email address in the command box.",
                           follow=True, then_idle=False)
            return True  # still waiting for the address
        if kind == "email_content":  # "What should I say to Sara?" -> your words become the email
            self.pending = None
            if STOP.match(t):
                return False
            if NO.match(t):
                await self.say("Alright, no email.", then_idle=True)
                return True
            if "@" in p["to"]:  # address already known (typed or from contacts): draft straight away
                from .tools.gmail import make_draft

                self._set_state("acting", icon="mail")
                await self._deliver([await make_draft(p.get("to_name") or p["to"], p["to"], raw.strip())])
            else:
                await self._deliver([await self.run_tool("draft_email", {"to": p["to"], "about": raw.strip()})])
            return True
        if kind == "reply_text":  # "What would you like me to reply?" -> your words become the draft
            self.pending = None
            if STOP.match(t):
                return False
            if NO.match(t):
                await self.say("Alright, I'll leave it unanswered for now.", then_idle=True)
                return True
            from .tools.gmail import make_reply

            self._set_state("thinking")
            await self._deliver([await make_reply(p["msg"], raw.strip())])
            return True
        if kind == "reply_list" and parse_choice(t) is None:  # "the one from Ahmed"
            msgs = p["messages"]
            name = re.sub(r"^(?:the )?(?:one |email |mail )?(?:from |by )?", "", t).strip()
            hit = next((i for i, x in enumerate(msgs) if name and (name in x["from_name"].lower()
                                                                    or name in x["subject"].lower())), None)
            if hit is not None:
                await self._choose(hit + 1)
                return True
            if YES.match(t) and len(msgs) == 1:
                await self._choose(1)
                return True
        if kind in ("choose_file", "choose_recipient", "email_list", "reply_list"):
            idx = parse_choice(t)
            if kind == "email_list" and idx is None:
                m = re.match(r"^(?:read |open )?(?:the )?(?:one |email |mail )?from (.+)$", t)
                if m:
                    msgs = p["messages"]
                    hit = next((i for i, x in enumerate(msgs) if m.group(1) in x["from_name"].lower()), None)
                    idx = None if hit is None else hit + 1
                elif re.match(r"^(?:read it|read that|read them|read the first)$", t):
                    idx = 1
            if idx is None:
                if NO.match(t):
                    self.pending = None
                    self.ui.send({"type": "card", "kind": "clear"})
                    await self.say("Okay.", then_idle=True)
                    return True
                self.pending = None
                return False
            await self._choose(idx)
            return True
        if kind == "need_address":
            addr = _spoken_email(raw)
            if addr:
                self.pending = None
                from .tools.gmail import make_draft

                self._set_state("acting", icon="mail")
                await self._deliver([await make_draft(p["to_name"], addr, p["about"])])
                return True
            self.pending = None
            return False
        return False

    async def _choose(self, idx: int) -> None:
        p, self.pending = self.pending, None
        kind = p["kind"]
        if kind == "choose_file":
            paths = p["paths"]
            path = paths[idx - 1] if idx == -1 or idx <= len(paths) else None
            if path is None:
                await self.say("There isn't one by that number.", then_idle=True)
                return
            from .tools.files import open_path

            self.ui.send({"type": "card", "kind": "clear"})
            await self._deliver([open_path(path)])
        elif kind == "choose_recipient":
            name, addr = p["matches"][idx - 1 if idx > 0 else -1]
            from .tools.gmail import make_draft

            self._set_state("acting", icon="mail")
            await self._deliver([await make_draft(name, addr, p["about"])])
        elif kind == "reply_list":
            msgs = p["messages"]
            if not (idx == -1 or idx <= len(msgs)):
                await self.say("There isn't one by that number.", then_idle=True)
                return
            from .tools.gmail import start_reply

            self._set_state("acting", icon="mail")
            await self._deliver([await start_reply(msgs[idx - 1 if idx > 0 else -1])])
        elif kind == "email_list":
            msgs = p["messages"]
            if not (idx == -1 or idx <= len(msgs)):
                await self.say("There isn't one by that number.", then_idle=True)
                return
            from .tools.gmail import summarise_message

            self._set_state("acting", icon="mail")
            await self._deliver([await summarise_message(msgs[idx - 1 if idx > 0 else -1])])

    async def _whatsapp_send(self, p: dict) -> None:
        """The ONLY path that sends a WhatsApp message: after you confirmed the exact text read back."""
        from .tools.whatsapp import WhatsAppError, send_ready_message

        self.pending = None
        self._set_state("acting", icon="whatsapp")
        try:
            await asyncio.to_thread(send_ready_message, p["text"])
        except WhatsAppError as e:
            await self._deliver([Result(str(e), ok=False, icon="whatsapp")])
            return
        except Exception as e:
            log.exception("whatsapp send failed")
            await self._deliver([Result(f"Sending failed: {str(e)[:60]}. Please check the chat.", ok=False)])
            return
        self.ui.send({"type": "card", "kind": "sent", "to": p["chat"], "subject": "WhatsApp message"})
        self.player.play(earcons.DONE)
        await self.say(f"Sent to {p['chat']} on WhatsApp.", then_idle=True)

    async def _launch_claude(self, p: dict, task: str | None = None) -> None:
        """Hand the confirmed task to Claude Code in a visible terminal."""
        from .tools.claude_code import launch

        self.pending = None
        if self.in_call:
            self.ui.send({"type": "call", "value": "mini"})  # the terminal should be visible
        self.ui.send({"type": "card", "kind": "clear"})
        self._set_state("acting", icon="code")
        result = await asyncio.to_thread(launch, p["cwd"], p["name"], task or p["task"], p.get("session", ""))
        await self._deliver([result])

    async def _send_email(self, p: dict, edited: dict | None = None) -> None:
        """The ONLY code path that sends email. Requires a pending draft the user confirmed."""
        self.pending = None
        to, subject, body = p["to"], p["subject"], p["body"]
        if edited:
            to, subject, body = edited.get("to") or to, edited.get("subject") or subject, edited.get("body") or body
        self._set_state("acting", icon="mail")
        try:
            if (to, subject, body) != (p["to"], p["subject"], p["body"]):
                await asyncio.to_thread(CTX.gmail.update_draft, p["draft_id"], to, subject, body)
            await asyncio.to_thread(CTX.gmail.send_draft, p["draft_id"])
        except Exception as e:
            log.exception("send failed")
            self.ui.send({"type": "card", "kind": "clear"})
            await self._deliver([Result(f"Sending failed: {str(e)[:60]}. Nothing was sent; the draft is still "
                                        "on screen.", ok=False)])
            return
        self.ui.send({"type": "card", "kind": "sent", "to": to, "subject": subject})
        self.player.play(earcons.DONE)
        await self.say(f"Sent to {p.get('to_name', to).split()[0]}.", then_idle=True)

    async def _revise_email(self, p: dict, instruction: str) -> None:
        """Apply one change to the draft, show it, and read the new text back before asking to send."""
        from .tools.gmail import draft_text, revise

        self._set_state("thinking")
        changed = await revise(p, instruction)
        self.pending = p
        if changed is None:  # never guess: the draft stays exactly as it was
            await self.say("I couldn't make that change, sir. The draft is unchanged. You can edit it on screen, "
                           "or tell me the whole message again. Shall I send it as it is?", follow=True, then_idle=False)
            return
        subject, body = changed
        await asyncio.to_thread(CTX.gmail.update_draft, p["draft_id"], p["to"], subject, body)
        p.update(subject=subject, body=body)
        self.ui.send({"type": "card", "kind": "email_draft", "id": p["draft_id"], "to": p["to"],
                      "to_name": p.get("to_name", ""), "subject": subject, "body": body})
        greeting = body.strip().splitlines()[0] if body.strip() else ""
        await self.say(f"Updated. It now reads: {greeting} {draft_text(body)} Shall I send it?",
                       follow=True, then_idle=False)

    # ------------------------------------------------------------------ chat (LLM)
    async def _chat(self, text: str) -> None:
        if not await self.llm.available():
            self._queue_speech("One moment, bringing my language model online.")
            if not await self.llm.ensure_running():
                await self._deliver([Result("My language model won't start, sir, so I can't answer that one. "
                                            "Commands like opening apps and playing music still work.", ok=False)])
                return
        self._set_state("thinking")
        cold = not await self.llm.loaded()
        if cold:
            self.ui.send({"type": "reply", "text": "", "append": False})
            self._queue_speech("One moment.")  # cold start takes a while on this CPU; don't leave silence
        if time.monotonic() - self._last_chat > 600:
            self.history.clear()  # a question from ten minutes ago only slows the model down now
        self._last_chat = time.monotonic()
        self.history.append({"role": "user", "content": text})
        buf, spoken, tool_results = "", [], []
        # The 3B model gets confused by tool schemas on plain questions (it emits broken tool calls),
        # and they cost ~700 prompt tokens. Only offer tools when the request sounds like an action.
        tools = llm_tools() if _ACTION_WORDS.search(text.lower()) else []
        if tools and not self.cfg["llm"].get("use_tools", False):
            # With the tool list the 3B model never answered within 35 s on this laptop. An action the
            # command engine couldn't read is usually a mishearing: ask again instead of a long silence.
            self.history.pop()
            log.info("unparsed command not sent to the model: %r", text)
            await self.say(f"Sorry, {self.cfg['address_as']}, I didn't get that command. Could you say it "
                           "another way? For example, open Spotify.", then_idle=True)
            return
        c = self.cfg["llm"]
        # Never "think" forever: a stuck or overloaded model once left the call on "thinking" for a minute
        # the tool list adds ~700 prompt tokens, which take this CPU another ~15 s to read
        first_wait = float(c.get("first_reply_seconds", 20)) + (25 if cold else 0) + (15 if tools else 0)
        filler = None if cold else asyncio.get_running_loop().call_later(
            float(c.get("filler_after_seconds", 4)), lambda: not spoken and self._queue_speech("Just a moment, sir."))
        try:
            for attempt in range(2):
                async for kind, data in _deadline(self.llm.chat(list(self.history), tools), first_wait,
                                                  float(c.get("gap_seconds", 15))):
                    filler and filler.cancel()
                    if kind == "tool":
                        name, args = data
                        tool_results.append(await self.run_tool(name, {k: str(v) for k, v in args.items()}))
                        continue
                    buf += data
                    while True:
                        m = re.search(r"(.+?[.!?])(\s+|$)(?=\S)", buf, re.S)
                        if not m:
                            break
                        sentence, buf = m.group(1).strip(), buf[m.end():]
                        spoken.append(sentence)
                        self._queue_speech(sentence)
                if spoken or buf.strip() or tool_results or not tools:
                    break
                log.info("empty LLM reply with tools; retrying without")
                tools = []
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError:
            log.warning("llm gave up after waiting (cold=%s, tools=%s): %r", cold, bool(tools), text)
            if not spoken and not buf.strip() and not tool_results:
                self.history.pop()
                await self._deliver([Result(f"Sorry, {self.cfg['address_as']}, that's taking me too long. "
                                            "Please ask me again, or say it a little differently.", ok=False)])
                return
        except Exception as e:
            log.exception("llm failed")
            if not spoken:
                self.history.pop()
            await self._deliver([Result(f"My brain hiccupped: {str(e)[:60]}.", ok=False)])
            return
        finally:
            filler and filler.cancel()
        if buf.strip():
            spoken.append(buf.strip())
            self._queue_speech(buf.strip())
        if tool_results:
            await self._deliver(tool_results)
            self.history.append({"role": "assistant", "content": " ".join(r.say for r in tool_results)})
            return
        reply = " ".join(spoken)
        self.history.append({"role": "assistant", "content": reply})
        await self._wait_speech()
        # Off by default: in an open office the follow-up window mostly captures other people talking
        if reply.rstrip().endswith("?") and self.cfg["listening"].get("follow_up_after_chat", False):
            self._follow()
        else:
            self._go_idle()

    # ------------------------------------------------------------------ speech
    def _queue_speech(self, sentence: str) -> None:
        self.ui.send({"type": "reply", "text": sentence, "append": True})
        if self._remote_said is not None:
            self._remote_said.append(sentence)
        self._speech_q.put_nowait(sentence)

    async def _speech_worker(self) -> None:
        while True:
            sentence = await self._speech_q.get()
            gen = self._speech_gen
            try:
                if self.speak_aloud:
                    audio = await asyncio.to_thread(self.tts.synth, sentence)
                    if gen != self._speech_gen:
                        continue  # interrupted while synthesising
                    if self.state not in ("idle",) or self.in_call:
                        self._set_state("speaking")
                    if self.mode == "off":
                        self.mode = "wake"  # "Hey Jarvis" cuts in while it talks
                    self.player.play(audio)
            except Exception:
                log.exception("tts failed")
            finally:
                self._speech_q.task_done()

    async def _wait_speech(self) -> None:
        # bounded: a stuck audio device must never freeze the conversation
        try:
            await asyncio.wait_for(self._speech_q.join(), timeout=45)
        except asyncio.TimeoutError:
            log.warning("speech queue did not drain; continuing")
        deadline = time.monotonic() + 60
        while self.player.busy and time.monotonic() < deadline:
            await asyncio.sleep(0.05)

    async def say(self, text: str, then_idle: bool = True, follow: bool = False) -> None:
        if not text:
            if then_idle:
                self._go_idle()
            return
        from .audio.tts import split_sentences

        self.ui.send({"type": "reply", "text": "", "append": False})
        for s in split_sentences(text):
            self._queue_speech(s)
        await self._wait_speech()
        if follow:
            self._follow()
        elif then_idle:
            self._go_idle()

    def _follow(self) -> None:
        if self.in_call:
            self._go_idle()
            return
        if not self.speak_aloud:  # typed conversation: wait for typing, don't open the mic
            self._set_state("waiting")
            return
        self.follow_up = True
        self._start_capture(self.cfg["listening"]["follow_up_seconds"])

    # ------------------------------------------------------------------ WhatsApp commands
    async def handle_remote(self, text: str, voice: bool, conf: float = 0.0) -> tuple[list[str], list[str]]:
        """One command from your phone. Runs exactly like a spoken or typed one (same commands, same
        confirmations), silently on the laptop. Returns (what Jarvis said, cards as text)."""
        from .services.whatsapp_remote import card_text

        async with self._remote_lock:
            for _ in range(120):  # a call on the laptop finishes first; the phone gets the next turn
                if not self.in_call and (self.task is None or self.task.done()):
                    break
                await asyncio.sleep(0.5)
            else:
                return [f"I'm busy on the laptop right now, {self.cfg['address_as']}. Please send that again "
                        "in a minute."], []
            self._remote_said, self._remote_cards = [], []
            self.speak_aloud = False
            self.ui.send({"type": "reply", "text": "", "append": False})
            if voice:
                self.ui.send({"type": "transcript", "text": f"📱 {text}", "final": True})
                self._heard_conf = conf
            self.task = asyncio.create_task(self.handle_text(text, typed=not voice))
            try:
                await self.task
            except asyncio.CancelledError:
                self._remote_said.append("That was interrupted on the laptop, sir.")
            finally:
                said, cards = self._remote_said, self._remote_cards
                self._remote_said, self._remote_cards = None, []
                self.speak_aloud = True
            return said, [card_text(c) for c in cards]

    # ------------------------------------------------------------------ email announcements
    async def _announce_mail(self, msgs: list[dict]) -> None:
        if self.state not in ("idle",) or self.muted:
            self.notices += msgs
            return
        for m in msgs[:3]:
            self.ui.send({"type": "notify", "kind": "email", "from": m["from_name"], "subject": m["subject"]})
        self.player.play(earcons.NOTIFY)
        if len(msgs) == 1:
            m = msgs[0]
            text = f"New email from {m['from_name']}: {m['subject']}."
        else:
            names = list(dict.fromkeys(m["from_name"] for m in msgs))
            text = f"{len(msgs)} new emails, from {', '.join(names[:3])}{' and others' if len(names) > 3 else ''}."
        self.pending = {"kind": "email_list", "messages": msgs}
        self.speak_aloud = True
        self._set_state("speaking", notify=True)
        await self.say(text, then_idle=True)

    # ------------------------------------------------------------------ UI messages
    async def on_ui(self, msg: dict) -> None:
        kind = msg.get("type")
        if self.state == "booting" and kind not in ("quit",):
            return
        if kind == "wake":
            self._wake("hotkey")
        elif kind == "end_call":
            if self.in_call:
                self._end_call("closed")
        elif kind == "text" and msg.get("text", "").strip():
            self._interrupt()
            self.follow_up = False
            if self.ducker:
                asyncio.get_running_loop().run_in_executor(None, self.ducker.duck)
            self.task = asyncio.create_task(self.handle_text(msg["text"].strip(), typed=True))
        elif kind == "stop":  # stop talking; in a call, go straight back to listening
            self._interrupt()
            self._go_idle()
        elif kind == "confirm":
            p = self.pending
            if p and p.get("kind") == "send_email" and msg.get("id") == p["draft_id"]:
                self.speak_aloud = True
                if msg.get("action") == "send":
                    self.task = asyncio.create_task(self._send_email(p, msg.get("edited")))
                else:
                    self._interrupt()
                    self.pending = None
                    self.task = asyncio.create_task(self.say(_unsent_line(p["draft_id"]), then_idle=True))
            elif p and p.get("kind") == "wa_send" and msg.get("id", "").startswith("wa-"):
                self._interrupt()
                if msg.get("action") == "send":
                    self.task = asyncio.create_task(self._whatsapp_send(p))
                else:
                    self.pending = None
                    await asyncio.to_thread(_quietly, "whatsapp.clear")
                    self.task = asyncio.create_task(self.say("Okay, not sent. I've cleared it.", then_idle=True))
            elif p and p.get("kind") == "claude_send" and msg.get("id", "").startswith("cc-"):
                self._interrupt()
                if msg.get("action") == "send":
                    task = ((msg.get("edited") or {}).get("text") or "").strip() or p["task"]
                    self.task = asyncio.create_task(self._launch_claude(p, task))
                else:
                    self.pending = None
                    self.task = asyncio.create_task(self.say("Okay, not sent.", then_idle=True))
            elif p and p.get("kind") == "need_address" and msg.get("action") == "send" and msg.get("edited"):
                addr = (msg["edited"].get("to") or "").strip()
                if "@" in addr:
                    self._interrupt()
                    self.task = asyncio.create_task(self._handle_pending(addr, addr))
        elif kind == "choose" and self.pending:
            self._interrupt()
            self.task = asyncio.create_task(self._choose(int(msg.get("index", 0)) + 1))
        elif kind == "dismiss":
            self.pending = None
        elif kind == "snapshot":  # debug: ask the overlay to save a PNG of itself to data/snapshot.png
            self.ui.send({"type": "snapshot"})
        elif kind == "mute":
            self.muted = bool(msg.get("value"))
            if self.muted:
                self._interrupt()
                self._idle()
            elif self.in_call:
                self._go_idle()
            self._status("Mic muted" if self.muted else "Ready", ready=True)
        elif kind == "quit":
            log.info("quit requested by UI")
            if self.ducker:
                self.ducker.restore()
            asyncio.get_running_loop().stop()


async def _deadline(stream, first: float, gap: float):
    """Pass a model's stream through, but raise TimeoutError if the first item takes longer than
    `first` seconds or any later one longer than `gap`. Closing the stream makes Ollama stop
    generating, so an abandoned answer doesn't keep the CPU busy for the next request."""
    wait = first
    try:
        while True:
            try:
                item = await asyncio.wait_for(stream.__anext__(), wait)
            except StopAsyncIteration:
                return
            wait = gap
            yield item
    finally:
        await stream.aclose()


def _quietly(what: str) -> None:
    """Best-effort clean-up that must never break the conversation."""
    try:
        if what == "whatsapp.clear":
            from .tools.whatsapp import clear_ready_message

            clear_ready_message()
    except Exception as e:
        log.info("%s skipped: %s", what, e)


def _unsent_line(draft_id: str) -> str:
    # app-password drafts live only in Jarvis until sent; OAuth drafts really are in Gmail's Drafts folder
    return "Okay, not sent." if str(draft_id).startswith("local-") else "Okay, I've kept it in your Gmail drafts."


_CMD_START = re.compile(r"\b(?:(?P<verb>open|launch|close|play|search)(?:s|es|ed|d)?|pause|resume|google|lock|"
                        r"mute|unmute|volume|what time|what's the time|check my|check (?:the )?e-?mails?)\b")


def _embedded_command(t: str) -> str | None:
    """A short request with up to three stray words before the command ("take this open whatsapp",
    "have this play dilruba on spotify"): the command part, or None."""
    words = t.split()
    if len(words) > 9:
        return None
    m = _CMD_START.search(t)
    if not m or m.start() == 0 or len(t[:m.start()].split()) > 3:
        return None
    # "there is closed spotify": the verb after stray words is the command, whatever ending it was heard with
    return (m.group("verb") + t[m.end():]) if m.group("verb") else t[m.start():]


def _project_words(text: str) -> str:
    """'C:\\Users\\me\\Desktop\\ocm _system' -> 'ocm system'; 'the P9-Construct one' -> 'the p9 construct one'."""
    name = re.split(r"[\\/]", text.rstrip("\\/"))[-1]
    return re.sub(r"\s+", " ", re.sub(r"[-_]+", " ", name.lower())).strip()


def _greeting_lines(sir: str) -> str:
    return (f"{_good_time_of_day()}, {sir}. Systems are online. "
            "Awaiting your command. What would you like me to do?")


def _good_time_of_day() -> str:
    hour = dt.datetime.now().hour
    return "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"


def _spoken_email(text: str) -> str | None:
    t = text.lower().strip()
    t = re.sub(r"\s+at\s+", "@", t)
    t = re.sub(r"\s+dot\s+", ".", t)
    t = re.sub(r"\s+underscore\s+", "_", t)
    t = t.replace(" ", "")
    m = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", t)
    return m.group(0) if m else None
