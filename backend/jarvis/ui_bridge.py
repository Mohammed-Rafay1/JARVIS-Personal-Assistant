"""Local WebSocket between the brain (this process) and the overlay window."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from websockets.asyncio.server import ServerConnection, serve

log = logging.getLogger(__name__)


class UIBridge:
    def __init__(self, token: str, on_message: Callable[[dict], Awaitable[None]]) -> None:
        self.token = token
        self.on_message = on_message
        self.clients: set[ServerConnection] = set()
        self.port = 0
        self._last: dict[str, dict] = {}  # replayed to a UI that (re)connects
        self.loop: asyncio.AbstractEventLoop | None = None

    async def start(self, port: int = 0) -> int:
        self.loop = asyncio.get_running_loop()
        self._server = await serve(self._handler, "127.0.0.1", port, max_size=2**20)
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("UI bridge on ws://127.0.0.1:%d", self.port)
        return self.port

    async def _handler(self, ws: ServerConnection) -> None:
        q = parse_qs(urlparse(ws.request.path).query)
        if q.get("token", [""])[0] != self.token:
            await ws.close(4001, "bad token")
            return
        self.clients.add(ws)
        log.info("overlay connected")
        for msg in self._last.values():
            await ws.send(json.dumps(msg))
        try:
            async for raw in ws:
                try:
                    await self.on_message(json.loads(raw))
                except Exception:
                    log.exception("bad UI message: %s", raw[:200])
        except Exception:
            pass
        finally:
            self.clients.discard(ws)

    def send(self, msg: dict[str, Any]) -> None:
        """Thread-safe, fire-and-forget."""
        if msg.get("type") in ("state", "status"):
            self._last[msg["type"]] = msg
        if self.loop is None:
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self._broadcast(msg)
        else:
            self.loop.call_soon_threadsafe(self._broadcast, msg)

    def _broadcast(self, msg: dict) -> None:
        data = json.dumps(msg)
        for ws in list(self.clients):
            asyncio.ensure_future(self._safe_send(ws, data))

    @staticmethod
    async def _safe_send(ws: ServerConnection, data: str) -> None:
        try:
            await ws.send(data)
        except Exception:
            pass
