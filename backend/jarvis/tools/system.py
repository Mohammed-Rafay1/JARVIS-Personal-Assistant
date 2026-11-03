"""Time, date, screen lock and sleep."""
from __future__ import annotations

import asyncio
import ctypes
import datetime as dt

from .base import Result, tool


def _time() -> str:
    return dt.datetime.now().strftime("%I:%M %p").lstrip("0")


@tool("Tell the current time or date", what="time | date")
async def tell_time(what: str = "time") -> Result:
    now = dt.datetime.now()
    if what == "date":
        return Result(f"It's {now.strftime('%A, %d %B')}.".replace(" 0", " "))
    return Result(f"It's {_time()}.")


@tool("Calculate an arithmetic expression exactly, e.g. '18% of 4500' or '12 * 7'",
      expression="The arithmetic to work out")
async def calculate(expression: str) -> Result:
    from ..brain.calc import evaluate, spoken

    value = evaluate(expression)
    if value is None:
        return Result("I couldn't work that out.", ok=False)
    return Result(f"That's {spoken(value)}.")


@tool("Lock the computer screen")
async def lock_screen() -> Result:
    ctypes.windll.user32.LockWorkStation()
    return Result("Locking.", icon="system")


def _screen_off() -> None:
    # This laptop has Modern Standby only (no S3): Windows sleeps when the screen goes off, the same as
    # pressing the power button. SetSuspendState would hibernate it instead.
    user32 = ctypes.windll.user32
    user32.LockWorkStation()
    HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, OFF = 0xFFFF, 0x0112, 0xF170, 2
    user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, OFF)


@tool("Put the laptop to sleep (locks it and turns the screen off)")
async def sleep_pc() -> Result:
    # a few seconds' grace so the reply is spoken, or reaches WhatsApp, before the laptop sleeps
    asyncio.get_running_loop().call_later(4, _screen_off)
    return Result("Putting the laptop to sleep. While it sleeps I can't hear you or answer on WhatsApp; "
                  "wake it with the keyboard or power button.", icon="system")
