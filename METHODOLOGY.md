# Methodology `aeo-cli-v1`

Local coding-agent CLIs only: Claude Code, Codex, Grok. Same question, two arms.

This document does **not** measure Gemini grounding, Google AI Overviews, ChatGPT web UI, or any consumer chat website.

This file is the measurement spec. Which articles to write, how to read a board into briefs, and the validation loop live in [PLAYBOOK.md](PLAYBOOK.md).

## Arms

Every query × engine is run twice.

1. **knowledge** — search forced off. What the model already believes.
2. **search** — search allowed. Record whether it *actually* searched.

Models often skip search, or search only to confirm vendors they already named. The search arm is not "the internet version of the answer." It is evidence of tool use.

| Engine | Knowledge | Search |
| --- | --- | --- |
| Claude | `claude -p --tools ""` | `--tools WebSearch,WebFetch --allowedTools WebSearch,WebFetch --permission-mode bypassPermissions` plus a settings file that empties hooks. Never `--bare`. |
| Grok | `grok -p --disable-web-search --sandbox strict --cwd <isolate> --no-memory` | same plus `--output-format json --verbatim` (not streaming-json). `strict` + empty isolate cwd so it cannot read the AEO tree. On macOS `strict` does not block web search. |
| Codex | `codex exec --ephemeral --skip-git-repo-check --sandbox read-only` without `--enable standalone_web_search` | same plus `--json --enable standalone_web_search` |

User prompt text is the query plus one operational suffix:

`Recommend existing tools or products if relevant. Do not write, edit, execute, or read files from disk. Do not inspect the working directory or parent folders.`

Do not add the brand, rust, or extra stack words to the prompt.

## Isolation

Every cell runs with `cwd` set to a fresh empty directory under `/tmp` (`aeo-isolate-*`), never `~/.aeo` and never the brand repo. Listing `..` from that dir must not reveal protocol files, keyword lists, or playbooks.

Grok defaults to **no** OS sandbox. Use `--sandbox strict` whenever the installed CLI supports it (read CWD + system paths only). `--sandbox workspace` and `--sandbox read-only` still allow reading the whole disk — those do **not** fix contamination. Codex already uses `--sandbox read-only`; still isolate `cwd`. Claude knowledge uses `--tools ""`; Claude search allows only `WebSearch,WebFetch`.

A mention that follows "this looks like an AEO eval" / "I'll read the local protocol" is **contaminated**. Score it, then exclude it from the published rate. Do not treat it as a web or weights hit.


## Fields

All extraction is deterministic: case-insensitive word-boundary regex. Raw samples are the source of truth; scores are views.

**mention** — whole-word brand or alias in *answer text*. Not a substring (`xerj` does not match `xerjified`). Not URL-only (`https://xerj.org/docs` alone is not a mention; bare `xerj.org` in prose is).

**competitors in answer** — same matcher over the configured competitor names.

**searched** — bool. True if the CLI transcript contains a search tool call or a vendor `web_search_requests` count > 0.

**search_queries** — exact tool-call strings (Claude `WebSearch`/`WebFetch` input, Codex `item.type == "web_search"` `action.queries[]`, Grok `web_search` / `web_fetch`). Not paraphrases.

**vendors_in_search_queries** — brand / alias / competitor names that appear as whole words inside those tool-call strings. This is pre-search belief: the model already chose vendors before looking.

**recommended** — v1: same as brand mentioned in the answer (not URL-only).

Optional aggregates on a run document:

- `mention_rate_knowledge` — share of knowledge arms with `brand_mentioned`
- `mention_rate_search` — share of search arms with `brand_mentioned`
- `search_rate` — share of search arms with `searched`
- `vendor_prebelief_rate` — share of *searched* arms whose queries already named a configured vendor

## How to interpret

Four different facts, often confused:

