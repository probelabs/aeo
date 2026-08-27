# aeo

Measure whether coding agents mention your product.

`aeo` asks Claude Code, Codex, and Grok the same realistic questions twice: once with search forced off, once with search allowed. It records the mention, whether they actually searched, the **literal** strings they typed into the search box, and which competitor names were already in those strings.

That is the whole product. It is not Gemini grounding, not Google AI Overviews, and not a login to claude.ai.

[Methodology](METHODOLOGY.md) · [Playbook](PLAYBOOK.md) · [Skills](skills/)

## Why two arms

A mention from weights and a mention after a web search are different facts.

| Arm | What you learn |
| --- | --- |
| **Knowledge** | What the model already believes. A new brand almost never wins this path in year one. Measure it anyway. |
| **Search** | Whether they searched, and what they typed. Most "search" is **confirmation** of an incumbent they already named, not discovery of you. |

If they never type your name, and your page is not in the backend they used, writing more blog posts will not change the grid. The [playbook](PLAYBOOK.md) is the operating loop for that: measure, ship one URL per cluster, check the page is live *and indexed*, re-run only the affected seeds.

## Install

Python 3.11+. Stdlib only. You need `claude`, `codex`, and/or `grok` on **your** machine. Do not run those CLIs from a VPS or datacenter.

```bash
pip install -e .
# or
PYTHONPATH=src python3 -m aeo --help
```

## Quick start

```bash
python3 -m aeo init --brand Acme --domain acme.example --out aeo.config.json
# or copy the XERJ example roster:
python3 -m aeo init --from-example xerj --out aeo.config.json

python3 -m aeo run --config aeo.config.json --engine all --arm both
python3 -m aeo board aeo-data/runs/<run_id>.json
python3 -m aeo report --html --out report.html aeo-data/runs/<run_id>.json
```

`--dry-run` prints the exact `claude` / `codex` / `grok` command and exits. `--only-id` re-runs one roster seed. `--samples N` repeats that invocation (default `n=1`; local CLIs are slow).

Never put the brand, a stack word, or an incumbent into a **core** prompt. If the model injects those into its own search call, that is a finding.

## What a run gives you

Each cell is isolated in a fresh empty `/tmp/aeo-isolate-*` directory so Grok cannot read your playbook and "discover" the brand.

| Artifact | What it is |
| --- | --- |
| `aeo-data/runs/<run_id>.json` | Raw evidence. Source of truth. [Schema](schemas/aeo-cli-evidence-v1.json). |
| `aeo board` | Decision board: `win` / `gap` / `search-blind` / `trap`, plus markdown + agent JSON. |
| `aeo report --html` | Compact self-contained report. Merges several engine files. |

Per arm the runner stores: `brand_mentioned`, `searched`, `search_queries` (verbatim), `vendors_in_search_queries`, and token/spend when the CLI JSON has it.

## After the numbers

A zero-mention grid is not a prompt to write fifty articles.

1. `curl` every URL you claim is live. Homepage-sized 200s do not count.
2. Split cells: confirmation vs discovery vs search-blind.
3. One URL per cluster, only if you can publish a run you actually did.
4. If the pages are already live and mentions stay 0, it is a **retrieval** problem. [Playbook §11](PLAYBOOK.md#11-retrieval-debug-pages-live-mentions-still-0): Search Console, Bing Webmaster, IndexNow. Not more slugs.

Portable agent skills live in [`skills/`](skills/): [aeo](skills/aeo/SKILL.md) (run), [aeo-board](skills/aeo-board/SKILL.md) (read), [aeo-playbook](skills/aeo-playbook/SKILL.md) (decide).

## Example

[`examples/xerj`](examples/xerj) is a real workspace (84 seeds, watch vs focus), not a hard-coded only-brand. Walkthrough of the fixture: [How to read a run](METHODOLOGY.md#how-to-read-a-run).

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
pip install -e '.[test]'   # optional, pulls jsonschema
```

## Raw CLI flags

Use these if the wrapper is blocked. Never pass `--bare` to Claude (it skips keychain).

```bash
# Claude
claude -p --tools "" --output-format json -- "PROMPT"
claude -p --tools WebSearch,WebFetch --allowedTools WebSearch,WebFetch \
  --permission-mode bypassPermissions \
  --settings src/aeo/data/claude-empty-hooks.json \
  --output-format stream-json --verbose -- "PROMPT"

# Grok
grok -p --disable-web-search --sandbox strict --cwd /tmp/aeo-isolate --no-memory -- "PROMPT"
grok -p --output-format json --verbatim --sandbox strict --cwd /tmp/aeo-isolate --no-memory -- "PROMPT"

# Codex
codex exec --ephemeral --skip-git-repo-check --sandbox read-only -- "PROMPT"
codex exec --ephemeral --skip-git-repo-check --sandbox read-only \
  --json --enable standalone_web_search -- "PROMPT"
```

## License

MIT. Copyright ProbeLabs.
