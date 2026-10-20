"""Load hand-entered national market figures."""

from __future__ import annotations

from pipeline.common import MANUAL, register_source, today

MARKET_STATS_CSV = MANUAL / "market_stats.csv"


def load(con) -> None:
    con.execute(
        f"""CREATE OR REPLACE TABLE market_stats AS
        SELECT * FROM read_csv('{MARKET_STATS_CSV.as_posix()}', header = true,
            columns = {{'metric': 'VARCHAR', 'value': 'DOUBLE', 'unit': 'VARCHAR', 'period': 'VARCHAR',
                       'geography': 'VARCHAR', 'source_title': 'VARCHAR', 'source_url': 'VARCHAR', 'page': 'INTEGER'}})"""
    )
    register_source(con, "market_stats", "Paketindex Q2 2026, Transportföretagen (hand-entered)",
                    "https://via.tt.se/files/3235972/4535013/411099/sv", "Public report", today(),
                    notes="Next edition (Q3 2026) due week 45 of 2026")
