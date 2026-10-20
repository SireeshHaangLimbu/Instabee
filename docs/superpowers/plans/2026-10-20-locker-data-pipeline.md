# Locker Data Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `data/lockers.duckdb`: Swedish parcel lockers, population, host sites and market figures on an H3 resolution-9 grid, rebuilt by one command.

**Architecture:** One module per source downloads a dated raw snapshot into `data/raw/<source>/<date>/`, then loads it into DuckDB. Pure logic (network normalisation, manual merge, age bands, PxWeb parsing) lives in small modules with offline unit tests. `hex_features.build()` is pure SQL over the loaded tables and is tested on a tiny in-memory database.

**Tech Stack:** Python 3.11, DuckDB 1.5.5 with `spatial`, `h3` (community) and `httpfs`, pandas 3.0.5, requests 2.34.2, pytest 8.4.2.

Spec: `docs/superpowers/specs/2026-10-20-locker-data-pipeline-design.md`

## Global Constraints

- Python 3.11; virtual environment in `.venv`; run modules from the repo root as `python -m pipeline.<name>`.
- Versions pinned to match the workshop template: `duckdb==1.5.5`, `pandas==3.0.5`, `numpy==2.4.6`, `requests==2.34.2`.
- H3 resolution 9. Distances in EPSG:3006 metres, capped at 5,000 m.
- All HTTP requests send the User-Agent `instabee-locker-agent/0.1 (public-data research pipeline)`; no personal data in requests.
- Raw downloads are never edited; every load step reads from disk.
- Instabox, Instabee and Budbee all normalise to network `Instabee`.
- Overture release `2026-09-23.1`; SCB layers `stat:DeSO_2025` and `stat:befolkning_1km_2025`; DeSO PxWeb codes end in `_DeSO2025`.
- SCB JSON is read with `encoding="utf-8-sig"` (PxWeb responses start with a BOM).

## File map

| File | Responsibility |
|---|---|
| `requirements.txt` | Pinned dependencies |
| `pipeline/common.py` | Paths, HTTP with retries, snapshot dirs, DuckDB connect, `register_source` |
| `pipeline/lockers.py` | Pure: `normalise_network`, `haversine_m`, `osm_elements_to_frame`, `manual_to_frame`, `merge_manual` |
| `pipeline/ingest_osm_lockers.py` | Overpass per county → `lockers` |
| `pipeline/scb.py` | Pure: age-band helpers, `pxweb_to_frame` |
| `pipeline/ingest_scb.py` | WFS + PxWeb → `deso`, `pop_grid_1km` |
| `pipeline/ingest_overture.py` | Overture places and building centroids → `hosts`, `buildings` |
| `pipeline/load_manual.py` | `data/manual/market_stats.csv` → `market_stats` |
| `pipeline/hex_features.py` | SQL build of `hex_features` |
| `pipeline/checks.py` | Data checks; non-zero exit on failure |
| `pipeline/run_all.py` | Orchestration CLI |
| `data/manual/own_lockers.csv` | Header-only template for manually collected lockers |
| `data/manual/market_stats.csv` | Paketindex Q2 2026 figures with page numbers |
| `tests/test_lockers.py`, `tests/test_scb.py`, `tests/test_hex_features.py` | Offline tests |

---

### Task 1: Environment and shared helpers

**Files:**
- Create: `requirements.txt`, `pipeline/__init__.py`, `tests/__init__.py`, `pipeline/common.py`, `tests/test_common.py`

**Interfaces:**
- Produces: `ROOT, DATA, RAW, MANUAL, DB_PATH, SWEDEN_BBOX`; `today() -> str`; `snapshot_dir(source, date=None) -> Path`; `latest_snapshot(source) -> Path`; `http(method, url, *, retries=3, timeout=300, **kwargs) -> requests.Response`; `load_extensions(con)`; `connect(read_only=False, path=DB_PATH) -> DuckDBPyConnection`; `register_source(con, table_name, source_title, url, licence, snapshot_date, notes="")`.

- [ ] **Step 1: Create the virtual environment and install**

`requirements.txt`:
```
duckdb==1.5.5
pandas==3.0.5
numpy==2.4.6
requests==2.34.2
pytest==8.4.2
```
Run: `py -3.11 -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`
Expected: installs without errors. Create empty `pipeline/__init__.py` and `tests/__init__.py`.

- [ ] **Step 2: Write the failing test**

`tests/test_common.py`:
```python
from pipeline import common


def test_register_source_records_row_count():
    con = common.connect(path=":memory:")
    con.execute("CREATE TABLE t AS SELECT * FROM range(3)")
    common.register_source(con, "t", "Test source", "https://example.org", "CC0", "2026-10-20")
    common.register_source(con, "t", "Test source", "https://example.org", "CC0", "2026-10-20")
    rows = con.execute("SELECT table_name, row_count FROM sources").fetchall()
    assert rows == [("t", 3)]


def test_connect_loads_spatial_and_h3():
    con = common.connect(path=":memory:")
    assert con.execute("SELECT ST_X(ST_Point(1, 2))").fetchone()[0] == 1
    assert con.execute("SELECT h3_h3_to_string(h3_latlng_to_cell(59.85, 17.63, 9))").fetchone()[0]


def test_latest_snapshot_picks_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "RAW", tmp_path)
    (tmp_path / "src" / "2026-01-01").mkdir(parents=True)
    (tmp_path / "src" / "2026-02-01").mkdir()
    assert common.latest_snapshot("src").name == "2026-02-01"
```

- [ ] **Step 3: Run it to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_common.py -v`
Expected: FAIL, `ModuleNotFoundError` or `AttributeError` for `pipeline.common`.

- [ ] **Step 4: Implement `pipeline/common.py`**

```python
"""Shared paths, HTTP, snapshots, DuckDB connection and the source registry."""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import duckdb
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw"
MANUAL = DATA / "manual"
DB_PATH = DATA / "lockers.duckdb"
USER_AGENT = "instabee-locker-agent/0.1 (public-data research pipeline)"
# xmin, ymin, xmax, ymax in WGS84; includes slivers of Norway and Finland,
# which joins to Swedish polygons remove later.
SWEDEN_BBOX = (10.95, 55.33, 24.17, 69.07)


def today() -> str:
    return dt.date.today().isoformat()


