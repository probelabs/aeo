# Post-run scripts

- `judge_run.py` — LLM per-hit testimony + board actions. Set `AEO_TYK_RUN` to the evidence dir with `claude.json` / `codex.json` / `grok.json`. Writes `judge.json` and `board.json`.
- `render_judge_html.py` — HTML with board actions on top and stance-colored K/S marks.

Today the scripts default brand/paths to the Tyk board (`tyk100-20260901`). Point `AEO_TYK_RUN` at any run directory with the same evidence shape.
