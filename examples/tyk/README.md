# Tyk example roster

100 pain queries for API management AEO (unaware / expert / agent / MCP / compare). Seeds never say Tyk.

```bash
PYTHONPATH=src python3.11 -m aeo run \
  --config examples/tyk/aeo.config.json \
  --engine all --arm both --class all \
  --out ~/.aeo/runs/tyk100/claude.json
```

Run one engine per process. After the board finishes, use `scripts/judge_run.py` (stance + vendor extract) and `scripts/render_judge_html.py`. After a second roster (e.g. `tyk100-20260901` → `tyk100-20260921`), `scripts/change_report.py --baseline … --current … --brand Tyk` diffs them.
