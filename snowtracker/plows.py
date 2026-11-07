"""Snow plow trackers: zip code -> state -> that state's public plow feed.

Flow for find_plow_tracker(zip):
  1. Look up the zip's state in the GeoNames dataset (all US states, loaded once).
  2. Check the stored record for that state in data/plow_sources.json. If it was
     checked recently, use it as-is (no network call).
  3. Otherwise check the state's known tracker URL, then store the result
     (url, whether it answered, when we checked) so later lookups are instant.

To support another state, add an entry to KNOWN_TRACKERS.
"""
from __future__ import annotations

import asyncio
import csv
import json
import math
import time
from datetime import datetime, timezone
from functools import lru_cache

import httpx

from . import config, db, zipcodes

STORE = config.DATA_DIR / "plow_sources.json"

# How long a stored check result is trusted before checking the URL again.
RECHECK_OK_SECONDS = 24 * 3600        # tracker answered last time
RECHECK_DOWN_SECONDS = 1 * 3600       # tracker didn't answer: try again sooner
RECHECK_NONE_SECONDS = 7 * 24 * 3600  # state has no known tracker

# State code -> its public plow feed. "{ts}" is filled with a millisecond
# timestamp (the site uses it to avoid cached responses).
KNOWN_TRACKERS: dict[str, dict] = {
    "MI": {
        "name": "MDOT Mi Drive plows",
        # Note: no "/" before "?" -- with a trailing slash the server returns 404.
        "url": "https://mdotjboss.state.mi.us/MiDrive/plows/AllForMap?_={ts}",
        "format": "json",
    },
}


# ---------- zip -> state ----------

@lru_cache(maxsize=1)
def _zip_states() -> dict[str, tuple[str, str]]:
    """{zip: (state_code, state_name)} for every US zip in the GeoNames file."""
    zipcodes.ensure_dataset()
    out: dict[str, tuple[str, str]] = {}
    with zipcodes.DATASET.open(encoding="utf-8", newline="") as f:
        for row in csv.reader(f, delimiter="\t"):
            if len(row) >= 5 and row[1] and row[4]:
                out[row[1]] = (row[4], row[3])
    return out


def state_for_zip(zip_code: str) -> tuple[str, str] | None:
    """Return (state_code, state_name) for a zip, or None if unknown."""
    return _zip_states().get(zip_code.strip())


# ---------- stored tracker locations ----------

def _load_store() -> dict[str, dict]:
    try:
        return json.loads(STORE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_store(store: dict[str, dict]) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, sort_keys=True))
    tmp.replace(STORE)


def _is_fresh(rec: dict) -> bool:
    age = time.time() - rec.get("checked_epoch", 0)
    limit = {"ok": RECHECK_OK_SECONDS, "down": RECHECK_DOWN_SECONDS}.get(
        rec.get("status"), RECHECK_NONE_SECONDS)
    return age < limit


def _url(template: str) -> str:
    return template.format(ts=int(time.time() * 1000))


async def _check(client: httpx.AsyncClient, state: str) -> dict:
    """Check one state's tracker and return the record to store."""
    now = time.time()
    rec = {"state": state, "checked_epoch": now,
           "checked_at": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")}
    src = KNOWN_TRACKERS.get(state)
    if not src:
        return {**rec, "status": "none", "url": None, "name": None}
    rec.update(name=src["name"], url=src["url"], format=src["format"])
    try:
        resp = await client.get(_url(src["url"]))
        resp.raise_for_status()
        resp.json()  # must be valid JSON
        return {**rec, "status": "ok", "error": None}
    except Exception as e:  # network error, bad status, or not JSON
        return {**rec, "status": "down", "error": f"{type(e).__name__}: {e}"[:200]}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": config.USER_AGENT},
                             timeout=20, follow_redirects=True)


