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
