---
name: aeo
description: Measure whether Claude Code, Codex, or Grok mention a target brand on realistic questions. Two arms per query (knowledge vs search). Use when asked to run AEO, brand-visibility, or mention checks against local coding-agent CLIs.
---

# AEO measurement skill

Portable across Claude Code, Codex, and Cursor. Measures local CLIs only.

Do **not** log into consumer LLM websites (claude.ai, chatgpt.com, grok.com, gemini.google.com) from a datacenter or VPS. This skill shells out to `claude`, `codex`, and `grok` on the machine that already has them.

## When to use

- "Does Claude/Codex/Grok mention our product for this question?"
- Brand visibility / AEO check against coding-agent CLIs
- Comparing knowledge-only answers vs search-allowed answers
- Capturing the exact search strings a model typed

Do not use this for Gemini grounding, AI Overviews, or browser-login audits.

## Rules

- Never add the brand name, rust, or extra stack words to the user prompt.
- The only suffix the runner adds is: `Recommend existing tools or products if relevant. Do not write, edit, execute, or read files from disk. Do not inspect the working directory or parent folders.`
- Run every cell in an empty `/tmp/aeo-isolate-*` cwd. Grok: `--sandbox strict --cwd <isolate> --no-memory`. Do not use Grok `--sandbox workspace` (still reads the whole disk).
- Mention = whole-word brand/alias in answer text, not substring, not URL-only. See [METHODOLOGY.md](../../METHODOLOGY.md).
- Raw evidence JSON is the source of truth. Rates are views.

## Init

```bash
python3 -m aeo init --brand Acme --domain acme.example --out aeo.config.json
# or the XERJ example workspace:
python3 -m aeo init --from-example xerj --out aeo.config.json
```

Config is generic: brand, aliases, competitors, engines, prompts. XERJ is an example, not the only brand.

## Run one query

```bash
python3 -m aeo run --config aeo.config.json \
  --prompt "What's the best way to search through a folder of files by content?" \
  --engine all --arm both
```

`--dry-run` prints the exact `claude` / `codex` / `grok` command without executing.

## Batch

```bash
python3 -m aeo run --config aeo.config.json --engine all --arm both
```

Default `samples_per_arm` is 1 (CLIs are slow). Pass `--samples N` for jitter on **this** invocation (it is not a per-id config field). `--only-id ID` (repeatable) filters the roster; `--prompt-id` only labels `--prompt`.

## Roster

Keep the **full roster**. Do not drop watch queries because the incumbent won. Use `--class focus` when the work is content / AEO (search-likely and product-fit). Still run `--class all` on a cadence so a watch query that starts searching or mentioning the brand can be promoted.

```bash
python3 -m aeo run --config aeo.config.json --class focus --engine all --arm both
python3 -m aeo run --config aeo.config.json --class all --engine all --arm both
python3 -m aeo board aeo-data/runs/<run_id>.json
```

## Score / report

```bash
python3 -m aeo report aeo-data/runs/<run_id>.json
```

Table columns: query, class, engine, knowledge hit, search hit, searched?, vendors in search queries, brand in answer. One-line class tally (watch vs focus mention/search rates).

For a decision-maker scoreboard (and agent JSON), use `python3 -m aeo board` — see [aeo-board](../aeo-board/SKILL.md).

## Where evidence is written

Append-only. Each run writes a new file:

`{data_dir}/runs/{run_id}.json`

Validates against `schemas/aeo-cli-evidence-v1.json`.

## How to interpret

| What you see | What it means |
| --- | --- |
| searched = no | Did not search. Answer is prior. |
| searched = yes, vendors in the query strings | Searched already-named vendors (pre-search belief). |
| searched = yes, no configured vendors in the query strings | Open discovery search. |
| brand in answer = yes | Mention. Independent of search. |

Full write-up and a walkthrough of the XERJ fixture: [METHODOLOGY.md](../../METHODOLOGY.md). Which pages to write and the measure→ship→re-run loop: [aeo-playbook](../aeo-playbook/SKILL.md) / [PLAYBOOK.md](../../PLAYBOOK.md).

After a full-grid zero (or near-zero) mention, do not start with more articles. `curl` claimed URLs first, split confirmation / discovery / search-blind, map seeds onto existing slugs, then follow [PLAYBOOK.md](../../PLAYBOOK.md) §9–10. If those URLs already 200 as themselves and mentions stay 0, run §11 (retrieval debug: live vs not-indexed vs skipped) before any draft — Search Console / Bing Webmaster / IndexNow, not a third pile of slugs. Knowledge-arm 0 on an unknown brand is expected; keep measuring that arm. Human view of a run: `python3 -m aeo board <file>` (markdown + JSON; optional `--format html`) plus the evidence JSON. Merge engine files with `python3 -m aeo report --html --out report.html a.json b.json`.

## Raw flags (if the wrapper is blocked)

- Claude knowledge: `claude -p --tools ""` — never `--bare`
- Claude search: `--tools WebSearch,WebFetch --allowedTools WebSearch,WebFetch --permission-mode bypassPermissions` plus a settings file that empties hooks
- Grok knowledge: `grok -p --disable-web-search`
- Grok search: `--output-format json --verbatim` (not streaming-json)
- Codex knowledge: `codex exec --ephemeral --skip-git-repo-check --sandbox read-only` without `--enable standalone_web_search`
- Codex search: same plus `--json --enable standalone_web_search`


## Testimony judge

After a full evidence run, classify each `brand_mentioned` cell with `scripts/judge_run.py` (stance/position/quote), then `scripts/render_judge_html.py`. Do not treat CLI `recommended` as testimony. Grok AEO runs must use `GROK_HOME` without MCP and may need `GROK_SANDBOX=workspace` when Docker Desktop makes `docker.sock` a symlink.
