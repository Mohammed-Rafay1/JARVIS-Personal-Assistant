import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from jarvis.app import _deadline  # noqa: E402


async def _stream(delays, log):
    try:
        for d in delays:
            await asyncio.sleep(d)
            yield ("text", f"after {d}")
    finally:
        log.append("closed")


def _collect(delays, first, gap):
    log, got = [], []

    async def run():
        async for item in _deadline(_stream(delays, log), first, gap):
            got.append(item)
    return run, got, log


def test_stream_within_limits_passes_through():
    run, got, log = _collect([0.01, 0.01], first=1, gap=1)
    asyncio.run(run())
    assert len(got) == 2 and log == ["closed"]


def test_slow_first_reply_times_out_and_closes_the_stream():
    run, got, log = _collect([0.5], first=0.05, gap=1)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run())
    assert got == [] and log == ["closed"]


def test_stall_mid_answer_times_out():
    run, got, log = _collect([0.01, 0.5], first=1, gap=0.05)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(run())
    assert len(got) == 1 and log == ["closed"]
