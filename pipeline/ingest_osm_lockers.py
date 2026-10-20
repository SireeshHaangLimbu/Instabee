"""Download OSM parcel lockers per Swedish county and load the lockers table."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from pipeline.common import MANUAL, http, register_source
from pipeline.lockers import manual_to_frame, merge_manual, osm_elements_to_frame

SOURCE = "osm_lockers"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
COUNTIES = [
    "SE-AB", "SE-AC", "SE-BD", "SE-C", "SE-D", "SE-E", "SE-F", "SE-G", "SE-H", "SE-I", "SE-K",
    "SE-M", "SE-N", "SE-O", "SE-S", "SE-T", "SE-U", "SE-W", "SE-X", "SE-Y", "SE-Z",
]
QUERY = (
    '[out:json][timeout:150];area["ISO3166-2"="{code}"]->.a;'
    'nwr["amenity"="parcel_locker"](area.a);out tags center;'
)
OWN_LOCKERS_CSV = MANUAL / "own_lockers.csv"
MANUAL_COLUMNS = ["brand", "name", "lat", "lon", "address", "note"]


def download(snapshot: Path) -> None:
    failed = []
    for code in COUNTIES:
        out = snapshot / f"{code}.json"
        if out.exists():
            continue
        try:
            payload = http("POST", OVERPASS_URL, data={"data": QUERY.format(code=code)}, timeout=200).json()
            if "error" in payload.get("remark", "").lower():
                raise RuntimeError(payload["remark"])
            out.write_text(json.dumps(payload), encoding="utf-8")
            print(f"  {code}: {len(payload['elements'])} lockers")
        except Exception as exc:  # report every failed county, then fail the step
            failed.append(f"{code} ({exc})")
        time.sleep(3)
    if failed:
        raise RuntimeError("Overpass failed for: " + "; ".join(failed))


def load(con, snapshot: Path) -> None:
    date = snapshot.name
    elements = []
    for path in sorted(snapshot.glob("SE-*.json")):
        elements += json.loads(path.read_text(encoding="utf-8"))["elements"]
    missing = [c for c in COUNTIES if not (snapshot / f"{c}.json").exists()]
    if missing:
        raise RuntimeError(f"Snapshot {snapshot} is missing counties: {missing}")
    osm = osm_elements_to_frame(elements, date)
    manual_raw = (
        pd.read_csv(OWN_LOCKERS_CSV) if OWN_LOCKERS_CSV.exists() else pd.DataFrame(columns=MANUAL_COLUMNS)
    )
    manual = manual_to_frame(manual_raw, date)
    lockers = merge_manual(osm, manual)
    con.register("lockers_df", lockers)
    con.execute(
        """CREATE OR REPLACE TABLE lockers AS
        SELECT * EXCLUDE (snapshot_date), CAST(snapshot_date AS DATE) AS snapshot_date,
               ST_Transform(ST_Point(lon, lat), 'EPSG:4326', 'EPSG:3006', always_xy := true) AS geom
        FROM lockers_df"""
    )
    con.unregister("lockers_df")
    register_source(
        con, "lockers", "OpenStreetMap amenity=parcel_locker (Overpass API)", OVERPASS_URL, "ODbL 1.0", date,
        notes="Queried per county. OSM holds only part of Instabee's network, so Instabee coverage is a lower bound.",
    )
    if not manual.empty:
        register_source(
            con, "lockers", "Manual own-locker list", "data/manual/own_lockers.csv", "User-collected", date,
            notes=f"{len(manual)} manual lockers replace OSM lockers of the same network within 20 m.",
        )
    print(f"  lockers: {len(lockers)} rows ({len(manual)} manual)")
