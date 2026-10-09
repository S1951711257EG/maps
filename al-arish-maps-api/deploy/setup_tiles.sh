#!/usr/bin/env bash
# Build the self-hosted Al Arish vector-tile set + map style (tilemaker +
# tileserver-gl). Produces data/tiles/{al-arish.mbtiles, style.json,
# sprite files, config.json} consumed by the `tiles` service in
# docker-compose.yml at http://localhost:8090
#
# Needs: docker + the Al Arish OSM extract created by setup_osrm.sh
# (data/osrm/al-arish.osm.pbf).

set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/tiles

PBF="data/osrm/al-arish.osm.pbf"
if [ ! -f "$PBF" ]; then
    echo "missing $PBF - run deploy/setup_osrm.sh first (it downloads + crops the OSM data)"
    exit 1
fi

echo "== 1/3 convert OSM -> vector mbtiles (tilemaker) =="
docker run -t --rm -v "$PWD:/data" ghcr.io/systemed/tilemaker:master \
    "/data/${PBF#data/}" --output /data/tiles/al-arish.mbtiles

echo "== 2/3 fetch the osm-bright-gl map style (one-time) =="
cd data/tiles
[ -f style.json ] || curl -sL -o style.json \
    "https://raw.githubusercontent.com/openmaptiles/osm-bright-gl-style/master/style.json"
for f in sprite.json sprite.png sprite@2x.json sprite@2x.png; do
    [ -f "$f" ] || curl -sL -o "$f" \
        "https://maputnik.github.io/osm-bright-gl-style/sprites/$f"
done

echo "== 3/3 point the style at the local tile server =="
# tileserver-gl serves the mbtiles as TileJSON on /data/al-arish.json
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path("style.json")
style = json.loads(p.read_text(encoding="utf-8"))
# rewire every vector source to the local tileserver-gl TileJSON
for src in style.get("sources", {}).values():
    if "url" in src and "openmaptiles" not in str(src.get("url", "")):
        pass
    if "url" in src:
        src["url"] = "http://localhost:8090/data/al-arish.json"
style.setdefault("name", "Al Arish")
p.write_text(json.dumps(style, indent=2), encoding="utf-8")
print("style sources rewired -> http://localhost:8090/data/al-arish.json")
EOF

cat > config.json <<'JSON'
{
  "options": {
    "paths": {
      "root": "/data",
      "styles": "/data",
      "mbtiles": "/data",
      "fonts": "/data/fonts"
    }
  },
  "styles": {
    "al-arish": { "style": "/data/style.json" }
  },
  "data": {
    "al-arish": "/data/al-arish.mbtiles"
  }
}
JSON

echo
echo "Done. Start the tile server with:"
echo "  docker compose -f ../../deploy/docker-compose.yml up -d tiles"
echo "Style URL for your app:  http://localhost:8090/styles/al-arish/style.json"
echo "Glyphs in style.json use the free fonts.openmaptiles.org CDN; to go"
echo "100% offline drop Noto Sans .pbf fonts into data/tiles/fonts/ and"
echo "point the style's 'glyphs' at http://localhost:8090/fonts/{fontstack}/{range}.pbf"
