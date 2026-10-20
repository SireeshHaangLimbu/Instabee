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
    previous = con.execute(
        "SELECT max(snapshot_date) FROM locker_snapshots WHERE snapshot_date < ?", [current]).fetchone()[0]
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