def snapshot_dir(source: str, date: str | None = None) -> Path:
    path = RAW / source / (date or today())
    path.mkdir(parents=True, exist_ok=True)
    return path


def latest_snapshot(source: str) -> Path:
    base = RAW / source
    dirs = sorted(p for p in base.iterdir() if p.is_dir()) if base.exists() else []
    if not dirs:
        raise FileNotFoundError(f"No snapshot for {source!r}; run without --skip-download first.")
    return dirs[-1]


def http(method: str, url: str, *, retries: int = 3, timeout: int = 300, **kwargs) -> requests.Response:
    headers = {"User-Agent": USER_AGENT, **kwargs.pop("headers", {})}
    for attempt in range(1, retries + 1):
        try:
            response = requests.request(method, url, headers=headers, timeout=timeout, **kwargs)
            if response.status_code in (429, 502, 503, 504):
                raise requests.HTTPError(f"HTTP {response.status_code} from {url}")
            response.raise_for_status()
            return response
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(15 * attempt)
    raise AssertionError("unreachable")


def load_extensions(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute("INSTALL h3 FROM community; LOAD h3;")


def connect(read_only: bool = False, path: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path), read_only=read_only)
    load_extensions(con)
    return con


def register_source(con, table_name, source_title, url, licence, snapshot_date, notes="") -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS sources (
            table_name VARCHAR, source_title VARCHAR, url VARCHAR, licence VARCHAR,
            snapshot_date DATE, row_count BIGINT, notes VARCHAR)"""
    )
    rows = con.execute(f'SELECT count(*) FROM "{table_name}"').fetchone()[0]
    con.execute(
        "DELETE FROM sources WHERE table_name = ? AND source_title = ?", [table_name, source_title]
    )
    con.execute(
        "INSERT INTO sources VALUES (?, ?, ?, ?, ?, ?, ?)",
        [table_name, source_title, url, licence, snapshot_date, rows, notes],
    )
```

- [ ] **Step 5: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_common.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt pipeline/__init__.py pipeline/common.py tests/__init__.py tests/test_common.py
git commit -m "feat: add pipeline environment and shared helpers"
```

---

### Task 2: Locker normalisation and manual merge (pure)

**Files:**
- Create: `pipeline/lockers.py`, `tests/test_lockers.py`

**Interfaces:**
- Produces: `LOCKER_COLUMNS: list[str]`; `normalise_network(brand: str|None, operator: str|None) -> str`; `haversine_m(lat1, lon1, lat2, lon2) -> float`; `osm_elements_to_frame(elements: list[dict], snapshot_date: str) -> DataFrame`; `manual_to_frame(manual: DataFrame, snapshot_date: str) -> DataFrame`; `merge_manual(osm: DataFrame, manual: DataFrame, radius_m=20.0) -> DataFrame`.

- [ ] **Step 1: Write the failing tests**

`tests/test_lockers.py`:
```python
import pandas as pd
import pytest

from pipeline.lockers import (
    haversine_m,
    manual_to_frame,
    merge_manual,
    normalise_network,
    osm_elements_to_frame,
)


@pytest.mark.parametrize(
    "brand, operator, expected",
    [
        ("Instabox", None, "Instabee"),
        ("instabox", None, "Instabee"),
        (None, "Instabee", "Instabee"),
        ("BudBee", None, "Instabee"),
        ("Instabee;Pressbyrån", None, "Instabee"),
        ("Postnord", None, "PostNord"),
        ("Bring", None, "Bring"),
        (None, "DHL Freight", "DHL"),
        ("Willys", None, "Other"),
        (None, None, "Other"),
        ("Willys", "PostNord", "PostNord"),
    ],
)
def test_normalise_network(brand, operator, expected):
    assert normalise_network(brand, operator) == expected


def test_haversine_one_hundredth_degree_latitude():
    assert haversine_m(59.0, 17.0, 59.01, 17.0) == pytest.approx(1112, abs=2)


def test_osm_elements_use_center_and_dedupe():
    elements = [
        {"type": "node", "id": 1, "lat": 59.8, "lon": 17.6, "tags": {"brand": "Instabox", "name": "A"}},
        {"type": "way", "id": 2, "center": {"lat": 59.9, "lon": 17.7}, "tags": {"operator": "PostNord"}},
        {"type": "node", "id": 1, "lat": 59.8, "lon": 17.6, "tags": {"brand": "Instabox"}},
    ]
    df = osm_elements_to_frame(elements, "2026-10-20")
    assert list(df.locker_id) == ["osm:node/1", "osm:way/2"]
    assert list(df.network) == ["Instabee", "PostNord"]
    assert df.loc[1, "lat"] == 59.9


def test_merge_manual_replaces_nearby_same_network_only():
    osm = osm_elements_to_frame(
        [
            {"type": "node", "id": 1, "lat": 59.80000, "lon": 17.6, "tags": {"brand": "Instabox"}},
            {"type": "node", "id": 2, "lat": 59.80005, "lon": 17.6, "tags": {"brand": "PostNord"}},
            {"type": "node", "id": 3, "lat": 59.90000, "lon": 17.6, "tags": {"brand": "Instabox"}},
        ],
        "2026-10-20",
    )
    manual_raw = pd.DataFrame(
        [{"brand": "Instabox", "name": "ICA X", "lat": 59.80010, "lon": 17.6, "address": "Gatan 1", "note": ""}]
    )
    merged = merge_manual(osm, manual_to_frame(manual_raw, "2026-10-20"))
    # node/1 is 11 m from the manual locker and is replaced; PostNord node/2 stays.
    assert sorted(merged.locker_id) == ["manual:1", "osm:node/2", "osm:node/3"]


def test_merge_manual_with_empty_manual_list():
    osm = osm_elements_to_frame([{"type": "node", "id": 1, "lat": 59.8, "lon": 17.6, "tags": {}}], "2026-10-20")
    empty = manual_to_frame(pd.DataFrame(columns=["brand", "name", "lat", "lon", "address", "note"]), "2026-10-20")
    assert list(merge_manual(osm, empty).locker_id) == ["osm:node/1"]
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_lockers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pipeline.lockers'`.

- [ ] **Step 3: Implement `pipeline/lockers.py`**

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_lockers.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/lockers.py tests/test_lockers.py
git commit -m "feat: add locker network normalisation and manual merge"
```

---

### Task 3: OSM locker ingest

**Files:**
- Create: `pipeline/ingest_osm_lockers.py`, `data/manual/own_lockers.csv`

**Interfaces:**
- Consumes: `common.http, common.register_source, common.MANUAL`; `lockers.osm_elements_to_frame, manual_to_frame, merge_manual`.
- Produces: `SOURCE = "osm_lockers"`; `download(snapshot: Path) -> None`; `load(con, snapshot: Path) -> None` creating table `lockers` (LOCKER_COLUMNS with `snapshot_date DATE`, plus `geom` in EPSG:3006).

- [ ] **Step 1: Create the manual template**

`data/manual/own_lockers.csv`:
```
brand,name,lat,lon,address,note
```

- [ ] **Step 2: Implement `pipeline/ingest_osm_lockers.py`**

```python
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
```

- [ ] **Step 3: Run it against the live API**

Run: `.venv/Scripts/python -c "from pipeline import common, ingest_osm_lockers as m; s=common.snapshot_dir(m.SOURCE); m.download(s); con=common.connect(); m.load(con, s); print(con.execute('select network, count(*) from lockers group by 1 order by 2 desc').fetchall())"`
Expected: 21 county lines, then counts with `Instabee` around 380 and `PostNord` the largest network.

- [ ] **Step 4: Commit**

```bash
git add pipeline/ingest_osm_lockers.py data/manual/own_lockers.csv
git commit -m "feat: ingest OSM parcel lockers per county"
```

---

### Task 4: SCB parsing helpers (pure)

**Files:**
- Create: `pipeline/scb.py`, `tests/test_scb.py`

**Interfaces:**
- Produces: `age_lower_from_pxweb(code: str) -> int|None`; `age_lower_from_grid_column(col: str) -> int`; `age_band(lower: int) -> str` returning one of `pop_0_19, pop_20_34, pop_35_64, pop_65_plus`; `AGE_BANDS: list[str]`; `pxweb_to_frame(response: dict) -> DataFrame`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scb.py`:
```python
import pytest

from pipeline.scb import (
    AGE_BANDS,
    age_band,
    age_lower_from_grid_column,
    age_lower_from_pxweb,
    pxweb_to_frame,
)


@pytest.mark.parametrize("code, lower", [("-4", 0), ("5-9", 5), ("80-", 80), ("100+", 100), ("totalt", None)])
def test_age_lower_from_pxweb(code, lower):
    assert age_lower_from_pxweb(code) == lower


@pytest.mark.parametrize("col, lower", [("ald0_4", 0), ("ald20_24", 20), ("ald100w", 100)])
def test_age_lower_from_grid_column(col, lower):
    assert age_lower_from_grid_column(col) == lower


@pytest.mark.parametrize("lower, band", [(0, "pop_0_19"), (15, "pop_0_19"), (20, "pop_20_34"), (30, "pop_20_34"),
                                         (35, "pop_35_64"), (60, "pop_35_64"), (65, "pop_65_plus"), (100, "pop_65_plus")])
def test_age_band(lower, band):
    assert age_band(lower) == band
    assert band in AGE_BANDS


def test_pxweb_to_frame():
    response = {
        "columns": [
            {"code": "Region", "text": "region", "type": "d"},
            {"code": "Alder", "text": "ålder", "type": "d"},
            {"code": "Tid", "text": "år", "type": "t"},
            {"code": "000007Y7", "text": "Antal", "type": "c"},
        ],
        "data": [
            {"key": ["0380C1010_DeSO2025", "-4", "2025"], "values": ["57"]},
            {"key": ["0380C1010_DeSO2025", "totalt", "2025"], "values": ["1200"]},
        ],
    }
    df = pxweb_to_frame(response)
    assert list(df.columns) == ["Region", "Alder", "Tid", "000007Y7"]
    assert df.iloc[1]["000007Y7"] == "1200"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_scb.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `pipeline/scb.py`**

```python
"""Pure helpers for SCB age codes and PxWeb JSON responses."""

from __future__ import annotations

import pandas as pd

AGE_BANDS = ["pop_0_19", "pop_20_34", "pop_35_64", "pop_65_plus"]


def age_lower_from_pxweb(code: str) -> int | None:
    """'-4' -> 0, '5-9' -> 5, '80-' -> 80, '100+' -> 100, 'totalt' -> None."""
    if code == "totalt":
        return None
    if code.startswith("-"):
        return 0
    return int(code.split("-")[0].rstrip("+"))


def age_lower_from_grid_column(col: str) -> int:
    """'ald0_4' -> 0, 'ald100w' -> 100."""
    return int(col[3:].split("_")[0].rstrip("w"))


def age_band(lower: int) -> str:
    if lower < 20:
        return "pop_0_19"
    if lower < 35:
        return "pop_20_34"
    if lower < 65:
        return "pop_35_64"
    return "pop_65_plus"


def pxweb_to_frame(response: dict) -> pd.DataFrame:
    cols = [c["code"] for c in response["columns"]]
    rows = [dict(zip(cols, item["key"] + item["values"])) for item in response["data"]]
    return pd.DataFrame(rows, columns=cols)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_scb.py -v`
Expected: all passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/scb.py tests/test_scb.py
git commit -m "feat: add SCB age-band and PxWeb parsing helpers"
```

---

### Task 5: SCB ingest

**Files:**
- Create: `pipeline/ingest_scb.py`

**Interfaces:**
- Consumes: `common.http, register_source`; `scb.*`.
- Produces: `SOURCE = "scb"`; `download(snapshot)`; `load(con, snapshot)` creating `deso(deso_code, kommun_code, kommun_name, lan_code, geom, area_km2, population, pop_0_19, pop_20_34, pop_35_64, pop_65_plus, households)` and `pop_grid_1km(cell_id, geom, population, ald*)`.

- [ ] **Step 1: Implement `pipeline/ingest_scb.py`**

```python
"""Download SCB DeSO boundaries, the 1 km population grid and DeSO tables."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from pipeline.common import http, register_source
from pipeline.scb import age_band, age_lower_from_pxweb, pxweb_to_frame

