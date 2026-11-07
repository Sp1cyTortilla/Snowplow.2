"""Background collector: polls the snowfall source for every tracked grid cell."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import httpx

from . import config, db
from .sources import OpenMeteoSource, Reading
from .zipcodes import load_zips


def setup() -> list[tuple[str, float, float]]:
    zips = load_zips(config.STATES)
    n = db.init(zips)
    print(f"Tracking {len(zips)} zip codes in {','.join(config.STATES)} -> {n} grid cells "
          f"({config.GRID_DEG} deg)")
    calls_per_day = n * 86400 / config.POLL_SECONDS
    if calls_per_day > 9000:
        print(f"WARNING: ~{calls_per_day:,.0f} location-calls/day exceeds the Open-Meteo free tier "
              "(10k/day). Raise SNOW_GRID_DEG or SNOW_POLL_SECONDS.")
    return db.tracked_cells()


async def collect_once(client: httpx.AsyncClient, cells) -> str:
    source = OpenMeteoSource(client)
    readings: list[Reading] = []
    for i in range(0, len(cells), config.BATCH_SIZE):
        batch = cells[i: i + config.BATCH_SIZE]
        try:
            readings += await source.fetch(batch)
        except Exception as e:  # keep the rest of the cycle going
            print(f"[collector] batch {i // config.BATCH_SIZE} failed: {e!r}")
        # Pace to ~400 locations/min; the free tier allows 600/min.
        if i + config.BATCH_SIZE < len(cells):
            await asyncio.sleep(len(batch) * 60 / 400)
    stamp = db.save(readings)
    print(f"[collector] {stamp}: saved {len(readings)}/{len(cells)} cells")
    return stamp


async def run_forever(on_update: Callable[[str], Awaitable[None]] | None = None) -> None:
    cells = await asyncio.to_thread(setup)
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": config.USER_AGENT}) as client:
        while True:
            try:
                stamp = await collect_once(client, cells)
                if on_update:
                    await on_update(stamp)
            except Exception as e:
                print(f"[collector] cycle failed: {e!r}")
            await asyncio.sleep(config.POLL_SECONDS)
