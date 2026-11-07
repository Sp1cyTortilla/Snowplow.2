"""Zip code boundary polygons (Census ZCTA via TIGERweb), cached on disk.

PO-box-only and some single-building zips have no ZCTA; those return None.
"""
from __future__ import annotations

import json
import re

import httpx

from . import config

TIGERWEB_URL = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
                "PUMA_TAD_TAZ_UGA_ZCTA/MapServer/7/query")  # 2020 Census ZCTAs
CACHE_DIR = config.DATA_DIR / "boundaries"
_ZIP_RE = re.compile(r"^\d{5}$")


async def get_boundary(zip_code: str) -> dict | None:
    """Return a GeoJSON Feature for the zip's boundary, or None if it has none."""
    if not _ZIP_RE.match(zip_code):
        return None
    path = CACHE_DIR / f"{zip_code}.json"
    if path.exists():
        data = json.loads(path.read_text())
        return data or None  # cached {} means "no boundary exists"

    params = {"where": f"ZCTA5='{zip_code}'", "outFields": "ZCTA5", "outSR": "4326",
              "geometryPrecision": "5", "f": "geojson"}
    async with httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT}, timeout=20) as c:
        resp = await c.get(TIGERWEB_URL, params=params)
        resp.raise_for_status()
    features = resp.json().get("features") or []
    feature = features[0] if features else None

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(feature or {}))
    return feature
