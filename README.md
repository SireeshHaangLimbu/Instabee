# Instabee locker network agent

An AI agent that answers locker-network questions such as *"Where should Instabox put its next 10 lockers in
Uppsala?"* from public data, built on the
[data-agent-workshop](https://github.com/FanisPapakonstantinou/data-agent-workshop) pattern: a read-only DuckDB
warehouse, schema tools, a data contract in the system prompt, and an LLM-judge eval suite. Coverage, demand and
site selection are tested Python tools, so every number in an answer comes from code, not from the model.

## Setup

```bash
py -3.11 -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -m pipeline.run_all        # download public data and build data/lockers.duckdb (~15 min)
.venv/Scripts/python -m pytest                  # offline tests
```

## Two ways to run the model

The tools are served two ways; the prompt, tools and answers are the same.

| Backend | Model access | Needs |
|---|---|---|
| `claude-code` (default) | Headless Claude Code with the `instabee-lockers` MCP server | Claude Code signed in with a Claude subscription; **no API credits** |
| `api` | Anthropic API tool-use loop (`agent/loop.py`) | `ANTHROPIC_API_KEY` with credits in `.env` (plus `ANTHROPIC_WORKSPACE_ID` for a user-scoped key) |

```bash
.venv/Scripts/python scripts/setup_mcp.py      # writes .mcp.json (machine-specific paths)
```

## Use

```bash
.venv/Scripts/python -m agent "Where should Instabox put its next 10 lockers in Uppsala?"
.venv/Scripts/python -m agent --effort high "Which cities over 50,000 people are least covered by lockers?"
.venv/Scripts/python -m agent --backend api "..."   # Anthropic API instead
.venv/Scripts/python -m evals.run_evals             # 8 graded questions
```

**Interactive demo:** open a new Claude Code session in this folder, approve the `instabee-lockers` MCP server,
and ask a locker question in the chat. The `locker-analyst` skill (`.claude/skills/`) applies the same answer rules.

Or open `locker_agent.ipynb` (`pip install -r requirements-notebook.txt`, then `jupyter lab`). Part 2 of the
notebook runs the tools without any model.

Every run is logged to `runs/agent_runs.jsonl` (question, each tool call with input and output, answer, tokens).
Maps are written to `outputs/`.

## What it can answer

| Question | Tool |
|---|---|
| How much of a city lives within 300/500 m of an Instabee (or competitor) locker? | `coverage` |
| Which cities are under-covered relative to population? | `coverage` with `group_by=kommun` |
| Which neighbourhoods have the most people without a locker nearby? | `coverage_gaps` |
| Where should the next N lockers go? | `optimise_next_n` (greedy maximal covering) |
| Which host sites score best? | `score_sites` |
| Would a new site cannibalise existing lockers? | `overlap_check` |
| How many parcels does an area generate? | `demand_estimate` |
| How many compartments for the December peak? | `peak_sizing` |
| Which chains host competitor lockers? | `competitor_presence` |
| Anything else in the data | `execute_sql` (read-only) |

## Data

| Table | Source |
|---|---|
| `lockers` | OpenStreetMap `amenity=parcel_locker` (Overpass), per county |
| `deso`, `pop_grid_1km` | SCB DeSO 2025 and 1 km population grid 2025 |
| `hosts`, `buildings` | Overture Maps Places and Buildings, release 2026-09-23.1 |
| `market_stats` | Paketindex Q2 2026 (Transportföretagen), hand-entered with page numbers |
| `hex_features` | Derived: H3 resolution 9, population spread over buildings, distances to lockers |

**Main limitation:** OSM holds only part of Instabee's network (377 lockers), so Instabee coverage is a lower
bound. Add known lockers to `data/manual/own_lockers.csv` (`brand,name,lat,lon,address,note`) and rebuild with
`python -m pipeline.run_all --skip-download`; they replace OSM lockers of the same network within 20 m.

## Results

8 of 8 eval questions pass (judge scores 4-5 of 5); see `docs/eval_results.md` for the table and what the evals
changed. Designs: `docs/superpowers/specs/`.
