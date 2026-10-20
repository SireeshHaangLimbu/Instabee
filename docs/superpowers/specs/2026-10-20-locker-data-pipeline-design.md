# Locker network data pipeline — design

Date: 2026-10-20
Status: approved in conversation (data layer). The agent and its tools are a separate, later spec.

## Goal

Build a reproducible pipeline that loads public data about Swedish parcel lockers,
population and candidate host sites into one read-only DuckDB file
(`data/lockers.duckdb`). A later LLM agent, built on the
[data-agent-workshop](https://github.com/FanisPapakonstantinou/data-agent-workshop)
template (smolagents + DuckDB), answers questions such as "where should the next 10
Instabox lockers go in Uppsala?" from this file.

Research basis: the "Locker Network AI Agent: Questions, Public Data and Build Plan" doc.

## Decisions

| Topic | Decision |
|---|---|
| Geography | All of Sweden, coarse: straight-line distances, no walking network |
| Spatial unit | H3 resolution 9 (about 0.08 km², 175 m across) |
| Storage | One DuckDB file with the `spatial` and `h3` (community) extensions |
| Instabee's own lockers | OSM as the baseline, plus an optional `data/manual/own_lockers.csv` merged in with `source='manual'` |
| LLM (later) | Anthropic API key through LiteLLM; switching to Bedrock is a model-string change |
| Not in this version | PostNord API, Trafiklab GTFS, Lantmäteriet buildings, DHL API, Budbee partner API |

## Verified source access (2026-10-20)

| Source | Endpoint | Status |
|---|---|---|
| OSM parcel lockers | Overpass `overpass-api.de` | Works; whole-Sweden queries can time out, so queries run per county |
| SCB DeSO 2025 boundaries | WFS `geodata.scb.se/geoserver/stat/wfs`, layer `stat:DeSO_2025` | Works; EPSG:3006 |
| SCB 1 km population grid | Same WFS, layer `stat:befolkning_1km_2025` | Works; total, sex and 5-year age bands |
| SCB DeSO tables | PxWeb `api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101/BE0101Y/` | Works; population by age (`FolkmDesoAldKon`), households (`HushallDesoTyp`) |
| Overture Places and Buildings | S3 `overturemaps-us-west-2`, release `2026-09-23.1` | Works; queried with DuckDB `httpfs` |
| Paketindex Q2 2026 | PDF | Works; figures entered by hand |
| All the Places | No Instabox or Budbee spider | Not used |

Completeness finding: OSM has 377 Instabox, Instabee and Budbee lockers in Sweden, and
6 Instabox plus 3 Budbee around Uppsala. The real network was already planned at about
3,000 Swedish lockers in 2020. Instabee coverage figures from this pipeline are therefore
lower bounds, and the agent must say so.

## Layout

```
pipeline/
  common.py              paths, HTTP session with User-Agent, snapshot dirs, DuckDB connect, source registry
  ingest_osm_lockers.py  Overpass per county -> lockers
  ingest_scb.py          DeSO polygons, 1 km grid, DeSO tables -> deso, pop_grid_1km
  ingest_overture.py     Places + building centroids for Sweden -> hosts, buildings
  load_manual.py         data/manual/*.csv -> market_stats, manual lockers
  build_hex_features.py  derived hex_features table
  checks.py              data checks, exit non-zero on failure
  run_all.py             runs every step in order; --skip-download reuses the latest snapshots
data/
  raw/<source>/<YYYY-MM-DD>/   raw downloads, never edited
  manual/own_lockers.csv       header only at first: brand,name,lat,lon,address,note
  manual/market_stats.csv      hand-entered national figures with source URL and page
  lockers.duckdb               output
tests/                         pytest unit tests on tiny made-up inputs
```

Every ingest step saves its raw download first and then loads from disk, so a rebuild
needs no network access.

## Tables

All coordinates are WGS84 longitude and latitude, plus a `geom` column in EPSG:3006
(SWEREF99 TM, metres) for distance work.

**lockers** — one row per physical locker.
`locker_id, network, brand_raw, operator_raw, name, lat, lon, geom, source, source_ref, snapshot_date`.
`network` is normalised: Instabox, Instabee and Budbee variants in brand or operator → `Instabee`;
PostNord/Postnord → `PostNord`; also `Bring`, `DHL`, `DB Schenker`, `Early Bird`, `iBoxen`,
`Paketbox`, everything else → `Other`. A semicolon list such as `Instabee;Pressbyrån` takes the
first known network. Rows from `own_lockers.csv` use `source='manual'`. A manual locker within
20 m of an OSM locker of the same network replaces it.

**deso** — `deso_code, kommun_code, kommun_name, lan_code, geom, area_km2, population,
pop_0_19, pop_20_34, pop_35_64, pop_65_plus, households`. `kommun_code` is the first four
characters of the DeSO code.

**pop_grid_1km** — `cell_id, geom, population` plus the age-band columns as delivered.

**hosts** — `host_id, overture_id, name, brand, category, host_type, lat, lon, geom, confidence`.
`host_type` is one of `supermarket`, `convenience`, `fuel`, `pharmacy`, `mall`, or `pickup_point`
(Overture `post_office`: competitor pickup points, not host candidates), mapped from `taxonomy.primary`. Rows with confidence below 0.5 are dropped.

**buildings** — `lat, lon, footprint_m2` (centroids only), used to weight population.

**hex_features** — one row per populated H3 resolution-9 cell.
`h3, lat, lon, kommun_code, kommun_name, deso_code, pop_est, pop_0_19_est, pop_20_34_est, pop_35_64_est,
pop_65_plus_est, dist_instabee_m, dist_any_competitor_m, dist_postnord_m, lockers_300m,
lockers_500m, instabee_300m, instabee_500m, hosts_300m, supermarkets_300m, fuel_300m,
pickup_points_300m`.
Population per cell: each 1 km cell's population and age bands are split across the hexes whose
centre lies in it, weighted by building footprint area; with no buildings, the split is even.
Distances use EPSG:3006 from the hex centre and are capped at 5,000 m (stored as 5000).

**market_stats** — `metric, value, unit, period, geography, source_title, source_url, page`.
Holds national parcels per year, the delivery mix (23% lockers, 40% pickup points, 37% home in
June 2026), monthly seasonality and the second-hand parcel volume, all from Paketindex Q2 2026.

**sources** — `table_name, source_title, url, licence, snapshot_date, row_count, notes`, written
by every step. The agent cites from this table.

## Checks (`checks.py`)

1. Every table exists and is non-empty; `hex_features` population totals within 1% of the 1 km grid total.
2. Lockers per network compared with the previous snapshot; a drop of more than 20% fails.
3. Duplicate lockers: same network within 20 m, reported as a count.
4. Source age: any snapshot older than 45 days for lockers or 400 days for SCB raises a warning.
5. Sanity: Sweden population in `deso` between 10 and 11 million.

## Tests

pytest, offline, on tiny inputs: network normalisation, manual-locker merge and deduplication,
population split (weights sum to the cell total; the no-building fallback), distance capping,
and the radius counts on a hand-built three-hex example.

## Error handling

HTTP calls retry three times with backoff. A failed county Overpass query is retried and then
reported by name; the run fails rather than loading a partial locker table. Overture is the
largest download: if the building-centroid extract fails or takes longer than 30 minutes, the
pipeline falls back to an even split and records that in `sources.notes`.

## Licences

OSM data is ODbL and Overture is CDLA-Permissive 2.0, Apache 2.0 or CC0 depending on the
dataset. SCB open geodata is used under SCB's terms. The database is for private analysis. It
is not republished, so ODbL share-alike is not triggered.
