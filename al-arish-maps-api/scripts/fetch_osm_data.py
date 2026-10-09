#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Fetch Al Arish street + neighbourhood data from OpenStreetMap (Overpass API).

Downloads every named road inside the Al Arish bounding box plus place nodes
(neighbourhoods, villages, ...), saving the raw JSON to data/osm_raw.json.
build_db.py turns that file into the geocoder's street index in SQLite.

Usage:
    python scripts/fetch_osm_data.py            # from the project root
    python scripts/fetch_osm_data.py --force    # ignore an existing file

No API key needed - Overpass is a free community service; the query is
one-shot per setup (be gentle, don't re-run it in a loop).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

# (south, west, north, east) - covers Al Arish city, airport and nearby
# villages (Beshtina, Masaid, ...). Order matters: Overpass bbox format.
BBOX = (30.95, 33.60, 31.30, 34.00)

# Public Overpass mirrors; the first one that answers wins.
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

ROAD_FILTER = ('way["highway"~"^(motorway|trunk|primary|secondary|tertiary|'
               'unclassified|residential|living_street|service)$"]["name"]')

QUERY = f"""
[out:json][timeout:240];
(
  {ROAD_FILTER}({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
  node["place"]({BBOX[0]},{BBOX[1]},{BBOX[2]},{BBOX[3]});
);
out geom;
"""


def fetch() -> dict:
    last_err = None
    for mirror in MIRRORS:
        try:
            print(f"Querying {mirror} ...")
            t0 = time.time()
            r = requests.post(mirror, data={"data": QUERY},
                              timeout=300,
                              headers={"User-Agent": "al-arish-maps-api/1.0"})
            r.raise_for_status()
            data = r.json()
            n_ways = sum(1 for e in data.get("elements", [])
                         if e.get("type") == "way")
            n_nodes = sum(1 for e in data.get("elements", [])
                          if e.get("type") == "node")
            print(f"  got {n_ways} named roads + {n_nodes} place nodes "
                  f"in {time.time() - t0:.0f}s")
            return data
        except Exception as exc:               # noqa: BLE001
            print(f"  !! mirror failed: {exc}")
            last_err = exc
            time.sleep(2)
    sys.exit(f"All Overpass mirrors failed: {last_err}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=None,
                    help="output path (default: <project>/data/osm_raw.json)")
    ap.add_argument("--force", action="store_true",
                    help="re-download even if the file already exists")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    out = Path(args.out) if args.out else root / "data" / "osm_raw.json"
    out.parent.mkdir(parents=True, exist_ok=True)

    if out.exists() and not args.force:
        print(f"{out} already exists - keeping it (use --force to refresh)")
        return

    data = fetch()
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"saved -> {out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
