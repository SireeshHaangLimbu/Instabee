import pytest

from pipeline import common, hex_features

CELL_WKT_3006 = "POLYGON((647000 6638000, 648000 6638000, 648000 6639000, 647000 6639000, 647000 6638000))"
DESO_WKT_3006 = "POLYGON((640000 6630000, 660000 6630000, 660000 6650000, 640000 6650000, 640000 6630000))"


def _fixture(with_buildings: bool):
    con = common.connect(path=":memory:")
    con.execute(f"""CREATE TABLE pop_grid_1km AS SELECT '6470006638000' AS cell_id,
        ST_GeomFromText('{CELL_WKT_3006}') AS geom, 100 AS population, 40 AS ald20_24, 60 AS ald70_74""")
    con.execute(f"""CREATE TABLE deso AS SELECT 'T0001' AS deso_code, '0380' AS kommun_code,
        'Uppsala' AS kommun_name, ST_GeomFromText('{DESO_WKT_3006}') AS geom""")
    hexes = con.execute(f"""SELECT h3_cell_to_lat(h) AS lat, h3_cell_to_lng(h) AS lon FROM (
        SELECT unnest(h3_polygon_wkt_to_cells(ST_AsText(ST_Transform(ST_GeomFromText('{CELL_WKT_3006}'),
        'EPSG:3006', 'EPSG:4326', always_xy := true)), 9)) AS h) ORDER BY h LIMIT 2""").fetchall()
    (lat_a, lon_a), (lat_b, lon_b) = hexes
    rows = f"({lat_a}, {lon_a}, 300.0), ({lat_b}, {lon_b}, 100.0)" if with_buildings else ""
    con.execute("CREATE TABLE buildings (lat DOUBLE, lon DOUBLE, footprint_m2 DOUBLE)")
    if rows:
        con.execute(f"INSERT INTO buildings VALUES {rows}")
    point = "ST_Transform(ST_Point({lon}, {lat}), 'EPSG:4326', 'EPSG:3006', always_xy := true)"
    con.execute(f"""CREATE TABLE lockers AS
        SELECT 'osm:node/1' AS locker_id, 'Instabee' AS network, {point.format(lon=lon_a, lat=lat_a)} AS geom
        UNION ALL
        SELECT 'osm:node/2', 'PostNord', {point.format(lon=lon_a, lat=lat_a + 0.06)}""")
    con.execute(f"""CREATE TABLE hosts AS
        SELECT 1 AS host_id, 'supermarket' AS host_type, {point.format(lon=lon_a, lat=lat_a)} AS geom""")
    return con, (lat_a, lon_a)


def _hex_a(con, lat_a, lon_a):
    return con.execute(
        "SELECT * FROM hex_features WHERE h3 = h3_h3_to_string(h3_latlng_to_cell(?, ?, 9))", [lat_a, lon_a]
    ).df().iloc[0]


def test_population_follows_building_footprint():
    con, (lat_a, lon_a) = _fixture(with_buildings=True)
    hex_features.build(con)
    assert con.execute("SELECT count(*) FROM hex_features").fetchone()[0] == 2
    assert con.execute("SELECT sum(pop_est) FROM hex_features").fetchone()[0] == pytest.approx(100)
    assert con.execute("SELECT sum(pop_65_plus_est) FROM hex_features").fetchone()[0] == pytest.approx(60)
    row = _hex_a(con, lat_a, lon_a)
    assert row.pop_est == pytest.approx(75)
    assert row.kommun_name == "Uppsala"


def test_distances_counts_and_cap():
    con, (lat_a, lon_a) = _fixture(with_buildings=True)
    hex_features.build(con)
    row = _hex_a(con, lat_a, lon_a)
    assert row.dist_instabee_m == pytest.approx(0, abs=1)
    assert row.dist_postnord_m == 5000  # 6.7 km away, capped
    assert row.dist_any_competitor_m == 5000
    assert row.instabee_300m == 1 and row.lockers_300m == 1 and row.lockers_500m == 1
    assert row.supermarkets_300m == 1 and row.hosts_300m == 1 and row.pickup_points_300m == 0


def test_even_split_without_buildings():
    con, _ = _fixture(with_buildings=False)
    hex_features.build(con)
    n, total, low, high = con.execute(
        "SELECT count(*), sum(pop_est), min(pop_est), max(pop_est) FROM hex_features").fetchone()
    assert n >= 10
    assert total == pytest.approx(100)
    assert low == pytest.approx(high)
