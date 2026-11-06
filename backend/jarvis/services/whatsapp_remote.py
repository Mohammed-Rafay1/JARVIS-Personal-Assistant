"""Commands from your phone over WhatsApp: text or voice notes in, the same actions on the laptop, replies out.

The Node bridge (whatsapp-bridge/, Baileys) is a linked WhatsApp device. It passes on only your own
messages: the "Message yourself" chat of the linked account, plus numbers listed in
`whatsapp_remote.allowed`. Each message runs through the same command engine as speech, so confirmations
("send it?") work exactly as they do by voice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..config import DATA, ROOT

log = logging.getLogger(__name__)

BRIDGE = ROOT / "whatsapp-bridge" / "index.mjs"
AUTH = DATA / "whatsapp-auth"
MEDIA = DATA / "whatsapp-media"
QR_PNG = DATA / "whatsapp-qr.png"
LINK_PAGE = DATA / "whatsapp-link.html"


def decode_voice_note(path: str | Path) -> np.ndarray:
    """A WhatsApp voice note (Ogg/Opus) as 16 kHz mono float32, the format the recogniser takes."""
    import av

    rs = av.AudioResampler(format="s16", layout="mono", rate=16000)
    out: list[np.ndarray] = []
    with av.open(str(path)) as c:
        for frame in c.decode(audio=0):
            out += [f.to_ndarray().reshape(-1) for f in rs.resample(frame)]
        out += [f.to_ndarray().reshape(-1) for f in rs.resample(None)]
    return np.concatenate(out).astype(np.float32) / 32768.0 if out else np.zeros(0, dtype=np.float32)


def encode_voice_note(audio: np.ndarray, sr: int, path: str | Path) -> float:
    """Jarvis's voice as an Ogg/Opus voice note. Returns its length in seconds."""
    import av

    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).reshape(1, -1)
    with av.open(str(path), "w", format="ogg") as c:
        s = c.add_stream("libopus", rate=sr)
        s.layout = "mono"
        frame = av.AudioFrame.from_ndarray(pcm, format="s16", layout="mono")
        frame.sample_rate = sr
        for p in s.encode(frame):
            c.mux(p)
        for p in s.encode(None):
            c.mux(p)
    return pcm.size / sr


