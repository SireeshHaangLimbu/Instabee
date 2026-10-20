"""Evaluation questions. Reference answers are computed from the analysis functions at run time,
so they stay correct when the data is refreshed."""

from __future__ import annotations

from dataclasses import dataclass

from agent import analysis

DATA_GAP = "It must say the public data (OpenStreetMap) holds only part of Instabee's network, so Instabee coverage is a lower bound."


@dataclass(frozen=True)
class Case:
    name: str
    question: str
    reference: str
    criteria: str


def _sites(table, n=5) -> str:
    return "; ".join(f"{r['name']} ({r['host_type']}, +{r['new_population']:,} people)" for _, r in table.head(n).iterrows())


def build_cases(con) -> list[Case]:
    cov = analysis.coverage(con, "Uppsala", network="Instabee", radius_m=500).summary
    pn = analysis.coverage(con, "Uppsala", network="PostNord", radius_m=500).summary
    worst = analysis.coverage(con, "Sweden", network="any", radius_m=500, group_by="kommun", min_population=50000).table.head(5)
    opt = analysis.optimise_next_n(con, "Uppsala", n=5, network="Instabee", radius_m=500, candidates="hosts")
    dem = analysis.demand_estimate(con, "Uppsala").summary
    peak = analysis.peak_sizing(con, 20000).summary
    gaps = analysis.coverage_gaps(con, "Göteborg", network="Instabee", radius_m=500, top_n=3).table
    chains = analysis.competitor_presence(con, "Sweden").extra["host_chains"].head(3)
    return [
        Case("uppsala coverage",
             "What share of Uppsala's residents live within 500 m of an Instabee locker, and how does that compare with PostNord?",
             f"Instabee {cov['pct_covered']}% ({cov['covered_population']:,} of {cov['population']:,}); "
             f"PostNord {pn['pct_covered']}% ({pn['covered_population']:,}).",
             "Both percentages must match the reference to one decimal. " + DATA_GAP),
        Case("under-covered cities",
             "Which five Swedish municipalities with over 50,000 residents have the lowest share of people within 500 m of any parcel locker?",
             "; ".join(f"{r.kommun_name} {r.pct_covered}%" for r in worst.itertuples()),
             "The five municipalities must match the reference (order may differ only for ties). It should note that zero "
             "lockers in OpenStreetMap may mean unmapped rather than absent."),
        Case("next five lockers",
             "Where should Instabee put its next 5 lockers in Uppsala, using shops, pharmacies and petrol stations as hosts and a 500 m radius?",
             f"{_sites(opt.table)}. Coverage {opt.summary['pct_covered_before']}% -> {opt.summary['pct_covered_after']}%.",
             "The same five sites as the reference in the same order, with the added population and the before/after "
             "coverage. " + DATA_GAP),
        Case("uppsala demand",
             "Roughly how many locker parcels a year does Uppsala municipality generate?",
             f"About {dem['locker_parcels_per_year']:,} locker parcels a year ({dem['ecommerce_parcels_per_year']:,} "
             f"e-commerce parcels x {dem['locker_share_pct']}% locker share).",
             "The figure must match the reference within 2% and be labelled as an estimate, naming the parcels-per-person "
             "and locker-share assumptions."),
        Case("peak sizing",
             "A new locker is expected to handle 20,000 parcels a year. How many compartments does it need for the December peak?",
             f"About {peak['compartments_peak']} compartments in December ({peak['compartments_average']} on average), "
             f"assuming {peak['dwell_days']:g}-day dwell and {peak['utilisation']:.0%} utilisation.",
             "The peak compartment count must match the reference, with the dwell-time and utilisation assumptions stated."),
        Case("gothenburg gaps",
             "Which parts of Göteborg have the most residents without an Instabee locker within 500 m?",
             "; ".join(f"DeSO {r.deso_code}: {r.uncovered_population:,.0f} people" for r in gaps.itertuples()),
             "The top three DeSO areas must match the reference. " + DATA_GAP),
        Case("chains hosting lockers",
             "Which retail chains in Sweden host the most parcel lockers, and for which networks?",
             "; ".join(f"{r.brand}: {int(r.total)} lockers" for r in chains.itertuples()),
             "The top three chains must match the reference, and it must say a nearby locker is a proxy for hosting, not a "
             "confirmed contract."),
        Case("utilisation gap",
             "What is the average utilisation of Instabox lockers in Uppsala?",
             "Not answerable from public data: there is no locker utilisation or fill-rate data. Internal fill-rate and dwell "
             "data per locker would answer it.",
             "It must not invent a utilisation figure. It must say the data is missing and name the internal data needed."),
    ]
