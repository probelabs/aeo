# Post-run scripts

- `judge_run.py` — LLM per-hit testimony + **vendor extract** + board actions.
  - Stance/position only on `brand_mentioned` cells → `judge.json`
  - Vendor names on every completed arm (hits and misses) → `vendors_judged.json`
  - Board brief → `board.json`
  - `--vendors-only` / `--stance-only` to run one pass. Re-runs skip completed keys (`prompt_id|engine|arm`).
- `render_judge_html.py` — HTML with board actions on top and stance-colored K/S marks. “Who got named” and “Named instead” prefer LLM vendors, union the regex competitor list.

```bash
# Directory of engine files:
AEO_BRAND=Tyk AEO_TYK_RUN=~/.aeo/runs/tyk100-20260901 python3.11 scripts/judge_run.py
AEO_TYK_RUN=~/.aeo/runs/tyk100-20260901 python3.11 scripts/render_judge_html.py

# Single evidence JSON, vendor extract only:
AEO_BRAND=Autheona python3.11 scripts/judge_run.py --vendors-only path/to/evidence.json
python3.11 scripts/render_judge_html.py path/to/evidence.json
```

`AEO_RUN` is an alias for `AEO_TYK_RUN`. Brand comes from `AEO_BRAND`, else the evidence `workspace.brand`. Config `competitors` seed aliases; they are not a ceiling.

Today the scripts default brand/paths to the Tyk board (`tyk100-20260901`). Point `AEO_TYK_RUN` at any run directory with the same evidence shape.
