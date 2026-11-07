"""Media keys and system volume. Works for YouTube, Spotify and any player."""
from __future__ import annotations

import ctypes

from ..config import WINDOWS
from .base import Result, tool

VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2, "mute": 0xAD}
KEYUP = 0x0002


def press(vk: int) -> None:
    if not WINDOWS:
        from ..linux import media_key

        return media_key(vk)
    u = ctypes.windll.user32
    u.keybd_event(vk, 0, 1, 0)          # KEYEVENTF_EXTENDEDKEY
    u.keybd_event(vk, 0, 1 | KEYUP, 0)


def _endpoint():
    if not WINDOWS:
        from ..linux import Endpoint

        return Endpoint()
    from pycaw.pycaw import AudioUtilities

    dev = AudioUtilities.GetSpeakers()
    if hasattr(dev, "EndpointVolume"):
        return dev.EndpointVolume
    from ctypes import POINTER, cast

    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import IAudioEndpointVolume

    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume() -> int:
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def set_volume_pct(pct: int) -> int:
    pct = max(0, min(100, pct))
    ep = _endpoint()
    ep.SetMute(0, None)
    ep.SetMasterVolumeLevelScalar(pct / 100, None)
    return pct


@tool("Control media playback: play_pause, next, previous", action="play_pause | next | previous")
async def media_control(action: str) -> Result:
    action = {"pause": "play_pause", "play": "play_pause", "resume": "play_pause", "skip": "next",
              "back": "previous"}.get(action, action)
    if action not in VK:
        return Result("I can play, pause, skip or go back.", ok=False)
    press(VK[action])
    say = {"play_pause": "Done.", "next": "Skipping.", "previous": "Going back.", "stop": "Stopped."}[action]
    return Result(say, icon="system")


@tool("Set, raise, lower or mute the system volume",
      action="set | up | down | mute | unmute", level="Percent for 'set', e.g. '40'")
async def set_volume(action: str, level: str = "") -> Result:
    ep = _endpoint()
    if action == "mute":
        ep.SetMute(1, None)
        return Result("Muted.", icon="system")
    if action == "unmute":
        ep.SetMute(0, None)
        return Result(f"Unmuted. Volume is {get_volume()} percent.", icon="system")
    cur = get_volume()
    if action == "up":
        new = set_volume_pct(cur + (int(level) if level.isdigit() else 10))
    elif action == "down":
        new = set_volume_pct(cur - (int(level) if level.isdigit() else 10))
    else:
        digits = "".join(c for c in level if c.isdigit())
        if not digits:
            return Result(f"Volume is {cur} percent.", icon="system")
        new = set_volume_pct(int(digits))
    return Result(f"Volume {new} percent.", icon="system",
                  card={"kind": "meter", "label": "Volume", "value": new})
