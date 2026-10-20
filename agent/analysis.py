"""Tested analysis functions behind the agent's tools. All maths happens here, never in the model."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from agent.areas import Area, resolve_area

CAP_M = 5000
NETWORKS = {
    # key: (distance expression on hex_features, label, SQL filter on lockers.network)
    "instabee": ("dist_instabee_m", "Instabee", "network = 'Instabee'"),
    "postnord": ("dist_postnord_m", "PostNord", "network = 'PostNord'"),
    "competitors": ("dist_any_competitor_m", "competitors", "network <> 'Instabee'"),
    "any": ("least(dist_instabee_m, dist_any_competitor_m)", "any network", "TRUE"),
}
AGE_WEIGHTS = {"pop_0_19": 0.4, "pop_20_34": 1.4, "pop_35_64": 1.2, "pop_65_plus": 0.6}
CANDIDATE_HOST_TYPES = ("supermarket", "convenience", "fuel", "pharmacy", "mall")
MONTH_DAYS = {"jan": 31, "feb": 28, "mar": 31, "apr": 30, "may": 31, "jun": 30,
              "jul": 31, "aug": 31, "sep": 30, "oct": 31, "nov": 30, "dec": 31}
# Overture's brand field is patchy, so chains are also matched on the start of the place name.
CHAINS = ["ICA", "Coop", "Willys", "Hemköp", "Lidl", "City Gross", "Tempo", "Netto", "Handlar'n", "Matöppet",
          "Preem", "Circle K", "OKQ8", "St1", "Ingo", "Qstar", "Tanka", "Shell", "Pressbyrån", "7-Eleven",
          "Apotek Hjärtat", "Apoteket", "Kronans Apotek", "Lloyds Apotek"]
LOWER_BOUND_NOTE = ("Instabee lockers come from OpenStreetMap, which holds only part of Instabee's real network, "
                    "so Instabee coverage is a lower bound and gaps are overstated.")
POPULATION_NOTE = "Population per hexagon is an estimate: the SCB 1 km grid spread over residential buildings."


@dataclass
class Result:
    summary: dict
    table: pd.DataFrame | None = None
    notes: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


def _network(network: str) -> tuple[str, str, str]:
    key = str(network).strip().lower()
    if key not in NETWORKS:
        raise ValueError(f"network must be one of Instabee, PostNord, competitors, any; got {network!r}")
    return NETWORKS[key]


def _radius(radius_m: float) -> float:
    if not 0 < radius_m <= CAP_M:
        raise ValueError(f"radius_m must be between 1 and {CAP_M} m; got {radius_m}")
    return float(radius_m)


def _notes(label: str) -> list[str]:
    return ([LOWER_BOUND_NOTE] if label in ("Instabee", "any network") else []) + [POPULATION_NOTE]


def chain_sql(brand_col: str = "brand", name_col: str = "name") -> str:
    cases = " ".join(
        f"WHEN lower({name_col}) LIKE '{c.lower().replace(chr(39), chr(39) * 2)}%' THEN '{c.replace(chr(39), chr(39) * 2)}'"
        for c in CHAINS)
    return f"coalesce({brand_col}, CASE {cases} END)"


def _municipality(con, area: str) -> Area:
    a = resolve_area(con, area)
    if a.is_sweden:
        raise ValueError("This tool works on one municipality at a time; pass a municipality name such as 'Uppsala'.")
    return a


# --- demand -------------------------------------------------------------------------------------

def demand_parameters(con) -> dict:
    parcels, period = con.execute(
        """SELECT value, period FROM market_stats WHERE metric = 'ecommerce_parcels' AND period NOT LIKE '%-%'
           ORDER BY period DESC LIMIT 1""").fetchone()
    nat = con.execute("SELECT sum(population), sum(pop_0_19), sum(pop_20_34), sum(pop_35_64), sum(pop_65_plus) FROM deso").fetchone()
    population, bands = float(nat[0]), {b: float(v) for b, v in zip(AGE_WEIGHTS, nat[1:])}
    mean_weight = sum(AGE_WEIGHTS[b] * bands[b] for b in AGE_WEIGHTS) / sum(bands.values())
    share = float(con.execute("SELECT value FROM market_stats WHERE metric = 'delivery_share_locker'").fetchone()[0])
    return {
        "parcels_per_person": float(parcels) / population,
        "parcels_period": period,
        "weights": {b: w / mean_weight for b, w in AGE_WEIGHTS.items()},
        "locker_share": share / 100,
    }


def demand_expression(weights: dict) -> str:
    return " + ".join(f"coalesce({band}_est, 0) * {w:.6f}" for band, w in weights.items())


def demand_estimate(con, area: str, locker_share: float | None = None) -> Result:
    a = resolve_area(con, area)
    p = demand_parameters(con)
    share = p["locker_share"] if locker_share is None else (locker_share / 100 if locker_share > 1 else locker_share)
    population, weighted = con.execute(
        f"SELECT sum(pop_est), sum({demand_expression(p['weights'])}) FROM hex_features WHERE {a.where()}").fetchone()
    parcels = weighted * p["parcels_per_person"]
    summary = {
        "area": a.name, "population": round(population), "age_weighted_population": round(weighted),
        "parcels_per_person": round(p["parcels_per_person"], 2),
        "ecommerce_parcels_per_year": round(parcels), "locker_share_pct": round(share * 100, 1),
        "locker_parcels_per_year": round(parcels * share),
    }
    weights = ", ".join(f"{b.removeprefix('pop_').replace('_', '-')}: {w:.2f}" for b, w in p["weights"].items())
    notes = [
        f"Estimate, not a measurement: Sweden's {p['parcels_period']} e-commerce parcels (Paketindex) divided by population "
        f"gives {p['parcels_per_person']:.1f} parcels per person per year.",
        f"Age weights are placeholders, not fitted to data (relative to the national average): {weights}.",
        f"Locker share {share * 100:.0f}% is the national June 2026 figure (Paketindex survey); local shares differ.",
        "Internal checkout and delivery-address data would replace every one of these assumptions.",
        POPULATION_NOTE,
    ]
    return Result(summary, notes=notes)


# --- coverage -----------------------------------------------------------------------------------

def coverage(con, area: str, network: str = "Instabee", radius_m: float = 500, group_by: str | None = None,
             min_population: float = 20000) -> Result:
    dist, label, locker_filter = _network(network)
    r = _radius(radius_m)
    a = resolve_area(con, area)
    covered = f"coalesce(sum(pop_est) FILTER (WHERE {dist} <= {r}), 0)"
    if group_by is None:
        pop, cov = con.execute(f"SELECT sum(pop_est), {covered} FROM hex_features WHERE {a.where()}").fetchone()
        summary = {"area": a.name, "network": label, "radius_m": r, "population": round(pop),
                   "covered_population": round(cov), "pct_covered": round(100 * cov / pop, 1)}
        return Result(summary, notes=_notes(label))
    if group_by != "kommun":
        raise ValueError("group_by must be null or 'kommun'")
    table = con.execute(f"""
        WITH h AS (
            SELECT kommun_code, kommun_name, sum(pop_est) AS pop, {covered} AS cov
            FROM hex_features WHERE {a.where()} AND kommun_code IS NOT NULL GROUP BY 1, 2),
        l AS (
            SELECT d.kommun_code, count(*) AS n FROM lockers l JOIN deso d ON ST_Within(l.geom, d.geom)
            WHERE {locker_filter} GROUP BY 1)
        SELECT h.kommun_name, h.kommun_code, round(h.pop) AS population, round(h.cov) AS covered_population,
               round(100 * h.cov / h.pop, 1) AS pct_covered, coalesce(l.n, 0) AS lockers,
               round(coalesce(l.n, 0) * 10000 / h.pop, 2) AS lockers_per_10k
        FROM h LEFT JOIN l USING (kommun_code)
        WHERE h.pop >= {float(min_population)}
        ORDER BY pct_covered, population DESC""").df()
    summary = {"area": a.name, "network": label, "radius_m": r, "municipalities": len(table),
               "min_population": min_population}
    return Result(summary, table, notes=_notes(label))


def coverage_gaps(con, area: str, network: str = "Instabee", radius_m: float = 500, top_n: int = 10) -> Result:
    dist, label, _ = _network(network)
    r = _radius(radius_m)
    a = resolve_area(con, area)
    gap = f"FILTER (WHERE {dist} > {r})"
    table = con.execute(f"""
        SELECT h.deso_code, any_value(h.kommun_name) AS kommun_name, round(sum(h.pop_est)) AS population,
               round(sum(h.pop_est) {gap}) AS uncovered_population,
               round(100 * sum(h.pop_est) {gap} / sum(h.pop_est), 1) AS pct_uncovered,
               round(median({dist}) {gap}) AS median_distance_m,
               round(sum(h.lat * h.pop_est) {gap} / sum(h.pop_est) {gap}, 5) AS lat,
               round(sum(h.lon * h.pop_est) {gap} / sum(h.pop_est) {gap}, 5) AS lon
        FROM hex_features h WHERE {a.where('h.kommun_code')} AND h.deso_code IS NOT NULL
        GROUP BY h.deso_code HAVING sum(h.pop_est) {gap} > 0
        ORDER BY uncovered_population DESC LIMIT {int(top_n)}""").df()
    summary = {"area": a.name, "network": label, "radius_m": r, "areas_listed": len(table),
               "uncovered_population_listed": round(table.uncovered_population.sum()) if len(table) else 0}
    notes = _notes(label) + [f"Distances are straight-line and capped at {CAP_M} m."]
    return Result(summary, table, notes=notes)


# --- site selection -----------------------------------------------------------------------------

def _hexes(con, a: Area, dist: str, weights: dict) -> pd.DataFrame:
    return con.execute(f"""SELECT h3, ST_X(geom) AS x, ST_Y(geom) AS y, pop_est AS pop,
               {demand_expression(weights)} AS demand, {dist} AS d
        FROM hex_features WHERE {a.where()}""").df()


def _candidates(con, a: Area, candidates: str, host_types, dist: str, r: float) -> pd.DataFrame:
    host_types = tuple(host_types or CANDIDATE_HOST_TYPES)
    bad = set(host_types) - set(CANDIDATE_HOST_TYPES)
    if bad:
        raise ValueError(f"host_types must be among {CANDIDATE_HOST_TYPES}; got {sorted(bad)}")
    frames = []
    if candidates in ("hosts", "both"):
        types = ", ".join(f"'{t}'" for t in host_types)
        frames.append(con.execute(f"""SELECT 'host:' || host_id AS cand_id, name, {chain_sql()} AS chain, host_type,
                   lat, lon, ST_X(geom) AS x, ST_Y(geom) AS y
            FROM hosts WHERE {a.where()} AND host_type IN ({types}) ORDER BY host_id""").df())
    if candidates in ("hexes", "both"):
        frames.append(con.execute(f"""SELECT 'hex:' || h3 AS cand_id,
                   'Residential site at ' || round(lat, 4) || ', ' || round(lon, 4) AS name, NULL AS chain,
                   'residential' AS host_type, lat, lon, ST_X(geom) AS x, ST_Y(geom) AS y
            FROM hex_features WHERE {a.where()} AND {dist} > {r} ORDER BY pop_est DESC LIMIT 3000""").df())
    if not frames:
        raise ValueError("candidates must be 'hosts', 'hexes' or 'both'")
    out = pd.concat(frames, ignore_index=True)
    if out.empty:
        raise ValueError(f"No candidate sites found in {a.name} for {candidates}")
    return out


def _cover_lists(cands: pd.DataFrame, hexes: pd.DataFrame, r: float) -> list[np.ndarray]:
    hx, hy = hexes.x.to_numpy(), hexes.y.to_numpy()
    covers = []
    for cx, cy in zip(cands.x.to_numpy(), cands.y.to_numpy()):
        covers.append(np.flatnonzero((hx - cx) ** 2 + (hy - cy) ** 2 <= r * r + 1e-6))
    return covers


def _locker_counts(con, cands: pd.DataFrame, r: float, locker_filter: str) -> tuple[np.ndarray, np.ndarray]:
    lockers = con.execute(f"SELECT ST_X(geom) AS x, ST_Y(geom) AS y, ({locker_filter}) AS own FROM lockers").df()
    if lockers.empty:
        zeros = np.zeros(len(cands), dtype=int)
        return zeros, zeros
    dx = cands.x.to_numpy()[:, None] - lockers.x.to_numpy()[None, :]
    dy = cands.y.to_numpy()[:, None] - lockers.y.to_numpy()[None, :]
    near = dx ** 2 + dy ** 2 <= r * r + 1e-6
    own = lockers.own.to_numpy(dtype=bool)
    return near[:, own].sum(axis=1), near[:, ~own].sum(axis=1)


def score_sites(con, area: str, network: str = "Instabee", radius_m: float = 500, host_types=None,
                top_n: int = 15) -> Result:
    dist, label, locker_filter = _network(network)
    r = _radius(radius_m)
    a = _municipality(con, area)
    p = demand_parameters(con)
    hexes = _hexes(con, a, dist, p["weights"])
    cands = _candidates(con, a, "hosts", host_types, dist, r)
    uncovered = (hexes.d > r).to_numpy()
    pop, demand = hexes["pop"].to_numpy(), hexes.demand.to_numpy()
    covers = _cover_lists(cands, hexes, r)
    own, comp = _locker_counts(con, cands, r, locker_filter)
    cands["catchment_population"] = [pop[i].sum() for i in covers]
    cands["uncovered_population"] = [pop[i][uncovered[i]].sum() for i in covers]
    cands["new_locker_parcels_est"] = [demand[i][uncovered[i]].sum() * p["parcels_per_person"] * p["locker_share"] for i in covers]
    cands["network_lockers"], cands["competitor_lockers"] = own, comp
    table = (cands.sort_values(["uncovered_population", "catchment_population"], ascending=False)
             .head(int(top_n)).drop(columns=["x", "y", "cand_id"]).round(
                 {"catchment_population": 0, "uncovered_population": 0, "new_locker_parcels_est": 0, "lat": 5, "lon": 5})
             .reset_index(drop=True))
    table.insert(0, "rank", range(1, len(table) + 1))
    summary = {"area": a.name, "network": label, "radius_m": r, "candidates_scored": len(cands)}
    notes = _notes(label) + [
        "Each site is scored on its own; sites near each other share catchments. Use optimise_next_n for a set of sites.",
        "Host willingness, rent and footfall are not in public data.",
    ]
    return Result(summary, table, notes=notes)


def optimise_next_n(con, area: str, n: int = 5, network: str = "Instabee", radius_m: float = 500,
                    candidates: str = "hosts", host_types=None) -> Result:
    dist, label, _ = _network(network)
    r = _radius(radius_m)
    a = _municipality(con, area)
    if not 1 <= int(n) <= 50:
        raise ValueError("n must be between 1 and 50")
    p = demand_parameters(con)
    hexes = _hexes(con, a, dist, p["weights"])
    cands = _candidates(con, a, candidates, host_types, dist, r)
    uncovered = (hexes.d > r).to_numpy()
    pop = hexes["pop"].to_numpy()
    remaining_demand = np.where(uncovered, hexes.demand.to_numpy(), 0.0)
    remaining_pop = np.where(uncovered, pop, 0.0)
    total, covered_before = pop.sum(), pop[~uncovered].sum()
    covers = _cover_lists(cands, hexes, r)
    chosen, covered = [], covered_before
    for rank in range(1, int(n) + 1):
        gains = np.array([remaining_demand[i].sum() for i in covers])
        best = int(np.argmax(gains))
        if gains[best] <= 0:
            break
        idx = covers[best]
        new_pop = remaining_pop[idx].sum()
        covered += new_pop
        row = cands.iloc[best]
        chosen.append({"rank": rank, "name": row["name"], "chain": row["chain"], "host_type": row.host_type,
                       "lat": round(row.lat, 5), "lon": round(row.lon, 5), "new_population": round(new_pop),
                       "new_locker_parcels_est": round(gains[best] * p["parcels_per_person"] * p["locker_share"]),
                       "cumulative_pct_covered": round(100 * covered / total, 1)})
        remaining_demand[idx] = 0.0
        remaining_pop[idx] = 0.0
    table = pd.DataFrame(chosen)
    summary = {"area": a.name, "network": label, "radius_m": r, "candidates": candidates, "sites_chosen": len(table),
               "population": round(total), "pct_covered_before": round(100 * covered_before / total, 1),
               "pct_covered_after": round(100 * covered / total, 1),
               # the sum of the rounded per-site figures, so the total matches the table exactly
               "new_population_covered": int(table.new_population.sum()) if len(table) else 0}
    notes = _notes(label) + [
        "Greedy maximal covering on uncovered, age-weighted population: each pick adds the most demand not yet covered "
        "(within 1 - 1/e of the optimum).",
        "new_locker_parcels_est uses the placeholder demand model; see demand_estimate for its assumptions.",
    ]
    if len(table) < int(n):
        notes.append(f"Only {len(table)} sites add any new coverage at {r:.0f} m.")
    return Result(summary, table, notes=notes)


def overlap_check(con, lat: float, lon: float, radius_m: float = 500, network: str = "Instabee") -> Result:
    dist, label, locker_filter = _network(network)
    r = _radius(radius_m)
    point = f"ST_Transform(ST_Point({float(lon)}, {float(lat)}), 'EPSG:4326', 'EPSG:3006', always_xy := true)"
    pop, cov = con.execute(f"""SELECT coalesce(sum(pop_est), 0), coalesce(sum(pop_est) FILTER (WHERE {dist} <= {r}), 0)
        FROM hex_features WHERE ST_DWithin(geom, {point}, {r})""").fetchone()
    nearest = con.execute(f"SELECT min(ST_Distance(geom, {point})) FROM lockers WHERE {locker_filter}").fetchone()[0]
    rivals = con.execute(f"SELECT count(*) FROM lockers WHERE NOT ({locker_filter}) AND ST_DWithin(geom, {point}, {r})").fetchone()[0]
    pct = round(100 * cov / pop, 1) if pop else 0.0
    verdict = ("mostly duplicates existing coverage" if pct >= 50 else
               "partly overlaps existing coverage" if pct >= 20 else "adds mostly new coverage")
    summary = {"lat": lat, "lon": lon, "network": label, "radius_m": r, "catchment_population": round(pop),
               "already_covered_population": round(cov), "already_covered_pct": pct,
               "nearest_network_locker_m": None if nearest is None else round(nearest),
               "other_network_lockers_within_radius": rivals, "verdict": verdict}
    return Result(summary, notes=_notes(label))


def peak_sizing(con, annual_locker_parcels: float, dwell_days: float = 2.0, utilisation: float = 0.8,
                peak_month: str = "dec") -> Result:
    month = peak_month.strip().lower()[:3]
    if month not in MONTH_DAYS:
        raise ValueError("peak_month must be a month name such as 'dec'")
    if not 0 < utilisation <= 1:
        raise ValueError("utilisation must be between 0 and 1")
    share = con.execute("SELECT value FROM market_stats WHERE metric = ?", [f"seasonality_share_{month}"]).fetchone()
    if share is None:
        raise ValueError(f"No seasonality figure for {month}")
    share = (float(share[0]),)
    avg_daily = annual_locker_parcels / 365
    peak_daily = annual_locker_parcels * share[0] / 100 / MONTH_DAYS[month]
    size = lambda daily: math.ceil(round(daily * dwell_days / utilisation, 6))
    summary = {"annual_locker_parcels": annual_locker_parcels, "dwell_days": dwell_days, "utilisation": utilisation,
               "peak_month": month, "peak_month_share_pct": share[0],
               "parcels_per_day_average": round(avg_daily, 1), "parcels_per_day_peak": round(peak_daily, 1),
               "compartments_average": size(avg_daily), "compartments_peak": size(peak_daily),
               "extra_compartments_for_peak": size(peak_daily) - size(avg_daily),
               "peak_vs_average_pct": round(100 * (size(peak_daily) - size(avg_daily)) / size(avg_daily), 1)}
    notes = ["Seasonality is the national 2021-2025 monthly average from Paketindex; a single site can peak harder.",
             "Dwell time and target utilisation are assumptions; internal fill-rate and dwell data replace them.",
             "Days are spread evenly within the month; Black Friday and the week before Christmas peak higher."]
    return Result(summary, notes=notes)


def competitor_presence(con, area: str, host_radius_m: float = 50) -> Result:
    a = resolve_area(con, area)
    lockers = con.execute(f"""SELECT l.network, count(*) AS lockers FROM lockers l
        JOIN deso d ON ST_Within(l.geom, d.geom) WHERE {a.where('d.kommun_code')}
        GROUP BY 1 ORDER BY 2 DESC, 1""").df()
    hosts = con.execute(f"""SELECT {chain_sql('h.brand', 'h.name')} AS brand, l.network, count(DISTINCT h.host_id) AS sites
        FROM hosts h JOIN lockers l ON ST_DWithin(h.geom, l.geom, {float(host_radius_m)})
        WHERE {a.where('h.kommun_code')} AND h.host_type <> 'pickup_point'
        GROUP BY 1, 2""").df()
    hosts = hosts.dropna(subset=["brand"])
    chains = (hosts.pivot_table(index="brand", columns="network", values="sites", aggfunc="sum", fill_value=0)
              .reset_index() if not hosts.empty else pd.DataFrame(columns=["brand"]))
    if not chains.empty:
        chains["total"] = chains.drop(columns="brand").sum(axis=1)
        chains = chains.sort_values(["total", "brand"], ascending=[False, True]).reset_index(drop=True)
    summary = {"area": a.name, "lockers_total": int(lockers.lockers.sum()) if len(lockers) else 0,
               "host_match_radius_m": host_radius_m, "chains_hosting_lockers": len(chains)}
    notes = [LOWER_BOUND_NOTE,
             f"A host 'hosts' a locker when the locker is within {host_radius_m:.0f} m of the store; this is a proxy, "
             "not a confirmed contract.",
             "Chains are matched from Overture brand data or the start of the store name."]
    return Result(summary, lockers, notes=notes, extra={"host_chains": chains})
