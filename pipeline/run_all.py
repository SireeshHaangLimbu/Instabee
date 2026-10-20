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
