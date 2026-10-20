"""Tool schemas exposed to Claude and the dispatcher that runs them against DuckDB."""

from __future__ import annotations

import re
from typing import Any, Callable

import pandas as pd

from agent import analysis, maps

MAX_ROWS = 50
TABLE_ROWS = 30
READ_ONLY = re.compile(r"^\s*(select|with|describe|show|summarize|from|pivot)\b", re.IGNORECASE)
TABLE_DOCS = {
    "hex_features": "one row per populated H3 res-9 hexagon (~175 m): estimated population by age, municipality, DeSO, distance to nearest locker per network, locker and host counts within 300/500 m",
    "lockers": "parcel lockers from OpenStreetMap, network normalised (Instabee = Instabox + Budbee); Instabee is incomplete",
    "hosts": "candidate host sites from Overture Places: supermarket, convenience, fuel, pharmacy, mall, pickup_point",
    "deso": "SCB DeSO 2025 statistical areas: population by age band, households, municipality",
    "pop_grid_1km": "SCB 1 km population grid 2025",
    "buildings": "Overture building centroids and footprint (used to place population)",
    "market_stats": "national figures from Paketindex Q2 2026: parcels per year, delivery mix, seasonality, second-hand",
    "sources": "every table's source, URL, licence and snapshot date",
}
AREA = {"type": "string", "description": "A Swedish municipality name such as 'Uppsala' or 'Göteborg', or 'Sweden'."}
AREA_KOMMUN = {"type": "string", "description": "A Swedish municipality name such as 'Uppsala'."}
NETWORK = {"type": "string", "enum": ["Instabee", "PostNord", "competitors", "any"],
           "description": "Instabee (Instabox + Budbee), PostNord, competitors (every non-Instabee network) or any. Default Instabee."}
RADIUS = {"type": "number", "description": "Straight-line walking radius in metres, 1-5000. Default 500; 300 is a strict walk."}
HOST_TYPES = {"type": "array", "items": {"type": "string", "enum": list(analysis.CANDIDATE_HOST_TYPES)},
              "description": "Host types to consider. Default all of them."}


