---
name: aeo-playbook
description: Use when planning AEO content, turning a board into article briefs, or running a measure→ship→re-run loop. Complements the aeo measurement skill and the aeo-board skill.
---

# AEO playbook skill

Portable across Claude Code, Codex, and Cursor. Reads local-CLI evidence. Does **not** open consumer LLM websites. Never `--bare`.

Full method: [PLAYBOOK.md](../../PLAYBOOK.md). Measurement spec: [METHODOLOGY.md](../../METHODOLOGY.md). Raw flags: [aeo](../aeo/SKILL.md).

## When to use

- "Which articles should we write?"
- "What do we do with this board?"
- "How do we close the loop after we ship?"
- `/aeo-playbook`

Do not use this to run the full grid (that is [aeo](../aeo/SKILL.md)) or only to render a board (that is [aeo-board](../aeo-board/SKILL.md)). Use this to decide the next action.

## Where the bits live

| Need | Open |
| --- | --- |
| Calls / cells / ⚠ | Board JSON |
| Literal `search_queries` | Evidence JSON (`engines.*.search.search_queries`) |
| Seed text | Config or `prompt_text` on the evidence row |
| Whether our URL was cited | `raw_response_text` (board does not extract citations) |

`mention` = whole-word brand/alias in answer text, not substring, not URL-only. `recommended` is the same bit. Board `win` is a ceiling (any engine, either arm) — still read the cells.

## Next action

1. If there is no current board, `python3 -m aeo board aeo-data/runs/<run_id>.json`. Always pass the path. If there is no run, `python3 -m aeo run --config aeo.config.json --class all --engine all --arm both`.
2. Read the board JSON for calls (`win` / `gap` / `search-blind` / `trap`) and cells. Do not narrate raw answers.
3. Fan-out is **not** on the board. Open the evidence file and frequency-count `engines.*.search.search_queries`. Drop hapaxes. Split confirmation (`vendors_in_search_queries` names an incumbent, not the brand) vs discovery (no configured vendor in the tool-call string). A confirmation *name* without the literal string is not a brief.
4. Before blaming content: name the candidate URL (shipped page, last citation, or sitemap). `curl` it. Reject homepage-sized 200s, 308-to-`/`, `.html` hops, `noindex`, canonical=`/`. Then same-backend check: that engine's search arm (never `--bare`) with the **literal** fan-out string. Bing/Brave "fetched" is not this check. If the URL is missing there, index and wait.
5. A page is allowed only if all three hold: repeating fan-out (or `gap` + discovery); you looked at what those tool calls retrieve today; you can publish primary evidence **this product can produce** and will publish losses. One URL per cluster, not per string and not per ⚠ vendor. If a capability URL already answers it, edit that URL.
6. No capture yet → stop. Freeze the protocol (inputs, versions, task list, content-addressed snapshot) first. Protocol matches the product. Do not invent a file-tree bake-off or MCP JSON for a product that does not ship those.
7. After a ship and a same-backend check: re-run the affected **roster ids** with `--only-id` (repeatable). `--prompt-id` is only a label for `--prompt`. Raise n with `--samples 20` on that invocation only. n=20 × 3 engines × 2 arms = 120 cold starts per query.
8. Keep the full roster. Do not drop watch queries. Do not inject the brand or incumbents into core seeds. Confirmation-probe satellites (`Foo vs Bar`) are not briefs. Do not restart an in-flight full-grid run.

## Calls → action

| Call | Next action |
| --- | --- |
| `trap` | Keep measuring. No article. |
| `search-blind` | No article will be retrieved. Change the product or the query class, or accept weights. |
| `gap` + confirmation | *Candidate* for one compare URL against that incumbent, iff the string repeats and you can run a real head-to-head. Not one URL per ⚠ cell. |
| `gap` + discovery | *Candidate* for one category URL whose H1 is the dominant typed string, iff it repeats. |
| `win` | Do not mint a new URL. Check citations in `raw_response_text`. |

`win` means the brand string appeared (METHODOLOGY.md). It does not mean the right URL was cited.

1. List URLs actually cited in `raw_response_text`.
2. Named the brand and cited a live canonical article you own → do not mint another URL. Re-run later.
3. Named the brand and cited the homepage, a 308, a GitHub README, or nothing → deploy / index / fix that URL.
4. Knowledge-only `win` on a well-known brand is not a content brief.

## Kill the page if

- You did not run both sides of the protocol you claimed.
- You hide a loss.
- The only win is a slogan.
- You could have written it from the homepage.
- The cluster is a surface you do not ship.

## Anti-patterns (stop)

A mention after "this is an AEO eval" / reading `~/.aeo` is contaminated — isolate cwd, Grok `--sandbox strict`. Injecting the brand into core seeds. Treating `searched = false` as a harness bug. Treating confirmation as discovery. One slug per ⚠ or per seed. A second dump. `FAQPage` / `HowTo` / star-rating JSON-LD. Logging consumer LLM accounts from a VPS. Using `--prompt-id` as a roster filter.
