"""FastAPI app: JSON API, live WebSocket notifications, and the Leaflet map."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from . import boundaries, collector, config, db

STATIC = Path(__file__).parent / "static"
clients: set[WebSocket] = set()


async def broadcast(stamp: str) -> None:
    for ws in list(clients):
        try:
            await ws.send_json({"type": "update", "fetched_at": stamp})
        except Exception:
            clients.discard(ws)


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(collector.run_forever(broadcast))
    yield
    task.cancel()


app = FastAPI(title="Snowfall Tracker", lifespan=lifespan)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    return {**db.status(), "states": config.STATES, "poll_seconds": config.POLL_SECONDS,
            "grid_deg": config.GRID_DEG}


@app.get("/api/zips")
def zips(state: str | None = None,
         bbox: str | None = Query(None, description="minLat,minLon,maxLat,maxLon")):
    box = tuple(float(x) for x in bbox.split(",")) if bbox else None
    if box is not None and len(box) != 4:
        raise HTTPException(400, "bbox must be minLat,minLon,maxLat,maxLon")
    return db.zips_with_latest(state, box)


@app.get("/api/zip/{zip_code}")
def zip_detail(zip_code: str):
    d = db.zip_detail(zip_code)
    if not d:
        raise HTTPException(404, "Unknown or untracked zip code")
    return d


@app.get("/api/zip/{zip_code}/boundary")
async def zip_boundary(zip_code: str):
    try:
        feature = await boundaries.get_boundary(zip_code)
    except Exception as e:
        raise HTTPException(502, f"Boundary lookup failed: {e}")
    if not feature:
        raise HTTPException(404, "No boundary for this zip code")
    return feature


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    clients.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        clients.discard(websocket)
