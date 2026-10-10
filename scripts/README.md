# Post-run scripts

- `judge_run.py` — LLM per-hit testimony + **vendor extract** + board actions.
  - Stance/position only on `brand_mentioned` cells → `judge.json`
  - Vendor names on every completed arm (hits and misses) → `vendors_judged.json`
  - Board brief → `board.json`
  - `--vendors-only` / `--stance-only` to run one pass. Re-runs skip completed keys (`prompt_id|engine|arm`).
- `render_judge_html.py` — HTML with board actions on top and stance-colored K/S marks. “Who got named” = brand + **known** seed competitors. “Surprise competitors” is a separate amber section (names not on the seed list after normalize).
- `change_report.py` — after **two** completed boards on the same roster, a first-class diff (not two HTML reports by hand). Opens with a deterministic **executive narrative** (what the Δ means for the brand: comparable-engine story, engine split, share vs absolute, caveats, 2–4 practical bullets). Then competitor dynamics: brand vs field, rank table (↑/↓/NEW/OUT), new (known vs surprise), disappeared, risers/fallers. Then brand mention Δpp and prompt-level miss→hit / hit→miss. Prefers `vendors_judged.json`; regex fallback when a side lacks it. `--floor` (default 1 = count==0) is the NEW/OUT threshold. Ranks **and blended brand Δ** use engines present on both sides; a skipped engine is labelled, not folded into the headline. Writes `change.json` + `*-change-report.html`. No LLM writes the narrative.

- `rescore_run.py` — re-apply the strict brand matcher to a finished run. Default writes `<engine>.rescored.json`; `--in-place` replaces `<engine>.json` (backup `<engine>.json.bak-prerescore`) and recomputes the summary rates so hits and totals agree.
- `merge_shards.py` — merge `<engine>.w0.json`, `<engine>.w1.json`, … from parallel workers into `<engine>.json`; the completed cell wins per arm.
- `compare_mapped.py` — after a roster rewrite, compare new questions with the old ones they replace (`MAPPING.json`: `{"new-id": ["old-id", …]}`) against one or more earlier runs, strict brand matching on every side. Writes `mapped-compare.json` + `<run>-mapped-compare.html`.
- `finish_run.py` — one command to finish a sharded run: optionally wait for live `*.pid` workers, merge shards, strict in-place rescore, judge, render (picks up `RUN/google`), mapped comparison, change report, copy outputs (`--copy-to`). `--detach` runs it as a background watchdog logging to `RUN/finish_run.log`.

Reports use reader-facing labels only: "Named without search" / "Named with search" instead of Mention K/S, plain question-class words instead of tags like `search_likely`, and the board headline's "N of M answers" is corrected to the real total when the judge gets the denominator wrong.

```bash
# Directory of engine files:
AEO_BRAND=Tyk AEO_TYK_RUN=~/.aeo/runs/tyk100-20260901 python3.11 scripts/judge_run.py
AEO_TYK_RUN=~/.aeo/runs/tyk100-20260901 python3.11 scripts/render_judge_html.py

# Single evidence JSON, vendor extract only:
AEO_BRAND=Autheona python3.11 scripts/judge_run.py --vendors-only path/to/evidence.json
python3.11 scripts/render_judge_html.py path/to/evidence.json

# After a second roster run, diff N vs N−1 (baseline may be a dir or one evidence file):
python3.11 scripts/change_report.py \
  --baseline ~/.aeo/runs/tyk100-20260901 \
  --current  ~/.aeo/runs/tyk100-20260921 \
  --brand Tyk
# optional: --floor 2 --top 15 --movers 10
# Finish a sharded run in the background (wait for workers, then everything else):
python3.11 scripts/finish_run.py ~/.aeo/runs/proof-v3-20261009 --config ~/.aeo/reqproof.config.json \
  --wait --detach --change-baseline ~/.aeo/runs/proof93-20261009 \
  --mapped-baseline "Oct 2=$HOME/.aeo/runs/proof93-20261002" --mapped-baseline "Oct 9=$HOME/.aeo/runs/proof93-20261009" \
  --copy-to ~/Downloads/proof-v3-20261009
```

`AEO_RUN` is an alias for `AEO_TYK_RUN`. Brand comes from `AEO_BRAND`, else the evidence `workspace.brand`. Config `competitors` is the seed / known set (keep adding names up front). Optional `competitor_aliases` maps a canonical display to extra spellings. Duplicate seeds still collapse (Amazon/AWS API Gateway, Azure APIM/Management). LLM extract still captures surprises; those are flagged, not folded into the known bars.

Today the scripts default brand/paths to the Tyk board (`tyk100-20260901`). Point `AEO_TYK_RUN` at any run directory with the same evidence shape.
