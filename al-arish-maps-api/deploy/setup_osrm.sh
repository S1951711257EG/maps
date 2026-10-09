#!/usr/bin/env bash
# Build the Al Arish road router (OSRM, car profile, MLD algorithm).
#
# Needs: docker, ~200 MB bandwidth (one-time), ~5 minutes.
# Result: data/osrm/al-arish.osrm*  -> used by the `osrm` service in
#         docker-compose.yml at http://localhost:5000
#
# The Egypt extract is only downloaded once; re-running this script refreshes
# the data (run it every few months if you want newer roads).

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/osrm
cd data/osrm

echo "== 1/4 Egypt extract from Geofabrik (one-time, ~180 MB) =="
if [ ! -f egypt-latest.osm.pbf ] || [ "$(find egypt-latest.osm.pbf -mtime +180 | wc -l)" -eq 1 ]; then
    curl -L -o egypt-latest.osm.pbf https://download.geofabrik.de/africa/egypt-latest.osm.pbf
fi

echo "== 2/4 crop Al Arish out of Egypt (osmium) =="
# bbox order for osmium: west,south,east,north
docker run -t --rm -v "$PWD:/data" osmiumtool/osmium \
    extract -b 33.60,30.95,34.00,31.30 \
    /data/egypt-latest.osm.pbf -o /data/al-arish.osm.pbf --overwrite

echo "== 3/4 osrm-extract (car profile) =="
docker run -t --rm -v "$PWD:/data" osrm/osrm-backend:latest \
    osrm-extract -p /opt/car.lua /data/al-arish.osm.pbf

echo "== 4/4 osrm-partition + osrm-customize (MLD) =="
docker run -t --rm -v "$PWD:/data" osrm/osrm-backend:latest osrm-partition /data/al-arish.osrm
docker run -t --rm -v "$PWD:/data" osrm/osrm-backend:latest osrm-customize /data/al-arish.osrm

echo
echo "Done. Start the router with:"
echo "  docker compose -f deploy/docker-compose.yml up -d osrm"
echo "Test: curl 'http://localhost:5000/route/v1/driving/33.8033,31.1321;33.7943,31.1390'"
