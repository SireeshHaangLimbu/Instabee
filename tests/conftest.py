"""A tiny synthetic warehouse whose answers can be worked out by hand.

Testby (kommun 9999): six hexes on an east-west line, 400 m apart, populations 100..600.
  hex0 x=650000  pop 100  DeSO D1   <- Instabee locker here
  hex1 x=650400  pop 200  DeSO D1
  hex2 x=650800  pop 300  DeSO D1   <- host H1 supermarket "ICA Testby"
  hex3 x=651200  pop 400  DeSO D2   <- host H3 pickup point (never a candidate)
  hex4 x=651600  pop 500  DeSO D2   <- host H2 fuel "OKQ8 Testby"
  hex5 x=652000  pop 600  DeSO D2   <- PostNord locker + host H4 supermarket "ICA Hörnan"
Annanstad (kommun 8888): one hex far away, pop 1000, no lockers.
All population is aged 35-64, so the age weight is exactly 1.
ecommerce_parcels 2025 = 31,000 for 3,100 people -> 10 parcels per person.
"""

import pytest

from pipeline import common

Y = 6_640_000
HEX_X = [650_000 + 400 * i for i in range(6)]
POPS = [100, 200, 300, 400, 500, 600]
TO_WGS = "'EPSG:3006', 'EPSG:4326', always_xy := true"
CAP = 5000


def _point(x, y=Y):
    return f"ST_Point({x}, {y})"


@pytest.fixture
def mini_db():
    con = common.connect(path=":memory:")
    rows = [(f"h{i}", x, "9999", "Testby", "D1" if i < 3 else "D2", POPS[i]) for i, x in enumerate(HEX_X)]
    rows.append(("h6", 700_000, "8888", "Annanstad", "D3", 1000))
    values = ", ".join(f"('{h}', {x}, '{k}', '{n}', '{d}', {p})" for h, x, k, n, d, p in rows)
    con.execute(f"CREATE TEMP TABLE _h (h3 VARCHAR, x DOUBLE, kommun_code VARCHAR, kommun_name VARCHAR, deso_code VARCHAR, pop DOUBLE)")
    con.execute(f"INSERT INTO _h VALUES {values}")
    con.execute(f"""CREATE TABLE lockers AS
        SELECT 'osm:node/1' AS locker_id, 'Instabee' AS network, 'Instabox Testby' AS name, {_point(HEX_X[0])} AS geom
        UNION ALL SELECT 'osm:node/2', 'PostNord', 'PostNord Box', {_point(HEX_X[5])}""")
    con.execute(f"""ALTER TABLE lockers ADD COLUMN lat DOUBLE; ALTER TABLE lockers ADD COLUMN lon DOUBLE;
        UPDATE lockers SET lat = ST_Y(ST_Transform(geom, {TO_WGS})), lon = ST_X(ST_Transform(geom, {TO_WGS}));""")
    con.execute(f"""CREATE TABLE hex_features AS
        WITH g AS (SELECT *, {_point('x')} AS geom FROM _h)
        SELECT g.h3, ST_Y(ST_Transform(g.geom, {TO_WGS})) AS lat, ST_X(ST_Transform(g.geom, {TO_WGS})) AS lon, g.geom,
               g.kommun_code, g.kommun_name, g.deso_code, g.pop AS pop_est,
               0.0 AS pop_0_19_est, 0.0 AS pop_20_34_est, g.pop AS pop_35_64_est, 0.0 AS pop_65_plus_est,
               least(coalesce((SELECT min(ST_Distance(g.geom, l.geom)) FROM lockers l WHERE l.network = 'Instabee'), {CAP}), {CAP}) AS dist_instabee_m,
               least(coalesce((SELECT min(ST_Distance(g.geom, l.geom)) FROM lockers l WHERE l.network <> 'Instabee'), {CAP}), {CAP}) AS dist_any_competitor_m,
               least(coalesce((SELECT min(ST_Distance(g.geom, l.geom)) FROM lockers l WHERE l.network = 'PostNord'), {CAP}), {CAP}) AS dist_postnord_m
        FROM g""")
    con.execute(f"""CREATE TABLE hosts AS
        SELECT * EXCLUDE (x), {_point('x')} AS geom FROM (VALUES
            (1, 'ICA Testby', 'ICA', 'supermarket', 'grocery_store', {HEX_X[2]}, '9999', 'D1'),
            (2, 'OKQ8 Testby', 'OKQ8', 'fuel', 'gas_station', {HEX_X[4]}, '9999', 'D2'),
            (3, 'Ombud Testby', NULL, 'pickup_point', 'post_office', {HEX_X[3]}, '9999', 'D2'),
            (4, 'ICA Hörnan', 'ICA', 'supermarket', 'grocery_store', {HEX_X[5]}, '9999', 'D2')
        ) t(host_id, name, brand, host_type, category, x, kommun_code, deso_code)""")
    con.execute(f"""ALTER TABLE hosts ADD COLUMN lat DOUBLE; ALTER TABLE hosts ADD COLUMN lon DOUBLE;
        UPDATE hosts SET lat = ST_Y(ST_Transform(geom, {TO_WGS})), lon = ST_X(ST_Transform(geom, {TO_WGS}));""")
    box = lambda x0, x1: f"ST_GeomFromText('POLYGON(({x0} {Y-500}, {x1} {Y-500}, {x1} {Y+500}, {x0} {Y+500}, {x0} {Y-500}))')"
    con.execute(f"""CREATE TABLE deso AS SELECT * FROM (VALUES
        ('D1', '9999', 'Testby', '99', 600.0, 0.0, 0.0, 600.0, 0.0, 300.0, {box(649_500, 651_000)}),
        ('D2', '9999', 'Testby', '99', 1500.0, 0.0, 0.0, 1500.0, 0.0, 700.0, {box(651_000, 652_500)}),
        ('D3', '8888', 'Annanstad', '88', 1000.0, 0.0, 0.0, 1000.0, 0.0, 450.0, {box(699_500, 700_500)})
    ) t(deso_code, kommun_code, kommun_name, lan_code, population, pop_0_19, pop_20_34, pop_35_64, pop_65_plus, households, geom)""")
    con.execute("""CREATE TABLE market_stats AS SELECT * FROM (VALUES
        ('ecommerce_parcels', 31000.0, 'parcels', '2025', 'Sweden', 'Test index', 'https://example.org', 7),
        ('delivery_share_locker', 23.0, 'percent', '2026-06', 'Sweden', 'Test index', 'https://example.org', 8),
        ('seasonality_share_nov', 10.1, 'percent of year', '2021-2025 average', 'Sweden', 'Test index', 'https://example.org', 14),
        ('seasonality_share_dec', 10.6, 'percent of year', '2021-2025 average', 'Sweden', 'Test index', 'https://example.org', 14)
    ) t(metric, value, unit, period, geography, source_title, source_url, page)""")
    con.execute("""CREATE TABLE sources AS SELECT * FROM (VALUES
        ('lockers', 'OpenStreetMap test', 'https://osm.org', 'ODbL 1.0', DATE '2026-10-20', 2, 'lower bound')
    ) t(table_name, source_title, url, licence, snapshot_date, row_count, notes)""")
    con.execute("DROP TABLE _h")
    return con
