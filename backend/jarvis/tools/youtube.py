"""Play the best YouTube match in the default browser. No API key needed."""
from __future__ import annotations

import asyncio
import logging

from .base import Result, tool

log = logging.getLogger(__name__)


def search(query: str) -> dict | None:
    from yt_dlp import YoutubeDL

    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch3:{query}", download=False)
    entries = [e for e in (info or {}).get("entries", []) if e and e.get("id")]
    # skip shorts and live streams when a normal video is available
    for e in entries:
        if (e.get("duration") or 0) >= 60 and "/shorts/" not in (e.get("url") or ""):
            return e
    return entries[0] if entries else None


@tool("Play a video or song on YouTube", query="What to play, e.g. 'Tum Hi Ho Arijit Singh'",
      browser="Optional: chrome, edge, firefox or brave")
async def play_youtube(query: str, browser: str = "") -> Result:
    from .browser import open_url

    try:
        video = await asyncio.wait_for(asyncio.to_thread(search, query), timeout=12)
    except Exception as e:
        log.warning("youtube search failed: %s", e)
        video = None
    if video is None:
        from urllib.parse import quote_plus
        open_url(f"https://www.youtube.com/results?search_query={quote_plus(query)}", browser)
        return Result(f"Here's YouTube's results for {query}.", icon="youtube")
    open_url(f"https://www.youtube.com/watch?v={video['id']}", browser)
    title = video.get("title") or query
    return Result(f"Playing {title}.", icon="youtube",
                  card={"kind": "media", "service": "youtube", "title": title,
                        "subtitle": video.get("channel") or video.get("uploader") or ""})
