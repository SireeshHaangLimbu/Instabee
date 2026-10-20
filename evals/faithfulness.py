"""Check that every number in an answer appears in the tool outputs of the same run."""

from __future__ import annotations

import re

# 1,234,567 / 1 234 567 (incl. no-break spaces) / 1234.5 — not parts of ids such as 0380C2140 or H3 cells.
NUMBER = re.compile(r"(?<![\w.,])(\d{1,3}(?:[,   ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\w])")
RELATIVE_TOLERANCE = 0.02  # "about 64,000" for 64,086


def extract_numbers(text: str) -> list[float]:
    out = []
    for match in NUMBER.finditer(text):
        raw = re.sub(r"[,   ]", "", match.group(1))
        value = float(raw)
        out.append(int(value) if value.is_integer() and "." not in raw else value)
    return out


def _decimals(value: float) -> int:
    text = repr(value)
    return len(text.split(".")[1]) if "." in text and not text.endswith(".0") else 0


def _supported(value: float, pool: list[float]) -> bool:
    d = _decimals(value)
    for t in pool:
        for candidate in (t, t * 100 if 0 < t < 1 else None):  # 0.8 in a tool output = "80%" in prose
            if candidate is None:
                continue
            if round(candidate, d) == value or (candidate and abs(value - candidate) / abs(candidate) <= RELATIVE_TOLERANCE):
                return True
    return False


def unsupported_numbers(answer: str, tool_outputs: list[str], question: str = "", context: str = "") -> list[float]:
    """Numbers in the answer found in neither the tool outputs nor the context the model was given."""
    pool = [n for text in [*tool_outputs, context] for n in extract_numbers(text)]
    asked = set(extract_numbers(question))
    flagged = []
    for value in extract_numbers(answer):
        if value <= 10 or (isinstance(value, int) and 1990 <= value <= 2035) or value in asked:
            continue
        if not _supported(value, pool) and value not in flagged:
            flagged.append(value)
    return flagged
