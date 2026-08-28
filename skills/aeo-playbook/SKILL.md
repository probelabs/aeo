---
name: aeo-playbook
description: Use when planning AEO content, turning a board into article briefs, running a measure→ship→re-run loop, or debugging why live pages still get zero mentions (retrieval / Search Console). Complements the aeo measurement skill and the aeo-board skill.
---

# AEO playbook skill

Portable across Claude Code, Codex, and Cursor. Reads local-CLI evidence. Does **not** open consumer LLM websites. Never `--bare`.

Full method: [PLAYBOOK.md](../../PLAYBOOK.md). Measurement spec: [METHODOLOGY.md](../../METHODOLOGY.md). Raw flags: [aeo](../aeo/SKILL.md).

## When to use

- "Which articles should we write?"
- "What do we do with this board?"
- "How do we close the loop after we ship?"
- "Pages are live, still zero mentions — write more?"
- "Do we need Search Console / Bing Webmaster?"
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
4. Before blaming content: name the candidate URL from the **sitemap** (not a guessed seed id). `curl` it. Reject homepage-sized 200s, homepage ETag, 308-to-`/`, `.html` hops, `noindex`, canonical=`/`. Then same-backend check: that engine's search arm (never `--bare`) with the **literal** fan-out string, plus `site:<domain>/<path>` and a branded H1 query. Homepage / `llms.txt` / GitHub do not count. Bing/Brave "fetched" or a VPS SERP scrape is not this check. If the URL is missing there, consoles + sitemap + IndexNow, then wait — do not draft. Full tree: [PLAYBOOK.md](../../PLAYBOOK.md) §11.
5. A page is allowed only if all three hold: repeating fan-out (or `gap` + discovery); you looked at what those tool calls retrieve today; you can publish primary evidence **this product can produce** and will publish losses. One URL per cluster, not per string and not per ⚠ vendor. If a capability URL already answers it, edit that URL.
6. No capture yet → stop. Freeze the protocol (inputs, versions, task list, content-addressed snapshot) first. Protocol matches the product. Do not invent a file-tree bake-off or MCP JSON for a product that does not ship those.
7. After a ship and a same-backend check: re-run the affected **roster ids** with `--only-id` (repeatable). `--prompt-id` is only a label for `--prompt`. Raise n with `--samples 20` on that invocation only. n=20 × 3 engines × 2 arms = 120 cold starts per query.
8. Keep the full roster. Do not drop watch queries. Do not inject the brand or incumbents into core seeds. Confirmation-probe satellites (`Foo vs Bar`) are not briefs. Do not restart an in-flight full-grid run.

## Calls → action

| Call | Next action |
| --- | --- |
| `trap` | Keep measuring. No article. |
| `search-blind` | Do not mint a twin. EDIT the existing capability URL (H1 / FAQ / `agent_prompt`) so a later search matches training-weight phrasing. Changing the product or accepting weights is also valid. |
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

## Reason after the board

After a full-grid zero (or near-zero) mention, do **not** start with "write more articles." Long form: [PLAYBOOK.md](../../PLAYBOOK.md) §9–10.

1. `curl` every URL you claim is live. Homepage-sized 200 or canonical=`/` means the search backend cannot retrieve the page. Unpublished-branch markdown is invisible to the search arm.
2. Knowledge-arm 0 on an unknown brand is expected year one. Keep measuring. Content budget = search-arm retrieval against incumbents the models already type.
3. High search + 0 mentions is usually "URL not in that backend" or "they confirmed an incumbent and the incumbent's docs won" — not "mint dozens of slugs."
4. Split cells: confirmation (vendor already in the tool-call string) vs discovery (category string, no configured vendor) vs search-blind (`searched=false`). Confirmation needs a live compare URL that can beat the incumbent's own page on the *same* backend.
5. Search-blind focus ids: EDIT existing H1 / FAQ / `agent_prompt`. Do not mint twins.
6. Map every seed to a shipped or in-PR slug before minting. If 100% cannibalize, new slugs only for clusters the tree does not own.
7. One URL per cluster, not per seed and not per ⚠ vendor.
8. Mention without search, or a transcript that says "this is an AEO eval," is contaminated. Isolate cwd. Do not publish that rate as a win.
9. Nobody typing the brand into the search box is a first-class finding. Record it.
10. Do not merge / deploy a dump of unpublished pages that 200 the homepage.
11. If Wave 0 already passed and mentions are still 0, **stop writing**. Run [PLAYBOOK.md](../../PLAYBOOK.md) §11 (live vs not-indexed vs skipped vs consensus). Verify Search Console + Bing Webmaster + IndexNow + Cloudflare AI Crawl Control. Re-check `site:` before any new slug. If the compare is indexed and still loses to the incumbent's third-party docs, that is Gate E (off-site mention), not a new `/answers` twin.