SOURCE = "scb"
WFS = "https://geodata.scb.se/geoserver/stat/wfs"
PXWEB = "https://api.scb.se/OV0104/v1/doris/sv/ssd/BE/BE0101/BE0101Y/"
DESO_LAYER = "stat:DeSO_2025"
GRID_LAYER = "stat:befolkning_1km_2025"
PAGE = 10_000
SCB_LICENCE = "SCB open geodata and open data terms"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _wfs_pages(layer: str, snapshot: Path, prefix: str) -> None:
    page, start = 0, 0
    while True:
        out = snapshot / f"{prefix}_{page:03d}.geojson"
        if not out.exists():
            params = {
                "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": layer,
                "outputFormat": "application/json", "count": PAGE, "startIndex": start, "sortBy": "objectid",
            }
            out.write_bytes(http("GET", WFS, params=params, timeout=600).content)
        n = len(_read_json(out)["features"])
        print(f"  {layer} page {page}: {n} features")
        if n < PAGE:
            return
        page, start = page + 1, start + PAGE


def _pxweb(table: str, snapshot: Path) -> None:
    meta_path = snapshot / f"{table}_meta.json"
    if not meta_path.exists():
        meta_path.write_bytes(http("GET", PXWEB + table).content)
    out = snapshot / f"{table}.json"
    if out.exists():
        return
    variables = {v["code"]: v for v in _read_json(meta_path)["variables"]}
    regions = [c for c in variables["Region"]["values"] if c.endswith("_DeSO2025")]
    year = max(variables["Tid"]["values"])
    query = [
        {"code": "Region", "selection": {"filter": "item", "values": regions}},
        {"code": "Tid", "selection": {"filter": "item", "values": [year]}},
    ]
    if table == "FolkmDesoAldKon":
        query += [
            {"code": "Alder", "selection": {"filter": "item", "values": variables["Alder"]["values"]}},
            {"code": "Kon", "selection": {"filter": "item", "values": ["1+2"]}},
        ]
    else:
        query += [{"code": "Hushallstyp", "selection": {"filter": "item", "values": ["TOTALT"]}}]
    body = {"query": query, "response": {"format": "json"}}
    out.write_bytes(http("POST", PXWEB + table, json=body, timeout=600).content)
    print(f"  PxWeb {table}: {len(regions)} DeSO, year {year}")
    time.sleep(2)


