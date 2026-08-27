---
name: aeo-board
description: Render and read an AEO decision board (scoreboard + per-query calls) from a local-CLI evidence run. Use when the user wants a table, scoreboard, what we should do, or analysis of an AEO run.
---

# AEO decision board

Two audiences, one command. Humans get markdown. Agents get JSON with the same cells and no prose.

Do **not** open consumer LLM websites. This skill only reads evidence JSON produced by `python3 -m aeo run`.

## When to use

- "Show me a table / scoreboard"
- "What should we do with this run?"
- "Analyze this AEO evidence"
- `/aeo-board`

## Steps

1. Find the latest evidence file, or use the path they gave. Typical: `aeo-data/runs/<run_id>.json` or `examples/xerj/aeo-data/example-run.json`.
2. Run `python3 -m aeo board <file>` (optional `--format md|html|json`). Default writes both:
   - `aeo-data/boards/<run_id>.md` — for humans / decision makers
   - `aeo-data/boards/<run_id>.json` — for agents (`schema_version: aeo-cli-board-v1`)
3. Show the markdown board to the human.
4. If asked to analyze: read the **board JSON**, not the raw answers first. Summarize only focus `gap` and `search-blind` rows. Watch `trap` rows are expected — mention the tally, do not narrate each miss. Promote a watch query to focus only if evidence shows they started searching or mentioned the brand.
5. Never invent checkmarks. Recompute from evidence (`python3 -m aeo board` again if unsure).

## Calls

| Call | Meaning |
| --- | --- |
| `win` | Brand mentioned on search or knowledge. |
| `gap` | Focus, no brand, they did search. |
| `search-blind` | Focus, search allowed, they did not search. |
| `trap` | Watch, no brand (expected). |

`⚠` = confirmation search (vendors already in the tool-call strings, brand was not). Not discovery.

Full rules: [METHODOLOGY.md](../../METHODOLOGY.md). Measurement skill: [aeo](../aeo/SKILL.md). Turning a board into articles: [aeo-playbook](../aeo-playbook/SKILL.md). Zero mentions after pages are already live: [PLAYBOOK.md](../../PLAYBOOK.md) §11, not more articles.

A human run write-up is not the board itself. It must follow [PLAYBOOK.md](../../PLAYBOOK.md) §10 (method, live URL check, per-engine×arm table, confirmation vs discovery, search-blind seeds, cannibalize map, waves, refuse list, `--only-id` re-run). After a zero-mention grid, reason with §9 before drafting. `--format html` is a view of the **board**, not a substitute for that artifact. Merge engine files with `python3 -m aeo report --html --out report.html a.json b.json`.
