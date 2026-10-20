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