def download(snapshot: Path) -> None:
    _wfs_pages(DESO_LAYER, snapshot, "deso")
    _wfs_pages(GRID_LAYER, snapshot, "grid")
    _pxweb("FolkmDesoAldKon", snapshot)
    _pxweb("HushallDesoTyp", snapshot)


def _union_read(files: list[Path], columns: str) -> str:
    return " UNION ALL ".join(f"SELECT {columns} FROM ST_Read('{p.as_posix()}')" for p in files)


def _value(df: pd.DataFrame) -> pd.Series:
    return pd.to_numeric(df[df.columns[-1]], errors="coerce").fillna(0)


def _deso_attributes(snapshot: Path) -> pd.DataFrame:
    pop = pxweb_to_frame(_read_json(snapshot / "FolkmDesoAldKon.json"))
    pop["value"] = _value(pop)
    pop["deso_code"] = pop["Region"].str.replace("_DeSO2025", "", regex=False)
    total = pop[pop["Alder"] == "totalt"].groupby("deso_code")["value"].sum().rename("population")
    by_age = pop[pop["Alder"] != "totalt"].copy()
    by_age["band"] = by_age["Alder"].map(lambda c: age_band(age_lower_from_pxweb(c)))
    bands = by_age.pivot_table(index="deso_code", columns="band", values="value", aggfunc="sum")
    hh = pxweb_to_frame(_read_json(snapshot / "HushallDesoTyp.json"))
    hh["households"] = _value(hh)
    hh["deso_code"] = hh["Region"].str.replace("_DeSO2025", "", regex=False)
    attrs = pd.concat([total, bands], axis=1).join(hh.set_index("deso_code")["households"])
    attrs.index.name = "deso_code"
    return attrs.reset_index()


def _kommuner(snapshot: Path) -> pd.DataFrame:
    region = next(v for v in _read_json(snapshot / "FolkmDesoAldKon_meta.json")["variables"] if v["code"] == "Region")
    pairs = [
        (code, text.split(" ", 1)[1])
        for code, text in zip(region["values"], region["valueTexts"])
        if len(code) == 4 and code.isdigit()
    ]
    return pd.DataFrame(pairs, columns=["kommun_code", "kommun_name"])


def load(con, snapshot: Path) -> None:
    date = snapshot.name
    deso_files = sorted(snapshot.glob("deso_*.geojson"))
    grid_files = sorted(snapshot.glob("grid_*.geojson"))
    con.register("deso_attrs", _deso_attributes(snapshot))
    con.register("kommuner", _kommuner(snapshot))
    con.execute(
        f"""CREATE OR REPLACE TABLE deso AS
        WITH raw AS ({_union_read(deso_files, 'desokod, kommunkod, lanskod, geom')})
        SELECT r.desokod AS deso_code, r.kommunkod AS kommun_code, k.kommun_name, r.lanskod AS lan_code,
               r.geom, ST_Area(r.geom) / 1e6 AS area_km2,
               a.population, a.pop_0_19, a.pop_20_34, a.pop_35_64, a.pop_65_plus, a.households
        FROM raw r
        LEFT JOIN deso_attrs a ON a.deso_code = r.desokod
        LEFT JOIN kommuner k ON k.kommun_code = r.kommunkod"""
    )
    con.unregister("deso_attrs")
    con.unregister("kommuner")
    con.execute(
        f"""CREATE OR REPLACE TABLE pop_grid_1km AS
        SELECT rutid_scb AS cell_id, geom, coalesce(beftotalt, 0) AS population, COLUMNS('^ald')
        FROM ({_union_read(grid_files, '*')})"""
    )
    register_source(con, "deso", "SCB DeSO 2025 boundaries (WFS)", WFS, SCB_LICENCE, date)
    register_source(con, "deso", "SCB PxWeb FolkmDesoAldKon + HushallDesoTyp", PXWEB, SCB_LICENCE, date)
    register_source(con, "pop_grid_1km", "SCB befolkning 1 km grid 2025 (WFS)", WFS, SCB_LICENCE, date)
    print(con.execute("SELECT count(*), sum(population) FROM deso").fetchone(), "deso rows, population")
    print(con.execute("SELECT count(*), sum(population) FROM pop_grid_1km").fetchone(), "grid rows, population")
