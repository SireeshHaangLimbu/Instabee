import json

from agent import tools


def test_every_tool_spec_is_well_formed_and_dispatchable():
    names = [spec["name"] for spec in tools.TOOL_SPECS]
    assert len(names) == len(set(names))
    for spec in tools.TOOL_SPECS:
        assert spec["description"] and spec["input_schema"]["type"] == "object"
        assert spec["name"] in tools.HANDLERS


def test_execute_sql_returns_rows(mini_db):
    text, is_error = tools.run_tool(mini_db, "execute_sql", {"query": "SELECT network, count(*) AS n FROM lockers GROUP BY 1 ORDER BY 1"})
    assert not is_error
    assert "Instabee" in text and "PostNord" in text


def test_execute_sql_rejects_writes_and_reports_errors(mini_db):
    text, is_error = tools.run_tool(mini_db, "execute_sql", {"query": "DROP TABLE lockers"})
    assert is_error and "read-only" in text
    text, is_error = tools.run_tool(mini_db, "execute_sql", {"query": "SELECT 1; SELECT 2"})
    assert is_error and "one" in text
    text, is_error = tools.run_tool(mini_db, "execute_sql", {"query": "SELECT no_such_column FROM lockers"})
    assert is_error and "no_such_column" in text  # the real error, unlike the template's "Query failed"


def test_execute_sql_hides_geometry_bytes(mini_db):
    text, _ = tools.run_tool(mini_db, "execute_sql", {"query": "SELECT locker_id, geom FROM lockers LIMIT 1"})
    assert "<geometry>" in text


def test_inspect_table_and_list_tables(mini_db):
    text, is_error = tools.run_tool(mini_db, "inspect_table", {"table_name": "hosts"})
    assert not is_error and "host_type" in text and "rows: 4" in text
    text, is_error = tools.run_tool(mini_db, "inspect_table", {"table_name": "nope"})
    assert is_error and "Unknown table" in text
    text, _ = tools.run_tool(mini_db, "list_tables", {})
    assert "hex_features" in text


def test_analysis_tool_output_has_summary_table_and_notes(mini_db):
    text, is_error = tools.run_tool(mini_db, "optimise_next_n", {"area": "Testby", "n": 2})
    assert not is_error
    assert "pct_covered_after: 100" in text
    assert "OKQ8 Testby" in text
    assert "lower bound" in text


def test_bad_arguments_come_back_as_tool_errors(mini_db):
    text, is_error = tools.run_tool(mini_db, "coverage", {"area": "Testbi"})
    assert is_error and "Did you mean" in text
    text, is_error = tools.run_tool(mini_db, "coverage", {"area": "Testby", "unexpected": 1})
    assert is_error
    text, is_error = tools.run_tool(mini_db, "no_such_tool", {})
    assert is_error


def test_numpy_values_are_printed_as_plain_numbers(mini_db):
    text, _ = tools.run_tool(mini_db, "optimise_next_n", {"area": "Testby", "n": 1})
    assert "np.float64" not in text


def test_render_map_writes_html(mini_db, tmp_path, monkeypatch):
    monkeypatch.setattr(tools.maps, "OUTPUT_DIR", tmp_path)
    sites = [{"name": "OKQ8 Testby", "lat": 59.9, "lon": 17.6}]
    text, is_error = tools.run_tool(mini_db, "render_map", {"area": "Testby", "sites": sites})
    assert not is_error
    path = next(tmp_path.glob("*.html"))
    html = path.read_text(encoding="utf-8")
    assert "leaflet" in html and json.dumps("OKQ8 Testby") in html
    assert str(path) in text
