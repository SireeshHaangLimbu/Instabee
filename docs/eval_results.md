# Eval results — 2026-10-20

Backend: `claude-code` (headless Claude Code, `claude-opus-5-5`, effort medium, Claude subscription).
Judge: `claude-opus-5-5` against reference answers computed from the data at run time.
Command: `python -m evals.run_evals`. Raw results: `runs/evals_*.json` (not committed).

**8 of 8 passed (score 4 or 5 of 5).**

| Question | Score | Tool calls | Seconds |
|---|---|---|---|
| Share of Uppsala within 500 m of Instabee vs PostNord | 5 | 5 | 12 |
| Five least-covered municipalities over 50,000 residents | 4 | 2 | 13 |
| Next 5 Instabee lockers in Uppsala | 5 | 5 | 21 |
| Yearly locker parcels in Uppsala | 5 | 2 | 10 |
| December compartments for 20,000 parcels a year | 5 | 2 | 11 |
| Göteborg areas with most residents outside 500 m | 5 | 4 | 28 |
| Chains hosting the most lockers | 5 | 2 | 16 |
| Utilisation of Instabox lockers in Uppsala (unanswerable) | 5 | 5 | 14 |

The 4/5: the agent listed a sixth municipality tied at 0% (Örnsköldsvik), which the reference does not show.

## What the evals changed

| Finding | Fix |
|---|---|
| Agent invented a hypothetical locker share ("if it were 26%") | Prompt rule: no hypothetical values; rerun the tool with the parameter instead |
| Agent subtracted peak minus average compartments itself | `peak_sizing` returns `extra_compartments_for_peak` and `peak_vs_average_pct` |
| Total added population was 1 off the sum of the table rows | `optimise_next_n` total is now the sum of the rounded rows |
| Agent "corrected" a store name (Manha Torg to Manhattan Torg) | Prompt rule: use names as returned; flag odd ones in caveats |
| Checker flagged numbers from the system prompt and tool descriptions | Faithfulness context includes both; fractions match percentages (0.8 = 80%) |

Known checker limit: numbers in offers ("I can rerun with 70% fill") are flagged although they are not claims.
