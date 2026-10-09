"""Local-first geocoder for Al Arish.

Search order:
    1. SQLite gazetteer - 149+ scraped Google-Maps POIs (shops, restaurants,
       pharmacies...), OSM named roads and OSM place nodes. This is instant,
       free and works offline.
    2. Nominatim fallback (optional, free, rate-limited to 1 req/s) for
       queries the local index can't answer; every response is cached in
       SQLite so repeat queries never touch the network again.

Why POI-first? In Al Arish, OSM has almost no street names (verified against
Nominatim), but the scraped POI list is rich - and a delivery customer types
"التوليب", "صيدلية العناني" or "Basata" far more often than a street address.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time

import requests

from . import config

# --- text normalisation ------------------------------------------------------

_TATWEEL = "\u0640"
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670]")
_CHAR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه"})

_last_nominatim_call = 0.0
_nom_lock = threading.Lock()


def normalize(text: str) -> str:
    t = (text or "").strip().lower()
    t = t.replace(_TATWEEL, "")
    t = _DIACRITICS.sub("", t)
    t = t.translate(_CHAR_MAP)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .,-")


def score(query: str, name: str) -> float:
    """0..1 similarity of the normalised query to one stored name form."""
    if not query or not name:
        return 0.0
    n = normalize(name)
    if not n:
        return 0.0
    if query == n:
        return 1.0
    if n.startswith(query):
        return 0.9
    if query in n:
        return 0.78
    q_tokens, n_tokens = set(query.split()), set(n.split())
    if q_tokens and n_tokens:
        ratio = len(q_tokens & n_tokens) / len(q_tokens)
        if ratio >= 0.5:
            return 0.5 + 0.38 * ratio
    return 0.0


def _best_score(query: str, *names) -> float:
    return max((score(query, n) for n in names if n), default=0.0)


# --- local search -------------------------------------------------------------

def search_local(con: sqlite3.Connection, query: str, limit: int = 8) -> list:
    q = normalize(query)
    if not q:
        return []
    hits: list[dict] = []

    for name, name_en, name_ar, cat, lat, lng, reviews, phone, addr in con.execute(
            "SELECT name,name_en,name_ar,category,lat,lng,reviews,phone,address "
            "FROM places"):
        s = _best_score(q, name, name_en, name_ar,
                        f"{cat} {name}" if cat else "")
        if s >= 0.55 and lat and lng:
            hits.append({"name": name, "kind": "poi", "category": cat,
                         "lat": lat, "lng": lng, "score": round(s, 3),
                         "phone": phone, "address": addr,
                         "source": "local_poi"})

    for name, name_en, name_ar, kind, lat, lng, pts in con.execute(
            "SELECT name,name_en,name_ar,kind,lat,lng,points FROM streets"):
        s = _best_score(q, name, name_en, name_ar)
        if s >= 0.55:
            hits.append({"name": name, "kind": "street", "road_type": kind,
                         "lat": lat, "lng": lng, "score": round(s, 2),
                         "source": "osm"})

    for name, name_en, name_ar, kind, lat, lng in con.execute(
            "SELECT name,name_en,name_ar,kind,lat,lng FROM named_places"):
        s = _best_score(q, name, name_en, name_ar)
        if s >= 0.55:
            hits.append({"name": name, "kind": "place", "place_type": kind,
                         "lat": lat, "lng": lng, "score": round(s, 2),
                         "source": "osm"})

    hits.sort(key=lambda h: (h["score"], 0 if h["kind"] == "poi" else 1),
              reverse=True)
    return hits[:limit]


# --- Nominatim fallback -------------------------------------------------------

def search_nominatim(con: sqlite3.Connection, query: str, limit: int = 8) -> list:
    """Free OpenStreetMap geocoder, 1 req/s max, responses cached forever."""
    global _last_nominatim_call

    cache_key = f"{normalize(query)}|nominatim"
    row = con.execute("SELECT response FROM geocode_cache WHERE q=?",
                      (cache_key,)).fetchone()
    if row:
        return json.loads(row[0])[:limit]

    b = config.BBOX
    with _nom_lock:                     # be a polite citizen: max 1 req/s
        wait = 1.1 - (time.time() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        _last_nominatim_call = time.time()
        r = requests.get(config.NOMINATIM_URL, params={
            "q": query, "format": "jsonv2", "limit": 8,
            "countrycodes": "eg",
            "viewbox": f'{b["west"]},{b["south"]},{b["east"]},{b["north"]}',
            "bounded": 1, "accept-language": "ar,en",
        }, headers={"User-Agent": config.USER_AGENT}, timeout=12)
        r.raise_for_status()
        raw = r.json()

    results = []
    for item in raw:
        try:
            lat, lng = float(item["lat"]), float(item["lon"])
        except (KeyError, ValueError):
            continue
        if not config.in_bbox(lat, lng):
            continue
        results.append({
            "name": item.get("name") or item.get("display_name", "")[:80],
            "display_name": item.get("display_name", ""),
            "kind": "nominatim", "type": item.get("type", ""),
            "lat": lat, "lng": lng, "score": 0.5, "source": "nominatim",
        })

    con.execute("INSERT OR REPLACE INTO geocode_cache(q,response) VALUES (?,?)",
                (cache_key, json.dumps(results, ensure_ascii=False)))
    con.commit()
    return results[:limit]


def geocode(con: sqlite3.Connection, query: str, limit: int = 8) -> dict:
    local = search_local(con, query, limit=limit)
    online: list = []
    if len(local) < limit and config.ALLOW_ONLINE_GEOCODE:
        try:
            online = search_nominatim(con, query, limit=limit)
        except Exception as exc:                    # noqa: BLE001
            online = []
            print(f"[geocode] nominatim unavailable: {exc}")
    return {"query": query, "count": len(local) + len(online),
            "results": local + online}
