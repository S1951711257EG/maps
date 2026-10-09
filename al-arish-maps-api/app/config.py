"""Central configuration - everything tunable via environment variables."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# database -------------------------------------------------------------------
DB_PATH = Path(os.environ.get("DB_PATH", str(ROOT / "data" / "al_arish_maps.sqlite3")))

# routing (hybrid: OSRM first, straight-line estimate as fallback) -----------
OSRM_URL = os.environ.get("OSRM_URL", "http://localhost:5000").rstrip("/")
OSRM_TIMEOUT_S = float(os.environ.get("OSRM_TIMEOUT_S", "2.5"))
CITY_SPEED_KMH = float(os.environ.get("CITY_SPEED_KMH", "30"))
CIRCUITRY_FACTOR = float(os.environ.get("CIRCUITRY_FACTOR", "1.4"))

# geocoding (local gazetteer first; optional free Nominatim fallback) --------
ALLOW_ONLINE_GEOCODE = os.environ.get("ALLOW_ONLINE_GEOCODE", "1").lower() \
    not in ("0", "false", "no")
NOMINATIM_URL = os.environ.get(
    "NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")

# self-hosted tiles (tileserver-gl from deploy/setup_tiles.sh) ---------------
TILE_STYLE_URL = os.environ.get(
    "TILE_STYLE_URL", "http://localhost:8090/styles/al-arish/style.json")
TILE_PBF_URL = os.environ.get(
    "TILE_PBF_URL", "http://localhost:8090/data/al-arish/{z}/{x}/{y}.pbf")

# optional shared-secret auth: set API_KEY to require "X-API-Key: <key>" -----
API_KEY = os.environ.get("API_KEY", "")

# city geometry ----------------------------------------------------------------
CENTER = {"lat": 31.132093, "lng": 33.8032762}
BBOX = {"south": 30.95, "west": 33.60, "north": 31.30, "east": 34.00}

USER_AGENT = "al-arish-maps-api/1.0 (Al Arish delivery app; self-hosted)"


def in_bbox(lat: float, lng: float) -> bool:
    return (BBOX["south"] <= lat <= BBOX["north"]
            and BBOX["west"] <= lng <= BBOX["east"])