def _schema(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


TOOL_SPECS = [
    {"name": "list_tables", "description": "List the warehouse tables with row counts and what each holds.",
     "input_schema": _schema({})},
    {"name": "inspect_table", "description": "Profile one table: columns, types, null counts and example values. Use before writing SQL.",
     "input_schema": _schema({"table_name": {"type": "string"}}, ["table_name"])},
    {"name": "execute_sql", "description": f"Run one read-only DuckDB SELECT and get up to {MAX_ROWS} rows. Spatial columns are EPSG:3006 metres. Prefer the analysis tools for coverage, demand and site choice.",
     "input_schema": _schema({"query": {"type": "string"}}, ["query"])},
    {"name": "data_sources", "description": "Sources, licences and snapshot dates for every table. Use to cite data and its age.",
     "input_schema": _schema({})},
    {"name": "coverage", "description": "Share of population within a radius of a locker network in an area. With group_by='kommun' and area='Sweden', ranks municipalities from least to most covered (with lockers per 10,000 residents).",
     "input_schema": _schema({"area": AREA, "network": NETWORK, "radius_m": RADIUS,
                              "group_by": {"type": "string", "enum": ["kommun"], "description": "Rank municipalities."},
                              "min_population": {"type": "number", "description": "With group_by, skip municipalities below this population. Default 20000."}},
                             ["area"])},
    {"name": "coverage_gaps", "description": "DeSO statistical areas with the most people outside the radius of a network, with the centre of the gap and the median distance to the nearest locker.",
     "input_schema": _schema({"area": AREA, "network": NETWORK, "radius_m": RADIUS,
                              "top_n": {"type": "integer", "description": "Rows to return, default 10."}}, ["area"])},
    {"name": "demand_estimate", "description": "Estimated yearly e-commerce parcels and locker parcels for an area, from population, age and national Paketindex volumes. Returns its assumptions.",
     "input_schema": _schema({"area": AREA, "locker_share": {"type": "number", "description": "Locker share of deliveries, e.g. 0.23 or 23. Default: national June 2026 figure."}}, ["area"])},
    {"name": "score_sites", "description": "Rank individual candidate host sites in one municipality by the uncovered population within the radius, with nearby own and competitor lockers.",
     "input_schema": _schema({"area": AREA_KOMMUN, "network": NETWORK, "radius_m": RADIUS, "host_types": HOST_TYPES,
                              "top_n": {"type": "integer", "description": "Rows to return, default 15."}}, ["area"])},
    {"name": "optimise_next_n", "description": "Choose the N new locker sites in one municipality that add the most uncovered, age-weighted population (greedy maximal covering). Returns each site's added population, estimated locker parcels and cumulative coverage.",
     "input_schema": _schema({"area": AREA_KOMMUN, "n": {"type": "integer", "description": "Number of sites, 1-50."},
                              "network": NETWORK, "radius_m": RADIUS,
                              "candidates": {"type": "string", "enum": ["hosts", "hexes", "both"],
                                             "description": "hosts = shops and petrol stations; hexes = residential locations (e.g. solar residential lockers); both. Default hosts."},
                              "host_types": HOST_TYPES}, ["area", "n"])},
    {"name": "overlap_check", "description": "For a proposed site, how much of its catchment a network already covers (cannibalisation), the nearest network locker and other networks' lockers nearby.",
     "input_schema": _schema({"lat": {"type": "number"}, "lon": {"type": "number"}, "radius_m": RADIUS, "network": NETWORK}, ["lat", "lon"])},
    {"name": "peak_sizing", "description": "Compartments a site needs at the average and the peak month, from yearly locker parcels, dwell time and target utilisation, using national monthly seasonality.",
     "input_schema": _schema({"annual_locker_parcels": {"type": "number"},
                              "dwell_days": {"type": "number", "description": "Average days a parcel waits for pickup. Default 2."},
                              "utilisation": {"type": "number", "description": "Target peak fill, 0-1. Default 0.8."},
                              "peak_month": {"type": "string", "description": "Month to size for, default 'dec'."}},
                             ["annual_locker_parcels"])},
    {"name": "competitor_presence", "description": "Lockers per network in an area, and which retail chains have a locker of each network at their store (within 50 m).",
     "input_schema": _schema({"area": AREA}, ["area"])},
    {"name": "render_map", "description": "Write an HTML map of one municipality: hexagons coloured by whether they are covered, lockers by network, and proposed sites. Returns the file path.",
     "input_schema": _schema({"area": AREA_KOMMUN, "network": NETWORK, "radius_m": RADIUS,
                              "sites": {"type": "array", "description": "Proposed sites to mark, e.g. from optimise_next_n.",
                                        "items": _schema({"name": {"type": "string"}, "lat": {"type": "number"}, "lon": {"type": "number"}},
                                                         ["name", "lat", "lon"])}},
                             ["area"])},
]


def _plain(value: Any) -> Any:
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _table(df: pd.DataFrame, rows: int = TABLE_ROWS) -> str:
    shown = df.head(rows).copy()
    for col in shown.columns:
        if shown[col].dtype == object and shown[col].map(lambda v: isinstance(v, (bytes, bytearray))).any():
            shown[col] = "<geometry>"
    text = shown.to_markdown(index=False)
    return text + (f"\n({len(df)} rows, first {rows} shown)" if len(df) > rows else "")


def format_result(result: analysis.Result) -> str:
    parts = ["Summary:\n" + "\n".join(f"- {k}: {_plain(v)}" for k, v in result.summary.items())]
    if result.table is not None:
        parts.append("Table:\n" + (_table(result.table) if len(result.table) else "(no rows)"))
    for name, extra in result.extra.items():
        if isinstance(extra, pd.DataFrame):
            parts.append(f"{name}:\n" + (_table(extra) if len(extra) else "(no rows)"))
    if result.notes:
        parts.append("Notes:\n" + "\n".join(f"- {n}" for n in result.notes))
    return "\n\n".join(parts)


def _tables(con) -> list[str]:
    return [r[0] for r in con.execute("SHOW TABLES").fetchall()]


def list_tables(con) -> str:
    lines = []
    for name in _tables(con):
        n = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
        lines.append(f"- {name} ({n:,} rows): {TABLE_DOCS.get(name, '')}".rstrip(": "))
    return "\n".join(lines)


def inspect_table(con, table_name: str) -> str:
    if table_name not in _tables(con):
        raise ValueError(f"Unknown table {table_name!r}. Available: {', '.join(_tables(con))}")
    table = '"' + table_name.replace('"', '""') + '"'
    rows = con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    lines = [f"table: {table_name}", f"rows: {rows}", f"about: {TABLE_DOCS.get(table_name, '-')}", "columns:"]
    for name, dtype, *_ in con.execute(f"DESCRIBE {table}").fetchall():
        col = '"' + name.replace('"', '""') + '"'
        nulls = con.execute(f"SELECT count(*) FILTER (WHERE {col} IS NULL) FROM {table}").fetchone()[0]
        if "GEOMETRY" in dtype.upper():
            lines.append(f"- {name}: {dtype}; nulls={nulls}; EPSG:3006 metres, use ST_Distance / ST_DWithin")
            continue
        examples = con.execute(f"SELECT DISTINCT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT 4").fetchall()
        lines.append(f"- {name}: {dtype}; nulls={nulls}; examples=[{', '.join(repr(_plain(r[0])) for r in examples)}]")
    return "\n".join(lines)


def execute_sql(con, query: str) -> str:
    if not READ_ONLY.match(query):
        raise ValueError("Only read-only queries (SELECT / WITH / DESCRIBE / SHOW) are allowed.")
    if ";" in query.strip().rstrip(";"):
        raise ValueError("Send one statement at a time.")
    df = con.execute(query).df()
    if df.empty:
        return "Query returned 0 rows."
    return _table(df, MAX_ROWS)


def data_sources(con) -> str:
    return _table(con.execute(
        "SELECT table_name, source_title, url, licence, snapshot_date, row_count, notes FROM sources ORDER BY 1").df())


def _analysis(fn: Callable) -> Callable:
    return lambda con, **kwargs: format_result(fn(con, **kwargs))


def render_map(con, area: str, network: str = "Instabee", radius_m: float = 500, sites: list | None = None) -> str:
    path = maps.render_map(con, area, network=network, radius_m=radius_m, sites=sites or [])
    return f"Map written to {path}"


HANDLERS: dict[str, Callable] = {
    "list_tables": list_tables,
    "inspect_table": inspect_table,
    "execute_sql": execute_sql,
    "data_sources": data_sources,
    "coverage": _analysis(analysis.coverage),
    "coverage_gaps": _analysis(analysis.coverage_gaps),
    "demand_estimate": _analysis(analysis.demand_estimate),
    "score_sites": _analysis(analysis.score_sites),
    "optimise_next_n": _analysis(analysis.optimise_next_n),
    "overlap_check": _analysis(analysis.overlap_check),
    "peak_sizing": _analysis(analysis.peak_sizing),
    "competitor_presence": _analysis(analysis.competitor_presence),
    "render_map": render_map,
}


def run_tool(con, name: str, args: dict) -> tuple[str, bool]:
    """Run a tool; returns (text, is_error). Errors go back to the model so it can correct itself."""
    handler = HANDLERS.get(name)
    if handler is None:
        return f"ERROR: unknown tool {name!r}", True
    try:
        return handler(con, **(args or {})), False
    except Exception as exc:  # every failure is reported to the model, never raised into the loop
        return f"ERROR: {type(exc).__name__}: {exc}", True
