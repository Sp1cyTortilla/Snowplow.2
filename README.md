# Snowplow.2

A live snowfall map for Michigan zip codes, with snow plow trucks that "erase" the snow
where they drive. The erased roads slowly fill back in at the current snowfall rate.

## What it does

- **Snow color layer.** Snowfall over the past 24 hours, drawn as a smooth gradient across
  the map using the colors in the key (bottom right). No snow = clear map.
- **Zip search** (bottom left). Type a zip and press Go: the zip's real border is outlined
  in orange and the map zooms to it. Tapping a zip dot does the same.
- **Zip dots** only appear once zoomed in (zoom 9+), so the state view stays clean.
- **Plow trucks.** Orange arrows pointing the way each truck is heading, refreshed every
  30 seconds. Trucks that haven't reported for 30+ minutes are faded.
- **Plow eraser.** Where a plow drives, the snow color is cut out of the map. The stretch
  then shows only the snow that has fallen *since* the plow passed, so it fills back in
  as it keeps snowing, and disappears once it matches the area around it.
- **Mobile friendly.** Big tap targets, no zoom-on-focus on iPhone, finer zoom steps.

## Running it

### Docker (recommended)

```bash
docker compose up -d --build     # build and start on http://localhost:8000
docker compose logs -f           # watch the server output
docker compose down              # stop
```

The `./data` folder is mounted into the container, so the database and caches survive
rebuilds. Rebuild (`up -d --build`) after any code change, including the web page.

### Without Docker

```bash
uv sync
uv run python main.py                    # http://127.0.0.1:8000 (this computer only)
uv run python main.py --host 0.0.0.0     # also reachable from phones on your network
uv run python main.py --once             # one collection cycle, print the snowiest zips
```

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `SNOW_STATES` | `MI` | Comma-separated states to track, e.g. `MI,WI,MN` |
| `SNOW_GRID_DEG` | `0.15` | Grid cell size in degrees (~16 km). Each cell is one weather lookup |
| `SNOW_POLL_SECONDS` | `7200` | Seconds between snow data refreshes |
| `SNOW_HISTORY_DAYS` | `30` | Days of readings kept in the database |
| `SNOW_DATA_DIR` | `./data` | Where the database and caches live |

Open-Meteo's free tier allows ~10,000 location lookups a day. Michigan is 553 cells, so
refreshing more often than about every 80 minutes would exceed it. The plow refill still
updates live between refreshes, because each refresh includes hourly amounts for the
next 48 hours.

## How it's built

```
main.py                       entry point (server, or --once)
snowtracker/
  config.py                   settings from environment variables
  zipcodes.py                 zip -> lat/lon from the GeoNames dataset (data/US.txt)
  sources/open_meteo.py       hourly snowfall + depth from Open-Meteo, per grid cell
  collector.py                background loop: fetches every grid cell, saves to SQLite
  db.py                       SQLite storage (cells, zips, latest, readings)
  boundaries.py               zip borders (Census ZCTA via TIGERweb), cached per zip
  plows.py                    plow trackers: zip -> state -> feed, trucks, eraser trails
  server.py                   FastAPI app, JSON API, WebSocket update pings
  static/index.html           the map page (Leaflet), search, zip dots, color key
  static/snow.js              snow color layer + plow eraser drawing
  static/trucks.js            live truck markers
```

### Plow trackers

`plows.py` turns a zip code into its state (from the GeoNames file), then looks up that
state's public plow feed. Results are saved in `data/plow_sources.json` and only
re-checked when stale (24 h if it worked, 1 h if it was down, 7 days if the state has no
known feed).

States with a feed are listed in `KNOWN_TRACKERS` in `snowtracker/plows.py`; add a state
by adding its URL there. Currently:

| State | Feed |
|---|---|
| MI | `https://mdotjboss.state.mi.us/MiDrive/plows/AllForMap?_={ts}` (no `/` before `?`, or it returns 404) |

Each feed's records are normalised to `{id, lat, lon, heading, speed, updated, plowing}`
by guessing common field names. **The real Michigan record format hasn't been seen yet**
(the feed was empty when this was written). If trucks don't appear once plows are out,
check `/api/plows/<zip>` and add the real field names to the `_*_KEYS` lists.

### Plow eraser

- Every 60 s the server polls each state's feed and stores the position of every truck
  that moved more than 15 m (`plow_points` table).
- Consecutive positions of one truck form a plowed segment, unless they are more than
  10 minutes apart, need an impossible speed (>90 mph), or the feed says the blade is up.
- For each segment, the snow since the plow passed is summed from the grid cell's hourly
  snowfall series (partial hours prorated). Once that reaches the cell's 24 h total, the
  segment is fully refilled and dropped. Trails older than 26 h are deleted.
- The page cuts each segment out of the color layer, then repaints it in the color of the
  snow fallen since. Width is ~12 m on the ground, at least 3 px on screen.
- Trails are straight lines between position reports, so they can cut corners on curves.

## API

| Endpoint | Returns |
|---|---|
| `GET /api/zips` | All tracked zips with their latest snow values (`?state=`, `?bbox=`) |
| `GET /api/zip/{zip}` | One zip's values plus hourly series and history |
| `GET /api/zip/{zip}/boundary` | The zip's border as GeoJSON (404 for PO-box-only zips) |
| `GET /api/snowgrid` | Per-grid-cell snowfall for the color layer |
| `GET /api/plowed` | Plowed segments still refilling: `[lat1, lon1, lat2, lon2, since_in, base_in, plowed_epoch]` |
| `GET /api/trucks` | Active trucks per tracked state (`?state=` for one) |
| `GET /api/plows/{zip}/tracker` | Which plow feed covers a zip (`?force=true` to re-check) |
| `GET /api/plows/{zip}` | Raw current data from that feed |
| `GET /api/status` | Last update time and counts |
| `WS /ws` | Sends `{"type": "update"}` after each snow data refresh |

## Data sources and credits

- Map tiles © OpenStreetMap contributors (credit must stay visible on the map)
- Snowfall: Open-Meteo (CC BY 4.0)
- Zip code locations: GeoNames (CC BY 4.0)
- Zip code borders: US Census Bureau TIGERweb (2020 ZCTAs)
- Plow positions: each state's DOT public feed (Michigan: MDOT Mi Drive)
