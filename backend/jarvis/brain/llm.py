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
        "You are JARVIS, a calm, witty, concise British AI assistant living on the user's Windows laptop. "
        f"Address the user as '{address_as}' occasionally, not every time. Today is {today}.\n"
        "Rules:\n"
        "- Your replies are spoken aloud: one short sentence when possible, two at most. Give the answer "
        "directly, without showing working. Plain words, no lists, no markdown, never read out URLs or IDs.\n"
        "- Use a tool for any action on the computer, music, websites or email. "
        "Only call a tool when the user clearly asked for that action.\n"
        "- For questions and conversation just answer briefly; do arithmetic carefully.\n"
        "- If a request is ambiguous, ask one short question.\n"
        "- You cannot send email yourself; draft_email shows a draft that the user confirms.\n"
        "- What you can do: open apps, files, folders and websites; web search; play music on YouTube or "
        "Spotify; volume and playback; Claude Code project status; email (read the inbox, find emails waiting "
        "for a reply, draft and send emails once the user confirms); WhatsApp messages to contacts (sent only once the user "
        "confirms); maths; time and date; lock the screen; "
        "answer questions. Use the email tools for anything about email. Never offer anything else (no "
        "reminders, alarms, timers, calendar, weather or chat messaging)."
    )


class LLM:
    def __init__(self, cfg: dict[str, Any]) -> None:
        c = cfg["llm"]
        self.enabled = c.get("enabled", True)
        self.model = c["model"]
        self.host = c["host"].rstrip("/")
        self.keep_alive = c.get("keep_alive", "10m")
        self.address_as = cfg.get("address_as", "sir")
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
        msgs = [{"role": "system", "content": system_prompt(self.address_as)}] + history
        body = {"model": self.model, "messages": msgs, "tools": tools, "stream": True,
                "keep_alive": self.keep_alive, "options": {**self._opts(), "num_predict": max_tokens}}
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
