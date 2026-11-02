"""Websites and web search, in the default browser or the one you name ("on Chrome")."""
from __future__ import annotations

import logging
import os
import re
import webbrowser
from urllib.parse import quote_plus

from rapidfuzz import fuzz, process

from . import CTX
from .base import Result, tool

log = logging.getLogger(__name__)
# Executable names registered under Windows "App Paths", so ShellExecute finds them without a full path
_BROWSER_EXE = {"chrome": "chrome.exe", "google chrome": "chrome.exe", "edge": "msedge.exe",
                "microsoft edge": "msedge.exe", "firefox": "firefox.exe", "brave": "brave.exe"}


def open_url(url: str, browser: str = "") -> None:
    exe = _BROWSER_EXE.get(browser.lower().strip())
    if exe:
        try:
            os.startfile(exe, arguments=f'"{url}"')
            return
        except OSError as e:
            log.warning("%s not available (%s); using the default browser", exe, e)
    webbrowser.open(url)


_DOMAIN = re.compile(r"^([a-z0-9-]+\s?(dot|\.)\s?)+(com|org|net|io|ai|dev|pk|co|uk|gov|edu|app)(\.[a-z]{2})?$")


def resolve_site(name: str) -> str | None:
    """'github' -> https://github.com, 'bbc dot co dot uk' -> https://bbc.co.uk."""
    n = name.lower().strip().removesuffix(" website").removesuffix(" site").strip()
    sites = {k.lower(): v for k, v in CTX.cfg.get("websites", {}).items()}
    if n in sites:
        return sites[n]
    if _DOMAIN.match(n):
        host = re.sub(r"\s?dot\s?", ".", n).replace(" ", "")
        return f"https://{host}"
    if n.startswith(("http://", "https://")):
        return n
    hit = process.extractOne(n, list(sites), scorer=fuzz.ratio, score_cutoff=88)
    return sites[hit[0]] if hit else None


@tool("Open a website by name or address, e.g. github, gmail, bbc.co.uk",
      site="Website name or domain", browser="Optional: chrome, edge, firefox or brave")
async def open_website(site: str, browser: str = "") -> Result:
    url = resolve_site(site)
    if url is None:
        url = f"https://www.google.com/search?q={quote_plus(site)}&btnI=1"  # "I'm feeling lucky"
    open_url(url, browser)
    label = re.sub(r"^https?://(www\.)?", "", url).split("/")[0] if "google.com/search" not in url else site
    return Result(f"Opening {label}.", icon="web")


@tool("Search the web with Google", query="What to search for", browser="Optional: chrome, edge, firefox or brave")
async def web_search(query: str, browser: str = "") -> Result:
    open_url(f"https://www.google.com/search?q={quote_plus(query)}", browser)
    return Result(f"Here are the results for {query}.", icon="web")
