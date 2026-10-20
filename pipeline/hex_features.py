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
