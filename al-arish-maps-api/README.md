# Al Arish Maps API

A tiny **self-hosted mapping service for one city only** - Al Arish (العريش),
North Sinai, Egypt - built so a delivery app never has to pay Google Maps
per request.

| Capability | How it works | Cost |
|---|---|---|
| **Geocoding** (`/geocode`) | Local SQLite gazetteer: 150+ real Al Arish POIs scraped from Google Maps (shops, restaurants, pharmacies, markets...) + OSM roads/places. Free Nominatim fallback with permanent caching. | free, mostly offline |
| **Route + ETA** (`/route`) | Hybrid: self-hosted OSRM road router when it's up, straight-line estimate (haversine x 1.4 at 30 km/h) when it's down. The response always says which one answered. | free |
| **Map tiles** (self-host) | tilemaker + tileserver-gl serve vector tiles of Al Arish only. | free |
| **Own landmarks** (`POST /places`) | Add saved addresses / hubs your app collects; instantly geocodable. | free |

Why POI-first geocoding? OSM (and even Nominatim) has almost **no street names
for Al Arish** - verified during development. But the scraped POI database is
rich, and delivery customers type shop names ("التوليب", "صيدلية العناني",
"Basata") far more often than street addresses anyway.

## Project layout

```
al-arish-maps-api/
├── app/                  FastAPI service
│   ├── main.py           endpoints + auth
│   ├── geocoder.py       local-first geocoding + Nominatim fallback/cache
│   ├── router.py         hybrid OSRM / straight-line routing
│   └── config.py         env-var configuration
├── scripts/
│   ├── fetch_osm_data.py Al Arish streets via Overpass (online path)
│   ├── fetch_osm_pbf.py  ...or offline: crop from a country .osm.pbf (pyosmium)
│   └── build_db.py       build data/al_arish_maps.sqlite3
├── deploy/
│   ├── docker-compose.yml  api + osrm + tiles, one command
│   ├── setup_osrm.sh       Egypt extract -> Al Arish road router
│   ├── setup_tiles.sh      Al Arish vector tiles + map style
│   └── Dockerfile
├── data/                 SQLite DB + raw data (committed, ready to run)
├── requirements.txt
└── run.sh
```

## Quickstart - API only (your own PC, no Docker)

```bash
cd al-arish-maps-api
python3 -m pip install -r requirements.txt
./run.sh                       # Windows: python -m uvicorn app.main:app --port 8000
```

Open http://localhost:8000/docs for interactive docs. That's it - geocoding
and estimate-routing work immediately (the committed SQLite DB is ready).

## Full stack (Docker) - road routing + self-hosted tiles

```bash
bash deploy/setup_osrm.sh      # ~5 min: Egypt extract -> Al Arish router
bash deploy/setup_tiles.sh     # Al Arish vector tiles + style
docker compose -f deploy/docker-compose.yml up -d --build
```

| service | URL |
|---|---|
| this API | http://localhost:8000 |
| OSRM router | http://localhost:5000 |
| tile server (tileserver-gl viewer) | http://localhost:8090 |

The API keeps working even if OSRM/tiles are down (estimates + SQLite).

## Endpoints

### `GET /geocode?q=...&limit=8`

```bash
curl "http://localhost:8000/geocode?q=tulip"
curl "http://localhost:8000/geocode?q=صيدلية"          # Arabic works
curl "http://localhost:8000/geocode?q=airport"          # -> Nominatim fallback
```

```json
{
  "query": "tulip", "count": 1,
  "results": [
    { "name": "Al TULIP", "kind": "poi", "category": "restaurant",
      "lat": 31.13907, "lng": 33.79427, "score": 0.78,
      "phone": "+201015558777", "source": "local_poi" }
  ]
}
```

### `GET /route?from=lat,lng&to=lat,lng&overview=false`

```bash
curl "http://localhost:8000/route?from=31.1321,33.8033&to=31.1390,33.7943&overview=true"
```

```json
{ "distance_m": 1610, "duration_s": 193, "source": "estimate",
  "geometry": null, "from": {...}, "to": {...} }
```

With OSRM running, `source` becomes `"osrm"` and `geometry` carries the
road path as `[[lng,lat], ...]` - draw it directly on your map.

### `POST /places`

```bash
curl -X POST http://localhost:8000/places -H "Content-Type: application/json" \
  -d '{"name":"My Delivery Hub","lat":31.135,"lng":33.80,"category":"hub"}'
```

### `GET /health` · `GET /config`

`/config` returns center/bbox plus the tile URLs your frontend needs:

```json
{ "tiles": { "style_url": "http://localhost:8090/styles/al-arish/style.json",
             "vector_pbf_url": "http://localhost:8090/data/al-arish/{z}/{x}/{y}.pbf" } }
```

## Using it from your delivery app (JavaScript)

```js
const API = "http://localhost:8000";            // or http://your-lan-ip:8000

// search box -> suggestions
const geo = await fetch(`${API}/geocode?q=${encodeURIComponent(text)}`).then(r => r.json());

// delivery fee / ETA
const r = await fetch(`${API}/route?from=${shopLat},${shopLng}&to=${custLat},${custLng}`).then(r => r.json());
const fee = base + perKm * r.distance_m / 1000;
const eta = r.duration_s / 60;

// MapLibre GL with your own tiles
const map = new maplibregl.Map({
  container: "map",
  style: "http://localhost:8090/styles/al-arish/style.json",
  center: [33.8033, 31.1321], zoom: 12.5
});
```

## Configuration (environment variables)

| var | default | meaning |
|---|---|---|
| `OSRM_URL` | `http://localhost:5000` | router address (`http://osrm:5000` inside compose) |
| `OSRM_TIMEOUT_S` | `2.5` | give up on OSRM after this long -> estimate |
| `CITY_SPEED_KMH` | `30` | average speed for ETA estimates |
| `CIRCUITRY_FACTOR` | `1.4` | straight-line -> road-distance multiplier |
| `ALLOW_ONLINE_GEOCODE` | `1` | set `0` to disable the Nominatim fallback (fully offline) |
| `API_KEY` | *(empty)* | set it to require `X-API-Key: <key>` on every request |
| `TILE_STYLE_URL` / `TILE_PBF_URL` | localhost:8090 | tile endpoints exposed via `/config` |
| `DB_PATH` | `data/al_arish_maps.sqlite3` | gazetteer location |

## Refreshing / extending the data

```bash
# more POIs: use the maps repo's scraper, then drop the *_raw.jsonl into data/
python scripts/fetch_osm_data.py          # refresh OSM streets (online)
python scripts/fetch_osm_pbf.py --download # ...or offline from the Egypt extract
python scripts/build_db.py                # rebuild SQLite
```

Scraped POIs carry Google Maps data (names, phones, ratings) and remain the
backbone of local search; OSM data is ODbL-licensed (fine for commercial apps
with attribution).

## Honest notes

- Al Arish street-name coverage in OSM is thin, so text geocoding is
  strongest for **POI/landmark names**; for everything else, delivery apps in
  such cities normally pin locations on the map (`POST /places` makes saved
  pins searchable).
- The straight-line estimate assumes roads are ~1.4x longer than the crow
  flies - fine for fee brackets, not for turn-by-turn (that's what OSRM is for).
- Public Nominatim is rate-limited (1 req/s enforced here) and every answer
  is cached; set `ALLOW_ONLINE_GEOCODE=0` if you want zero external calls.