```

- [ ] **Step 2: Run it against the live services**

Run: `.venv/Scripts/python -c "from pipeline import common, ingest_scb as m; s=common.snapshot_dir(m.SOURCE); m.download(s); m.load(common.connect(), s)"`
Expected: `(6160, ~10.6 million)` deso rows and population; about 115,000 grid rows with a similar population total. Spot check: `select kommun_name, sum(population) from deso where kommun_code='0380' group by 1` returns Uppsala with about 245,000.

- [ ] **Step 3: Commit**

```bash
git add pipeline/ingest_scb.py
git commit -m "feat: ingest SCB DeSO, 1 km grid and DeSO tables"
```

---

### Task 6: Overture ingest

**Files:**
- Create: `pipeline/ingest_overture.py`

**Interfaces:**
- Consumes: `common.SWEDEN_BBOX, load_extensions, register_source`; table `deso` (Task 5).
- Produces: `SOURCE = "overture"`; `download(snapshot)`; `load(con, snapshot)` creating `hosts(host_id, overture_id, name, brand, category, host_type, lat, lon, geom, confidence, deso_code, kommun_code)` and `buildings(lat, lon, footprint_m2)`. `host_type` ∈ `supermarket, convenience, fuel, pharmacy, mall, pickup_point`.

- [ ] **Step 1: Implement `pipeline/ingest_overture.py`**

```python
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
```

- [ ] **Step 2: Run it against S3 (long; run in the background)**

Run: `.venv/Scripts/python -c "from pipeline import common, ingest_overture as m; s=common.snapshot_dir(m.SOURCE); m.download(s); m.load(common.connect(), s)"`
Expected: host counts per type with `supermarket` in the thousands; buildings either several million rows or the fallback note.

- [ ] **Step 3: Commit**

```bash
git add pipeline/ingest_overture.py
git commit -m "feat: ingest Overture host places and building centroids"
```

---

### Task 7: Market statistics

**Files:**
- Create: `data/manual/market_stats.csv`, `pipeline/load_manual.py`

**Interfaces:**
- Produces: `load(con) -> None` creating `market_stats(metric, value, unit, period, geography, source_title, source_url, page)`.

- [ ] **Step 1: Write `data/manual/market_stats.csv`** (figures checked against the PDF on 2026-10-20)

```
metric,value,unit,period,geography,source_title,source_url,page
ecommerce_parcels,256183415,parcels,2025,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,7
ecommerce_parcels,69979000,parcels,2026-Q2,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,7
ecommerce_parcels_growth_yoy,11.0,percent,2026-Q2,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,7
domestic_parcels,176501424,parcels,2025,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,9
foreign_parcels,79681991,parcels,2025,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,10
foreign_parcel_share,32.4,percent,2026-Q2,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,3
delivery_share_pickup_point,40,percent,2026-06,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,8
delivery_share_home,37,percent,2026-06,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,8
delivery_share_locker,23,percent,2026-06,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,8
delivery_share_locker_indoor,13,percent,2026-06,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,8
delivery_share_locker_outdoor,10,percent,2026-06,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,8
delivery_survey_respondents,898,people,2026-06,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,8
secondhand_parcels,8193000,parcels,2026-Q2,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,11
secondhand_parcels_growth_yoy,36.3,percent,2026-Q2,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,11
secondhand_parcels,25175525,parcels,2025,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,11
return_rate,7.3,percent,2026-Q2,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,12
seasonality_share_jan,8.1,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_feb,6.8,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_mar,7.8,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_apr,7.9,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_may,8.6,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_jun,8.0,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_jul,7.4,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_aug,8.0,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_sep,8.0,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_oct,8.6,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_nov,10.1,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
seasonality_share_dec,10.6,percent of year,2021-2025 average,Sweden,Paketindex Q2 2026 (Transportföretagen),https://via.tt.se/files/3235972/4535013/411099/sv,14
```

- [ ] **Step 2: Implement `pipeline/load_manual.py`**

```python
"""Load hand-entered national market figures."""

from __future__ import annotations

from pipeline.common import MANUAL, register_source, today

MARKET_STATS_CSV = MANUAL / "market_stats.csv"


def load(con) -> None:
    con.execute(
        f"""CREATE OR REPLACE TABLE market_stats AS
        SELECT * FROM read_csv('{MARKET_STATS_CSV.as_posix()}', header = true,
            columns = {{'metric': 'VARCHAR', 'value': 'DOUBLE', 'unit': 'VARCHAR', 'period': 'VARCHAR',
                       'geography': 'VARCHAR', 'source_title': 'VARCHAR', 'source_url': 'VARCHAR', 'page': 'INTEGER'}})"""
    )
    register_source(con, "market_stats", "Paketindex Q2 2026, Transportföretagen (hand-entered)",
                    "https://via.tt.se/files/3235972/4535013/411099/sv", "Public report", today(),
                    notes="Next edition (Q3 2026) due week 45 of 2026")
```

- [ ] **Step 3: Verify**

Run: `.venv/Scripts/python -c "from pipeline import common, load_manual as m; con=common.connect(); m.load(con); print(con.execute(\"select sum(value) from market_stats where metric like 'seasonality%'\").fetchone())"`
Expected: `(99.9,)` (rounding in the source).

- [ ] **Step 4: Commit**

```bash
git add data/manual/market_stats.csv pipeline/load_manual.py
git commit -m "feat: add Paketindex market statistics"
```

---

### Task 8: Hex feature table

**Files:**
- Create: `pipeline/hex_features.py`, `tests/test_hex_features.py`

**Interfaces:**
- Consumes: tables `pop_grid_1km, buildings, deso, lockers, hosts`; `scb.age_band, age_lower_from_grid_column`; `common.register_source, today`.
- Produces: `CAP_M = 5000`; `build(con) -> None` creating `hex_features(h3 VARCHAR, lat, lon, geom, kommun_code, kommun_name, deso_code, pop_est, pop_0_19_est, pop_20_34_est, pop_35_64_est, pop_65_plus_est, dist_instabee_m, dist_any_competitor_m, dist_postnord_m, lockers_300m, lockers_500m, instabee_300m, instabee_500m, hosts_300m, supermarkets_300m, fuel_300m, pickup_points_300m)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_hex_features.py`:
```python
import pytest

