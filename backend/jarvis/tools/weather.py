"""Weather from Open-Meteo (free, no account or key). The language model once made up "22°C in Lahore";
this reads the real forecast instead."""
from __future__ import annotations

import logging

import httpx

from . import CTX
from .base import Result, tool

log = logging.getLogger(__name__)

# WMO weather codes -> plain words
_CODES = {0: "clear skies", 1: "mostly clear skies", 2: "partly cloudy skies", 3: "overcast skies", 45: "fog",
          48: "freezing fog", 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle",
          57: "freezing drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain",
          67: "freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
          80: "light showers", 81: "showers", 82: "heavy showers", 85: "snow showers", 86: "heavy snow showers",
          95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def describe(code) -> str:
    try:
        return _CODES.get(int(code), "mixed weather")
    except (TypeError, ValueError):
        return "mixed weather"


def spoken(city: str, when: str, data: dict) -> str:
    cur, day = data.get("current", {}), data.get("daily", {})
    i = 1 if when == "tomorrow" else 0
    try:
        hi, lo = round(day["temperature_2m_max"][i]), round(day["temperature_2m_min"][i])
        rain = day.get("precipitation_probability_max", [None, None])[i]
        sky = describe(day["weather_code"][i])
    except (KeyError, IndexError, TypeError):
        return f"I couldn't read the forecast for {city}."
    rain_part = f" Chance of rain {rain} percent." if rain is not None else ""
    if when == "tomorrow":
        return f"Tomorrow in {city}: {sky}, a high of {hi} and a low of {lo} degrees.{rain_part}"
    now = ""
    if "temperature_2m" in cur:
        now = f"It's {round(cur['temperature_2m'])} degrees in {city} right now with {describe(cur.get('weather_code'))}"
        if "apparent_temperature" in cur and abs(cur["apparent_temperature"] - cur["temperature_2m"]) >= 3:
            now += f", feeling like {round(cur['apparent_temperature'])}"
        now += ". "
    return f"{now}Today's high is {hi} and the low {lo}.{rain_part}"


async def fetch(city: str) -> tuple[str, dict] | None:
    async with httpx.AsyncClient(timeout=10) as c:
        g = await c.get("https://geocoding-api.open-meteo.com/v1/search",
                        params={"name": city, "count": 1, "language": "en", "format": "json"})
        hits = (g.json() or {}).get("results") or []
        if not hits:
            return None
        place = hits[0]
        f = await c.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,weather_code,relative_humidity_2m,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code"})
        return place.get("name", city), f.json()


@tool("Current weather and today's or tomorrow's forecast for a city",
      city="City name (empty = the user's home city)", when="today or tomorrow")
async def weather(city: str = "", when: str = "today") -> Result:
    city = (city or CTX.cfg.get("weather", {}).get("city", "Lahore")).strip()
    when = "tomorrow" if "tomorrow" in (when or "").lower() else "today"
    try:
        got = await fetch(city)
    except Exception as e:
        log.warning("weather failed: %s", e.__class__.__name__)
        return Result("I couldn't reach the weather service just now.", ok=False, icon="web")
    if got is None:
        return Result(f"I couldn't find a place called {city}.", ok=False, icon="web")
    name, data = got
    say = spoken(name, when, data)
    log.info("weather %s %s: %s", name, when, say)
    cur = data.get("current", {})
    card = {"kind": "weather", "city": name, "when": when, "summary": say,
            "temp": cur.get("temperature_2m"), "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m")}
    return Result(say, icon="web", card=card)
