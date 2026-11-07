"""Open-Meteo forecast API: free, no key, many locations per request.

Values are model analysis/forecast (blended HRRR/GFS etc.), not station reports.
Free tier limits: about 600 calls/min, 5,000/hour, 10,000/day, where each location counts.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx

from .. import config
from .base import Reading

URL = "https://api.open-meteo.com/v1/forecast"
CM_TO_IN = 1 / 2.54
M_TO_IN = 39.3701


class OpenMeteoSource:
    name = "open-meteo"

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(self, cells: list[tuple[str, float, float]]) -> list[Reading]:
        params = {
            "latitude": ",".join(f"{c[1]}" for c in cells),
            "longitude": ",".join(f"{c[2]}" for c in cells),
            "hourly": "snowfall,snow_depth",
            "past_days": 1,
            "forecast_days": 3,
            "timezone": "UTC",
        }
        data = await self._get(params)
        if isinstance(data, dict):  # a single location returns an object, not a list
            data = [data]
        return [self._parse(cell[0], loc) for cell, loc in zip(cells, data)]

    async def _get(self, params: dict) -> list | dict:
        for attempt in range(5):
            resp = await self.client.get(URL, params=params)
            if resp.status_code == 429 or resp.status_code >= 500:
                wait = 2 ** attempt * 5
                print(f"[open-meteo] HTTP {resp.status_code}, retrying in {wait}s")
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        resp.raise_for_status()
        raise RuntimeError("open-meteo: retries exhausted")

    @staticmethod
    def _parse(cell_id: str, loc: dict) -> Reading:
        h = loc["hourly"]
        times: list[str] = h["time"]
        snow = [None if v is None else v * CM_TO_IN for v in h["snowfall"]]
        depth = [None if v is None else v * M_TO_IN for v in h["snow_depth"]]

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:00")
        idx = max((i for i, t in enumerate(times) if t <= now), default=0)

        def total(vals):
            vals = [v for v in vals if v is not None]
            return round(sum(vals), 2) if vals else None

        def r(v):
            return None if v is None else round(v, 2)

        lo, hi = max(0, idx - 23), min(len(times), idx + 49)
        return Reading(
            cell_id=cell_id,
            model_time=times[idx],
            snow_depth_in=r(depth[idx]),
            snowfall_1h_in=r(snow[idx]),
            snowfall_24h_in=total(snow[idx - 23: idx + 1] if idx >= 23 else snow[: idx + 1]),
            forecast_24h_in=total(snow[idx + 1: idx + 25]),
            hourly=[[times[i], r(snow[i])] for i in range(lo, hi)],
        )
