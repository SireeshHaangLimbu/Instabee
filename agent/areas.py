"""Resolve a user-supplied area name to a municipality (kommun) or the whole of Sweden."""

from __future__ import annotations

import difflib
import unicodedata
from dataclasses import dataclass

SWEDEN_ALIASES = {"sweden", "sverige", "se", "all", "national", "riket", "whole country"}
ENGLISH_NAMES = {"gothenburg": "goteborg", "malmo": "malmo", "gavle": "gavle"}


@dataclass(frozen=True)
class Area:
    name: str
    kommun_code: str | None  # None means all of Sweden

    def where(self, column: str = "kommun_code") -> str:
        return "TRUE" if self.kommun_code is None else f"{column} = '{self.kommun_code}'"

    @property
    def kommun_name(self) -> str | None:
        return None if self.kommun_code is None else self.name

    @property
    def is_sweden(self) -> bool:
        return self.kommun_code is None


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().strip()
    for suffix in (" kommun", " municipality", " stad", " city"):
        text = text.removesuffix(suffix)
    return ENGLISH_NAMES.get(text, text)


def resolve_area(con, area: str | None) -> Area:
    if area is None or _fold(area) in SWEDEN_ALIASES:
        return Area("Sweden", None)
    rows = con.execute(
        "SELECT DISTINCT kommun_code, kommun_name FROM deso WHERE kommun_name IS NOT NULL").fetchall()
    text = area.strip()
    by_name = {}
    for code, name in rows:
        if text == code:
            return Area(name, code)
        by_name[_fold(name)] = (code, name)
    if _fold(text) in by_name:
        code, name = by_name[_fold(text)]
        return Area(name, code)
    close = difflib.get_close_matches(_fold(text), list(by_name), n=3, cutoff=0.6)
    hint = f" Did you mean: {', '.join(by_name[c][1] for c in close)}?" if close else ""
    raise ValueError(f"Unknown area {area!r}. Use a Swedish municipality name or 'Sweden'.{hint}")
