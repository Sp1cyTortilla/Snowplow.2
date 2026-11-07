"""Interface every snowfall data source implements, so sources can be swapped or combined."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Reading:
    cell_id: str
    model_time: str          # ISO hour (UTC) the values refer to
    snow_depth_in: float | None
    snowfall_1h_in: float | None
    snowfall_24h_in: float | None
    forecast_24h_in: float | None
    # Hourly snowfall in inches: list of [iso_time, value]; past 24h plus next 48h.
    hourly: list[list] = field(default_factory=list)


class SnowSource(Protocol):
    name: str

    async def fetch(self, cells: list[tuple[str, float, float]]) -> list[Reading]:
        """Fetch readings for (cell_id, lat, lon) tuples."""
        ...