Waves (unpublished PR + measured grid): **0** existing slugs 200 as themselves, sitemap, own canonical (merge-blocking; new drafts do not change AEO until then). If Wave 0 is already true and mentions stay 0, §11 — not Wave 2/3 content. **1** one compare URL per repeating confirmation incumbent you can bake off; shared frozen corpus; kill if the incumbent finds more in-family evidence. **2** EDIT existing slugs for search-blind / mismatched H1s; no new paths; no invented numbers. **3** remaining confirmation incumbents, then honesty pages only after capture; refuse surfaces you do not ship. If it could be written without running the product, it does not ship.

## FAQ (short)

- **Pages 200, still 0 mentions → more articles?** No. §11. Index first.
- **Guessed slug 404?** Start from the sitemap / `*/index.json`, not the seed id.
- **`site:` empty, homepage and `llms.txt` retrieve?** Not indexed. Consoles + IndexNow. Wait.
- **`site:` hits the article, confirmation string still only the incumbent?** Skipped. Edit that compare after a bake-off. No twin.
- **Compare says "no benchmark was run"?** Wave 0 ≠ Wave 1. Edit that URL after a frozen corpus.
- **VPS Bing/Google HTML as proof?** No. Datacenter scrapes lie. Use the cell's own search tool or Search Console coverage.
- **"All the SEO"?** Search Console + Bing Webmaster + sitemap submit + IndexNow. Stop until `site:` returns the slugs.
- **Cloudflare / GPTBot?** `robots.txt` `Allow: /` is not enough if CF "Block AI bots" is on. `curl -A` `OAI-SearchBot` / `ChatGPT-User` / `Claude-SearchBot` / `PerplexityBot`. Open the dashboard.
- **YouTube / Ahrefs 0.737 / Brand Radar?** Off-site mention is Gate E after the compare already loses. Not a reason to mint slugs, rewrite seeds, or measure ChatGPT web / AI Overviews with this skill.
- **Mention after "this is an AEO eval"?** Contaminated. Isolate cwd. Do not publish the rate.

## Write-up checklist

A run summary for humans is not done if any item is missing. Recompute every number from evidence JSON + live `curl` the same day. Do not invent a number — say the file was not opened.

1. Method (two-arm, verbatim seeds, isolate cwd, engines, n).
2. Live URL check of claimed pages + sitemap (status, bytes vs homepage, canonical). If that already passes, Gate B (`site:` / branded / literal fan-out), consoles + IndexNow + CF AI-bot status, and whether Gate E (consensus) applies ([PLAYBOOK.md](../../PLAYBOOK.md) §11).
3. Mention / search / prebelief per engine × arm. Isolate contaminated cells; do not blend them into the win rate.
4. Confirmation vs discovery counts and vendor fan-out (`search_queries` / `vendors_in_search_queries`), including "typed brand into the box?"
5. Search-blind focus ids (verbatim seeds).
6. Cannibalize / coverage map: seeds → existing slugs. New slugs only if the tree does not own the cluster.
7. Calls → next action (`trap` / `search-blind` / `gap`+confirmation / `gap`+discovery / `win`).
8. Wave plan + merge/deploy gates + kill rules + refuse list with reasons.
9. Safe product claims vs claims you will not make.
10. What you will re-run (`--only-id`) after ship, and what you will not (do not restart a full grid).

Human view of the same payload: `python3 -m aeo board <file>` (markdown + JSON; `--format html` writes the standalone report) plus the evidence JSON. Merge engine files with `python3 -m aeo report --html --out report.html run-a.json run-b.json`.
