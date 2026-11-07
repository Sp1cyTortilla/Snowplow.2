"""Runtime settings. Everything can be overridden with environment variables."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("SNOW_DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "snow.db"

# Comma-separated two-letter state codes to track, e.g. "MI,WI,MN".
STATES = [s.strip().upper() for s in os.getenv("SNOW_STATES", "MI").split(",") if s.strip()]

# Zip codes are snapped to a lat/lon grid of this size (degrees) and each grid
# cell is fetched once. Bigger cells mean fewer API calls; 0.15 deg is about 16 km.
GRID_DEG = float(os.getenv("SNOW_GRID_DEG", "0.15"))

# Seconds between collection cycles. Weather models update hourly, so polling
# more often than every 15 minutes gains little.
POLL_SECONDS = int(os.getenv("SNOW_POLL_SECONDS", "7200"))

# Locations per upstream request.
BATCH_SIZE = int(os.getenv("SNOW_BATCH_SIZE", "100"))

# Days of reading history to keep in the database.
HISTORY_DAYS = int(os.getenv("SNOW_HISTORY_DAYS", "30"))

USER_AGENT = "snowtracker/0.1 (local snowfall map)"
