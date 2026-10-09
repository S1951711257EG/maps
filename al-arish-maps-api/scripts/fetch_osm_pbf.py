#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build the Al Arish street/place index from an OpenStreetMap .osm.pbf file.

This is the offline alternative to fetch_osm_data.py (Overpass). It needs a
country-sized extract once (Egypt: https://download.geofabrik.de/africa/egypt-latest.osm.pbf,
~180 MB) and crops everything inside the Al Arish bounding box locally, so it
works even when Overpass mirrors are unreachable or rate-limited.

Output: data/osm_raw.json in an Overpass-compatible shape ({"elements": [...]}),
identical to what fetch_osm_data.py produces - build_db.py consumes either.

Two streaming passes keep RAM low:
    pass 1: remember every node inside the bbox (+ place nodes with tags)
    pass 2: keep named highways that use any of those nodes

Usage:
    python scripts/fetch_osm_pbf.py --pbf /path/to/egypt-latest.osm.pbf
    python scripts/fetch_osm_pbf.py --download        # fetch Egypt extract first
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import osmium

# (south, west, north, east) - Al Arish city + airport + nearby villages
BBOX = (30.95, 33.60, 31.30, 34.00)

EGYPT_PBF_URL = "https://download.geofabrik.de/africa/egypt-latest.osm.pbf"

ROAD_TYPES = {"motorway", "trunk", "primary", "secondary", "tertiary",
              "unclassified", "residential", "living_street", "service"}
PLACE_TYPES = {"city", "town", "village", "suburb", "neighbourhood",
               "quarter", "hamlet", "isolated_dwelling"}


def in_bbox(lat: float, lon: float) -> bool:
    s, w, n, e = BBOX
    return s <= lat <= n and w <= lon <= e


class NodePass(osmium.SimpleHandler):
    """Collect coordinates of all nodes inside the bbox (one big dict)."""

    def __init__(self):
        super().__init__()
        self.coords: dict[int, tuple[float, float]] = {}
        self.place_elements: list[dict] = []

    def node(self, n):
        if n.location.valid() and in_bbox(n.location.lat, n.location.lon):
            lat, lon = n.location.lat, n.location.lon
            self.coords[n.id] = (lat, lon)
            tags = dict(n.tags)
            if tags.get("place") in PLACE_TYPES and tags.get("name"):
                self.place_elements.append({
                    "type": "node", "id": n.id, "lat": lat, "lon": lon,
                    "tags": {k: v for k, v in tags.items() if k in (
                        "name", "name:en", "name:ar", "place")},
                })


class WayPass(osmium.SimpleHandler):
    """Keep named roads that touch the node cache."""

    def __init__(self, coords: dict):
        super().__init__()
        self.coords = coords
        self.way_elements: list[dict] = []

    def way(self, w):
        tags = dict(w.tags)
        if tags.get("name") and tags.get("highway") in ROAD_TYPES:
            geometry = []
            for nd in w.nodes:
                c = self.coords.get(nd.ref)
                if c:
                    geometry.append({"lat": c[0], "lon": c[1]})
            if len(geometry) >= 2:
                keep = {"name", "name:en", "name:ar", "highway"}
                self.way_elements.append({
                    "type": "way", "id": w.id,
                    "tags": {k: v for k, v in tags.items() if k in keep},
                    "geometry": geometry,
                })


def download_egypt(dest: Path):
    import requests
    if dest.exists() and dest.stat().st_size > 100_000_000:
        print(f"{dest} already downloaded")
        return
    print(f"Downloading {EGYPT_PBF_URL} (~180 MB)...")
    with requests.get(EGYPT_PBF_URL, stream=True, timeout=60,
                      allow_redirects=True) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    print(f"saved {dest} ({dest.stat().st_size / 1e6:.0f} MB)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pbf", default=None,
                    help="path to a country .osm.pbf extract")
    ap.add_argument("--download", action="store_true",
                    help="download the Egypt extract to data/ first")
    ap.add_argument("--out", default=None,
                    help="output path (default: <project>/data/osm_raw.json)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else root / "data" / "osm_raw.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    pbf = Path(args.pbf) if args.pbf else root / "data" / "egypt-latest.osm.pbf"
    if args.download or not pbf.exists():
        download_egypt(pbf)

    t0 = time.time()
    print("pass 1/2: scanning nodes inside the Al Arish bbox ...")
    np_ = NodePass()
    np_.apply_file(str(pbf))
    print(f"  {len(np_.coords):,} bbox nodes, "
          f"{len(np_.place_elements)} named place nodes")

    print("pass 2/2: collecting named roads ...")
    wp = WayPass(np_.coords)
    wp.apply_file(str(pbf))
    print(f"  {len(wp.way_elements)} named roads")

    out.write_text(json.dumps(
        {"elements": wp.way_elements + np_.place_elements},
        ensure_ascii=False), encoding="utf-8")
    print(f"saved -> {out}  ({out.stat().st_size / 1e6:.1f} MB) "
          f"in {time.time() - t0:.0f}s total")


if __name__ == "__main__":
    main()
