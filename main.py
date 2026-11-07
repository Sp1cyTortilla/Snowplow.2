"""Snowfall tracker entry point.

    python main.py            # run the collector + web map on http://127.0.0.1:8000
    python main.py --once     # run a single collection cycle and print the snowiest zips
"""
import argparse
import asyncio

import httpx
import uvicorn

from snowtracker import collector, config, db


async def once() -> None:
    cells = collector.setup()
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": config.USER_AGENT}) as client:
        await collector.collect_once(client, cells)
    rows = [r for r in db.zips_with_latest() if r["snowfall_24h_in"] is not None]
    for key, label in (("snowfall_24h_in", "past 24h"), ("forecast_24h_in", "next 24h"),
                       ("snow_depth_in", "depth")):
        top = sorted(rows, key=lambda r: r[key] or 0, reverse=True)[:5]
        print(f"\nTop zips by snowfall {label}:")
        for r in top:
            print(f"  {r['zip']} {r['place']:<20} {r[key]:5.2f} in")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--once", action="store_true")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    a = p.parse_args()
    if a.once:
        asyncio.run(once())
    else:
        uvicorn.run("snowtracker.server:app", host=a.host, port=a.port)
