import pytest

from agent import analysis
from agent.areas import resolve_area


def test_resolve_area_variants(mini_db):
    assert resolve_area(mini_db, "Testby").kommun_code == "9999"
    assert resolve_area(mini_db, "testby kommun").kommun_name == "Testby"
    assert resolve_area(mini_db, "9999").kommun_name == "Testby"
    assert resolve_area(mini_db, "Sweden").kommun_code is None
    assert resolve_area(mini_db, "Sverige").kommun_code is None
    with pytest.raises(ValueError, match="Did you mean.*Testby"):
        resolve_area(mini_db, "Testbi")


def test_coverage_by_network(mini_db):
    own = analysis.coverage(mini_db, "Testby", network="Instabee", radius_m=500).summary
    assert own["population"] == pytest.approx(2100)
    assert own["covered_population"] == pytest.approx(300)
    assert own["pct_covered"] == pytest.approx(14.3, abs=0.05)
    assert analysis.coverage(mini_db, "Testby", network="competitors", radius_m=500).summary["pct_covered"] == pytest.approx(52.4, abs=0.05)
    assert analysis.coverage(mini_db, "Testby", network="any", radius_m=500).summary["pct_covered"] == pytest.approx(66.7, abs=0.05)
    assert analysis.coverage(mini_db, "Testby", network="Instabee", radius_m=300).summary["covered_population"] == pytest.approx(100)


def test_coverage_ranked_by_municipality(mini_db):
    result = analysis.coverage(mini_db, "Sweden", network="any", radius_m=500, group_by="kommun", min_population=0)
    table = result.table
    assert list(table.kommun_name) == ["Annanstad", "Testby"]  # least covered first
    assert table.iloc[0].pct_covered == 0
    assert table.set_index("kommun_name").loc["Testby", "lockers"] == 2


def test_coverage_rejects_bad_inputs(mini_db):
    with pytest.raises(ValueError, match="radius_m"):
        analysis.coverage(mini_db, "Testby", radius_m=6000)
    with pytest.raises(ValueError, match="network"):
        analysis.coverage(mini_db, "Testby", network="Bring")


def test_coverage_gaps_ranked_by_uncovered_people(mini_db):
    table = analysis.coverage_gaps(mini_db, "Testby", network="Instabee", radius_m=500).table
    assert list(table.deso_code) == ["D2", "D1"]
    assert list(table.uncovered_population) == pytest.approx([1500, 300])


def test_demand_estimate(mini_db):
    result = analysis.demand_estimate(mini_db, "Testby")
    assert result.summary["parcels_per_person"] == pytest.approx(10)
    assert result.summary["ecommerce_parcels_per_year"] == pytest.approx(21000)
    assert result.summary["locker_parcels_per_year"] == pytest.approx(4830)
    assert any("SCB's 2025 survey" in note for note in result.notes)


def test_score_sites_ranks_by_uncovered_population(mini_db):
    table = analysis.score_sites(mini_db, "Testby", network="Instabee", radius_m=500).table
    assert list(table.name) == ["OKQ8 Testby", "ICA Hörnan", "ICA Testby"]  # pickup point excluded
    assert list(table.uncovered_population) == pytest.approx([1500, 1100, 700])
    assert table.set_index("name").loc["OKQ8 Testby", "competitor_lockers"] == 1


def test_optimise_next_n_greedy(mini_db):
    result = analysis.optimise_next_n(mini_db, "Testby", n=2, network="Instabee", radius_m=500, candidates="hosts")
    assert list(result.table.name) == ["OKQ8 Testby", "ICA Testby"]
    assert list(result.table.new_population) == pytest.approx([1500, 300])
    assert result.summary["pct_covered_before"] == pytest.approx(14.3, abs=0.05)
    assert result.summary["pct_covered_after"] == pytest.approx(100)
    assert result.summary["new_population_covered"] == result.table.new_population.sum()


def test_optimise_next_n_on_residential_hexes(mini_db):
    result = analysis.optimise_next_n(mini_db, "Testby", n=1, network="Instabee", radius_m=500, candidates="hexes")
    assert result.table.iloc[0].new_population == pytest.approx(1500)  # hex4 covers hex3-hex5


def test_optimise_requires_a_municipality(mini_db):
    with pytest.raises(ValueError, match="municipality"):
        analysis.optimise_next_n(mini_db, "Sweden", n=3)


def test_overlap_check(mini_db):
    lat, lon = mini_db.execute("SELECT lat, lon FROM hex_features WHERE h3 = 'h1'").fetchone()
    result = analysis.overlap_check(mini_db, lat, lon, radius_m=500, network="Instabee")
    assert result.summary["catchment_population"] == pytest.approx(600)
    assert result.summary["already_covered_pct"] == pytest.approx(50)
    assert result.summary["nearest_network_locker_m"] == pytest.approx(400, abs=1)


def test_peak_sizing(mini_db):
    result = analysis.peak_sizing(mini_db, annual_locker_parcels=3650, dwell_days=2, utilisation=0.8, peak_month="dec")
    assert result.summary["compartments_average"] == 25
    assert result.summary["compartments_peak"] == 32  # 3650 * 10.6% / 31 * 2 / 0.8 = 31.2
    assert result.summary["extra_compartments_for_peak"] == 7
    assert result.summary["peak_vs_average_pct"] == 28.0


def test_competitor_presence(mini_db):
    result = analysis.competitor_presence(mini_db, "Testby")
    counts = result.table.set_index("network")["lockers"]
    assert counts["Instabee"] == 1 and counts["PostNord"] == 1
    hosts = result.extra["host_chains"]
    assert hosts.set_index("brand").loc["ICA", "PostNord"] == 1
