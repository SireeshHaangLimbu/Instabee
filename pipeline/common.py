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
    con.execute("SET enable_progress_bar = false")


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
