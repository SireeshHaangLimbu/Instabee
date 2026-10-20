"""Pure helpers for SCB age codes and PxWeb JSON responses."""

from __future__ import annotations

import pandas as pd

AGE_BANDS = ["pop_0_19", "pop_20_34", "pop_35_64", "pop_65_plus"]


def age_lower_from_pxweb(code: str) -> int | None:
    """'-4' -> 0, '5-9' -> 5, '80-' -> 80, '100+' -> 100, 'totalt' -> None."""
    if code == "totalt":
        return None
    if code.startswith("-"):
        return 0
    return int(code.split("-")[0].rstrip("+"))


def age_lower_from_grid_column(col: str) -> int:
    """'ald0_4' -> 0, 'ald100w' -> 100."""
    return int(col[3:].split("_")[0].rstrip("w"))


def age_band(lower: int) -> str:
    if lower < 20:
        return "pop_0_19"
    if lower < 35:
        return "pop_20_34"
    if lower < 65:
        return "pop_35_64"
    return "pop_65_plus"


def pxweb_to_frame(response: dict) -> pd.DataFrame:
    cols = [c["code"] for c in response["columns"]]
    rows = [dict(zip(cols, item["key"] + item["values"])) for item in response["data"]]
    return pd.DataFrame(rows, columns=cols)
