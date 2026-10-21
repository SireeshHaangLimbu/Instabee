/* Locker network tools for the browser: a port of agent/analysis.py that runs on the files in data/.
 * Works in the page (window.LockerTools) and in Node (module.exports) for parity tests. */
(function (root) {
  "use strict";

  const CAP_M = 5000;
  const NETWORKS = {
    instabee: { label: "Instabee", dist: "ib", own: (n) => n === "Instabee" },
    postnord: { label: "PostNord", dist: "pn", own: (n) => n === "PostNord" },
    competitors: { label: "competitors", dist: "comp", own: (n) => n !== "Instabee" },
    any: { label: "any network", dist: "any", own: () => true },
  };
  const CANDIDATE_HOST_TYPES = ["supermarket", "convenience", "fuel", "pharmacy", "mall"];
  const MONTH_DAYS = { jan: 31, feb: 28, mar: 31, apr: 30, may: 31, jun: 30, jul: 31, aug: 31, sep: 30, oct: 31, nov: 30, dec: 31 };
  const SWEDEN = new Set(["sweden", "sverige", "se", "all", "national", "riket", "whole country"]);
  const ENGLISH = { gothenburg: "goteborg" };
  const LOWER_BOUND_NOTE = "Instabee lockers come from OpenStreetMap, which holds only part of Instabee's real network, " +
    "so Instabee coverage is a lower bound and gaps are overstated.";
  const POPULATION_NOTE = "Population per hexagon is an estimate: the SCB 1 km grid spread over residential buildings.";

  // ---------- helpers ----------
  const round = (v, d = 0) => { const f = 10 ** d; return Math.round(v * f) / f; };
  const fold = (s) => {
    let t = String(s).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
    for (const suf of [" kommun", " municipality", " stad", " city"]) if (t.endsWith(suf)) t = t.slice(0, -suf.length);
    return ENGLISH[t] || t;
  };
  function similarity(a, b) {
    const m = a.length, n = b.length, d = Array.from({ length: m + 1 }, (_, i) => [i, ...Array(n).fill(0)]);
    for (let j = 1; j <= n; j++) d[0][j] = j;
    for (let i = 1; i <= m; i++) for (let j = 1; j <= n; j++)
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    return 1 - d[m][n] / Math.max(m, n, 1);
  }
  function network(name) {
    const key = String(name ?? "Instabee").trim().toLowerCase();
    if (!NETWORKS[key]) throw new Error(`network must be one of Instabee, PostNord, competitors, any; got ${name}`);
    return NETWORKS[key];
  }
  function radius(r) {
    const v = Number(r ?? 500);
    if (!(v > 0 && v <= CAP_M)) throw new Error(`radius_m must be between 1 and ${CAP_M} m; got ${r}`);
    return v;
  }
  const notesFor = (label) => [...(label === "Instabee" || label === "any network" ? [LOWER_BOUND_NOTE] : []), POPULATION_NOTE];
  function quantileCont(values, q) {
    if (!values.length) return null;
    const s = [...values].sort((a, b) => a - b), pos = (s.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos);
    return s[lo] + (s[hi] - s[lo]) * (pos - lo);
  }

  // SWEREF99 TM (EPSG:3006) forward projection, Gauss-Krüger formulas as published by Lantmäteriet.
  function toSweref(lat, lon) {
    const a = 6378137, f = 1 / 298.257222101, k0 = 0.9996, FE = 500000, lon0 = 15 * Math.PI / 180;
    const e2 = f * (2 - f), n = f / (2 - f), aRoof = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64);
    const A = e2, B = (5 * e2 ** 2 - e2 ** 3) / 6, C = (104 * e2 ** 3 - 45 * e2 ** 4) / 120, D = 1237 * e2 ** 4 / 1260;
    const b1 = n / 2 - 2 * n * n / 3 + 5 * n ** 3 / 16 + 41 * n ** 4 / 180, b2 = 13 * n * n / 48 - 3 * n ** 3 / 5 + 557 * n ** 4 / 1440;
    const b3 = 61 * n ** 3 / 240 - 103 * n ** 4 / 140, b4 = 49561 * n ** 4 / 161280;
    const phi = lat * Math.PI / 180, dl = lon * Math.PI / 180 - lon0, s = Math.sin(phi);
    const phiStar = phi - s * Math.cos(phi) * (A + B * s ** 2 + C * s ** 4 + D * s ** 6);
    const xi = Math.atan(Math.tan(phiStar) / Math.cos(dl)), eta = Math.atanh(Math.cos(phiStar) * Math.sin(dl));
    const N = k0 * aRoof * (xi + b1 * Math.sin(2 * xi) * Math.cosh(2 * eta) + b2 * Math.sin(4 * xi) * Math.cosh(4 * eta)
      + b3 * Math.sin(6 * xi) * Math.cosh(6 * eta) + b4 * Math.sin(8 * xi) * Math.cosh(8 * eta));
    const E = k0 * aRoof * (eta + b1 * Math.cos(2 * xi) * Math.sinh(2 * eta) + b2 * Math.cos(4 * xi) * Math.sinh(4 * eta)
      + b3 * Math.cos(6 * xi) * Math.sinh(6 * eta) + b4 * Math.cos(8 * xi) * Math.sinh(8 * eta)) + FE;
    return { x: E, y: N };
  }

  // ---------- data ----------
  function createTools(loader, { onLoad } = {}) {
    let ready = null, meta, lockers, hosts, kByCode, kList;
    const counties = new Map();

    function init() {
      ready ??= (async () => {
        [meta, lockers, hosts] = await Promise.all([loader.json("meta.json"), loader.json("lockers.json"), loader.json("hosts.json")]);
        kList = meta.kommuner;
        kByCode = new Map(kList.map((k, i) => [k.code, { ...k, index: i }]));
        lockers = lockers.map(([network, name, lat, lon, x, y, kommun]) => ({ network, name, lat, lon, x, y, kommun }));
        hosts = hosts.map(([id, name, chain, type, lat, lon, x, y, kommun]) => ({ id, name, chain, type, lat, lon, x, y, kommun }));
      })();
      return ready;
    }

    async function county(lan) {
      if (!counties.has(lan)) {
        counties.set(lan, (async () => {
          const info = meta.counties[lan];
          onLoad?.(`Loading map data for county ${lan}`);
          const buf = await loader.binary(info.file);
          const dv = new DataView(buf), n = dv.getUint32(0, true), out = { n };
          let off = 4;
          for (const c of meta.layout.f32) { out[c] = new Float32Array(buf, off, n); off += 4 * n; }
          for (const c of meta.layout.u16) { out[c] = new Uint16Array(buf, off, n); off += 2 * n; }
          return out;
        })());
      }
      return counties.get(lan);
    }

    function resolveArea(area) {
      if (area == null || SWEDEN.has(fold(area))) return { name: "Sweden", code: null };
      const text = String(area).trim();
      if (kByCode.has(text)) return { name: kByCode.get(text).name, code: text };
      const byName = new Map(kList.map((k) => [fold(k.name), k]));
      const hit = byName.get(fold(text));
      if (hit) return { name: hit.name, code: hit.code };
      const close = [...byName.entries()].map(([f, k]) => [similarity(fold(text), f), k.name])
        .filter(([s]) => s >= 0.6).sort((a, b) => b[0] - a[0]).slice(0, 3).map(([, n]) => n);
      throw new Error(`Unknown area '${area}'. Use a Swedish municipality name or 'Sweden'.` + (close.length ? ` Did you mean: ${close.join(", ")}?` : ""));
    }

    function municipality(area) {
      const a = resolveArea(area);
      if (!a.code) throw new Error("This tool works on one municipality at a time; pass a municipality name such as 'Uppsala'.");
      return a;
    }

    // Visit every hexagon of an area: fn(countyArrays, index, distance)
    async function eachHex(a, net, fn) {
      const lans = a.code ? [kByCode.get(a.code).lan] : Object.keys(meta.counties);
      const kIdx = a.code ? kByCode.get(a.code).index : -1;
      for (const lan of lans) {
        const c = await county(lan);
        for (let i = 0; i < c.n; i++) {
          if (kIdx >= 0 && c.k[i] !== kIdx) continue;
          const d = net.dist === "any" ? Math.min(c.d_ib[i], c.d_comp[i]) : c["d_" + net.dist][i];
          fn(c, i, d);
        }
      }
    }

    function demandParams() { return meta.demand; }

    // ---------- tools ----------
    async function coverage({ area, network: netName, radius_m, group_by, min_population } = {}) {
      await init();
      const net = network(netName), r = radius(radius_m), a = resolveArea(area);
      if (!group_by) {
        let pop = 0, cov = 0;
        await eachHex(a, net, (c, i, d) => { pop += c.pop[i]; if (d <= r) cov += c.pop[i]; });
        return { summary: { area: a.name, network: net.label, radius_m: r, population: round(pop), covered_population: round(cov), pct_covered: round(100 * cov / pop, 1) }, notes: notesFor(net.label) };
      }
      if (group_by !== "kommun") throw new Error("group_by must be null or 'kommun'");
      const minPop = Number(min_population ?? 20000);
      const agg = new Map();
      await eachHex(a, net, (c, i, d) => {
        const k = c.k[i]; const g = agg.get(k) || { pop: 0, cov: 0 };
        g.pop += c.pop[i]; if (d <= r) g.cov += c.pop[i]; agg.set(k, g);
      });
      const lockerCount = new Map();
      for (const l of lockers) if (l.kommun && net.own(l.network)) lockerCount.set(l.kommun, (lockerCount.get(l.kommun) || 0) + 1);
      const rows = [...agg.entries()].map(([k, g]) => {
        const info = kList[k], n = lockerCount.get(info.code) || 0;
        return { kommun_name: info.name, kommun_code: info.code, population: round(g.pop), covered_population: round(g.cov),
          pct_covered: round(100 * g.cov / g.pop, 1), lockers: n, lockers_per_10k: round(n * 10000 / g.pop, 2) };
      }).filter((row) => row.population >= minPop).sort((x, y) => x.pct_covered - y.pct_covered || y.population - x.population);
      return { summary: { area: a.name, network: net.label, radius_m: r, municipalities: rows.length, min_population: minPop }, table: rows.slice(0, 40), notes: notesFor(net.label) };
    }

    async function coverage_gaps({ area, network: netName, radius_m, top_n } = {}) {
      await init();
      const net = network(netName), r = radius(radius_m), a = resolveArea(area);
      const groups = new Map();
      await eachHex(a, net, (c, i, d) => {
        if (c.d[i] === 65535) return;
        const g = groups.get(c.d[i]) || { pop: 0, unc: 0, lat: 0, lon: 0, dists: [], k: c.k[i] };
        g.pop += c.pop[i];
        if (d > r) { g.unc += c.pop[i]; g.lat += c.lat[i] * c.pop[i]; g.lon += c.lon[i] * c.pop[i]; g.dists.push(d); }
        groups.set(c.d[i], g);
      });
      const rows = [...groups.entries()].filter(([, g]) => g.unc > 0).map(([di, g]) => ({
        deso_code: meta.deso[di][0], kommun_name: kList[g.k].name, population: round(g.pop), uncovered_population: round(g.unc),
        pct_uncovered: round(100 * g.unc / g.pop, 1), median_distance_m: round(quantileCont(g.dists, 0.5)),
        lat: round(g.lat / g.unc, 5), lon: round(g.lon / g.unc, 5),
      })).sort((x, y) => y.uncovered_population - x.uncovered_population).slice(0, Number(top_n ?? 10));
      return { summary: { area: a.name, network: net.label, radius_m: r, areas_listed: rows.length, uncovered_population_listed: rows.reduce((s, x) => s + x.uncovered_population, 0) },
        table: rows, notes: [...notesFor(net.label), `Distances are straight-line and capped at ${CAP_M} m.`] };
    }

    async function demand_estimate({ area, locker_share } = {}) {
      await init();
      const a = resolveArea(area), p = demandParams();
      let share = locker_share == null ? p.locker_share : Number(locker_share);
      if (share > 1) share /= 100;
      let pop = 0, weighted = 0;
      await eachHex(a, NETWORKS.instabee, (c, i) => { pop += c.pop[i]; weighted += c.demand[i]; });
      const parcels = weighted * p.parcels_per_person;
      const w = Object.entries(p.weights).map(([b, v]) => `${b.replace("pop_", "").replace("_", "-")}: ${v.toFixed(2)}`).join(", ");
      return {
        summary: { area: a.name, population: round(pop), age_weighted_population: round(weighted), parcels_per_person: round(p.parcels_per_person, 2),
          ecommerce_parcels_per_year: round(parcels), locker_share_pct: round(share * 100, 1), locker_parcels_per_year: round(parcels * share) },
        notes: [
          `Estimate, not a measurement: Sweden's ${p.parcels_period} e-commerce parcels (Paketindex) divided by population gives ${p.parcels_per_person.toFixed(1)} parcels per person per year.`,
          `Age weights (relative to the national average) come from SCB's 2025 survey of how often each age group buys online: ${w}.`,
          `Locker share ${Math.round(share * 100)}% is the national June 2026 figure (Paketindex survey); local shares differ.`,
          "Internal checkout and delivery-address data would replace every one of these assumptions.", POPULATION_NOTE],
      };
    }

    async function areaHexes(a, net) {
      const h = { x: [], y: [], lat: [], lon: [], pop: [], demand: [], d: [] };
      await eachHex(a, net, (c, i, d) => {
        h.x.push(c.x[i]); h.y.push(c.y[i]); h.lat.push(c.lat[i]); h.lon.push(c.lon[i]);
        h.pop.push(c.pop[i]); h.demand.push(c.demand[i]); h.d.push(d);
      });
      return h;
    }

    function candidateSites(a, kind, hostTypes, hx, r) {
      const types = hostTypes?.length ? hostTypes.map(String) : CANDIDATE_HOST_TYPES;
      const bad = types.filter((t) => !CANDIDATE_HOST_TYPES.includes(t));
      if (bad.length) throw new Error(`host_types must be among ${CANDIDATE_HOST_TYPES.join(", ")}; got ${bad.join(", ")}`);
      if (!["hosts", "hexes", "both"].includes(kind)) throw new Error("candidates must be 'hosts', 'hexes' or 'both'");
      const out = [];
      if (kind !== "hexes") for (const s of hosts) if (s.kommun === a.code && types.includes(s.type))
        out.push({ name: s.name, chain: s.chain, host_type: s.type, lat: s.lat, lon: s.lon, x: s.x, y: s.y });
      if (kind !== "hosts") {
        const idx = hx.d.map((d, i) => i).filter((i) => hx.d[i] > r).sort((i, j) => hx.pop[j] - hx.pop[i]).slice(0, 3000);
        for (const i of idx) out.push({ name: `Residential site at ${round(hx.lat[i], 4)}, ${round(hx.lon[i], 4)}`, chain: null,
          host_type: "residential", lat: hx.lat[i], lon: hx.lon[i], x: hx.x[i], y: hx.y[i] });
      }
      if (!out.length) throw new Error(`No candidate sites found in ${a.name} for ${kind}`);
      return out;
    }

    function covers(cands, hx, r) {
      const r2 = r * r + 1e-6;
      return cands.map((c) => { const idx = []; for (let i = 0; i < hx.x.length; i++) { const dx = hx.x[i] - c.x, dy = hx.y[i] - c.y; if (dx * dx + dy * dy <= r2) idx.push(i); } return idx; });
    }

    async function score_sites({ area, network: netName, radius_m, host_types, top_n } = {}) {
      await init();
      const net = network(netName), r = radius(radius_m), a = municipality(area), p = demandParams();
      const hx = await areaHexes(a, net), cands = candidateSites(a, "hosts", host_types, hx, r), cv = covers(cands, hx, r);
      const r2 = r * r + 1e-6;
      const rows = cands.map((c, ci) => {
        let catchment = 0, unc = 0, dem = 0;
        for (const i of cv[ci]) { catchment += hx.pop[i]; if (hx.d[i] > r) { unc += hx.pop[i]; dem += hx.demand[i]; } }
        let own = 0, comp = 0;
        for (const l of lockers) { const dx = l.x - c.x, dy = l.y - c.y; if (dx * dx + dy * dy <= r2) (net.own(l.network) ? own++ : comp++); }
        return { name: c.name, chain: c.chain, host_type: c.host_type, lat: round(c.lat, 5), lon: round(c.lon, 5),
          catchment_population: round(catchment), uncovered_population: round(unc),
          new_locker_parcels_est: round(dem * p.parcels_per_person * p.locker_share), network_lockers: own, competitor_lockers: comp };
      }).sort((x, y) => y.uncovered_population - x.uncovered_population || y.catchment_population - x.catchment_population)
        .slice(0, Number(top_n ?? 15)).map((row, i) => ({ rank: i + 1, ...row }));
      return { summary: { area: a.name, network: net.label, radius_m: r, candidates_scored: cands.length }, table: rows,
        notes: [...notesFor(net.label), "Each site is scored on its own; sites near each other share catchments. Use optimise_next_n for a set of sites.", "Host willingness, rent and footfall are not in public data."] };
    }

    async function optimise_next_n({ area, n, network: netName, radius_m, candidates, host_types } = {}) {
      await init();
      const net = network(netName), r = radius(radius_m), a = municipality(area), p = demandParams();
      const N = Number(n ?? 5);
      if (!(N >= 1 && N <= 50)) throw new Error("n must be between 1 and 50");
      const kind = candidates ?? "hosts";
      const hx = await areaHexes(a, net), cands = candidateSites(a, kind, host_types, hx, r), cv = covers(cands, hx, r);
      const remDemand = hx.demand.map((v, i) => (hx.d[i] > r ? v : 0)), remPop = hx.pop.map((v, i) => (hx.d[i] > r ? v : 0));
      const total = hx.pop.reduce((s, v) => s + v, 0), before = hx.pop.reduce((s, v, i) => s + (hx.d[i] > r ? 0 : v), 0);
      let covered = before; const rows = [];
      for (let rank = 1; rank <= N; rank++) {
        let best = -1, bestGain = 0;
        cv.forEach((idx, ci) => { let g = 0; for (const i of idx) g += remDemand[i]; if (g > bestGain) { bestGain = g; best = ci; } });
        if (best < 0 || bestGain <= 0) break;
        let newPop = 0; for (const i of cv[best]) { newPop += remPop[i]; remDemand[i] = 0; remPop[i] = 0; }
        covered += newPop;
        const c = cands[best];
        rows.push({ rank, name: c.name, chain: c.chain, host_type: c.host_type, lat: round(c.lat, 5), lon: round(c.lon, 5),
          new_population: round(newPop), new_locker_parcels_est: round(bestGain * p.parcels_per_person * p.locker_share),
          cumulative_pct_covered: round(100 * covered / total, 1) });
      }
      const notes = [...notesFor(net.label),
        "Greedy maximal covering on uncovered, age-weighted population: each pick adds the most demand not yet covered (within 1 - 1/e of the optimum).",
        "new_locker_parcels_est uses the placeholder demand model; see demand_estimate for its assumptions."];
      if (rows.length < N) notes.push(`Only ${rows.length} sites add any new coverage at ${r} m.`);
      return { summary: { area: a.name, network: net.label, radius_m: r, candidates: kind, sites_chosen: rows.length, population: round(total),
        pct_covered_before: round(100 * before / total, 1), pct_covered_after: round(100 * covered / total, 1),
        new_population_covered: rows.reduce((s, x) => s + x.new_population, 0) }, table: rows, notes };
    }

    async function overlap_check({ lat, lon, radius_m, network: netName } = {}) {
      await init();
      const net = network(netName), r = radius(radius_m), la = Number(lat), lo = Number(lon);
      if (!Number.isFinite(la) || !Number.isFinite(lo)) throw new Error("lat and lon must be numbers");
      const { x, y } = toSweref(la, lo), r2 = r * r + 1e-6;
      const lans = new Set();
      for (const [code, [x0, x1, y0, y1]] of Object.entries(meta.kommun_bbox))
        if (x >= x0 - r && x <= x1 + r && y >= y0 - r && y <= y1 + r) lans.add(kByCode.get(code).lan);
      let pop = 0, cov = 0;
      for (const lan of lans) {
        const c = await county(lan);
        for (let i = 0; i < c.n; i++) {
          const dx = c.x[i] - x, dy = c.y[i] - y;
          if (dx * dx + dy * dy > r2) continue;
          const d = net.dist === "any" ? Math.min(c.d_ib[i], c.d_comp[i]) : c["d_" + net.dist][i];
          pop += c.pop[i]; if (d <= r) cov += c.pop[i];
        }
      }
      let nearest = null, rivals = 0;
      for (const l of lockers) {
        const dist = Math.hypot(l.x - x, l.y - y);
        if (net.own(l.network)) nearest = nearest == null ? dist : Math.min(nearest, dist); else if (dist <= r) rivals++;
      }
      const pct = pop ? round(100 * cov / pop, 1) : 0;
      return { summary: { lat: la, lon: lo, network: net.label, radius_m: r, catchment_population: round(pop), already_covered_population: round(cov),
        already_covered_pct: pct, nearest_network_locker_m: nearest == null ? null : round(nearest), other_network_lockers_within_radius: rivals,
        verdict: pct >= 50 ? "mostly duplicates existing coverage" : pct >= 20 ? "partly overlaps existing coverage" : "adds mostly new coverage" },
        notes: notesFor(net.label) };
    }

    async function peak_sizing({ annual_locker_parcels, dwell_days, utilisation, peak_month } = {}) {
      await init();
      const annual = Number(annual_locker_parcels), dwell = Number(dwell_days ?? 2), util = Number(utilisation ?? 0.8);
      const month = String(peak_month ?? "dec").trim().toLowerCase().slice(0, 3);
      if (!(annual > 0)) throw new Error("annual_locker_parcels must be a positive number");
      if (!MONTH_DAYS[month]) throw new Error("peak_month must be a month name such as 'dec'");
      if (!(util > 0 && util <= 1)) throw new Error("utilisation must be between 0 and 1");
      const stat = meta.market_stats.find((m) => m.metric === `seasonality_share_${month}`);
      const avgDaily = annual / 365, peakDaily = annual * stat.value / 100 / MONTH_DAYS[month];
      const size = (daily) => Math.ceil(round(daily * dwell / util, 6));
      const avg = size(avgDaily), peak = size(peakDaily);
      return { summary: { annual_locker_parcels: annual, dwell_days: dwell, utilisation: util, peak_month: month, peak_month_share_pct: stat.value,
        parcels_per_day_average: round(avgDaily, 1), parcels_per_day_peak: round(peakDaily, 1), compartments_average: avg, compartments_peak: peak,
        extra_compartments_for_peak: peak - avg, peak_vs_average_pct: round(100 * (peak - avg) / avg, 1) },
        notes: ["Seasonality is the national 2021-2025 monthly average from Paketindex; a single site can peak harder.",
          "Dwell time and target utilisation are assumptions; internal fill-rate and dwell data replace them.",
          "Days are spread evenly within the month; Black Friday and the week before Christmas peak higher."] };
    }

    async function competitor_presence({ area, host_radius_m } = {}) {
      await init();
      const a = resolveArea(area), hr = Number(host_radius_m ?? 50), hr2 = hr * hr + 1e-6;
      const counts = new Map();
      for (const l of lockers) if (l.kommun && (!a.code || l.kommun === a.code)) counts.set(l.network, (counts.get(l.network) || 0) + 1);
      const table = [...counts.entries()].map(([networkName, n]) => ({ network: networkName, lockers: n })).sort((x, y) => y.lockers - x.lockers || x.network.localeCompare(y.network));
      const chains = new Map(), networks = new Set();
      for (const h of hosts) {
        if (h.type === "pickup_point" || !h.chain || (a.code && h.kommun !== a.code)) continue;
        const seen = new Set();
        for (const l of lockers) { const dx = l.x - h.x, dy = l.y - h.y; if (dx * dx + dy * dy <= hr2) seen.add(l.network); }
        for (const nw of seen) { networks.add(nw); const row = chains.get(h.chain) || {}; row[nw] = (row[nw] || 0) + 1; chains.set(h.chain, row); }
      }
      const nets = [...networks].sort();
      const hostChains = [...chains.entries()].map(([brand, row]) => {
        const out = { brand }; let total = 0; for (const nw of nets) { out[nw] = row[nw] || 0; total += out[nw]; } out.total = total; return out;
      }).sort((x, y) => y.total - x.total || (x.brand < y.brand ? -1 : x.brand > y.brand ? 1 : 0)).slice(0, 25);
      return { summary: { area: a.name, lockers_total: table.reduce((s, x) => s + x.lockers, 0), host_match_radius_m: hr, chains_hosting_lockers: chains.size },
        table, extra: { host_chains: hostChains },
        notes: [LOWER_BOUND_NOTE, `A host 'hosts' a locker when the locker is within ${hr} m of the store; this is a proxy, not a confirmed contract.`,
          "Chains are matched from Overture brand data or the start of the store name."] };
    }

    async function data_sources() {
      await init();
      return { sources: meta.sources, market_stats: meta.market_stats.filter((m) => !m.metric.startsWith("seasonality")) };
    }

    async function mapLayer(area, netName = "Instabee", radius_m = 500) {
      await init();
      const net = network(netName), r = radius(radius_m), a = municipality(area);
      const hx = await areaHexes(a, net);
      return { area: a.name, radius: r, network: net.label, hexes: hx, lockers: lockers.filter((l) => l.kommun === a.code) };
    }

    let shapesPromise = null;
    async function geo() {
      await init();
      shapesPromise ??= loader.json("kommun_shapes.json");
      return { shapes: await shapesPromise, places: meta.places, stats: meta.kommun_stats, kommuner: kList };
    }

    return { init, resolveArea, toSweref, mapLayer, geo, municipalities: () => kList,
      tools: { coverage, coverage_gaps, demand_estimate, score_sites, optimise_next_n, overlap_check, peak_sizing, competitor_presence, data_sources } };
  }

  const api = { createTools, CANDIDATE_HOST_TYPES };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.LockerTools = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
