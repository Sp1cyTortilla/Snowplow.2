"""SQLite storage. The schema is plain SQL so moving to Postgres/PostGIS later is simple."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from . import config
from .sources import Reading
from .zipcodes import ZipCode, cell_for

SCHEMA = """
CREATE TABLE IF NOT EXISTS cells (
    cell_id TEXT PRIMARY KEY,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS zips (
    zip TEXT PRIMARY KEY,
    place TEXT, state TEXT, county TEXT,
    lat REAL NOT NULL, lon REAL NOT NULL,
    cell_id TEXT NOT NULL REFERENCES cells(cell_id)
);
CREATE INDEX IF NOT EXISTS zips_state ON zips(state);
CREATE INDEX IF NOT EXISTS zips_latlon ON zips(lat, lon);
CREATE TABLE IF NOT EXISTS latest (
    cell_id TEXT PRIMARY KEY REFERENCES cells(cell_id),
    fetched_at TEXT NOT NULL,
    model_time TEXT,
    snow_depth_in REAL, snowfall_1h_in REAL, snowfall_24h_in REAL, forecast_24h_in REAL,
    hourly_json TEXT
);
CREATE TABLE IF NOT EXISTS readings (
    cell_id TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    model_time TEXT,
    snow_depth_in REAL, snowfall_1h_in REAL, snowfall_24h_in REAL, forecast_24h_in REAL,
    PRIMARY KEY (cell_id, fetched_at)
);
CREATE INDEX IF NOT EXISTS readings_time ON readings(fetched_at);
"""

ZIP_COLUMNS = """z.zip, z.place, z.state, z.county, z.lat, z.lon, z.cell_id,
    l.fetched_at, l.model_time, l.snow_depth_in, l.snowfall_1h_in,
    l.snowfall_24h_in, l.forecast_24h_in"""


@contextmanager
def connect():
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init(zips: list[ZipCode]) -> int:
    """Create tables and register zips/cells. Returns the number of cells."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        for z in zips:
            cid, clat, clon = cell_for(z.lat, z.lon)
            conn.execute("INSERT OR IGNORE INTO cells VALUES (?,?,?)", (cid, clat, clon))
            conn.execute(
                "INSERT OR REPLACE INTO zips VALUES (?,?,?,?,?,?,?)",
                (z.zip, z.place, z.state, z.county, z.lat, z.lon, cid),
            )
        return conn.execute(
            "SELECT COUNT(DISTINCT cell_id) FROM zips WHERE state IN (%s)"
            % ",".join("?" * len(config.STATES)), config.STATES,
        ).fetchone()[0]


def tracked_cells() -> list[tuple[str, float, float]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.cell_id, c.lat, c.lon FROM cells c JOIN zips z USING(cell_id) "
            "WHERE z.state IN (%s) ORDER BY c.cell_id" % ",".join("?" * len(config.STATES)),
            config.STATES,
        ).fetchall()
    return [(r["cell_id"], r["lat"], r["lon"]) for r in rows]


def save(readings: list[Reading]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=config.HISTORY_DAYS)).isoformat()
    with connect() as conn:
        for r in readings:
            vals = (r.cell_id, now, r.model_time, r.snow_depth_in, r.snowfall_1h_in,
                    r.snowfall_24h_in, r.forecast_24h_in)
            conn.execute("INSERT OR REPLACE INTO readings VALUES (?,?,?,?,?,?,?)", vals)
            conn.execute("INSERT OR REPLACE INTO latest VALUES (?,?,?,?,?,?,?,?)",
                         vals + (json.dumps(r.hourly),))
        conn.execute("DELETE FROM readings WHERE fetched_at < ?", (cutoff,))
    return now


def zips_with_latest(state: str | None = None, bbox: tuple[float, float, float, float] | None = None):
    sql = f"SELECT {ZIP_COLUMNS} FROM zips z LEFT JOIN latest l USING(cell_id) WHERE 1=1"
    args: list = []
    if state:
        sql += " AND z.state = ?"
        args.append(state.upper())
    if bbox:
        sql += " AND z.lat BETWEEN ? AND ? AND z.lon BETWEEN ? AND ?"
        args += [bbox[0], bbox[2], bbox[1], bbox[3]]
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, args)]


def zip_detail(zip_code: str):
    with connect() as conn:
        row = conn.execute(
            f"SELECT {ZIP_COLUMNS}, l.hourly_json FROM zips z LEFT JOIN latest l USING(cell_id) "
            "WHERE z.zip = ?", (zip_code,)).fetchone()
        if not row:
            return None
        out = dict(row)
        out["hourly"] = json.loads(out.pop("hourly_json") or "[]")
        out["history"] = [dict(r) for r in conn.execute(
            "SELECT fetched_at, model_time, snow_depth_in, snowfall_24h_in, forecast_24h_in "
            "FROM readings WHERE cell_id = ? ORDER BY fetched_at DESC LIMIT 200",
            (out["cell_id"],))]
    return out


def snow_grid() -> list[list]:
    """[[lat, lon, snowfall_24h_in, snowfall_1h_in], ...] for every tracked cell with data."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.lat, c.lon, l.snowfall_24h_in, l.snowfall_1h_in "
            "FROM cells c JOIN zips z USING(cell_id) JOIN latest l USING(cell_id) "
            "WHERE z.state IN (%s)" % ",".join("?" * len(config.STATES)), config.STATES,
        ).fetchall()
    return [[r[0], r[1], r[2], r[3]] for r in rows]


def status():
    with connect() as conn:
        r = conn.execute("SELECT MAX(fetched_at) AS last, COUNT(*) AS cells FROM latest").fetchone()
        z = conn.execute("SELECT COUNT(*) FROM zips").fetchone()[0]
    return {"last_update": r["last"], "cells_with_data": r["cells"], "zips": z}
