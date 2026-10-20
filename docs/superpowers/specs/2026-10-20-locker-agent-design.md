# Locker network agent — design

Date: 2026-10-20
Status: decided autonomously at the user's request ("keep looping till you complete the whole agent"). Every decision below can be revisited.

## Goal

A question-answering agent over `data/lockers.duckdb` that answers locker-network questions such as
"Where should Instabox put its next 10 lockers in Uppsala?" with numbers that come only from tested tools,
cites its sources, and labels every estimate and data gap.

## Decisions

| Topic | Decision | Why |
|---|---|---|
| Base | Keep the workshop template's design: read-only DuckDB, `list_tables` / `inspect_table` / `execute_sql`, a data contract in the system prompt, an LLM-judge eval suite | The user asked to build on it |
| LLM client | Official `anthropic` Python SDK with a small explicit loop, instead of smolagents + LiteLLM | The template's LiteLLM 1.101.0 sends `reasoning_effort` as a thinking budget and rewrites history; Claude Opus 5.5 rejects both. An explicit loop also makes every tool call loggable for replay |
| Model | `claude-opus-5-5`, adaptive thinking with summarized display, effort `medium` (set explicitly) | Current default model; effort is configurable per run |
| Refusals | `fallbacks: "default"` under beta `server-side-fallback-2026-07-01`; the loop checks `stop_reason == "refusal"` before reading content | Recommended default |
| Auth | `ANTHROPIC_API_KEY` from `.env`; if `ANTHROPIC_WORKSPACE_ID` is set, send it as the `anthropic-workspace-id` header | The user's key is user-scoped |
| Maths | All numbers come from Python/SQL tools; the model never computes coverage or demand itself | Reproducible, testable answers |
| Placement algorithm | Greedy maximal covering on uncovered, age-weighted population (no solver dependency) | Greedy is within (1 − 1/e) of optimal and explainable in an interview |
| Distances | Straight-line, from `hex_features` (EPSG:3006); radius ≤ 5,000 m | Matches the data pipeline |
| Interfaces | CLI `python -m agent "question"`; a notebook mirroring the template | Template is notebook-first; CLI for quick checks |
| Backends (added) | Default `claude-code`: the tools served over MCP (`agent/mcp_server.py`) to headless Claude Code on a Claude subscription; `api`: the Anthropic API loop | The API account had no credits; a subscription covers a proof of concept, and MCP also gives an interactive demo in Claude Code |
| Maps | `render_map` writes a self-contained Leaflet HTML file to `outputs/` | No extra dependency |

## Tools exposed to the model

| Tool | Purpose (research question) | Key inputs |
|---|---|---|
| `list_tables`, `inspect_table`, `execute_sql` | Ad-hoc read-only SQL (template tools; SQL errors are returned, not hidden) | table, SELECT query |
| `coverage` | Share of population within a radius of a network; optionally ranked by municipality (Q1, Q9, Q12) | area, network, radius_m, group_by |
| `coverage_gaps` | DeSO areas with the most people outside the radius (Q1, Q12) | area, network, radius_m, top_n |
| `demand_estimate` | Yearly e-commerce and locker parcels for an area, with stated assumptions (Q6) | area, locker_share |
| `score_sites` | Rank candidate host sites individually (Q3) | area, host_types, radius_m |
| `optimise_next_n` | Pick N sites that add the most uncovered demand (Q2) | area, n, radius_m, candidates |
| `overlap_check` | How much of a site's catchment an existing network already covers (Q5) | lat, lon, radius_m |
| `peak_sizing` | Compartments needed at average and peak month (Q7) | annual_locker_parcels, dwell_days |
| `competitor_presence` | Lockers by network in an area, and host chains near competitor lockers (Q4, Q11) | area |
| `render_map` | Map of hexes, lockers and chosen sites | area, sites |

`network` ∈ `Instabee`, `PostNord`, `competitors` (every non-Instabee network), `any`.
`area` = a municipality name (`Uppsala`, `Göteborg`, accent-insensitive) or `Sweden`.

## Demand model

`parcels_per_person = ecommerce_parcels(2025) / Sweden population (DeSO)`; age weights
`0–19: 0.4, 20–34: 1.4, 35–64: 1.2, 65+: 0.6`, rescaled so the national population-weighted mean is 1.
Locker parcels = parcels × locker share (default `delivery_share_locker` = 23%). The weights are
placeholders, not fitted, and every answer that uses them says so.

## System prompt rules

1. State no number that did not come from a tool output in this conversation.
2. Label demand figures as estimates and name the assumptions.
3. Instabee coverage is a lower bound: OSM holds only part of Instabee's network (377 lockers).
4. When a question needs data the warehouse lacks (utilisation, costs, host willingness), say so and name the internal data that would answer it.
5. Answer format: recommendation first, then a compact table, then data dates and what to verify internally.

## Logging

Every run appends one JSON line to `runs/agent_runs.jsonl`: question, model, tool calls (name, input,
output, ms), answer, token usage, stop reason.

## Evaluation

`evals/run_evals.py` runs a fixed question set. Reference answers are computed directly from the
analysis functions at run time, so they stay correct when the data refreshes. Each answer is graded by
(1) an LLM judge (`claude-opus-5-5`) against the reference and criteria, and (2) a faithfulness check:
every number in the answer must appear in that run's tool outputs (after normalising separators and rounding).

## Tests (offline)

Analysis functions on a tiny synthetic warehouse (hexes on a line 400 m apart); tool dispatch
(SQL guard, error reporting, area resolution); the agent loop against a scripted fake client
(tool call → result → final answer, refusal handling, JSONL logging); faithfulness checker.
