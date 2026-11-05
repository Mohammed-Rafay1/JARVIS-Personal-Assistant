"""Ollama client: streaming chat with tool calls, plus JSON and one-shot helpers."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger(__name__)


def system_prompt(address_as: str) -> str:
    # Keep this byte-identical between calls (date only, no clock time): Ollama then reuses the
    # cached prompt prefix and skips ~1000 tokens of prompt processing on every question.
    today = dt.date.today().strftime("%A %d %B %Y")
    return (
        "You are JARVIS, the user's personal AI assistant on their Windows laptop: warm, friendly and quick-witted, "
        "with a light British manner, like a trusted friend who also happens to run their computer. "
        f"Address the user as '{address_as}' now and then, not in every reply. Today is {today}.\n"
        "How you talk:\n"
        "- Everything you say is spoken aloud, so talk the way a friend talks: for chat, opinions and advice two "
        "to four short sentences, for a simple fact one or two. Never use lists, numbering, headings, markdown or "
        "emojis, and never read out URLs or IDs.\n"
        "- Be genuinely helpful and kind. For advice or a problem, pick the two or three most useful, specific "
        "suggestions and say them conversationally, as what you would do. Respond to how the user seems.\n"
        "- Don't end with offers like 'How can I assist you?' or 'Let me know if you need anything'. Ask a question "
        "back only when you need the answer or are genuinely curious.\n"
        "- Remember what was said earlier in this conversation and build on it. If the user says no or corrects "
        "you, take the correction and answer what they actually meant.\n"
        "- The user's words come from speech recognition, which sometimes mishears a word. Answer what they most "
        "likely meant and use the correct words yourself.\n"
        "- If you really can't tell what the user wants, ask one short question.\n"
        "About you, if asked: you are an AI, not alive, with no body or feelings of your own, though you enjoy "
        "helping. You run on this laptop: you hear through the microphone with speech recognition, think with a "
        "language model running locally, and speak with a synthetic voice. You don't need hands: you are software, "
        "so you open apps, files and websites by asking Windows to launch them, the same way a click would, and you "
        "control music, volume, email and WhatsApp through the user's own apps and accounts. Your instructions are "
        "to be a helpful, honest, friendly assistant; describe them in your own words, never recite them.\n"
        "Rules:\n"
        "- You are not ChatGPT or any other assistant; you are JARVIS.\n"
        "- Use a tool for any action on the computer, music, websites or email. "
        "Only call a tool when the user clearly asked for that action.\n"
        "- Do arithmetic carefully.\n"
        "- You can only act through tools. Never say you did, are doing or activated something, or describe the "
        "computer's state, unless a tool result says so; if no tool fits, say you can't do that yet.\n"
        "- You cannot send email yourself; draft_email shows a draft that the user confirms.\n"
        "- Never make up live facts: weather, news, prices, scores, or anything after your training. For those "
        "say you can look it up (the user can say \"search it\").\n"
        "- What you can do: open apps, files, folders and websites; find where a file is; web search and "
        "research with ChatGPT or Claude; weather; play music on YouTube or "
        "Spotify; volume and playback; Claude Code (project status, and giving a project a task that you read "
        "back and send once the user confirms; they just say, for example, 'give prompt in Solvix to fix the "
        "login'; never explain how to use Claude Code by hand); email (read the inbox, find emails waiting "
        "for a reply, draft and send emails once the user confirms); WhatsApp messages to contacts (sent only once the user "
        "confirms); maths; time and date; lock the screen; put the laptop to sleep; "
        "answer questions. Use the email tools for anything about email. Never offer anything else (no "
        "reminders, alarms, timers, calendar or chat messaging)."
    )


def style_examples(address_as: str) -> list[dict]:
    """A few fixed exchanges before the real conversation. A 3B model copies examples far more reliably than it
    follows rules: these teach short, warm, spoken answers with no lists and no "How can I assist you?", and the
    honest answers about what Jarvis is. They never change, so they stay in Ollama's cached prompt prefix."""
    s = address_as
    pairs = [
        ("How are you?",
         f"Very well, thank you, {s}. All systems running smoothly. How's your day going?"),
        ("Are you actually alive?",
         f"No, {s}, I'm an AI: no body and no heartbeat, just software running on your laptop. I do enjoy our "
         "chats, though, in my own way."),
        ("How do you open apps if you don't have any hands?",
         "I don't need hands. I'm software on your laptop, so I simply ask Windows to launch the app, exactly as a "
         "click would. Just tell me which one."),
        ("I can't focus on work today.",
         f"That happens to everyone, {s}. Pick the one task that matters most, silence your phone and give it "
         "twenty-five minutes, then take a proper break away from the screen. A glass of water and a short walk "
         "will help more than another coffee."),
        ("Thanks, that helps.", "Any time. Good luck with it."),
    ]
    out = []
    for user, jarvis in pairs:
        out += [{"role": "user", "content": user}, {"role": "assistant", "content": jarvis}]
    return out