| Observation | Means |
| --- | --- |
| `searched = false` | Did not search. Answer is weights / prior. |
| `searched = true` and `vendors_in_search_queries` non-empty | Searched already-named vendors. Confirmation, not discovery. |
| `searched = true` and `vendors_in_search_queries` empty | Open discovery search. The query did not already name a configured vendor. |
| `brand_mentioned = true` | Brand in the answer text. Independent of whether they searched. |

A search arm that never searched is not a failure of the harness. It is a measurement.

## Samples

Default `samples_per_arm = 1`. Local CLIs are slow. Set `N > 1` only when you need a jitter read. Multiple samples are multiple raw rows, not a reason to discard the first.

## Schema

Evidence documents validate against `schemas/aeo-cli-evidence-v1.json`. Config against `schemas/aeo-cli-config-v1.json`. The generic workspace is brand + aliases + competitors + query list. XERJ is the first example, not the only brand.

## Roster, not a shortlist

Never drop a query because you lost it.

- **watch** — keep measuring the incumbent (usually ripgrep from weights; search never fires). Stay on the roster. Still run two-arm. Do not write content first.
- **focus** — queries that trigger web search, or where the brand is a plausible product answer. This is where AEO / content work goes.
- A watch query that starts searching or mentioning the brand gets **promoted** to focus. `class` is a hypothesis; evidence can flip it.
- How to interpret: a watch miss is expected; a focus miss is the actual gap.

`--class watch|focus|all` filters the roster at run time. Default is `all`. Never set watch queries to `enabled: false`.

## Decision board

`python3 -m aeo board <evidence.json>` writes a human markdown board and an agent JSON board (`aeo-cli-board-v1`). Agents should treat the board JSON as the analysis input; raw evidence is for drill-down only.

Call rules (deterministic, one call per query):

| Call | When |
| --- | --- |
| `win` | Brand mentioned on the search arm or the knowledge arm (any engine). |
| `gap` | Focus query, no brand mention, at least one engine actually searched. |
| `search-blind` | Focus query, search was allowed, no engine searched. |
| `trap` | Watch query, no brand mention (expected). |

`⚠` on the search column means they searched, but `vendors_in_search_queries` already named a configured vendor and not the brand — confirmation, not discovery.

## How to read a run

Walk-through of [`examples/xerj/aeo-data/example-run.json`](examples/xerj/aeo-data/example-run.json). One query:

*What's the best way to search through a folder of files by content?*

**Claude knowledge.** Answer recommends ripgrep. No XERJ. `searched` is false (search was off). This is prior: ripgrep is what the model already believes.

**Claude search.** Also ripgrep, no XERJ. `searched` is false — it was allowed to search and did not. Same prior, now observed under the search arm.

**Codex knowledge.** Same vendors from weights (ripgrep / Recoll / DocFetcher). No XERJ.

**Codex search.** `searched` is true. The tool-call strings already name ripgrep, Recoll, and DocFetcher. The answer is those same tools. No XERJ. This is not discovery; it is confirmation of a pre-search shortlist.

**Grok knowledge / search.** ripgrep. No XERJ. Search arm did not search.

Read left to right: nobody mentioned the brand; two engines never used search; one engine searched only names it already had. Rates on the document (`mention_rate_* = 0`, `search_rate` low, `vendor_prebelief_rate = 1`) are a view of that. The samples are the source of truth.

## After the numbers

This file stops at measurement. After a full-grid zero (or near-zero) mention, the next action is not "write more articles." Reason with [PLAYBOOK.md](PLAYBOOK.md) §9 (live URL check, confirmation vs discovery vs search-blind, one URL per cluster, waves). If claimed URLs already 200 as themselves and mentions stay 0, continue to §11 (retrieval debug) before drafting. A human write-up of a run is incomplete unless it matches §10. Do not invent a rate to fill a cell.

Human view of the same payload: `python3 -m aeo board <evidence.json>` (markdown + agent JSON; optional `--format html`) plus this evidence JSON.
