"""Research on the web: a cleaner search query, the Google results opened for you, and a short spoken answer.

Google blocks programs from reading its results page, so the answer is read from DuckDuckGo's plain
results page (titles and snippets of the top results), then summarised by the local model.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from urllib.parse import quote_plus

import httpx

from . import CTX
from .base import Result, tool
from .browser import open_url

log = logging.getLogger(__name__)

_FILLER = re.compile(r"^(?:about|on|into|regarding|for|the topic of|some information (?:on|about)|information (?:on|about)|"
                     r"details (?:on|of|about)|everything about|more about|the latest on)\s+", re.I)


def search_query(query: str) -> str:
    """Spoken words -> a search query: "about the history of lahore please" -> "history of lahore"."""
    q = re.sub(r"\s+", " ", query).strip().rstrip(".?!")
    for _ in range(3):
        q = _FILLER.sub("", q)
    q = re.sub(r"^(?:the|a|an)\s+(?=\w+\s+\w)", "", q, flags=re.I)        # "the history of x" -> "history of x"
    q = re.sub(r"\s+(?:please|for me|now|and tell me.*|and summari[sz]e.*)$", "", q, flags=re.I)
    return q.strip() or query.strip()


def parse_results(page: str, limit: int = 5) -> list[tuple[str, str]]:
    """(title, snippet) of the top results on DuckDuckGo's plain HTML page."""
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)

    def text(s: str) -> str:
        return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()
    return [(text(a), text(b)) for a, b in zip(titles, snippets) if text(b)][:limit]


async def fetch_results(query: str) -> list[tuple[str, str]]:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            r = await c.post("https://html.duckduckgo.com/html/", data={"q": query},
                             headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Jarvis/1.0"})
            return parse_results(r.text) if r.status_code == 200 else []
    except Exception as e:
        log.warning("web results failed: %s", e.__class__.__name__)
        return []


def speakable(snippet: str) -> str:
    """A result snippet fit to read aloud: no brackets of other scripts or romanisations, no '...', two
    sentences at most. ("Lahore (Punjabi: ... romanized: ...) refers to..." -> "Lahore refers to...")"""
    group = r"\s*\((?:[^()]|\([^()]*\))*\)"  # brackets, one level of nesting: "(Punjabi: x (Shahmukhi); ...)"
    s = re.sub(group, lambda m: "" if re.search(r"[^\x00-\x7f]|romani[sz]ed|pronounced|listen", m.group(0))
               else m.group(0), snippet)
    s = re.sub(r"[^\x00-\x7f‘’“”]+", " ", s)
    s = re.sub(r"\s*(?:\.\.\.|…)\s*$", ".", s.strip())
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", s).strip())
    return " ".join(sentences[:2]).strip()


async def answer_from(query: str, results: list[tuple[str, str]]) -> str:
    if not results:
        return ""
    if CTX.llm and await CTX.llm.available():
        # the top three, trimmed: five full snippets took the local model 36 s to read on this laptop
        body = "\n".join(f"- {t}: {speakable(s)[:220]}" for t, s in results[:3])
        try:
            text = await asyncio.wait_for(CTX.llm.complete(
                "Answer the user's search from these web results, to be read aloud: two or three short plain "
                "sentences, only facts the results support, no lists, no markdown, no URLs.",
                f"Search: {query}\n\nResults:\n{body}", max_tokens=150), timeout=30)
            if text:
                return re.sub(r"\s+", " ", re.sub(r"[*#`]", "", text)).strip()
        except Exception as e:
            log.warning("web summary failed: %s", e.__class__.__name__)
    return next((s for s in (speakable(r[1]) for r in results) if len(s) > 40), speakable(results[0][1]))


@tool("Research something on the web (Google): open the results and summarise what they say",
      query="What to research", browser="Optional: chrome, edge, firefox or brave")
async def web_research(query: str, browser: str = "") -> Result:
    q = search_query(query)
    log.info("web research: %r -> %r", query, q)
    open_url(f"https://www.google.com/search?q={quote_plus(q)}", browser)
    results = await fetch_results(q)
    summary = await answer_from(q, results)
    card = {"kind": "research", "query": q, "summary": summary,
            "answers": [{"service": "Top web results", "problem": "" if results else "unread",
                         "text": "\n".join(f"{t}: {s}" for t, s in results)[:1500]}]}
    if not summary:
        return Result(f"The Google results for {q} are open, but I couldn't read them back.", ok=False,
                      icon="web", card=card)
    return Result(f"{summary} The Google results for {q} are open in your browser.", icon="web", card=card)