from pipeline import common, hex_features

CELL_WKT_3006 = "POLYGON((647000 6638000, 648000 6638000, 648000 6639000, 647000 6639000, 647000 6638000))"
DESO_WKT_3006 = "POLYGON((640000 6630000, 660000 6630000, 660000 6650000, 640000 6650000, 640000 6630000))"


def _fixture(with_buildings: bool):
    con = common.connect(path=":memory:")
    con.execute(f"""CREATE TABLE pop_grid_1km AS SELECT '6470006638000' AS cell_id,
        ST_GeomFromText('{CELL_WKT_3006}') AS geom, 100 AS population, 40 AS ald20_24, 60 AS ald70_74""")
    con.execute(f"""CREATE TABLE deso AS SELECT 'T0001' AS deso_code, '0380' AS kommun_code,
        'Uppsala' AS kommun_name, ST_GeomFromText('{DESO_WKT_3006}') AS geom""")
    hexes = con.execute(f"""SELECT h3_cell_to_lat(h) AS lat, h3_cell_to_lng(h) AS lon FROM (
        SELECT unnest(h3_polygon_wkt_to_cells(ST_AsText(ST_Transform(ST_GeomFromText('{CELL_WKT_3006}'),
        'EPSG:3006', 'EPSG:4326', always_xy := true)), 9)) AS h) ORDER BY h LIMIT 2""").fetchall()
    (lat_a, lon_a), (lat_b, lon_b) = hexes
    rows = f"({lat_a}, {lon_a}, 300.0), ({lat_b}, {lon_b}, 100.0)" if with_buildings else ""
    con.execute("CREATE TABLE buildings (lat DOUBLE, lon DOUBLE, footprint_m2 DOUBLE)")
    if rows:
        con.execute(f"INSERT INTO buildings VALUES {rows}")
    point = "ST_Transform(ST_Point({lon}, {lat}), 'EPSG:4326', 'EPSG:3006', always_xy := true)"
    con.execute(f"""CREATE TABLE lockers AS
        SELECT 'osm:node/1' AS locker_id, 'Instabee' AS network, {point.format(lon=lon_a, lat=lat_a)} AS geom
        UNION ALL
        SELECT 'osm:node/2', 'PostNord', {point.format(lon=lon_a, lat=lat_a + 0.06)}""")
    con.execute(f"""CREATE TABLE hosts AS
        SELECT 1 AS host_id, 'supermarket' AS host_type, {point.format(lon=lon_a, lat=lat_a)} AS geom""")
    return con, (lat_a, lon_a)


def _hex_a(con, lat_a, lon_a):
    return con.execute(
        "SELECT * FROM hex_features WHERE h3 = h3_h3_to_string(h3_latlng_to_cell(?, ?, 9))", [lat_a, lon_a]
    ).df().iloc[0]


def test_population_follows_building_footprint():
    con, (lat_a, lon_a) = _fixture(with_buildings=True)
    hex_features.build(con)
    assert con.execute("SELECT count(*) FROM hex_features").fetchone()[0] == 2
    assert con.execute("SELECT sum(pop_est) FROM hex_features").fetchone()[0] == pytest.approx(100)
    assert con.execute("SELECT sum(pop_65_plus_est) FROM hex_features").fetchone()[0] == pytest.approx(60)
    row = _hex_a(con, lat_a, lon_a)
    assert row.pop_est == pytest.approx(75)
    assert row.kommun_name == "Uppsala"


def test_distances_counts_and_cap():
    con, (lat_a, lon_a) = _fixture(with_buildings=True)
    hex_features.build(con)
    row = _hex_a(con, lat_a, lon_a)
    assert row.dist_instabee_m == pytest.approx(0, abs=1)
    assert row.dist_postnord_m == 5000  # 6.7 km away, capped
    assert row.dist_any_competitor_m == 5000
    assert row.instabee_300m == 1 and row.lockers_300m == 1 and row.lockers_500m == 1
    assert row.supermarkets_300m == 1 and row.hosts_300m == 1 and row.pickup_points_300m == 0


def test_even_split_without_buildings():
    con, _ = _fixture(with_buildings=False)
    hex_features.build(con)
    n, total, low, high = con.execute(
        "SELECT count(*), sum(pop_est), min(pop_est), max(pop_est) FROM hex_features").fetchone()
    assert n >= 10
    assert total == pytest.approx(100)
    assert low == pytest.approx(high)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python -m pytest tests/test_hex_features.py -v`
Expected: FAIL with `ImportError: cannot import name 'hex_features'`.

- [ ] **Step 3: Implement `pipeline/hex_features.py`**

```python
"""Build hex_features: population, locker distances and host counts per H3 res-9 cell."""

from __future__ import annotations

from pipeline.common import register_source, today
from pipeline.scb import AGE_BANDS, age_band, age_lower_from_grid_column

RESOLUTION = 9
CAP_M = 5000
TO_3006 = "'EPSG:4326', 'EPSG:3006', always_xy := true"


def _band_sums(con) -> str:
    """SQL expressions summing the grid's ald* columns into the four age bands."""
    cols = [r[0] for r in con.execute("DESCRIBE pop_grid_1km").fetchall() if r[0].startswith("ald")]
    exprs = []
    for band in AGE_BANDS:
        members = [f"coalesce({c}, 0)" for c in cols if age_band(age_lower_from_grid_column(c)) == band]
        exprs.append(f"({' + '.join(members) if members else '0'}) AS {band}")
    return ", ".join(exprs)