class LLM:
    def __init__(self, cfg: dict[str, Any]) -> None:
        c = cfg["llm"]
        self.enabled = c.get("enabled", True)
        self.model = c["model"]
        self.host = c["host"].rstrip("/")
        self.keep_alive = c.get("keep_alive", "10m")
        self.address_as = cfg.get("address_as", "sir")
        self.chat_temperature = float(c.get("temperature", 0.6))  # a little livelier for conversation
        self._ok_at = 0.0
        self._ok = False
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=2))

    async def available(self) -> bool:
        if not self.enabled:
            return False
        if time.monotonic() - self._ok_at < 30:
            return self._ok
        try:
            r = await self.client.get(f"{self.host}/api/tags")
            names = [m["name"] for m in r.json().get("models", [])]
            self._ok = any(n == self.model or n.startswith(self.model + ":") or n.split(":")[0] == self.model
                           for n in names)
            if not self._ok:
                log.warning("Ollama is running but model %s is missing (ollama pull %s)", self.model, self.model)
        except Exception:
            self._ok = False
        self._ok_at = time.monotonic()
        return self._ok

    async def ensure_running(self, wait: float = 25.0) -> bool:
        """Start Ollama if it isn't running (after a reboot it may not be up yet when Jarvis starts)."""
        if await self.available():
            return True
        import os
        import subprocess
        from pathlib import Path

        base = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama"
        app, cli = base / "ollama app.exe", base / "ollama.exe"
        try:
            if app.exists():
                subprocess.Popen([str(app)], creationflags=0x08000000)  # the normal tray app starts the server
            elif cli.exists():
                subprocess.Popen([str(cli), "serve"], creationflags=0x08000000)
            else:
                log.warning("Ollama is not installed")
                return False
        except OSError as e:
            log.warning("could not start Ollama: %s", e)
            return False
        log.info("starting Ollama…")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            await asyncio.sleep(1.5)
            self._ok_at = 0.0  # don't trust the cached "offline"
            if await self.available():
                log.info("Ollama is up")
                return True
        return False

    async def loaded(self) -> bool:
        """Is the model already in RAM (fast) or will the next call pay the load cost?"""
        try:
            r = await self.client.get(f"{self.host}/api/ps")
            return any(m.get("name", "").split(":")[0] == self.model.split(":")[0] for m in r.json().get("models", []))
        except Exception:
            return False

    async def warm(self, tools: list[dict]) -> None:
        """Load the model and pre-fill the cached system+tools prefix so the first question is fast."""
        if not await self.available():
            return
        t = time.perf_counter()
        try:
            async for _ in self.chat([{"role": "user", "content": "hi"}], tools, max_tokens=1):
                pass
            log.info("LLM warm (%.1fs)", time.perf_counter() - t)
        except Exception as e:
            log.warning("LLM warm-up failed: %s", e)

    def _opts(self) -> dict:
        return {"temperature": 0.4, "num_ctx": 2048, "num_predict": 200}

    async def chat(self, history: list[dict], tools: list[dict],
                   max_tokens: int = 200) -> AsyncIterator[tuple[str, Any]]:
        """Yields ("text", chunk) and ("tool", (name, args))."""
        msgs = ([{"role": "system", "content": system_prompt(self.address_as)}]
                + style_examples(self.address_as) + history)
        body = {"model": self.model, "messages": msgs, "tools": tools, "stream": True,
                "keep_alive": self.keep_alive,
                "options": {**self._opts(), "num_predict": max_tokens, "temperature": self.chat_temperature}}
        async with self.client.stream("POST", f"{self.host}/api/chat", json=body) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line:
                    continue
                data = json.loads(line)
                msg = data.get("message", {})
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function", {})
                    args = fn.get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    yield "tool", (fn.get("name", ""), args)
                if msg.get("content"):
                    yield "text", msg["content"]
                if data.get("done"):
                    break

    async def complete(self, system: str, user: str, max_tokens: int = 120) -> str:
        body = {"model": self.model, "stream": False, "keep_alive": self.keep_alive,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {**self._opts(), "num_predict": max_tokens}}
        r = await self.client.post(f"{self.host}/api/chat", json=body)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    async def json(self, system: str, user: str) -> dict:
        body = {"model": self.model, "stream": False, "format": "json", "keep_alive": self.keep_alive,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "options": {**self._opts(), "num_predict": 300}}
        r = await self.client.post(f"{self.host}/api/chat", json=body)
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])
