"""Hybrid routing: self-hosted OSRM road routing with a straight-line
estimate fallback, so the API keeps answering even when the router is down.

OSRM  -> exact driving distance/ETA/geometry along Al Arish roads (free,
         self-hosted, see deploy/setup_osrm.sh).
Fallback -> haversine distance x CIRCUITRY_FACTOR (roads are never straight)
            at CITY_SPEED_KMH. Good enough for fee estimation; flag it via
            "source": "estimate" in the response so the app can decide.
"""

from __future__ import annotations

import math

import requests

from . import config


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _estimate(lat1, lng1, lat2, lng2) -> dict:
    d = haversine_m(lat1, lng1, lat2, lng2) * config.CIRCUITRY_FACTOR
    seconds = d / (config.CITY_SPEED_KMH / 3.6)
    return {"distance_m": round(d), "duration_s": round(seconds),
            "source": "estimate", "geometry": None}


def _osrm(lat1, lng1, lat2, lng2, overview: bool):
    url = (f"{config.OSRM_URL}/route/v1/driving/"
           f"{lng1},{lat1};{lng2},{lat2}")
    params = {"overview": "full" if overview else "false",
              "geometries": "geojson", "steps": "false"}
    r = requests.get(url, params=params, timeout=config.OSRM_TIMEOUT_S)
    data = r.json()
    if data.get("code") != "Ok" or not data.get("routes"):
        raise ValueError(f"osrm returned {data.get('code')}")
    route = data["routes"][0]
    geometry = None
    if overview and route.get("geometry", {}).get("coordinates"):
        geometry = route["geometry"]["coordinates"]      # [[lng, lat], ...]
    return {"distance_m": round(route["distance"]),
            "duration_s": round(route["duration"]),
            "source": "osrm", "geometry": geometry}


def route(flat: float, flng: float, tlat: float, tlng: float,
          overview: bool = False) -> dict:
    try:
        out = _osrm(flat, flng, tlat, tlng, overview)
    except Exception:                                   # noqa: BLE001
        out = _estimate(flat, flng, tlat, tlng)
    out["from"] = {"lat": flat, "lng": flng}
    out["to"] = {"lat": tlat, "lng": tlng}
    return out
