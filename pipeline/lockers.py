"""Locker network normalisation and manual-list merging (pure functions)."""

from __future__ import annotations

import math

import pandas as pd

NETWORK_KEYS = [
    ("Instabee", ("instabox", "instabee", "budbee")),
    ("PostNord", ("postnord",)),
    ("Bring", ("bring",)),
    ("DHL", ("dhl",)),
    ("DB Schenker", ("schenker",)),
    ("Early Bird", ("early bird", "earlybird")),
    ("iBoxen", ("iboxen",)),
    ("Paketbox", ("paketbox",)),
]
LOCKER_COLUMNS = [
    "locker_id", "network", "brand_raw", "operator_raw", "name",
    "lat", "lon", "source", "source_ref", "snapshot_date",
]


def normalise_network(brand: str | None, operator: str | None) -> str:
    """Brand first, then operator; in a ';' list the first known network wins."""
    for raw in (brand, operator):
        if not isinstance(raw, str) or not raw:
            continue
        for part in raw.split(";"):
            text = part.strip().lower()
            for network, keys in NETWORK_KEYS:
                if any(key in text for key in keys):
                    return network
    return "Other"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def osm_elements_to_frame(elements: list[dict], snapshot_date: str) -> pd.DataFrame:
    rows, seen = [], set()
    for el in elements:
        ref = f"{el['type']}/{el['id']}"
        if ref in seen:
            continue
        seen.add(ref)
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = el.get("tags", {})
        brand, operator = tags.get("brand"), tags.get("operator")
        rows.append({
            "locker_id": f"osm:{ref}", "network": normalise_network(brand, operator),
            "brand_raw": brand, "operator_raw": operator, "name": tags.get("name"),
            "lat": float(lat), "lon": float(lon), "source": "osm", "source_ref": ref,
            "snapshot_date": snapshot_date,
        })
    return pd.DataFrame(rows, columns=LOCKER_COLUMNS)


def manual_to_frame(manual: pd.DataFrame, snapshot_date: str) -> pd.DataFrame:
    rows = []
    for i, r in enumerate(manual.to_dict("records"), start=1):
        rows.append({
            "locker_id": f"manual:{i}", "network": normalise_network(r.get("brand"), None),
            "brand_raw": r.get("brand"), "operator_raw": None, "name": r.get("name"),
            "lat": float(r["lat"]), "lon": float(r["lon"]), "source": "manual",
            "source_ref": r.get("address") if isinstance(r.get("address"), str) else None,
            "snapshot_date": snapshot_date,
        })
    return pd.DataFrame(rows, columns=LOCKER_COLUMNS)


def merge_manual(osm: pd.DataFrame, manual: pd.DataFrame, radius_m: float = 20.0) -> pd.DataFrame:
    """Manual lockers replace OSM lockers of the same network within radius_m."""
    if manual.empty:
        return osm.reset_index(drop=True)
    keep = []
    for row in osm.itertuples(index=False):
        same = manual[manual["network"] == row.network]
        duplicate = any(
            haversine_m(row.lat, row.lon, m.lat, m.lon) <= radius_m for m in same.itertuples(index=False)
        )
        keep.append(not duplicate)
    return pd.concat([osm[keep], manual], ignore_index=True)
