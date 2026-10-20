import pytest

from pipeline.scb import (
    AGE_BANDS,
    age_band,
    age_lower_from_grid_column,
    age_lower_from_pxweb,
    pxweb_to_frame,
)


@pytest.mark.parametrize("code, lower", [("-4", 0), ("5-9", 5), ("80-", 80), ("100+", 100), ("totalt", None)])
def test_age_lower_from_pxweb(code, lower):
    assert age_lower_from_pxweb(code) == lower


@pytest.mark.parametrize("col, lower", [("ald0_4", 0), ("ald20_24", 20), ("ald100w", 100)])
def test_age_lower_from_grid_column(col, lower):
    assert age_lower_from_grid_column(col) == lower


@pytest.mark.parametrize("lower, band", [(0, "pop_0_19"), (15, "pop_0_19"), (20, "pop_20_34"), (30, "pop_20_34"),
                                         (35, "pop_35_64"), (60, "pop_35_64"), (65, "pop_65_plus"), (100, "pop_65_plus")])
def test_age_band(lower, band):
    assert age_band(lower) == band
    assert band in AGE_BANDS


def test_pxweb_to_frame():
    response = {
        "columns": [
            {"code": "Region", "text": "region", "type": "d"},
            {"code": "Alder", "text": "ålder", "type": "d"},
            {"code": "Tid", "text": "år", "type": "t"},
            {"code": "000007Y7", "text": "Antal", "type": "c"},
        ],
        "data": [
            {"key": ["0380C1010_DeSO2025", "-4", "2025"], "values": ["57"]},
            {"key": ["0380C1010_DeSO2025", "totalt", "2025"], "values": ["1200"]},
        ],
    }
    df = pxweb_to_frame(response)
    assert list(df.columns) == ["Region", "Alder", "Tid", "000007Y7"]
    assert df.iloc[1]["000007Y7"] == "1200"
