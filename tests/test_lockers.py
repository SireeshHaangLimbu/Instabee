import pandas as pd
import pytest

from pipeline.lockers import (
    haversine_m,
    manual_to_frame,
    merge_manual,
    normalise_network,
    osm_elements_to_frame,
)


@pytest.mark.parametrize(
    "brand, operator, expected",
    [
        ("Instabox", None, "Instabee"),
        ("instabox", None, "Instabee"),
        (None, "Instabee", "Instabee"),
        ("BudBee", None, "Instabee"),
        ("Instabee;Pressbyrån", None, "Instabee"),
        ("Postnord", None, "PostNord"),
        ("Bring", None, "Bring"),
        (None, "DHL Freight", "DHL"),
        ("Willys", None, "Other"),
        (None, None, "Other"),
        ("Willys", "PostNord", "PostNord"),
    ],
)
def test_normalise_network(brand, operator, expected):
    assert normalise_network(brand, operator) == expected


def test_haversine_one_hundredth_degree_latitude():
    assert haversine_m(59.0, 17.0, 59.01, 17.0) == pytest.approx(1112, abs=2)


def test_osm_elements_use_center_and_dedupe():
    elements = [
        {"type": "node", "id": 1, "lat": 59.8, "lon": 17.6, "tags": {"brand": "Instabox", "name": "A"}},
        {"type": "way", "id": 2, "center": {"lat": 59.9, "lon": 17.7}, "tags": {"operator": "PostNord"}},
        {"type": "node", "id": 1, "lat": 59.8, "lon": 17.6, "tags": {"brand": "Instabox"}},
    ]
    df = osm_elements_to_frame(elements, "2026-10-20")
    assert list(df.locker_id) == ["osm:node/1", "osm:way/2"]
    assert list(df.network) == ["Instabee", "PostNord"]
    assert df.loc[1, "lat"] == 59.9


def test_merge_manual_replaces_nearby_same_network_only():
    osm = osm_elements_to_frame(
        [
            {"type": "node", "id": 1, "lat": 59.80000, "lon": 17.6, "tags": {"brand": "Instabox"}},
            {"type": "node", "id": 2, "lat": 59.80005, "lon": 17.6, "tags": {"brand": "PostNord"}},
            {"type": "node", "id": 3, "lat": 59.90000, "lon": 17.6, "tags": {"brand": "Instabox"}},
        ],
        "2026-10-20",
    )
    manual_raw = pd.DataFrame(
        [{"brand": "Instabox", "name": "ICA X", "lat": 59.80010, "lon": 17.6, "address": "Gatan 1", "note": ""}]
    )
    merged = merge_manual(osm, manual_to_frame(manual_raw, "2026-10-20"))
    # node/1 is 11 m from the manual locker and is replaced; PostNord node/2 stays.
    assert sorted(merged.locker_id) == ["manual:1", "osm:node/2", "osm:node/3"]


def test_merge_manual_with_empty_manual_list():
    osm = osm_elements_to_frame([{"type": "node", "id": 1, "lat": 59.8, "lon": 17.6, "tags": {}}], "2026-10-20")
    empty = manual_to_frame(pd.DataFrame(columns=["brand", "name", "lat", "lon", "address", "note"]), "2026-10-20")
    assert list(merge_manual(osm, empty).locker_id) == ["osm:node/1"]
