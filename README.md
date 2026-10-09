# aeo

Measure whether coding agents mention your product.

`aeo` asks Claude Code, Codex, and Grok the same realistic questions twice: once with search forced off, once with search allowed. It records the mention, whether they actually searched, the **literal** strings they typed into the search box, and which competitor names were already in those strings.

That is the whole board. It is not Gemini grounding and not a login to claude.ai. Google (AI Overviews, rankings) and Search Console are optional [separate layers](#google-and-search-console-layers) that never change the board scores.

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
python3 -m aeo run --config aeo.config.json --engine all --arm both --concurrency 4
python3 -m aeo board aeo-data/runs/<run_id>.json
python3 -m aeo report --html --out report.html aeo-data/runs/<run_id>.json
```

`--dry-run` prints the exact `claude` / `codex` / `grok` command and exits. `--only-id` re-runs one roster seed. `--samples N` repeats that invocation (default `n=1`; local CLIs are slow). `--concurrency N` (default 1) runs up to N remaining cells in **one** process; workers write temp shards and the parent merges them into `--out` so resume cannot drop a cell. Do not share one `--out` across multiple `aeo run` processes.

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

After a full grid, `scripts/judge_run.py` labels stance on brand hits **and** extracts product names from every completed arm. Config `competitors` is the seed / known set; names not on that list after normalize are **surprises** (flagged separately). Brand hit rate stays the deterministic `brand_mentioned` regex. See [scripts/README.md](scripts/README.md).

## Google and Search Console layers

Most traffic still comes from Google, so a run can also check Google itself. Both layers are separate sections in the report (`aeo report --html`, `aeo board`, `scripts/render_judge_html.py`) and in the markdown/JSON outputs. They never touch evidence cells or board scores.

**Google (DataForSEO).** On by default when DataForSEO credentials are set; skipped silently when they are not.

```bash
export DATAFORSEO_LOGIN=...  DATAFORSEO_PASSWORD=...        # or DATAFORSEO_USERNAME
# or: export DATAFORSEO_CREDENTIALS_FILE=path/to/file      # any file with DATAFORSEO_LOGIN=/: and DATAFORSEO_PASSWORD=/: lines (env or YAML)
python3 -m aeo google --config aeo.config.json --estimate  # cost estimate, no API calls
python3 -m aeo google --config aeo.config.json             # fetch now -> <data_dir>/google/<timestamp>/
python3 -m aeo google --config aeo.config.json --out-dir runs/<run>/google   # attach to a run dir (render_judge_html.py)
```

`aeo run` fetches both layers after a full roster run into `runs/<run_id>.google/` (skipped for `--prompt`, `--only-id`, `--dry-run`, and on resume when the folder already exists). `--no-google` turns them off for one run; `"google": {"enabled": false}` / `"gsc": {"enabled": false}` turn them off in config.

Config fields:

| Field | What it does |
| --- | --- |
| `google_targets` | About 10 short searches you want to win (strings or `{query, id, priority, lead, page_should_demonstrate, notes}`). Full analysis every run: AI Overview yes/no, full AIO text, cited sources (domain, url, title, ours highlighted), whether our domain is cited, top 10 organic (rank, title, url, domain), our position in the top 100, optional AI Mode. |
| `google_watch` | Optional wider list, any size. Rankings and AIO citations only, shown as one table. |
| `google.location_code` / `language_code` | Default 2840 (US) / `en`. |
| `google.device`, `google.mobile`, `google.mobile_depth` | Desktop by default; `mobile: true` adds a mobile snapshot of each target. |
| `google.depth`, `google.watch_depth` | Organic results to fetch (default 100 so we can report our position). |
| `google.ai_mode` | Google AI Mode for targets (default on). |
| `google.max_cost_usd` | Spending cap per run (default 2.0). If the pre-flight estimate is over it, nothing is fetched; retries stop at the cap. `--max-cost` / `--google-max-cost` override it. |
| `google.own_url_patterns` | Extra URL prefixes that count as ours (e.g. `github.com/acme/tool`). Domain-style `aliases` count automatically; plain words never do. |

Cost per search (DataForSEO list price, billed per 10 organic results): top 10 + AI Overview about $0.004; top 100 + AI Overview about $0.022; AI Mode about $0.004; mobile top 10 about $0.004. Ten targets at the defaults are about $0.26 per run; a 50-search watch list at depth 100 adds about $1.10 (set `watch_depth: 10` for about $0.20). Task errors such as 40101 are retried; 40106 (some deep pages missing) is kept and flagged as partial.

If `google_targets` is missing, the run drafts 10 target and up to 20 watch searches with the same headless `claude` call the judge uses, writes them to `<config>.google-proposals.json` with `"status": "unapproved"`, and checks nothing from that list. Copy the ones you want into the config. `aeo google --propose` redrafts them.

When an earlier layer folder for the same domain exists next to this one, each search shows what changed: rank moves, entering/leaving the top 100, AIO citation gained/lost, AIO appeared/disappeared, and new/dropped domains in the AIO sources and the top 10.

**Search Console.** On by default when credentials are found; skipped silently otherwise. Read-only scope (`webmasters.readonly`) is enough.

| Credential | Use |
| --- | --- |
| `GSC_CREDENTIALS_FILE` | Google `authorized_user` JSON (client_id, client_secret, refresh_token), or a `service_account` key (needs `pip install google-auth`; add the service account as a user on the property). |
| `GSC_OAUTH_TOKEN_FILE` + `GSC_OAUTH_SECRETS_FILE` | The token `npx suganthan-gsc-mcp setup` writes (default `~/.gsc-mcp/oauth-token.json`) plus the Google OAuth client JSON used to refresh it. |

| Field | What it does |
| --- | --- |
| `gsc.site` | Property, e.g. `sc-domain:acme.example` (default `sc-domain:<domain>`) or `https://acme.example/`. |
| `gsc.window_days` / `lag_days` | Last 28 days ending 3 days ago (GSC data settles late) vs the 28 days before. |
| `gsc.min_impressions`, `striking_min_position`, `striking_max_position`, `top_n`, `row_limit`, `suggest_limit` | Thresholds for the tables below. |

Per run: totals (clicks, impressions, CTR, average position) for both windows, top queries and top pages, striking-distance queries (position 5-20 with real impressions), queries with impressions and no clicks, and GSC numbers for every `google_targets` / `google_watch` search, shown on its Google card next to the live rank. Queries with impressions that are not tracked yet go into the proposals file as unapproved `gsc_watch_suggestions`. When an earlier `gsc.json` exists, totals and tracked searches also show the change since that run.

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
