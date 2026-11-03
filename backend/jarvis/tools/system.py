"""Time, date and screen lock."""
from __future__ import annotations

import ctypes
import datetime as dt

from ..config import WINDOWS
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
    if not WINDOWS:
        from ..linux import lock_screen as lock

        lock()
        return Result("Locking.", icon="system")
    ctypes.windll.user32.LockWorkStation()
    return Result("Locking.", icon="system")
