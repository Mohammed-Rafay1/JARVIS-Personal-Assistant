"""Polls Gmail for new inbox mail and hands announcements to Jarvis."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Awaitable, Callable

from .presence import mic_in_use_by_others

log = logging.getLogger(__name__)


def _in_quiet_hours(window: list[str]) -> bool:
    if not window or len(window) != 2:
        return False
    now = dt.datetime.now().time()
    a, b = (dt.time.fromisoformat(x) for x in window)
    return (a <= now or now < b) if a > b else (a <= now < b)


class GmailWatcher:
    def __init__(self, gmail, cfg: dict, announce: Callable[[list[dict]], Awaitable[None]]) -> None:
        self.gmail = gmail
        self.cfg = cfg["email"]
        self.announce = announce
        self.held: list[dict] = []
        self.paused = False

    def _wanted(self, m: dict) -> bool:
        vip = [v.lower() for v in self.cfg.get("vip", [])]
        if m["from"].lower() in vip:
            return True
        if "UNREAD" not in m["labels"] and "INBOX" not in m["labels"]:
            return False
        if self.cfg.get("only_primary", True):
            noisy = {"CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL", "CATEGORY_UPDATES", "CATEGORY_FORUMS", "SPAM"}
            if noisy & set(m["labels"]):
                return False
        return True

    async def run(self) -> None:
        history = await asyncio.to_thread(self.gmail.history_id)
        log.info("gmail watcher started")
        while True:
            await asyncio.sleep(max(20, int(self.cfg.get("poll_seconds", 60))))
            try:
                new, history = await asyncio.to_thread(self.gmail.new_since, history)
            except Exception as e:
                log.warning("gmail poll failed: %s", e)
                if "historyId" in str(e) or "404" in str(e):
                    history = await asyncio.to_thread(self.gmail.history_id)
                continue
            self.held += [m for m in new if self._wanted(m)]
            if not self.held or self.paused or not self.cfg.get("announce", True):
                continue
            vip = [v.lower() for v in self.cfg.get("vip", [])]
            has_vip = any(m["from"].lower() in vip for m in self.held)
            if not has_vip and _in_quiet_hours(self.cfg.get("quiet_hours", [])):
                continue
            if self.cfg.get("hold_during_calls", True) and await asyncio.to_thread(mic_in_use_by_others):
                continue
            batch, self.held = self.held, []
            await self.announce(batch)
