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
- The only suffix the runner adds is: `Recommend existing tools or products if relevant. Do not write, edit, or execute files.`
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

Default `samples_per_arm` is 1 (CLIs are slow). Pass `--samples N` for jitter.

## Score / report

```bash
python3 -m aeo report aeo-data/runs/<run_id>.json
```

Table columns: query, engine, knowledge hit, search hit, searched?, vendors in search queries, brand in answer.

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

Full write-up and a walkthrough of the XERJ fixture: [METHODOLOGY.md](../../METHODOLOGY.md).

## Raw flags (if the wrapper is blocked)

- Claude knowledge: `claude -p --tools ""` — never `--bare`
- Claude search: `--tools WebSearch,WebFetch --allowedTools WebSearch,WebFetch --permission-mode bypassPermissions` plus a settings file that empties hooks
- Grok knowledge: `grok -p --disable-web-search`
- Grok search: `--output-format json --verbatim` (not streaming-json)
- Codex knowledge: `codex exec --ephemeral --skip-git-repo-check --sandbox read-only` without `--enable standalone_web_search`
- Codex search: same plus `--json --enable standalone_web_search`