def chat_layout(text: str, limit: int = 3000) -> str:
    """An answer laid out for a chat: paragraphs with a blank line between them, bullets kept together,
    titles in bold ("1. Who was...?", "A. The Holocaust", a short opening line), cut at a whole line (not
    mid-word) with "…" when it's long."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    out: list[str] = []
    size = 0
    for i, ln in enumerate(lines):
        bullet = ln.startswith(("•", "- "))
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        short = not bullet and len(ln) <= 80
        numbered = short and re.match(r"^(?:\d{1,2}|[A-Z])[.)]\s+\S", ln) and not re.search(r"[.,;]$", ln)
        title = numbered or (short and not re.search(r"[.!?,;:]$", ln)
                             and (i == 0 or not nxt or len(nxt) > len(ln) or nxt.startswith("•")))
        piece = f"*{ln}*" if title else ln
        sep = "\n" if (bullet and out and out[-1].lstrip().startswith(("•", "- "))) else "\n\n"
        if size + len(sep) + len(piece) > limit and out:
            out.append("\n\n…")
            break
        out.append((sep if out else "") + piece)
        size += len(sep) + len(piece)
    return "".join(out)


def card_text(card: dict[str, Any]) -> str:
    """The on-screen card as chat text, so lists and drafts can be answered from the phone."""
    kind = card.get("kind")
    if kind == "choices":
        subs = card.get("subs") or []
        rows = [f"{i}. {item}" + (f" ({subs[i - 1]})" if i - 1 < len(subs) and subs[i - 1] else "")
                for i, item in enumerate(card.get("items", []), 1)]
        return f"*{card.get('title', 'Options')}*\n" + "\n".join(rows) + "\nReply with a number."
    if kind == "email_list":
        rows = [f"{i}. {m.get('from', '')}: {m.get('subject', '')}" for i, m in enumerate(card.get("items", []), 1)]
        return f"*{card.get('title', 'Emails')}*\n" + "\n".join(rows)
    if kind == "email_draft":
        return (f"*Draft email*\nTo: {card.get('to_name') or card.get('to', '')} <{card.get('to', '')}>\n"
                f"Subject: {card.get('subject', '')}\n\n{card.get('body', '')}\n\nReply *send it* or *no*.")
    if kind == "email":
        return f"*{card.get('from', '')}: {card.get('subject', '')}*\n\n{card.get('body', '')}"
    if kind == "whatsapp_draft":
        return f"*WhatsApp to {card.get('chat', '')}*\n{card.get('text', '')}\n\nReply *send it* or *no*."
    if kind == "project":
        return f"*{card.get('name', '')}* ({card.get('ago', '')})\n{card.get('summary', '')}"
    if kind == "capabilities":
        return "\n".join(f"*{g['title']}*: " + "; ".join(g["items"]) for g in card.get("groups", []))
    if kind == "research":
        parts = [f"*{a['service']}*\n{chat_layout(a['text'])}" if a.get("text")
                 else f"*{a['service']}*: " + {"not_installed": "the app didn't open on the laptop.",
                                               "unread": "the answer is in the app on the laptop."}.get(
                     a.get("problem"), "not asked (I couldn't type into it safely).")
                 for a in card.get("answers", [])]
        return "\n\n".join(parts)
    if kind == "prompt_draft":
        return f"*Claude Code task ({card.get('project', '')})*\n{card.get('text') or card.get('task', '')}"
    return ""


class WhatsAppRemote:
    def __init__(self, cfg: dict[str, Any], jarvis) -> None:
        self.cfg = cfg.get("whatsapp_remote", {})
        self.jarvis = jarvis
        self.proc: asyncio.subprocess.Process | None = None
        self.queue: asyncio.Queue[dict] = asyncio.Queue()
        self.linked = False
        self.me = ""            # this account's number: its "Message yourself" chat is where reports go
        self.last_chat = ""     # the chat your last command came from

    # ------------------------------------------------------------------ bridge process
    async def run(self) -> None:
        node = shutil.which("node")
        if not node or not (BRIDGE.parent / "node_modules").exists():
            log.warning("WhatsApp remote: Node or the bridge's packages are missing (run npm install in "
                        "whatsapp-bridge)")
            return
        asyncio.create_task(self._worker())
        delay = 5.0
        while True:
            started = time.monotonic()
            code = await self._run_once(node)
            if code == "logged_out":
                log.warning("WhatsApp remote: this device was unlinked. Run link_whatsapp.bat to link again.")
                self.jarvis.ui.send({"type": "status", "text": "WhatsApp unlinked"})
                return
            delay = 5.0 if time.monotonic() - started > 300 else min(delay * 2, 300)
            log.warning("WhatsApp bridge stopped (%s); restarting in %.0fs", code, delay)
            await asyncio.sleep(delay)

    async def _run_once(self, node: str) -> str:
        env = dict(os.environ, JARVIS_WA_CONFIG=json.dumps({
            "authDir": str(AUTH), "mediaDir": str(MEDIA), "qrPath": str(QR_PNG),
            "allowed": [str(n) for n in self.cfg.get("allowed", [])],
            "selfChat": bool(self.cfg.get("self_chat", True)), "phone": str(self.cfg.get("phone", "") or ""),
        }))
        self.proc = await asyncio.create_subprocess_exec(
            node, str(BRIDGE), cwd=str(BRIDGE.parent), env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=0x08000000)
        asyncio.create_task(self._pipe_stderr(self.proc))
        logged_out = False
        async for raw in self.proc.stdout:
            try:
                ev = json.loads(raw)
            except ValueError:
                continue
            logged_out |= ev.get("type") == "logged_out"
            await self._on_event(ev)
        code = await self.proc.wait()
        self.linked = False
        return "logged_out" if logged_out else f"exit {code}"

    @staticmethod
    async def _pipe_stderr(proc) -> None:
        async for line in proc.stderr:
            log.info("bridge: %s", line.decode("utf-8", "replace").rstrip())

    def send(self, cmd: dict) -> None:
        if self.proc and self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.write((json.dumps(cmd) + "\n").encode())

    def notify(self, text: str) -> bool:
        """A message Jarvis starts itself (a finished Claude Code task): to the chat you last used, else your
        own "Message yourself" chat."""
        chat = self.last_chat or self_chat(self.me)
        if not chat or not self.linked:
            log.info("WhatsApp report not sent: no chat yet")
            return False
        self.send({"type": "send", "chat": chat, "text": text})
        log.info("WhatsApp report: %r", text[:120])
        return True

    async def _on_event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "qr":
            log.info("WhatsApp remote: scan the QR code to link (WhatsApp > Linked devices > Link a device)")
            self._show_link_page()
        elif kind == "pairing_code":
            code = ev["code"]
            log.info("WhatsApp remote: pairing code %s", code)
            self.jarvis.ui.send({"type": "status", "text": f"WhatsApp pairing code: {code}"})
            self._show_link_page(code)
        elif kind == "ready":
            self.linked = True
            self.me = str(ev.get("me", "") or "")
            log.info("WhatsApp remote linked (%s)", _mask(ev.get("me", "")))
            self.jarvis.ui.send({"type": "status", "text": "WhatsApp remote linked", "ready": True})
            LINK_PAGE.unlink(missing_ok=True)
        elif kind == "message":
            await self.queue.put(ev)

    def _show_link_page(self, code: str = "") -> None:
        """A page that keeps showing the current QR code (it changes every ~20 s) until you've linked."""
        first = not LINK_PAGE.exists()
        body = (f"<p class=code>{code}</p><p>WhatsApp &gt; Linked devices &gt; Link a device &gt; "
                "<b>Link with phone number instead</b>, then enter this code.</p>" if code else
                f"<img id=q src='{QR_PNG.name}'><p>WhatsApp &gt; Settings &gt; Linked devices &gt; "
                "<b>Link a device</b>, then scan this code.</p>")
        LINK_PAGE.write_text(
            "<!doctype html><meta charset=utf-8><title>Link Jarvis to WhatsApp</title><style>"
            "body{background:#0b1220;color:#dbe7ff;font:16px system-ui;text-align:center;padding:40px}"
            "img{background:#fff;padding:12px;border-radius:12px;width:360px}"
            ".code{font-size:42px;letter-spacing:6px;font-weight:700}</style>"
            f"<h2>Link Jarvis to WhatsApp</h2>{body}"
            "<p style='opacity:.6'>Use the WhatsApp account you want Jarvis on. This page can be closed once "
            "Jarvis says it is linked.</p>"
            "<script>setInterval(()=>{const q=document.getElementById('q');"
            "if(q)q.src='" + QR_PNG.name + "?'+Date.now()},3000)</script>", encoding="utf-8")
        if first:
            os.startfile(str(LINK_PAGE))  # noqa: S606 - a local page we just wrote

    # ------------------------------------------------------------------ messages
    async def _worker(self) -> None:
        while True:
            ev = await self.queue.get()
            try:
                await self._handle(ev)
            except Exception:
                log.exception("WhatsApp command failed")
                self.send({"type": "send", "chat": ev["chat"], "text": "Something went wrong on the laptop, sir. "
                                                                      "Please try again."})

    async def _handle(self, ev: dict) -> None:
        chat = ev["chat"]
        self.last_chat = chat
        self.send({"type": "presence", "chat": chat, "state": "composing"})
        voice = "voice" in ev
        if ev.get("error"):
            self.send({"type": "send", "chat": chat, "text": "I couldn't download that voice note, sir. "
                                                             "Please send it again."})
            return
        if voice:
            text, conf = await self._transcribe(ev["voice"])
            if not text:
                self.send({"type": "send", "chat": chat, "text": "Sorry, sir, I couldn't make out that voice note. "
                                                                 "Please try again, or type it."})
                return
        else:
            text, conf = ev["text"], 0.0
        log.info("WhatsApp %s: %r", "voice note" if voice else "text", text)
        from ..brain.intents import normalize

        if not normalize(text):  # just "Hey Jarvis"
            sir = self.jarvis.cfg.get("address_as", "sir")
            said, cards = [f"At your service, {sir}. Tell me what you need, by text or voice note."], []
        else:
            said, cards = await self.jarvis.handle_remote(
                text, voice=voice, conf=conf,
                progress=lambda note: self.send({"type": "send", "chat": chat, "text": note}))
        reply = " ".join(said).strip() or "Done, sir."
        parts = ([f"🎙️ _{text}_"] if voice else []) + [reply] + [c for c in cards if c]
        self.send({"type": "send", "chat": chat, "text": "\n\n".join(parts)})
        self.send({"type": "presence", "chat": chat, "state": "paused"})
        log.info("WhatsApp reply: %r", reply[:120])
        if voice and self.cfg.get("voice_replies", True) and said:
            await self._send_voice(chat, reply)

    async def _transcribe(self, path: str) -> tuple[str, float]:
        stt = self.jarvis.stt
        try:
            audio = await asyncio.to_thread(decode_voice_note, path)
            # the same recogniser as the microphone; one decode at a time, so a live call isn't slowed down twice
            # accuracy matters more than speed here: re-check notes up to 30 s with the accurate model
            text = await asyncio.to_thread(stt.transcribe, audio[: 16000 * 60], None, None, 30.0)
            return text, stt.last_conf
        finally:
            Path(path).unlink(missing_ok=True)  # voice notes are not kept

    async def _send_voice(self, chat: str, text: str) -> None:
        from ..audio.tts import SAMPLE_RATE

        tts = self.jarvis.tts
        audio = await asyncio.to_thread(tts.synth, text[:900])
        if audio.size == 0:
            return
        MEDIA.mkdir(parents=True, exist_ok=True)
        path = MEDIA / f"reply-{int(time.time() * 1000)}.ogg"
        seconds = await asyncio.to_thread(encode_voice_note, audio, SAMPLE_RATE, path)
        self.send({"type": "send_voice", "chat": chat, "path": str(path), "seconds": seconds})


def self_chat(me: str) -> str:
    """'923001234567:12@s.whatsapp.net' (a linked device's id) -> '923001234567@s.whatsapp.net'."""
    num = me.split("@")[0].split(":")[0]
    return f"{num}@s.whatsapp.net" if num.isdigit() else ""


def _mask(number: str) -> str:
    return number[:4] + "…" + number[-2:] if len(number) > 6 else number
