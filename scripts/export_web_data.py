"""Export the warehouse to compact files for the hosted (in-browser) agent: web/data/.

Hexagons go to one binary file per county (lazy-loaded by the page); lockers, hosts, areas and market
figures go to JSON. Run: .venv/Scripts/python scripts/export_web_data.py
"""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.analysis import chain_sql, demand_expression, demand_parameters  # noqa: E402
from pipeline.common import connect  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "web" / "data"
# Binary layout per county (base64-encoded in hex_<lan>.txt): uint32 n, then x,y,lat,lon,pop,demand as float32[n],
# then d_instabee, d_competitor, d_postnord, kommun_index, deso_index as uint16[n] (little-endian).
F32 = ["x", "y", "lat", "lon", "pop", "demand"]
U16 = ["d_ib", "d_comp", "d_pn", "k", "d"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    con = connect(read_only=True)
    p = demand_parameters(con)

    kommuner = con.execute("""SELECT kommun_code, any_value(kommun_name), any_value(lan_code), sum(population)
        FROM deso GROUP BY 1 ORDER BY 1""").fetchall()
    k_index = {code: i for i, (code, *_) in enumerate(kommuner)}
    deso = con.execute("SELECT deso_code, kommun_code, population, households FROM deso ORDER BY 1").fetchall()
    d_index = {code: i for i, (code, *_) in enumerate(deso)}

    hexes = con.execute(f"""SELECT ST_X(geom) AS x, ST_Y(geom) AS y, lat, lon, pop_est AS pop,
               {demand_expression(p['weights'])} AS demand,
               dist_instabee_m AS d_ib, dist_any_competitor_m AS d_comp, dist_postnord_m AS d_pn,
               kommun_code, deso_code
        FROM hex_features WHERE kommun_code IS NOT NULL ORDER BY kommun_code, pop_est DESC""").df()
    hexes["k"] = hexes.kommun_code.map(k_index)
    hexes["d"] = hexes.deso_code.map(d_index).fillna(65535)
    hexes["lan"] = hexes.kommun_code.str[:2]
    files = {}
    for lan, part in hexes.groupby("lan"):
        buf = bytearray(struct.pack("<I", len(part)))
        for col in F32:
            buf += part[col].to_numpy(dtype="<f4").tobytes()
        for col in U16:
            buf += np.clip(part[col].round(), 0, 65535).to_numpy(dtype="<u2").tobytes()
        # Artifacts serve text, not arbitrary binary, so the bytes ship as base64 text.
        name = f"hex_{lan}.txt"
        (OUT / name).write_text(base64.b64encode(bytes(buf)).decode("ascii"), encoding="ascii")
        files[lan] = {"file": name, "n": len(part)}

    bbox = hexes.groupby("kommun_code").agg(x0=("x", "min"), x1=("x", "max"), y0=("y", "min"), y1=("y", "max"))
    lockers = con.execute("""SELECT l.network, l.name, round(l.lat, 6), round(l.lon, 6),
               round(ST_X(l.geom), 1), round(ST_Y(l.geom), 1), d.kommun_code
        FROM lockers l LEFT JOIN deso d ON ST_Within(l.geom, d.geom) ORDER BY l.locker_id""").fetchall()
    hosts = con.execute(f"""SELECT host_id, name, {chain_sql()} AS chain, host_type, round(lat, 6), round(lon, 6),
               round(ST_X(geom), 1), round(ST_Y(geom), 1), kommun_code
        FROM hosts ORDER BY host_id""").fetchall()
    market = con.execute("SELECT metric, value, unit, period, source_title, source_url, page FROM market_stats").fetchall()
    sources = con.execute("SELECT table_name, source_title, url, licence, CAST(snapshot_date AS VARCHAR), row_count, notes FROM sources ORDER BY 1").fetchall()

    meta = {
        "layout": {"f32": F32, "u16": U16},
        "counties": files,
        "kommuner": [{"code": c, "name": n, "lan": l, "population": round(float(pop))} for c, n, l, pop in kommuner],
        "kommun_bbox": {c: [round(float(v), 1) for v in row] for c, row in bbox.iterrows()},
        "deso": [[c, k, round(float(pop or 0)), round(float(hh or 0))] for c, k, pop, hh in deso],
        "demand": {"parcels_per_person": p["parcels_per_person"], "parcels_period": p["parcels_period"],
                   "weights": p["weights"], "locker_share": p["locker_share"]},
        "market_stats": [dict(zip(["metric", "value", "unit", "period", "source_title", "source_url", "page"], r)) for r in market],
        "sources": [dict(zip(["table", "source", "url", "licence", "snapshot_date", "rows", "notes"], r)) for r in sources],
        "totals": {"hexes": len(hexes), "population": round(float(hexes["pop"].sum()))},
    }
    (OUT / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "lockers.json").write_text(json.dumps(lockers, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (OUT / "hosts.json").write_text(json.dumps(hosts, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    total = sum(f.stat().st_size for f in OUT.iterdir())
    largest = max(OUT.iterdir(), key=lambda f: f.stat().st_size)
    print(f"{len(files)} county files, {len(hexes):,} hexes, {len(lockers)} lockers, {len(hosts):,} hosts")
    print(f"total {total / 1e6:.1f} MB, largest {largest.name} {largest.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
