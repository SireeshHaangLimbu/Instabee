"""Derive the age weights for the demand model from SCB's survey of online shopping (2025).

SCB table LE0108T25N2 gives, per age group, the share of people who bought goods or services online
1-2, 3-5, 6-10 or more than 10 times in the last 3 months. From it:

1. purchases per person per age group = 1.5 x share(1-2) + 4 x share(3-5) + 8 x share(6-10) + 13 x share(>10)
   (mid-points of each range; ">10" is taken as 13; cells SCB suppresses as too uncertain count as 0;
   children 0-15 are not surveyed and count as 0 because parents order for them)
2. each of the model's four age bands gets the population-weighted average of the survey groups inside it,
   using Sweden's population by 5-year age group (SCB 1 km grid, 2025)
3. the demand model then rescales the bands so the average Swede weighs exactly 1

Writes data/manual/online_purchases_by_age.csv (the survey figures) and data/manual/age_weights.csv.
Run: .venv/Scripts/python scripts/derive_age_weights.py
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.common import MANUAL, connect  # noqa: E402

URL = "https://api.scb.se/OV0104/v1/doris/sv/ssd/LE/LE0108/LE0108G/LE0108T25N2"
GROUPS = ["16-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75-84", "85+"]
FREQ = {"0200": 1.5, "0202": 4.0, "0204": 8.0, "0206": 13.0}  # mid-points of 1-2, 3-5, 6-10, >10
# Which survey group covers each 5-year age band of the population grid (lower age of the band).
GROUP_FOR_AGE = {0: None, 5: None, 10: None, 15: "16-24", 20: "16-24", 25: "25-34", 30: "25-34", 35: "35-44",
                 40: "35-44", 45: "45-54", 50: "45-54", 55: "55-64", 60: "55-64", 65: "65-74", 70: "65-74",
                 75: "75-84", 80: "75-84", 85: "85+", 90: "85+", 95: "85+", 100: "85+"}
BAND_FOR_AGE = lambda a: "pop_0_19" if a < 20 else "pop_20_34" if a < 35 else "pop_35_64" if a < 65 else "pop_65_plus"


def fetch() -> dict[str, dict[str, float]]:
    query = {"query": [
        {"code": "GenomsnittGang", "selection": {"filter": "item", "values": list(FREQ)}},
        {"code": "Kon", "selection": {"filter": "item", "values": ["1+2"]}},
        {"code": "Redovisningsgrupp", "selection": {"filter": "item", "values": GROUPS}},
        {"code": "ContentsCode", "selection": {"filter": "item", "values": ["0000074N"]}},
        {"code": "Tid", "selection": {"filter": "item", "values": ["2025"]}}], "response": {"format": "json"}}
    data = json.loads(requests.post(URL, json=query, timeout=60).content.decode("utf-8-sig"))
    shares: dict[str, dict[str, float]] = {g: {} for g in GROUPS}
    for item in data["data"]:
        freq, _, group, _ = item["key"]
        value = item["values"][0]
        shares[group][freq] = 0.0 if value in ("..", "-") else float(value) / 100
    return shares


def main() -> None:
    shares = fetch()
    rate = {g: sum(FREQ[f] * s for f, s in shares[g].items()) for g in GROUPS}
    con = connect(read_only=True)
    cols = [r[0] for r in con.execute("DESCRIBE pop_grid_1km").fetchall() if r[0].startswith("ald")]
    totals = con.execute("SELECT " + ", ".join(f"sum({c})" for c in cols) + " FROM pop_grid_1km").fetchone()
    band_people, band_purchases = {}, {}
    for col, people in zip(cols, totals):
        lower = int(col[3:].split("_")[0].rstrip("w"))
        group = GROUP_FOR_AGE[lower]
        # Ages 15-19: only 16-19 are surveyed; 15-year-olds count as 0 (one fifth of the band).
        purchasing = people * (0.8 if lower == 15 else 1.0) if group else 0.0
        band = BAND_FOR_AGE(lower)
        band_people[band] = band_people.get(band, 0) + people
        band_purchases[band] = band_purchases.get(band, 0) + purchasing * (rate[group] if group else 0.0)
    weights = {b: band_purchases[b] / band_people[b] for b in band_people}

    with open(MANUAL / "online_purchases_by_age.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["age_group", "share_1_2", "share_3_5", "share_6_10", "share_gt10", "purchases_per_3_months", "source", "url", "year"])
        for g in GROUPS:
            w.writerow([g, *(round(shares[g].get(k, 0.0), 3) for k in FREQ), round(rate[g], 3),
                        "SCB LE0108T25N2: bought goods/services online, by number of times", URL, 2025])
    with open(MANUAL / "age_weights.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["band", "purchases_per_person_3_months", "people"])
        for band in ("pop_0_19", "pop_20_34", "pop_35_64", "pop_65_plus"):
            w.writerow([band, round(weights[band], 4), round(band_people[band])])
    for g in GROUPS:
        print(f"{g:6s} purchases per person per 3 months: {rate[g]:.2f}")
    for band in ("pop_0_19", "pop_20_34", "pop_35_64", "pop_65_plus"):
        print(f"{band:12s} {weights[band]:.3f}")


if __name__ == "__main__":
    main()
