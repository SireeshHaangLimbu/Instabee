"""Check that the browser tools (web/tools.js) give the same answers as the Python tools.

Run: .venv/Scripts/python scripts/check_web_parity.py   (needs Node.js and web/data from export_web_data.py)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import analysis as A  # noqa: E402
from pipeline.common import connect  # noqa: E402

CASES = [
    ("coverage", {"area": "Uppsala"}),
    ("coverage", {"area": "Göteborg", "network": "PostNord", "radius_m": 300}),
    ("coverage", {"area": "Sweden", "network": "any", "group_by": "kommun", "min_population": 50000}),
    ("coverage_gaps", {"area": "Göteborg", "top_n": 5}),
    ("demand_estimate", {"area": "Uppsala"}),
    ("score_sites", {"area": "Uppsala", "top_n": 5}),
    ("optimise_next_n", {"area": "Uppsala", "n": 10}),
    ("optimise_next_n", {"area": "Stockholm", "n": 5, "candidates": "both"}),
    ("optimise_next_n", {"area": "Malmö", "n": 5, "radius_m": 300, "host_types": ["supermarket", "fuel"]}),
    ("overlap_check", {"lat": 59.86668, "lon": 17.64872}),
    ("overlap_check", {"lat": 57.7089, "lon": 11.9746, "network": "any", "radius_m": 800}),
    ("peak_sizing", {"annual_locker_parcels": 20000}),
    ("competitor_presence", {"area": "Sweden"}),
    ("competitor_presence", {"area": "Uppsala"}),
]


def python_result(con, tool, args):
    r = getattr(A, tool)(con, **args)
    out = {"summary": r.summary}
    if r.table is not None:
        out["table"] = r.table.head(10).to_dict("records")
    if "host_chains" in r.extra:
        out["chains"] = r.extra["host_chains"].head(10).to_dict("records")
    return out


def close(a, b, tol):
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol * max(1, abs(a), abs(b))
    return str(a) == str(b)


def main() -> int:
    con = connect(read_only=True)
    js = json.loads(subprocess.run(["node", str(ROOT / "scripts" / "web_tools_cli.js"), json.dumps(CASES + [("toSweref", {"lat": 59.8586, "lon": 17.6389})])],
                                   capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    proj = js.pop()
    x, y = con.execute("SELECT ST_X(p), ST_Y(p) FROM (SELECT ST_Transform(ST_Point(17.6389, 59.8586), 'EPSG:4326', 'EPSG:3006', always_xy := true) p)").fetchone()
    print(f"projection: JS ({proj['x']:.2f}, {proj['y']:.2f}) vs DuckDB ({x:.2f}, {y:.2f})")
    failures = 0
    for (tool, args), j in zip(CASES, js):
        p = python_result(con, tool, args)
        issues = []
        if "error" in j:
            issues.append(j["error"])
        else:
            for k, v in p["summary"].items():
                if k in j["summary"] and not close(v, j["summary"][k], 0.002):
                    issues.append(f"summary.{k}: py={v} js={j['summary'][k]}")
            for key, jkey in (("table", "table"), ("chains", None)):
                rows_p = p.get(key) or []
                rows_j = (j.get("table") if key == "table" else (j.get("extra") or {}).get("host_chains")) or []
                for i, (rp, rj) in enumerate(zip(rows_p, rows_j[:10])):
                    for k, v in rp.items():
                        if k in rj and not close(v, rj[k], 0.002) and not (v != v):  # skip NaN
                            issues.append(f"{key}[{i}].{k}: py={v} js={rj[k]}")
        status = "OK " if not issues else "DIFF"
        failures += bool(issues)
        print(f"[{status}] {tool} {json.dumps(args, ensure_ascii=False)}")
        for issue in issues[:6]:
            print("        ", issue)
    print(f"\n{len(CASES) - failures}/{len(CASES)} cases match")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
