"""Download Overture places (host candidates) and building centroids for Sweden."""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb

from pipeline.common import SWEDEN_BBOX, load_extensions, register_source

SOURCE = "overture"
RELEASE = "2026-09-23.1"
BASE = f"s3://overturemaps-us-west-2/release/{RELEASE}"
HOST_TYPES = {
    "supermarket": ("supermarket", "grocery_store", "organic_grocery_store"),
    "convenience": ("convenience_store",),
    "fuel": ("gas_station",),
    "pharmacy": ("pharmacy", "drugstore"),
    "mall": ("shopping_mall",),
    "pickup_point": ("post_office",),
}
NON_RESIDENTIAL = (
    "agricultural", "civic", "commercial", "education", "entertainment", "industrial",
    "medical", "military", "outbuilding", "religious", "service", "transportation",
)
BUILDINGS_TIMEOUT_S = 1800


def _remote() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    load_extensions(con)
    con.execute("INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    return con


def _bbox_filter() -> str:
    xmin, ymin, xmax, ymax = SWEDEN_BBOX
    return f"bbox.xmin >= {xmin} AND bbox.xmax <= {xmax} AND bbox.ymin >= {ymin} AND bbox.ymax <= {ymax}"


def _host_type_sql() -> str:
    cases = " ".join(
        f"WHEN taxonomy.primary IN ({', '.join(repr(c) for c in cats)}) THEN '{host_type}'"
        for host_type, cats in HOST_TYPES.items()
    )
    return f"CASE {cases} WHEN basic_category IN ('gas_station', 'fueling_station') THEN 'fuel' END"


def download_places(snapshot: Path) -> None:
    out, tmp = snapshot / "places.parquet", snapshot / "places.tmp.parquet"
    if out.exists():
        return
    _remote().execute(
        f"""COPY (
            SELECT id AS overture_id, names.primary AS name, brand.names.primary AS brand,
                   taxonomy.primary AS category, {_host_type_sql()} AS host_type, confidence,
                   ST_X(geometry) AS lon, ST_Y(geometry) AS lat
            FROM read_parquet('{BASE}/theme=places/type=place/*')
            WHERE {_bbox_filter()} AND confidence >= 0.5
              AND coalesce(operating_status, 'open') <> 'permanently_closed'
              AND ({_host_type_sql()}) IS NOT NULL
        ) TO '{tmp.as_posix()}' (FORMAT parquet)"""
    )
    tmp.replace(out)


def download_buildings(snapshot: Path, timeout_s: int = BUILDINGS_TIMEOUT_S) -> str:
    out, tmp = snapshot / "building_centroids.parquet", snapshot / "building_centroids.tmp.parquet"
    if out.exists():
        return "ok"
    con = _remote()
    timer = threading.Timer(timeout_s, con.interrupt)
    timer.start()
    try:
        con.execute(
            f"""COPY (
                SELECT ST_X(c) AS lon, ST_Y(c) AS lat, footprint_m2 FROM (
                    SELECT ST_Centroid(geometry) AS c,
                           ST_Area(ST_Transform(geometry, 'EPSG:4326', 'EPSG:3006', always_xy := true)) AS footprint_m2
                    FROM read_parquet('{BASE}/theme=buildings/type=building/*')
                    WHERE {_bbox_filter()}
                      AND coalesce(subtype, 'residential') NOT IN {NON_RESIDENTIAL}
                      AND NOT coalesce(is_underground, false))
                WHERE footprint_m2 >= 30
            ) TO '{tmp.as_posix()}' (FORMAT parquet)"""
        )
        tmp.replace(out)
        return "ok"
    except Exception as exc:  # fall back to an even population split
        (snapshot / "buildings_FAILED.txt").write_text(str(exc), encoding="utf-8")
        return f"failed: {exc}"
    finally:
        timer.cancel()


def download(snapshot: Path) -> None:
    download_places(snapshot)
    print("  places downloaded")
    print("  buildings:", download_buildings(snapshot))


def load(con, snapshot: Path) -> None:
    date = snapshot.name
    places = (snapshot / "places.parquet").as_posix()
    con.execute(
        f"""CREATE OR REPLACE TABLE hosts AS
        WITH p AS (
            SELECT *, ST_Transform(ST_Point(lon, lat), 'EPSG:4326', 'EPSG:3006', always_xy := true) AS geom
            FROM read_parquet('{places}'))
        SELECT row_number() OVER (ORDER BY p.overture_id) AS host_id, p.overture_id, p.name, p.brand,
               p.category, p.host_type, p.lat, p.lon, p.geom, p.confidence, d.deso_code, d.kommun_code
        FROM p JOIN deso d ON ST_Within(p.geom, d.geom)"""
    )
    register_source(con, "hosts", f"Overture Maps Places {RELEASE}", BASE, "CDLA-Permissive-2.0 / Apache-2.0 / CC0 by source", date,
                    notes="confidence >= 0.5; points outside Swedish DeSO polygons removed")
    buildings = snapshot / "building_centroids.parquet"
    if buildings.exists():
        con.execute(f"CREATE OR REPLACE TABLE buildings AS SELECT * FROM read_parquet('{buildings.as_posix()}')")
        note = "Residential or unclassified buildings >= 30 m2, centroids only"
    else:
        con.execute("CREATE OR REPLACE TABLE buildings (lat DOUBLE, lon DOUBLE, footprint_m2 DOUBLE)")
        note = "Building extract unavailable: population split evenly within each 1 km cell"
    register_source(con, "buildings", f"Overture Maps Buildings {RELEASE}", BASE, "ODbL 1.0 (OSM-derived) / CDLA by source", date, notes=note)
    print(con.execute("SELECT host_type, count(*) FROM hosts GROUP BY 1 ORDER BY 2 DESC").fetchall())
    print(con.execute("SELECT count(*) FROM buildings").fetchone(), "buildings")
