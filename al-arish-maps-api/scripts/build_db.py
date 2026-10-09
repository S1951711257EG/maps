#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build data/al_arish_maps.sqlite3 - the local database behind the API.

Inputs (already in data/):
    scraped_part*_raw.jsonl   Google-Maps POI records (from the maps scraper)
    osm_raw.json              named roads + place nodes (Overpass or PBF crop)

Tables:
    places          scraped POIs: name, category, lat/lng, phone, address...
    streets         named OSM roads with downsampled geometry
    named_places    OSM place nodes (neighbourhoods, villages, ...)
    geocode_cache   Nominatim responses, so repeat queries never re-hit the net

Usage (from the project root):
    python scripts/build_db.py
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "al_arish_maps.sqlite3"

SCHEMA = """
CREATE TABLE IF NOT EXISTS places (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    name_en   TEXT,
    name_ar   TEXT,
    category  TEXT,
    address   TEXT,
    phone     TEXT,
    website   TEXT,
    email     TEXT,
    rating    TEXT,
    reviews   INTEGER DEFAULT 0,
    plus_code TEXT,
    lat       REAL,
    lng       REAL,
    maps_url  TEXT,
    source    TEXT DEFAULT 'google_scrape',
    added_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_places_name ON places(name);

CREATE TABLE IF NOT EXISTS streets (
    id      INTEGER PRIMARY KEY,
    osm_id  INTEGER UNIQUE,
    name    TEXT,
    name_en TEXT,
    name_ar TEXT,
    kind    TEXT,
    lat     REAL,
    lng     REAL,
    points  TEXT
);
CREATE INDEX IF NOT EXISTS idx_streets_name ON streets(name);

CREATE TABLE IF NOT EXISTS named_places (
    id      INTEGER PRIMARY KEY,
    osm_id  INTEGER UNIQUE,
    name    TEXT,
    name_en TEXT,
    name_ar TEXT,
    kind    TEXT,
    lat     REAL,
    lng     REAL
);
CREATE INDEX IF NOT EXISTS idx_named_places_name ON named_places(name);

CREATE TABLE IF NOT EXISTS geocode_cache (
    q          TEXT PRIMARY KEY,
    response   TEXT NOT NULL,
    fetched_at TEXT DEFAULT (datetime('now'))
);
"""


def split_name(name: str) -> tuple[str, str, str]:
    """A Google Maps name often mixes Latin + Arabic; keep both forms."""
    name = (name or "").strip()
    ar_chars = sum("\u0600" <= c <= "\u06FF" for c in name)
    if ar_chars > len(name) / 2:
        return name, "", name
    return name, name, ""


def load_places(con: sqlite3.Connection, data_dir: Path) -> int:
    raw_files = sorted(data_dir.glob("scraped_part*_raw.jsonl"))
    if not raw_files:
        print("  !! no scraped_part*_raw.jsonl found - run the maps scraper first")
        return 0
    seen, rows = set(), []
    for path in raw_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            name = (r.get("name") or "").strip()
            if not name:
                continue
            try:
                lat = float(r.get("latitude") or 0) or None
                lng = float(r.get("longitude") or 0) or None
            except ValueError:
                lat = lng = None
            phone = (r.get("phone") or "").strip()
            key = (name.lower(), phone or f"{lat},{lng}")
            if key in seen:
                continue
            seen.add(key)
            base, en, ar = split_name(name)
            rows.append((
                base, en, ar, r.get("category") or "", r.get("address") or "",
                phone, r.get("website") or "", r.get("email") or "",
                r.get("rating") or "",
                int(re.sub(r"\D", "", r.get("reviews") or "") or 0),
                r.get("plus_code") or "", lat, lng, r.get("maps_url") or "",
            ))
    con.executemany(
        "INSERT INTO places(name,name_en,name_ar,category,address,phone,"
        "website,email,rating,reviews,plus_code,lat,lng,maps_url) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def load_streets(con: sqlite3.Connection, data_dir: Path) -> int:
    path = data_dir / "osm_raw.json"
    if not path.exists():
        print("  !! osm_raw.json missing - run fetch_osm_data.py or fetch_osm_pbf.py")
        return 0
    elements = json.loads(path.read_text(encoding="utf-8")).get("elements", [])
    n_streets = n_places = 0
    for el in elements:
        tags = el.get("tags", {})
        if el.get("type") == "way" and tags.get("name"):
            pts = el.get("geometry", [])
            if len(pts) < 2:
                continue
            # downsample long geometries (keep <= 300 points)
            step = max(1, len(pts) // 300)
            pts = pts[::step]
            mid = pts[len(pts) // 2]
            base, en, ar = split_name(tags["name"])
            con.execute(
                "INSERT OR REPLACE INTO streets(osm_id,name,name_en,name_ar,"
                "kind,lat,lng,points) VALUES (?,?,?,?,?,?,?,?)",
                (el["id"], base, en, ar, tags.get("highway") or "",
                 mid["lat"], mid["lon"], json.dumps(pts, ensure_ascii=False)))
            n_streets += 1
        elif el.get("type") == "node" and tags.get("name") and el.get("lat"):
            base, en, ar = split_name(tags["name"])
            con.execute(
                "INSERT OR REPLACE INTO named_places(osm_id,name,name_en,"
                "name_ar,kind,lat,lng) VALUES (?,?,?,?,?,?,?)",
                (el["id"], base, en, ar, tags.get("place") or "yes",
                 el["lat"], el["lon"]))
            n_places += 1
    print(f"  streets: {n_streets}, named places: {n_places}")
    return n_streets + n_places


def main():
    data_dir = ROOT / "data"
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript(SCHEMA)
    print("loading scraped POIs ...")
    n1 = load_places(con, data_dir)
    print(f"  places: {n1}")
    print("loading OSM streets/places ...")
    n2 = load_streets(con, data_dir)
    con.commit()
    n_cache = con.execute("SELECT COUNT(*) FROM geocode_cache").fetchone()[0]
    con.close()
    print(f"\nOK -> {DB}")
    print(f"   places={n1}  streets+places={n2}  geocode_cache={n_cache}")


if __name__ == "__main__":
    main()