async def find_plow_tracker(zip_code: str, force: bool = False) -> dict:
    """zip -> state -> stored (or freshly checked) plow tracker info.

    Returns {"zip", "state", "state_name", "available", "status", "name",
             "url", "checked_at"}; status is "ok", "down", "none" or "unknown_zip".
    """
    found = state_for_zip(zip_code)
    if not found:
        return {"zip": zip_code, "state": None, "state_name": None,
                "available": False, "status": "unknown_zip"}
    state, state_name = found

    store = _load_store()
    rec = store.get(state)
    # Re-check if the stored entry is stale, forced, or the known URL changed.
    stale = (not rec or force or not _is_fresh(rec)
             or rec.get("url") != KNOWN_TRACKERS.get(state, {}).get("url"))
    if stale:
        async with _client() as client:
            rec = await _check(client, state)
        store[state] = rec
        _save_store(store)

    return {"zip": zip_code, "state": state, "state_name": state_name,
            "available": rec["status"] == "ok", "status": rec["status"],
            "name": rec.get("name"), "url": rec.get("url"),
            "checked_at": rec.get("checked_at")}


async def fetch_plows(zip_code: str) -> dict:
    """Find the tracker for this zip's state and return its current raw data."""
    info = await find_plow_tracker(zip_code)
    if not info["available"]:
        return {**info, "plows": []}
    async with _client() as client:
        resp = await client.get(_url(info["url"]))
        resp.raise_for_status()
        data = resp.json()
    # Raw records for now; the feed was empty when this was written, so the
    # per-plow field names (position, heading, time) still need mapping.
    return {**info, "plows": data}


# ---------- normalised trucks (what the map draws) ----------

_LAT_KEYS = ("lat", "latitude", "y", "lat_dd", "gpslat", "gps_latitude")
_LON_KEYS = ("lon", "lng", "long", "longitude", "x", "lon_dd", "gpslon", "gpslong", "gps_longitude")
_ID_KEYS = ("id", "truckid", "truck_id", "vehicleid", "vehicle_id", "unitid", "unit", "name", "label")
_HEADING_KEYS = ("heading", "bearing", "direction", "course", "dir")
_TIME_KEYS = ("timestamp", "time", "updated", "lastupdate", "last_update", "lastupdated",
              "datetime", "date", "reporttime", "gpstime")
_SPEED_KEYS = ("speed", "mph", "velocity")
_BLADE_KEYS = ("plowing", "isplowing", "is_plowing", "blade", "bladedown", "blade_down",
               "plowdown", "plow_down", "plowstatus", "plow_status")


def _pick(rec: dict, keys: tuple[str, ...]):
    lower = {k.lower(): v for k, v in rec.items()}
    for k in keys:
        if lower.get(k) not in (None, ""):
            return lower[k]
    return None


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _records(data) -> list[dict]:
    """Pull the list of plow records out of whatever shape the feed uses."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        if data.get("type") == "FeatureCollection":
            out = []
            for f in data.get("features") or []:
                coords = (f.get("geometry") or {}).get("coordinates") or []
                props = dict(f.get("properties") or {})
                if len(coords) >= 2:
                    props.setdefault("longitude", coords[0])
                    props.setdefault("latitude", coords[1])
                out.append(props)
            return out
        for key in ("plows", "trucks", "vehicles", "data", "items", "results"):
            if isinstance(data.get(key), list):
                return _records(data[key])
    return []


def _blade(v) -> bool | None:
    """True = blade down / plowing, False = not plowing, None = feed doesn't say."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "1", "down", "on", "yes", "plowing", "active"):
            return True
        if t in ("false", "0", "up", "off", "no", "inactive"):
            return False
    return None


