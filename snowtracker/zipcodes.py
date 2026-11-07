"""Zip code -> coordinates, from the GeoNames US postal code dump (CC-BY 4.0)."""
from __future__ import annotations

import csv
import io
import zipfile
from dataclasses import dataclass

import httpx

from . import config

GEONAMES_URL = "https://download.geonames.org/export/zip/US.zip"
DATASET = config.DATA_DIR / "US.txt"


@dataclass(frozen=True)
class ZipCode:
    zip: str
    place: str
    state: str
    county: str
    lat: float
    lon: float


def ensure_dataset() -> None:
    if DATASET.exists():
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading zip code dataset from {GEONAMES_URL} ...")
    resp = httpx.get(GEONAMES_URL, headers={"User-Agent": config.USER_AGENT},
                     timeout=60, follow_redirects=True)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        DATASET.write_bytes(zf.read("US.txt"))


def load_zips(states: list[str]) -> list[ZipCode]:
    ensure_dataset()
    wanted = set(states)
    out: list[ZipCode] = []
    with DATASET.open(encoding="utf-8", newline="") as f:
        # Columns: country, postal, place, state name, state code, county, county code,
        # admin3 name, admin3 code, lat, lon, accuracy
        for row in csv.reader(f, delimiter="\t"):
            if len(row) < 11 or row[4] not in wanted or not row[9] or not row[10]:
                continue
            out.append(ZipCode(row[1], row[2], row[4], row[5], float(row[9]), float(row[10])))
    return out


def cell_for(lat: float, lon: float, grid: float = config.GRID_DEG) -> tuple[str, float, float]:
    """Snap a point to the centre of its grid cell. Returns (cell_id, lat, lon)."""
    clat = (int(lat // grid) + 0.5) * grid
    clon = (int(lon // grid) + 0.5) * grid
    return f"{clat:.4f},{clon:.4f}", round(clat, 4), round(clon, 4)
