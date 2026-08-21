---
name: aeo-playbook
description: Use when planning AEO content, turning a board into article briefs, or running a measure→ship→re-run loop. Complements the aeo measurement skill and the aeo-board skill.
---

# AEO playbook skill

Portable across Claude Code, Codex, and Cursor. Reads local-CLI evidence. Does **not** open consumer LLM websites.

Full method: [PLAYBOOK.md](../../PLAYBOOK.md). Measurement spec: [METHODOLOGY.md](../../METHODOLOGY.md).

## When to use

- "Which articles should we write?"
- "What do we do with this board?"
- "How do we close the loop after we ship?"
- Planning content from fan-out / confirmation vs discovery
- `/aeo-playbook`

Do not use this to run cells (that is [aeo](../aeo/SKILL.md)) or to render a board (that is [aeo-board](../aeo-board/SKILL.md)). Use this to decide the next action.

## Next action

1. If there is no current board, run `python3 -m aeo board` on the latest `aeo-data/runs/<run_id>.json`. If there is no run, measure first (`python3 -m aeo run --class all --engine all --arm both`). Do not write pages from vibes.
2. Read the board JSON, not the raw answers. Tally calls: `win` / `gap` / `search-blind` / `trap`.
3. Build the fan-out map from `search_queries` on search-allowed arms. Frequency-count. Split confirmation (`vendors_in_search_queries` names an incumbent) vs discovery (no configured vendor in the tool-call string).
4. Before blaming content: curl the candidate URLs. Reject homepage-sized 200s, `.html` hops, and sitemap misses. If the page is not retrievable, the next action is deploy / index, not a new draft.
5. A page is allowed only if all three hold: models already fire the string (or the call is `gap` + discovery); no incumbent owns it in agent language; you can run the product against the incumbent on a disclosed corpus and publish losses.
6. If the article does not yet have a capture, **stop and run the corpus**. Do not draft. Freeze file list + tree SHA + task list first. Install incumbents on the same machine as the CLIs.
7. After a ship: search-engine fetch, then re-run **only** the focus prompt ids whose fan-out named that incumbent or category. Compare boards on the same ids. Raise `samples_per_arm` to ~20 only on invested queries.
8. Keep the full roster. Do not drop watch queries. Do not inject the brand or incumbents into seeds. Do not restart an in-flight full-grid run.

## Calls → action

| Call | Next action |
| --- | --- |
| `trap` | Keep measuring. No article. |
| `search-blind` | No article will be retrieved. Change the product or the query class, or accept that this question is answered from weights. |
| `gap` + confirmation | Compare page vs the named incumbent. Rank for *their* name. |
| `gap` + discovery | Category page whose H1 is the string they typed. |
| `win` | Do not rewrite. Re-run later to see if it holds. |

## Kill the page if

- You did not run both sides.
- You hide a loss.
- The only win is a slogan.
- You could have written it from the homepage.
- The cluster is one the product cannot win (missing format, no OCR, live API you do not ship).

## Anti-patterns (stop)

Injecting the brand into seeds. Treating `searched = false` as a harness bug. Treating confirmation as discovery. A second 50-page dump. `FAQPage` / `HowTo` / star-rating JSON-LD. Logging consumer LLM accounts from a VPS.