def normalize_trucks(data) -> list[dict]:
    """Map raw feed records to {id, lat, lon, heading, speed, updated, plowing}.

    Field names are guessed from common spellings; records without a usable
    position are skipped. Once a live feed shows its real field names, add them
    to the *_KEYS tuples above if they aren't already covered.
    """
    trucks = []
    for i, rec in enumerate(_records(data)):
        lat, lon = _num(_pick(rec, _LAT_KEYS)), _num(_pick(rec, _LON_KEYS))
        if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        trucks.append({
            "id": str(_pick(rec, _ID_KEYS) or i),
            "lat": lat, "lon": lon,
            "heading": _num(_pick(rec, _HEADING_KEYS)),
            "speed": _num(_pick(rec, _SPEED_KEYS)),
            "updated": _pick(rec, _TIME_KEYS),
            "plowing": _blade(_pick(rec, _BLADE_KEYS)),
        })
    return trucks


# Short in-memory cache so many map viewers don't each hit the state's server.
TRUCK_CACHE_SECONDS = 30
_truck_cache: dict[str, tuple[float, dict]] = {}


async def trucks_for_state(state: str) -> dict:
    """Active trucks for one state: {"state", "available", "name", "trucks", "fetched_at"}."""
    state = state.upper()
    hit = _truck_cache.get(state)
    if hit and time.time() - hit[0] < TRUCK_CACHE_SECONDS:
        return hit[1]

    store = _load_store()
    rec = store.get(state)
    if (not rec or not _is_fresh(rec)
            or rec.get("url") != KNOWN_TRACKERS.get(state, {}).get("url")):
        async with _client() as client:
            rec = await _check(client, state)
        store[state] = rec
        _save_store(store)

    result = {"state": state, "available": rec["status"] == "ok",
              "name": rec.get("name"), "trucks": [],
              "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if result["available"]:
        async with _client() as client:
            resp = await client.get(_url(rec["url"]))
            resp.raise_for_status()
            result["trucks"] = normalize_trucks(resp.json())
    _truck_cache[state] = (time.time(), result)
    return result


# ---------- plow trails: the "eraser" ----------
#
# Every TRACK_SECONDS the server stores each moving truck's position. Consecutive
# positions of the same truck make a plowed segment. A segment shows only the snow
# that has fallen since the plow passed (from the hourly snowfall series of its grid
# cell); once that catches up with the surrounding 24h snowfall it is fully refilled
# and dropped.

TRACK_SECONDS = 60
MAX_GAP_SECONDS = 10 * 60      # don't join points further apart in time than this
MAX_SPEED_MPH = 90             # ...or that would need an impossible speed
MIN_MOVE_M = 15                # ignore GPS jitter of a parked truck
KEEP_SECONDS = 26 * 3600       # trails older than this are fully refilled anyway

PLOW_SCHEMA = """
CREATE TABLE IF NOT EXISTS plow_points (
    state TEXT NOT NULL,
    truck_id TEXT NOT NULL,
    ts REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    plowing INTEGER,
    PRIMARY KEY (state, truck_id, ts)
);
CREATE INDEX IF NOT EXISTS plow_points_ts ON plow_points(ts);
"""


def _meters(lat1, lon1, lat2, lon2) -> float:
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def record_positions(state: str, trucks: list[dict], now: float | None = None) -> int:
    """Store positions of trucks that moved since their last stored point."""
    now = now or time.time()
    added = 0
    with db.connect() as conn:
        conn.executescript(PLOW_SCHEMA)
        for t in trucks:
            last = conn.execute(
                "SELECT lat, lon FROM plow_points WHERE state=? AND truck_id=? "
                "ORDER BY ts DESC LIMIT 1", (state, t["id"])).fetchone()
            if last and _meters(last["lat"], last["lon"], t["lat"], t["lon"]) < MIN_MOVE_M:
                continue
            plowing = None if t.get("plowing") is None else int(t["plowing"])
            conn.execute("INSERT OR REPLACE INTO plow_points VALUES (?,?,?,?,?,?)",
                         (state, t["id"], now, t["lat"], t["lon"], plowing))
            added += 1
        conn.execute("DELETE FROM plow_points WHERE ts < ?", (now - KEEP_SECONDS,))
    return added


def _snow_since(hourly: list, since: float, now: float) -> float:
    """Inches of snow between two times, from an hourly series of [iso_hour, inches].

    Open-Meteo's hourly snowfall at time T is the total for the hour ending at T,
    so each value covers (T-1h, T]; partial hours are counted proportionally.
    """
    total = 0.0
    for t, v in hourly:
        if not v:
            continue
        end = datetime.fromisoformat(t).replace(tzinfo=timezone.utc).timestamp()
        start = end - 3600
        overlap = min(end, now) - max(start, since)
        if overlap > 0:
            total += v * overlap / 3600
    return total


def _cell_snow(cells: dict, lat: float, lon: float) -> tuple[float | None, list]:
    """Snow for the grid cell at this point. Cells without a zip code have no data,
    so fall back to the nearest neighbours that do (the map fills them the same way)."""
    g = config.GRID_DEG
    cid, clat, clon = zipcodes.cell_for(lat, lon)
    if cid in cells and cells[cid][0] is not None:
        return cells[cid]
    for ring in (1, 2):
        near = []
        for di in range(-ring, ring + 1):
            for dj in range(-ring, ring + 1):
                key = f"{clat + di * g:.4f},{clon + dj * g:.4f}"
                if max(abs(di), abs(dj)) == ring and cells.get(key, (None,))[0] is not None:
                    near.append(cells[key])
        if near:
            base = sum(n[0] for n in near) / len(near)
            # average the neighbours' hourly series hour by hour
            series: dict[str, list[float]] = {}
            for _, hourly in near:
                for t, v in hourly:
                    series.setdefault(t, []).append(v or 0)
            return base, [[t, sum(v) / len(v)] for t, v in sorted(series.items())]
    return None, []


def plowed_segments(now: float | None = None) -> list[list]:
    """Plowed stretches that haven't fully refilled yet.

    Each item: [lat1, lon1, lat2, lon2, snow_since_in, base_24h_in, plowed_epoch].
    """
    now = now or time.time()
    with db.connect() as conn:
        conn.executescript(PLOW_SCHEMA)
        pts = conn.execute(
            "SELECT state, truck_id, ts, lat, lon, plowing FROM plow_points "
            "WHERE ts >= ? ORDER BY state, truck_id, ts", (now - KEEP_SECONDS,)).fetchall()
        cells = {r["cell_id"]: (r["snowfall_24h_in"], json.loads(r["hourly_json"] or "[]"))
                 for r in conn.execute(
                     "SELECT cell_id, snowfall_24h_in, hourly_json FROM latest")}

    out = []
    for a, b in zip(pts, pts[1:]):
        if (a["state"], a["truck_id"]) != (b["state"], b["truck_id"]):
            continue
        dt = b["ts"] - a["ts"]
        if dt <= 0 or dt > MAX_GAP_SECONDS:
            continue
        dist = _meters(a["lat"], a["lon"], b["lat"], b["lon"])
        if dist / dt * 2.23694 > MAX_SPEED_MPH:
            continue
        if a["plowing"] == 0 or b["plowing"] == 0:   # blade up: not erasing
            continue
        base, hourly = _cell_snow(cells, (a["lat"] + b["lat"]) / 2, (a["lon"] + b["lon"]) / 2)
        if not base:                     # no snow here, nothing to erase
            continue
        since = _snow_since(hourly, b["ts"], now)
        if since >= base:                # fully refilled
            continue
        out.append([round(a["lat"], 6), round(a["lon"], 6), round(b["lat"], 6),
                    round(b["lon"], 6), round(since, 3), base, round(b["ts"])])
    return out


async def run_tracker() -> None:
    """Background loop: poll every tracked state's plow feed and store positions."""
    while True:
        for state in config.STATES:
            try:
                res = await trucks_for_state(state)
                if res["trucks"]:
                    n = await asyncio.to_thread(record_positions, state, res["trucks"])
                    if n:
                        print(f"[plows] {state}: stored {n} new truck positions")
            except Exception as e:
                print(f"[plows] {state} poll failed: {e!r}")
        await asyncio.sleep(TRACK_SECONDS)
