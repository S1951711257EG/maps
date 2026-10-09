"""
Al Arish Maps API - a tiny self-hosted mapping service for ONE city only.

Built for delivery apps that refuse to pay Google Maps per request.

Endpoints
---------
GET  /health          service status, row counts, OSRM reachability
GET  /config          center/bbox + tile-server URLs to wire into your app
GET  /geocode?q=...   local gazetteer (scraped POIs + OSM) -> lat/lng,
                       optional free Nominatim fallback with caching
GET  /route           driving distance + ETA + geometry; self-hosted OSRM
      ?from=lat,lng   first, straight-line estimate fallback when OSRM is
      &to=lat,lng     down (the response always tells you which was used)
POST /places          add your own landmark/saved address to the gazetteer

Run (from the project root):
    pip install -r requirements.txt
    ./run.sh                      # or: uvicorn app.main:app --port 8000
Interactive docs: http://localhost:8000/docs
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config, geocoder, router

app = FastAPI(
    title="Al Arish Maps API",
    version="1.0.0",
    description="Self-hosted geocoding + routing for Al Arish, North Sinai, Egypt.",
)

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"],          # the delivery web app may live anywhere
)

_db_lock = threading.Lock()
_con: Optional[sqlite3.Connection] = None


def con() -> sqlite3.Connection:
    global _con
    if _con is None:
        if not config.DB_PATH.exists():
            raise RuntimeError(
                f"database not found at {config.DB_PATH} - run "
                "scripts/build_db.py first (see README)")
        _con = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _con.row_factory = sqlite3.Row
    return _con


def check_key(x_api_key: str = Header(default="")):
    """When API_KEY env is set, every request must send the same header."""
    if config.API_KEY and x_api_key != config.API_KEY:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.get("/health", dependencies=[Depends(check_key)])
def health():
    c = con()
    counts = {
        "places": c.execute("SELECT COUNT(*) FROM places").fetchone()[0],
        "streets": c.execute("SELECT COUNT(*) FROM streets").fetchone()[0],
        "named_places": c.execute("SELECT COUNT(*) FROM named_places").fetchone()[0],
        "geocode_cache": c.execute("SELECT COUNT(*) FROM geocode_cache").fetchone()[0],
    }
    probe = router.route(31.1320, 33.8032, 31.1400, 33.8100, overview=False)
    return {"status": "ok", "db": str(config.DB_PATH), "counts": counts,
            "routing_source_last_probe": probe["source"],
            "osrm_url": config.OSRM_URL,
            "online_geocode": config.ALLOW_ONLINE_GEOCODE}


@app.get("/config", dependencies=[Depends(check_key)])
def config_endpoint():
    """Everything a client app needs to wire itself up."""
    return {
        "city": "Al Arish, North Sinai, Egypt",
        "center": config.CENTER,
        "bbox": config.BBOX,
        "tiles": {"style_url": config.TILE_STYLE_URL,
                  "vector_pbf_url": config.TILE_PBF_URL},
        "osrm_url": config.OSRM_URL,
        "online_geocode_fallback": config.ALLOW_ONLINE_GEOCODE,
        "endpoints": ["/geocode?q=", "/route?from=lat,lng&to=lat,lng",
                      "/health", "/places (POST)"],
    }


class PlaceIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    lat: float = Field(ge=27, le=34)
    lng: float = Field(ge=32, le=36)
    category: str = "custom"
    phone: str = ""
    address: str = ""
    name_ar: str = ""


@app.post("/places", dependencies=[Depends(check_key)])
def add_place(p: PlaceIn):
    """Add a saved address / landmark your app collected (free-text geocode)."""
    if not config.in_bbox(p.lat, p.lng):
        raise HTTPException(400, "coordinates outside the Al Arish bbox")
    with _db_lock:
        cur = con().execute(
            "INSERT INTO places(name,name_en,name_ar,category,address,phone,"
            "lat,lng,source) VALUES (?,?,?,?,?,?,?,?,?)",
            (p.name.strip(), p.name.strip() if not p.name_ar else "",
             p.name_ar, p.category, p.address, p.phone, p.lat, p.lng, "user"))
        con().commit()
    return {"id": cur.lastrowid, "status": "added", "name": p.name}


@app.get("/geocode", dependencies=[Depends(check_key)])
def geocode_endpoint(q: str, limit: int = 8):
    if len(q.strip()) < 2:
        raise HTTPException(400, "q must be at least 2 characters")
    limit = max(1, min(limit, 25))
    return geocoder.geocode(con(), q.strip(), limit)


@app.get("/route", dependencies=[Depends(check_key)])
def route_endpoint(from_: str = Query(..., alias="from"),
                   to: str = Query(...), overview: bool = False):
    def parse(pair: str) -> tuple[float, float]:
        try:
            lat, lng = (float(x) for x in pair.split(","))
        except ValueError:
            raise HTTPException(400, f"expected 'lat,lng', got '{pair}'")
        return lat, lng

    flat, flng = parse(from_)
    tlat, tlng = parse(to)
    if not (config.in_bbox(flat, flng) and config.in_bbox(tlat, tlng)):
        raise HTTPException(400,
                            "both points must be inside the Al Arish bbox "
                            f'{config.BBOX} (lat,lng order!)')
    return router.route(flat, flng, tlat, tlng, overview)