def build(con) -> None:
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _cells AS
        SELECT cell_id, population, {_band_sums(con)},
               ST_AsText(ST_Transform(geom, 'EPSG:3006', 'EPSG:4326', always_xy := true)) AS wkt,
               ST_Transform(ST_Centroid(geom), 'EPSG:3006', 'EPSG:4326', always_xy := true) AS centre
        FROM pop_grid_1km WHERE population > 0""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _cell_hex AS
        SELECT cell_id, unnest(h3_polygon_wkt_to_cells(wkt, {RESOLUTION})) AS h3 FROM _cells""")
    # A cell whose hexes all have centres outside it still gets the hex at its centre.
    con.execute(f"""INSERT INTO _cell_hex
        SELECT cell_id, h3_latlng_to_cell(ST_Y(centre), ST_X(centre), {RESOLUTION}) FROM _cells
        WHERE cell_id NOT IN (SELECT DISTINCT cell_id FROM _cell_hex)""")
    con.execute("CREATE OR REPLACE TEMP TABLE _cell_hex AS SELECT h3, min(cell_id) AS cell_id FROM _cell_hex GROUP BY h3")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _footprint AS
        SELECT h3_latlng_to_cell(lat, lon, {RESOLUTION}) AS h3, sum(footprint_m2) AS fp FROM buildings GROUP BY 1""")
    band_cols = ", ".join(f"c.{b} * w.weight AS {b}_est" for b in AGE_BANDS)
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _hex AS
        WITH w AS (
            SELECT ch.h3, ch.cell_id, coalesce(f.fp, 0) AS fp,
                   sum(coalesce(f.fp, 0)) OVER (PARTITION BY ch.cell_id) AS cell_fp,
                   count(*) OVER (PARTITION BY ch.cell_id) AS n_hex
            FROM _cell_hex ch LEFT JOIN _footprint f USING (h3)),
        weighted AS (
            SELECT h3, cell_id, CASE WHEN cell_fp > 0 THEN fp / cell_fp ELSE 1.0 / n_hex END AS weight FROM w)
        SELECT w.h3, h3_cell_to_lat(w.h3) AS lat, h3_cell_to_lng(w.h3) AS lon,
               ST_Transform(ST_Point(h3_cell_to_lng(w.h3), h3_cell_to_lat(w.h3)), {TO_3006}) AS geom,
               c.population * w.weight AS pop_est, {band_cols}
        FROM weighted w JOIN _cells c USING (cell_id)
        WHERE w.weight > 0""")
    con.execute("""CREATE OR REPLACE TEMP TABLE _hex_deso AS
        SELECT h.h3, d.deso_code, d.kommun_code, d.kommun_name
        FROM _hex h JOIN deso d ON ST_Within(h.geom, d.geom)""")
    con.execute(f"""CREATE OR REPLACE TEMP TABLE _hex_lockers AS
        SELECT h3,
               min(d) FILTER (WHERE network = 'Instabee') AS d_instabee,
               min(d) FILTER (WHERE network <> 'Instabee') AS d_competitor,
               min(d) FILTER (WHERE network = 'PostNord') AS d_postnord,
               count(*) FILTER (WHERE d <= 300) AS lockers_300m,
               count(*) FILTER (WHERE d <= 500) AS lockers_500m,
               count(*) FILTER (WHERE d <= 300 AND network = 'Instabee') AS instabee_300m,
               count(*) FILTER (WHERE d <= 500 AND network = 'Instabee') AS instabee_500m
        FROM (SELECT h.h3, l.network, ST_Distance(h.geom, l.geom) AS d
              FROM _hex h JOIN lockers l ON ST_DWithin(h.geom, l.geom, {CAP_M}))
        GROUP BY h3""")
    con.execute("""CREATE OR REPLACE TEMP TABLE _hex_hosts AS
        SELECT h.h3,
               count(*) FILTER (WHERE s.host_type <> 'pickup_point') AS hosts_300m,
               count(*) FILTER (WHERE s.host_type = 'supermarket') AS supermarkets_300m,
               count(*) FILTER (WHERE s.host_type = 'fuel') AS fuel_300m,
               count(*) FILTER (WHERE s.host_type = 'pickup_point') AS pickup_points_300m
        FROM _hex h JOIN hosts s ON ST_DWithin(h.geom, s.geom, 300)
        GROUP BY h.h3""")
    bands_out = ", ".join(f"h.{b}_est" for b in AGE_BANDS)
    cap = lambda col: f"least(coalesce(l.{col}, {CAP_M}), {CAP_M})"
    count = lambda tbl, col: f"coalesce({tbl}.{col}, 0) AS {col}"
    con.execute(f"""CREATE OR REPLACE TABLE hex_features AS
        SELECT h3_h3_to_string(h.h3) AS h3, h.lat, h.lon, h.geom,
               d.kommun_code, d.kommun_name, d.deso_code,
               h.pop_est, {bands_out},
               {cap('d_instabee')} AS dist_instabee_m,
               {cap('d_competitor')} AS dist_any_competitor_m,
               {cap('d_postnord')} AS dist_postnord_m,
               {count('l', 'lockers_300m')}, {count('l', 'lockers_500m')},
               {count('l', 'instabee_300m')}, {count('l', 'instabee_500m')},
               {count('s', 'hosts_300m')}, {count('s', 'supermarkets_300m')},
               {count('s', 'fuel_300m')}, {count('s', 'pickup_points_300m')}
        FROM _hex h
        LEFT JOIN _hex_deso d USING (h3)
        LEFT JOIN _hex_lockers l USING (h3)
        LEFT JOIN _hex_hosts s USING (h3)""")
    for tmp in ("_cells", "_cell_hex", "_footprint", "_hex", "_hex_deso", "_hex_lockers", "_hex_hosts"):
        con.execute(f"DROP TABLE IF EXISTS {tmp}")
    register_source(con, "hex_features", "Derived: SCB grid x Overture buildings x OSM lockers x Overture places",
                    "pipeline/hex_features.py", "Derived (inherits ODbL from OSM)", today(),
                    notes=f"H3 res {RESOLUTION}; population split by building footprint; straight-line distances capped at {CAP_M} m")
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_hex_features.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pipeline/hex_features.py tests/test_hex_features.py
git commit -m "feat: build hex feature table"
```

---

### Task 9: Checks and the run-all CLI

**Files:**
- Create: `pipeline/checks.py`, `pipeline/run_all.py`, `README.md`

**Interfaces:**
- Consumes: every module above.
- Produces: `run_checks(con) -> list[tuple[str, str]]` with levels `FAIL`, `WARN`, `INFO`; CLI `python -m pipeline.run_all [--skip-download] [--only osm,scb,overture,manual,hex,checks]`.

- [ ] **Step 1: Implement `pipeline/checks.py`**

```python
"""Data checks for the locker warehouse. Exit code 1 on any FAIL."""

from __future__ import annotations

import datetime as dt
import sys

from pipeline.common import connect

REQUIRED = ["lockers", "deso", "pop_grid_1km", "hosts", "market_stats", "hex_features", "sources"]
MAX_AGE_DAYS = {"lockers": 45, "hosts": 120, "deso": 400, "pop_grid_1km": 400}


def run_checks(con) -> list[tuple[str, str]]:
    results: list[tuple[str, str]] = []
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    for table in REQUIRED:
        n = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] if table in tables else 0
        results.append(("INFO" if n else "FAIL", f"{table}: {n} rows"))
    if any(level == "FAIL" for level, _ in results):
        return results
    if con.execute("SELECT count(*) FROM buildings").fetchone()[0] == 0:
        results.append(("WARN", "buildings is empty: population split evenly within 1 km cells"))

    grid, hexes = con.execute(
        "SELECT (SELECT sum(population) FROM pop_grid_1km), (SELECT sum(pop_est) FROM hex_features)").fetchone()
    gap = abs(hexes - grid) / grid
    results.append(("FAIL" if gap > 0.01 else "INFO", f"hex population {hexes:,.0f} vs grid {grid:,.0f} ({gap:.2%})"))

    deso_pop = con.execute("SELECT sum(population) FROM deso").fetchone()[0]
    results.append(("INFO" if 10e6 <= deso_pop <= 11e6 else "FAIL", f"Sweden population in deso: {deso_pop:,.0f}"))

    con.execute("CREATE TABLE IF NOT EXISTS locker_snapshots (snapshot_date DATE, network VARCHAR, n BIGINT)")
    current = con.execute("SELECT max(snapshot_date) FROM lockers").fetchone()[0]
    con.execute("DELETE FROM locker_snapshots WHERE snapshot_date = ?", [current])
    con.execute("INSERT INTO locker_snapshots SELECT snapshot_date, network, count(*) FROM lockers GROUP BY ALL")
    previous = con.execute("SELECT max(snapshot_date) FROM locker_snapshots WHERE snapshot_date < ?", [current]).fetchone()[0]
    if previous is None:
        results.append(("INFO", "lockers: first snapshot, no comparison"))
    else:
        rows = con.execute("""SELECT p.network, p.n, coalesce(c.n, 0)
            FROM locker_snapshots p LEFT JOIN locker_snapshots c ON c.network = p.network AND c.snapshot_date = ?
            WHERE p.snapshot_date = ?""", [current, previous]).fetchall()
        for network, before, now in rows:
            drop = (before - now) / before if before else 0
            results.append(("FAIL" if drop > 0.2 else "INFO", f"lockers {network}: {before} -> {now} since {previous}"))

    dupes = con.execute("""SELECT count(*) FROM lockers a JOIN lockers b
        ON a.network = b.network AND a.locker_id < b.locker_id AND ST_DWithin(a.geom, b.geom, 20)""").fetchone()[0]
    results.append(("INFO", f"possible duplicate lockers (same network within 20 m): {dupes}"))

    for table, snapshot in con.execute("SELECT table_name, min(snapshot_date) FROM sources GROUP BY 1").fetchall():
        age = (dt.date.today() - snapshot).days
        limit = MAX_AGE_DAYS.get(table)
        if limit and age > limit:
            results.append(("WARN", f"{table}: snapshot {snapshot} is {age} days old (limit {limit})"))
    return results


def main() -> int:
    results = run_checks(connect())
    for level, message in results:
        print(f"[{level}] {message}")
    return 1 if any(level == "FAIL" for level, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Implement `pipeline/run_all.py`**

```python
"""Run the whole pipeline: python -m pipeline.run_all [--skip-download] [--only a,b]."""

from __future__ import annotations

import argparse
import sys
import time

from pipeline import checks, hex_features, ingest_osm_lockers, ingest_overture, ingest_scb, load_manual
from pipeline.common import connect, latest_snapshot, snapshot_dir

INGESTS = {"osm": ingest_osm_lockers, "scb": ingest_scb, "overture": ingest_overture}
ORDER = ["osm", "scb", "overture", "manual", "hex", "checks"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-download", action="store_true", help="reuse the latest raw snapshots")
    parser.add_argument("--only", default=",".join(ORDER), help=f"comma list from {ORDER}")
    args = parser.parse_args(argv)
    selected = [s for s in ORDER if s in args.only.split(",")]
    con = connect()
    for step in selected:
        started = time.time()
        print(f"== {step}")
        if step in INGESTS:
            module = INGESTS[step]
            snapshot = latest_snapshot(module.SOURCE) if args.skip_download else snapshot_dir(module.SOURCE)
            if not args.skip_download:
                module.download(snapshot)
            module.load(con, snapshot)
        elif step == "manual":
            load_manual.load(con)
        elif step == "hex":
            hex_features.build(con)
        elif step == "checks":
            results = checks.run_checks(con)
            for level, message in results:
                print(f"[{level}] {message}")
            if any(level == "FAIL" for level, _ in results):
                return 1
        print(f"   done in {time.time() - started:.0f} s")
    con.execute("CHECKPOINT")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Write `README.md`**

````markdown
# Instabee locker network agent: data pipeline

Builds `data/lockers.duckdb` from public data: OSM parcel lockers, SCB population (DeSO and 1 km grid),
Overture places and buildings, and Paketindex market figures, joined on an H3 resolution-9 grid.

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pipeline.run_all            # full download + build
.venv/Scripts/python -m pipeline.run_all --skip-download --only hex,checks
.venv/Scripts/python -m pytest
```

Instabee's own lockers are only partly mapped in OSM. Add known lockers to `data/manual/own_lockers.csv`
(`brand,name,lat,lon,address,note`); they replace OSM lockers of the same network within 20 m.

Design: `docs/superpowers/specs/2026-10-20-locker-data-pipeline-design.md`.
````

- [ ] **Step 4: Full run**

Run: `.venv/Scripts/python -m pipeline.run_all --skip-download` (after Tasks 3, 5 and 6 have downloaded)
Expected: every step prints `done`; checks print no `[FAIL]`; exit code 0.

- [ ] **Step 5: Run all tests and commit**

Run: `.venv/Scripts/python -m pytest -v` → all passed.
```bash
git add pipeline/checks.py pipeline/run_all.py README.md
git commit -m "feat: add data checks and run-all CLI"
```
