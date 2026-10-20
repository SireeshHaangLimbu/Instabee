from pipeline import common


def test_register_source_records_row_count():
    con = common.connect(path=":memory:")
    con.execute("CREATE TABLE t AS SELECT * FROM range(3)")
    common.register_source(con, "t", "Test source", "https://example.org", "CC0", "2026-10-20")
    common.register_source(con, "t", "Test source", "https://example.org", "CC0", "2026-10-20")
    rows = con.execute("SELECT table_name, row_count FROM sources").fetchall()
    assert rows == [("t", 3)]


def test_connect_loads_spatial_and_h3():
    con = common.connect(path=":memory:")
    assert con.execute("SELECT ST_X(ST_Point(1, 2))").fetchone()[0] == 1
    assert con.execute("SELECT h3_h3_to_string(h3_latlng_to_cell(59.85, 17.63, 9))").fetchone()[0]


def test_latest_snapshot_picks_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "RAW", tmp_path)
    (tmp_path / "src" / "2026-01-01").mkdir(parents=True)
    (tmp_path / "src" / "2026-02-01").mkdir()
    assert common.latest_snapshot("src").name == "2026-02-01"
