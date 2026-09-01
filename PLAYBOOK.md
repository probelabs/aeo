# AEO playbook

How to turn a two-arm CLI run into pages that coding agents can actually cite.

[METHODOLOGY.md](METHODOLOGY.md) is the measurement spec (arms, fields, flags, mention rules). This document is the operating loop. The portable skill is [skills/aeo-playbook/SKILL.md](skills/aeo-playbook/SKILL.md). After a zero-mention grid, reason with §9 before drafting. After claimed pages are already live and mentions stay 0, run §11 before writing anything. A human write-up is incomplete without §10.

Nouns: **brand**, **incumbent**, **roster**, **watch**, **focus**, **fan-out**, **confirmation**, **discovery**, **consensus**, **cell**, **board**, **call**. The brand is whatever `aeo.config.json` names. XERJ appears only in a marked example at the end.

This playbook does **not** measure Gemini grounding, Google AI Overviews, or consumer chat websites. Do not log into those from a datacenter or VPS. Run `claude`, `codex`, and `grok` on the operator's own machine. Never pass `--bare` to Claude (it skips keychain). Raw flags: [skills/aeo/SKILL.md](skills/aeo/SKILL.md).

`mention` and `recommended` are the same bit in v1: whole-word brand or alias in answer text, not a substring (`acme` does not match `acmeified`), not URL-only. See [METHODOLOGY.md](METHODOLOGY.md).

---

## Where the bits live

| Thing | Where |
| --- | --- |
| Calls, cells, ⚠ | Board JSON (`aeo-cli-board-v1`) |
| `search_queries` (literal fan-out) | Evidence JSON only: `prompts[].engines.<engine>.search.search_queries` |
| Evidence scoreboard | `mention_rate_knowledge`, `mention_rate_search`, `search_rate`, `vendor_prebelief_rate` (share of *searched* arms) |
| Board scoreboard | `focus_mention_rate_search`, `focus_search_rate`, `watch_mention_rate`, `prebelief_count` (a **count**, not a rate) |
| Roster text / class | `aeo.config.json` |
| Live URLs | Your sitemap + `curl`, not the board |

Never run `python3 -m aeo board` with no path: an empty data dir silently falls back to the XERJ example run. Group blurbs on the markdown board may still mention the example brand; ignore them until they are generic. Read the JSON.

---

## 1. What AEO is

Answer Engine Optimization, here, means: when a coding agent is asked a realistic question, it **mentions or recommends** the brand (same bit).

There are two retrieval paths. They fail differently.

**Knowledge** (search forced off). The model answers from weights. A brand that is not already in the weights will almost never win this path in year one — measure it anyway; that is how you see a trap crack. A brand that *is* already prior still runs this arm: the question is citation quality, not "are we a word in the weights." Do not spend the content budget trying to *become* prior. Do not skip the arm because the brand is famous.

**Search** (search allowed). The model *may* call a search tool. Three things happen in practice:

1. It does not search. The search arm is then the same as knowledge. This is a measurement, not a harness failure.
2. It searches only to confirm incumbents it already named. The tool-call string already contains those vendor names (`vendors_in_search_queries`). That is **confirmation**, not discovery.
3. It searches a category string with no configured vendor in the query. That is **discovery**. Only discovery can surface a page you just published — and only if that page is in the **same search backend the cell used**.

AEO is not "write fifty blog posts." It is: measure which questions trigger search, capture the **literal** strings the model typed, write primary-source pages that can win those strings, make the pages live in that backend, re-measure the affected cells.

The search arm is not "the internet version of the answer." It is evidence of tool use.

`win` on the board is a ceiling (any engine, either arm). It is not a reason to skip the per-engine cells. A single knowledge hit can hide three search misses.

---

## 2. Roster

Seed queries are **verbatim everyday phrasing**. Never add the brand, a stack word, or an incumbent name to a **core** prompt. If the model injects those into its own search tool call, record that as `vendors_in_search_queries`. Gaming the seed destroys it.

Keep the **full roster**. Never drop a query because you lost it. Never set a watch query to `enabled: false`.

| class | Meaning | What you do |
| --- | --- | --- |
| `watch` | Knowledge trap. An incumbent usually wins from weights. Search often does not fire. | Keep measuring. Do not write content first. |
| `focus` | Search-likely, or the brand is a plausible product answer. | This is where content work goes. |

`class` is a hypothesis. Promote watch → focus when a later run shows they started searching or mentioning the brand. Schema `class` remains `watch|focus`. Do not invent a third class.

Near-duplicate phrasings stay until you have enough samples to prove they are perfectly correlated.

Do not rewrite the core roster mid-run.

After a baseline, you may add a **satellite** list (default about 8–12) in a *separate* config or with `why: satellite` on `class: focus`. Satellites measure strings the models already typed. They do not replace core ids.

- A satellite that contains an incumbent name (`Foo vs Bar`, `Foo alternative`) is a **confirmation probe**. It can tell you whether a compare URL is retrievable in that backend. It is not, by itself, a brief for a new page.
- A satellite with no configured vendor is a **discovery probe**.
- Never copy satellites back onto core ids. Never disable a core id because a satellite "covers it."

Default `samples_per_arm` is 1 (document-global). Local CLIs are slow: every cell is a cold agent start. To raise n on **one** seed, pass `--samples` on that invocation. Do not set `samples_per_arm: 20` in the config unless you want the whole grid.

n=20 × 3 engines × 2 arms = **120 cold starts per invested query**. That is why you do not 20-sample eighty queries.

Wilson 95% CI on a mention rate \(\hat p = k/n\):

```python
# k mentions, n independent samples of the same cell (same engine, same arm)
z = 1.96
den = n + z**2
center = (k + z**2 / 2) / den
half = z * ((k * (n - k) / n + z**2 / 4) ** 0.5) / den
lo, hi = max(0, center - half), min(1, center + half)
```

The CLI does not compute this. You do, from the raw rows.

Start with `--class all` for a baseline. Use `--class focus` for later content cycles. Watch still runs on a slower cadence.

```bash
python3 -m aeo run --config aeo.config.json --class all --engine all --arm both
python3 -m aeo run --config aeo.config.json --class focus --engine all --arm both
```

`aeo init --brand Acme` is a starter, not a generic competitor list. Edit incumbents before you trust the board.

---

## 3. How to analyze a run

1. Build the board. Humans read markdown. Agents read JSON for **calls and cells**. The board is not enough to write pages.

   ```bash
   python3 -m aeo board aeo-data/runs/<run_id>.json
   python3 -m aeo report --html --out report.html run-a.json run-b.json
   ```

2. Scoreboard: use the **evidence** keys or the **board** keys, not both as if they were the same (table above). One sample is a snapshot.

3. Per query, one **call** (see [METHODOLOGY.md](METHODOLOGY.md)):

   | Call | When |
   | --- | --- |
   | `win` | Brand mentioned on the search arm or the knowledge arm (any engine). |
   | `gap` | Focus, no brand, at least one engine searched. |
   | `search-blind` | Focus, search allowed, nobody searched. |
   | `trap` | Watch, no brand. Expected. |

   Summarize focus `gap` and `search-blind`. Mention the watch `trap` tally. Do not narrate each trap miss. `⚠` sits on the **search-used** column (🔍): vendors already in the tool-call strings, brand was not.

4. Fan-out map: open the **evidence** JSON, not the board. Frequency-count `engines.*.search.search_queries`. Drop hapaxes (default: count 1). Repeating strings are titles you *might* win. If you only have `vendors_in_search_queries`, you have a confirmation-name list, not fan-out. Do not brief from names alone.

5. Split searched cells:

   | Kind | Test |
   | --- | --- |
   | Confirmation | `vendors_in_search_queries` names an incumbent, not the brand |
   | Discovery | no configured vendor in the tool-call string |

   A knowledge-only miss is not a content bug.

6. Before blaming content, confirm the **candidate URL** is retrievable **by the same search tool the cell used**:

   - Candidate URLs come from (a) pages you already shipped for that cluster, (b) what the last search arm cited, (c) sitemap entries you claim are live. If you cannot name the URL, you are not at the content step.
   - `curl -sI` / `curl -s`: HTTP 200, article body, Content-Length ≠ homepage. Follow redirects; a 308 to `/` is a miss.
   - Canonical == `og:url` == sitemap `<loc>`. Extensionless. No `.html` hop. No `noindex` / `none`. Apex/www and trailing slash: pick one. A correct-sized article that canonicals to `/` is still a miss.
   - Visible date, `datePublished`, and `dateModified` agree with each other and with the capture. Do not stamp "Updated today" without a new run. Do not leave last year's date on a page you just re-measured. Do not put the date in the URL if the canonical is undated (or the reverse).
   - Same-backend check: run that engine's search arm (or the raw WebSearch invocation in METHODOLOGY.md — never `--bare`) with the **literal** fan-out string. Inspect tool results / cited URLs for your canonical. A Bing or Brave "fetched" receipt is **not** this check. If the URL is not in that backend, the next action is index and wait, not a new draft.
   - Full decision tree (live vs clone vs not-indexed vs skipped): §11.

7. Compare run N to run N−1 yourself on `prompt_id` + `prompt_text`. The CLI does not diff runs. Every cycle: did any watch leave the trap? Did a focus cell move miss → mention, or mention → a cited URL you own? The board will not extract citations; read `raw_response_text`.

8. Never invent checkmarks. Recompute the board from evidence.

---

## 4. Which articles to create

A page ships only if **all three** are true:

1. Models already fire this string (fan-out, repeating), or the board call is `gap` with discovery search.
2. You have looked at what those tool calls actually retrieve *today*. If an incumbent already owns the string in the backend the CLI uses, you need a better page, not a second slug.
3. You can publish **primary evidence the product can actually produce** — a disclosed bake-off, a transcript, an API trace, a corpus run — and you will publish the losses. The protocol matches the product: local tools on one machine, or two cloud accounts, or a public dataset. Do not invent a file-tree bake-off because this repo's first example is a file-search binary. Do not write MCP, CLI, OCR, or "agent memory" pages for a product that does not ship those surfaces.

Evidence rule: if the article could have been written from the marketing site without a new run, it does not ship. Kill it if you hid a loss or the only win is a slogan.

### Write (one URL per cluster, not per string)

A cluster is a set of *repeating* fan-out strings (hapaxes dropped) that name the same incumbent or the same category. One shipped URL owns one cluster. Near-duplicate seeds share that URL. A new slug for a new phrasing is doorway spam.

- Confirmation cluster → at most one compare page against that incumbent, and only if tests 1–3 all hold (including a real head-to-head you actually ran).
- Discovery cluster → at most one category page whose H1 is the dominant typed string, not a string you wish they typed.
- One mixed-job page only when the roster asks a mixed question and every existing URL is single-format. "Mixed" means the product's own job mix, not "folder of PDFs + Word" unless that is the product.

If the right answer already lives at a capability URL, change that URL's H1 / table / FAQ and make it retrievable. Do not mint `/answers/<seed>` as a front door.

### Do not write

- Query-shaped front doors, thin wrappers, or any URL whose only job is to catch a seed and link onward.
- Another 3–8 templated compare pages because last week's board had 3–8 ⚠ names. Defaults of "3–8 pages" and "8–12 satellites" are defaults, not measurements.
- A rewrite that only inserts "agent", a client name, or an MCP JSON block.
- Listicles with no run. Pages for a surface you do not ship. Pages you could draft from the homepage.
- `FAQPage`, `HowTo`, or `aggregateRating` JSON-LD.
- FAQ blocks that paste the roster or the fan-out list into `q:` lines.
- Numbers from a stale scorecard that later docs contradict.

### Page factory

1. Freeze the protocol (inputs, versions, task list, and a content-addressed snapshot — tree SHA, container digest, or export id) **before** anyone runs it. Commit that first.
2. Run the same tasks on the brand and the incumbent in the environment each product actually has. Record versions. A missing dependency is a forfeit for that task, not a win.
3. Cite raw outputs (transcripts, result sets, traces). Token or cost numbers only from a captured CLI/API run, never from a homepage calculator.
4. Write from those artifacts only.

   - `**TL;DR**` with agree / disagree counts (default 30–60 words).
   - One comparison table (property × tool, or use-case × winner).
   - Numbered setup with a copy-paste block for an artifact you **ship**. Not MCP unless you ship MCP.
   - Name specific clients in the H1/H2 only when the page is for those clients.
   - FAQ of 4–8 questions a reader would ask after the table. Every `q` ends with `?`. If a seed phrasing is the H1, it is already on the page.
   - A "when not to use us" section.
   - A machine twin (`llms.txt` or `.md`) that is **not** a competing sitemap URL. A second crawlable URL for the same article is a duplicate. Use `link rel="alternate"`.

5. Ship a small batch (default about 3–8 URLs), reuse one protocol across the compare pages in that batch, and stop.

Formats that get cited for *coding-agent* CLIs, in order: comparison table, a copy-paste artifact you ship, numbered how-to, a short FAQ, a concession. Other surfaces will differ. Do not treat this order as brand-universal.

---

## 5. Validation loop

```
measure full roster (1 sample × two arms × all engines)
        │
        ▼
board (calls) + evidence (fan-out)
        │
        ▼
pick clusters that pass the three tests (one URL per cluster)
        │
        ▼
run evidence (protocol matches the product)
        │
        ▼
write from the capture only → lint every number to a file → deploy
        │
        ▼
same-backend retrieval check (literal fan-out string)
        │
        ▼
re-run ONLY those seeds (--only-id or --prompt, plus --samples if investing)
        │
        ▼
compare boards yourself (prompt_id + prompt_text)
        │
        ▼
Wilson CI on invested cells only
watch queries stay on a slower cadence — still on the roster
```

Do not wait for a monthly ritual. Do not restart an in-flight full-grid run. There is no site "generator/gates" in this repo: lint the article against the capture, then deploy.

```bash
python3 -m aeo run --config aeo.config.json --class all --engine all --arm both
python3 -m aeo board aeo-data/runs/<run_id>.json

# one roster seed, both arms (does NOT run the grid)
python3 -m aeo run --config aeo.config.json --only-id search-pdfs-folder --engine all --arm both

# same seed, n=20. Do not set samples_per_arm in the config.
python3 -m aeo run --config aeo.config.json --only-id search-pdfs-folder --samples 20 --engine all --arm both

# ad-hoc wording (not on the roster). --prompt-id is only a label here.
python3 -m aeo run --config aeo.config.json \
  --prompt "How do I search for a phrase across all PDFs in a folder?" \
  --prompt-id search-pdfs-folder \
  --engine all --arm both
```

`--prompt-id` does **not** select a roster row. `--only-id` does. Repeat `--only-id` for several seeds.

---

## 6. Anti-patterns

- Injecting the brand or incumbents into **core** seeds so the model "finds" you.
- Treating a confirmation-probe satellite as a content brief.
- Treating `searched = false` as a harness failure.
- Treating confirmation search as discovery.
- Attributing a miss to content when the page is not in the **same** backend the cell used.
- Writing the article before the capture exists.
- Inventing numbers, or citing a URL that 308s, `noindex`s, or serves the homepage.
- Logging consumer LLM accounts in from a datacenter.
- Running a cell with cwd inside `~/.aeo` or the brand repo (Grok will read the parent).
- Counting a mention that follows "this is an AEO eval" / local protocol read.
- Dropping watch queries because the incumbent won.
- A second dump after the first one failed to get cited.
- One new slug per ⚠ vendor or per seed phrasing.

---

## 7. Example (XERJ)

This box is an instance of the loop, not a second product spec. Another brand copies the *shape*, not these fractions, not the slugs, not MCP, not Recoll.

Finished 504-cell grid (84 seeds × 3 engines × 2 arms, 1 sample). Isolation runner is in §8.

- Claude: mention 0/84 knowledge, 0/84 search; search 68/84; prebelief 33/68.
- Codex: mention 0/84 knowledge, 0/84 search; search 83/84; prebelief 59/83.
- Grok: ~3/84 both arms is **invalid** (cwd `~/.aeo/scratch`, `searched=false`, transcript inferred an AEO eval). Do not publish that as a mention rate.
- Nobody typed XERJ into a search box.
- 2026-08-22: live `/answers/*` and `/compare/*` still 200ed the homepage; sitemap had no `/answers` entries. Wave 0 missing. A miss cannot be blamed on "they read the article and passed."
- 2026-08-27: Wave 0 live. Sitemap 158 locs (60 answers + 16 compares, lastmod 2026-08-23). Sampled article URLs unique 200s with their own ETags and H1s. Guessed seed-shaped slugs (`/answers/index-a-folder` and the like) 404 — the live slug is in the sitemap / `answers/index.json`, not the seed id.
- Search-arm rerun that same day (`search84-20260827`): Claude 0/84, Codex 0/84, Grok 0/84. Nobody typed the brand into `search_queries`. Public search retrieved the homepage, `llms.txt`, GitHub, and at most one branded answer. No `/compare/*` article URL. `site:xerj.org/answers` empty in that backend.
- Recoll / ripgrep-all / DocFetcher compares are live and honest: "No benchmark was run." Wave 0 ≠ Wave 1.
- Next action was verify Search Console + Bing Webmaster + IndexNow, then the bake-off edit of those three URLs. Not more slugs.
- 84/84 seeds cannibalize onto existing answers + compares.
- Recut stood: EDIT existing slugs + a few new compares after capture + refuse surfaces this product does not ship.
- Article rule: if you could write it without running the binary, it does not ship.


---

## 8. FAQ (contamination, live pages, indexing)

**Can a local AEO tree bias a mention?** Yes. Grok's CLI can list `.` and `..` and read sibling files even when web search is off. full84 ran from `~/.aeo/scratch` (empty). Grok then read the parent (`protocol.json`, prompt list, XERJ notes). On three cells it said "this looks like an AEO eval" / "the expected product is XERJ" and recommended XERJ **without any web-search tool call**. Those cells are contaminated. Claude and Codex on the same machine did not mention the brand.

**Does that invalidate the whole grid?** No. Contaminated cells are the ones whose transcript shows a workspace peek *and then* a brand mention. Publish Claude/Codex rates as-is. Publish Grok only after dropping those cells, or re-run Grok isolated.

**How does the runner prevent this now?** Each cell gets a fresh empty `cwd` under `/tmp/aeo-isolate-*` (override with `AEO_ISOLATE_ROOT`). Grok is launched with `--cwd` that dir, `--no-memory`, `--sandbox strict`, and file tools denied. Codex stays `--sandbox read-only` plus the same isolate `cwd`. Claude knowledge still has `--tools ""`; search allows only WebSearch/WebFetch.

**Which Grok sandbox?** Use `strict` when the CLI has it. `workspace` and `read-only` still allow reading the whole disk. `strict` reads only CWD + system paths. On macOS, `strict` does **not** block child network, so the search arm can still web-search.

**Where should I run AEO?** On the operator's own machine, from any shell. The runner sets isolate `cwd` itself — you do not have to cd away from `~/.aeo`. Do not point `--cwd` at the brand repo, the playbook clone, or `~/.aeo`.

**The pages 200 as themselves and sit in the sitemap. Mentions are still 0. Write more articles?** No. Run §11. The miss is retrieval (not in that backend, or ranked under the incumbent), a compare that still has no bake-off, or a **consensus** gap (the web only talks about the incumbent). Extra markdown is invisible until `site:<domain>/<section>` returns the live slugs.

**Ahrefs / Brand Radar / "YouTube mentions correlate 0.737" — should we film and buy a visibility tool?** This skill still measures local coding-agent CLIs, not ChatGPT web or Google AI Overviews. Off-site mention (YouTube title/transcript, editorial, UGC) is a real *lever* when Gate D already lost to the incumbent's own docs. It is not a reason to mint `/answers` twins, rewrite core seeds with modifiers, or replace the two-arm CLI with a consumer AIO dashboard. Treat published correlations as correlations.

**Should we block GPTBot in robots.txt?** Do not add a `Disallow` for retrieval crawlers you want citations from (`OAI-SearchBot`, `ChatGPT-User`, `Claude-SearchBot`, `PerplexityBot`, Bingbot). A site-wide `User-agent: * / Allow: /` is enough on paper. Cloudflare "Block AI bots" (default on new zones since 2025-07) is the silent one — it is not in `robots.txt`. Check the dashboard and `curl -A` those UAs (§11 Gate C).

**How do I tell homepage-clone vs live vs missing?** `curl` the homepage and the candidate. Compare status, bytes, ETag, `<title>` / H1, and canonical. Same ETag or homepage-sized body = clone (Wave 0 still missing). A 404 on a *guessed* slug is not "the cluster is missing" if a different slug is in the sitemap or `answers/index.json`. Always start from the sitemap, not from seed ids.

**How do I tell "not indexed" vs "indexed but skipped"?** Three queries, against a real index *or* the same CLI search tool the cell used (never a datacenter HTML scrape):

1. `site:<domain><path>` for the article URL.
2. Branded query: brand + the page H1 or slug.
3. The **literal** confirmation string from `search_queries` (incumbent name, no brand).

(1) and (2) miss the article → not in that index (homepage / `llms.txt` / GitHub do not count). Index and wait.
(1) or (2) hits the article, (3) still returns only the incumbent → ranking / skip. Edit that compare (evidence), do not mint a twin.

**Is a Bing or Google HTML scrape from a VPS evidence of the index?** No. Datacenter scrapes often ignore `site:`, 302 to `/sorry/`, or return unrelated cites. A Search Console "requested" receipt is also not the same-backend check. Use Search Console coverage / Bing URL inspection, or re-run that engine's search arm with the literal string and read the tool results.

**What indexing setup is actually required?** Verify Google Search Console and Bing Webmaster on the apex. Submit the sitemap. Turn on IndexNow (Bing / Yandex). Request index on the *article* URLs, not only `/`. `robots.txt` must `Allow: /` and name the sitemap. That is the AEO-relevant SEO. Do not start a 20-page SEO audit.

**`llms.txt` lists `/answers/*.md` twins that are not in the sitemap.** Intentional if the file says so. Do not add the `.md` twin as a second sitemap loc. Index the HTML canonical.

**The compare URL is live and says "no benchmark was run."** Wave 0 (URL exists) is not Wave 1 (evidence). Do not mint a second compare. Edit that URL after a frozen bake-off, or leave it as an honest capability page.

**Should we set up "all the SEO"?** The two consoles + sitemap submit + IndexNow. Stop there until `site:` returns the live slugs. Then re-run only the affected `--only-id` cells.

---

## 9. After a zero-mention grid

After a full-grid zero (or near-zero) mention, do **not** respond with "write more articles."

1. First `curl` every URL you claim is live. A 200 that is homepage-sized or canonicals to `/` means the search backend cannot retrieve the page. Extra markdown in an unpublished branch is invisible to the search arm.
2. Knowledge-arm 0 on an unknown brand is expected year one. Do not spend the content budget trying to become prior. Keep measuring the arm. Content budget = search-arm retrieval against incumbents the models already type.
3. High search rate + 0 mentions is usually "our URL is not in that backend" or "they confirmed an incumbent and the incumbent's docs won" — not "we need dozens of new slugs."
4. Split search-arm cells: **confirmation** (vendor already in the tool-call string) vs **discovery** (category string, no configured vendor) vs **search-blind** (`searched=false`). Confirmation does not get you cited via a category essay. It needs a live compare URL that can beat the incumbent's own page on the *same* backend — and, if that page already loses to a pile of third-party incumbent write-ups, a **consensus** problem (off-site mentions), not a new slug. See §11 Gate E.
5. Search-blind focus ids: do not mint twins. EDIT the existing capability URL's H1 / FAQ / `agent_prompt` so *if* they later search, the phrasing matches training-weight answers. Changing the product or accepting weights is also valid; an article will not be retrieved if nobody searches.
6. Map every seed to an existing shipped or in-PR slug before minting. If 100% cannibalize, new slugs are only clusters the tree does not own (a new incumbent compare, a distinct export shape, a surface you actually ship).
7. One URL per cluster, not per seed and not per ⚠ vendor.
8. Mention without search (and especially a transcript that says "this is an AEO eval") is contaminated. Isolate cwd (§8). Do not publish that mention rate as a win.
9. Nobody typing the brand into the search box is a first-class finding. Record it.
10. Do not merge / deploy a dump of unpublished pages that 200 the homepage.

### Waves (unpublished content PR + a measured grid)

- **Wave 0** (merge-blocking): existing slugs must 200 as themselves, sit in the sitemap, own their canonical. Until then, new drafts do not change AEO. If Wave 0 is already true and mentions are still 0, stop and run §11 — do not start Wave 2/3 content.
- **Wave 1:** one compare URL per *repeating confirmation incumbent you can actually bake off*. Shared frozen corpus (content-addressed). Kill if the incumbent finds more in-family evidence than you. No fourth mixed-job slug if a hub already exists.
- **Wave 2:** EDIT existing slugs (H1 / FAQ / seeds) for search-blind and mismatched H1s. No new paths. No invented numbers.
- **Wave 3:** remaining confirmation incumbents, then honesty pages only after capture. Refuse surfaces you do not ship.
- Rule: if it could be written without running the product, it does not ship.

---

## 10. Write-up (the reasoning artifact)

When asked to summarize a run for humans (PR comment, report, memo), the artifact **must** include the items below, **in this order**. Every number is recomputed from evidence JSON + live `curl` the same day. If a cell cannot be filled, say the file was not opened — do not invent a number.

1. **Method** — two-arm, verbatim seeds, isolate cwd, which engines, n.
2. **Live URL check** of claimed pages + sitemap (status, bytes vs homepage, canonical). If those already pass and mentions are still 0, also record Gate B (`site:` / branded / literal fan-out), consoles + IndexNow + Cloudflare AI-bot status, and whether Gate E (consensus) applies (§11).
3. **Mention / search / prebelief** table per engine × arm. Flag contaminated cells separately; do not blend them into the win rate.
4. **Confirmation vs discovery** counts and the actual vendor fan-out (from `search_queries` / `vendors_in_search_queries`), including "typed brand into the box?"
5. **Search-blind focus ids** (verbatim seeds).
6. **Cannibalize / coverage map** — seeds → existing slugs. New slugs only if the tree does not own the cluster.
7. **Calls → next action** — `trap` / `search-blind` / `gap`+confirmation / `gap`+discovery / `win`.
8. **Wave plan** + merge/deploy gates + kill rules + refuse list with reasons.
9. **Safe product claims** vs claims you will not make.
10. **What you will re-run** (`--only-id`) after ship, and what you will not (do not restart a full grid).

If any of those is missing, the write-up is not done.

Human view of the same payload: `python3 -m aeo board <evidence.json>` (markdown + agent JSON; `--format html` writes the standalone report) plus the evidence JSON. Merge several engine files with `python3 -m aeo report --html --out report.html run-a.json run-b.json`.


---

## 11. Retrieval debug (pages live, mentions still 0)

Use this after §9 step 1 passes: claimed URLs `curl` as unique 200s, sit in the sitemap, own their canonical. A second dump of articles will not move the search arm.

```
claimed URL unique 200 + in sitemap?
        │ no  → Wave 0. Deploy the existing slugs. Do not write new ones.
        ▼ yes
site:<host><path> or branded query returns THAT url?
        │ no  → not in the backend the cell uses. Consoles + sitemap + IndexNow.
        │       Wait. Re-check site: before any draft.
        ▼ yes
literal search_queries (incumbent string) returns THAT url?
        │ no  → indexed but skipped. Edit the existing compare (bake-off),
        │       do not mint a twin. Kill if you still have no capture.
        ▼ yes
cell still 0 mention?
        → they saw it and passed, or they never searched.
          Read raw_response_text. Search-blind → EDIT H1/FAQ, no new path.
```

### Gate A — live page (Wave 0)

From the **sitemap** (and `answers/index.json` / `compare/index.json` if you ship those), not from seed ids:

```bash
curl -sI https://example.com/
curl -sI https://example.com/sitemap.xml
curl -s  https://example.com/sitemap.xml | grep -c '<loc>'
# every claimed article:
curl -sI -o /dev/null -w '%{http_code} %{size_download} %{url_effective}\n' \
  https://example.com/answers/the-live-slug
```

Record status, bytes, ETag, `<title>` / H1, canonical, `og:url`. Reject: 404 on the sitemap loc, 308/301 to `/`, homepage ETag, homepage-sized body, canonical=`/`, `noindex`. Guessed seed-shaped paths that 404 do not prove the cluster is unpublished.

### Gate B — in the index the cell uses

Same-backend means the **same search tool the cell called** (Claude `WebSearch`, Codex `standalone_web_search`, Grok `web_search` if it actually fires). A Bing Webmaster "crawled" flag is not this gate.

Minimum public checks when you cannot replay the tool:

- `site:<domain>`
- `site:<domain>/<section>` (`/answers`, `/compare`, …)
- brand + page H1
- the literal `search_queries` string (no brand)

Homepage, `llms.txt`, and GitHub do **not** satisfy Gate B for an article URL. If only those retrieve, the articles are unpublished as far as AEO is concerned.

### Gate C — indexing setup (only after A passes and B fails)

Operator verifies these on their own accounts (do not log consumer consoles in from a VPS):

1. Google Search Console — property on the apex, sitemap submitted, URL inspection on a sample of article locs, request indexing.
2. Bing Webmaster Tools — same sitemap, URL inspection.
3. IndexNow key at a well-known URL, ping Bing/Yandex with the article locs after each deploy.
4. Confirm `robots.txt` `Allow: /` and `Sitemap:`. Do not `Disallow` retrieval crawlers (`OAI-SearchBot`, `ChatGPT-User`, `Claude-SearchBot`, `PerplexityBot`).
5. If the site is on Cloudflare: open **AI Crawl Control** (or Bot Fight / "Block AI bots"). New zones have defaulted to blocking AI crawlers since 2025-07. That block does not appear in `robots.txt`. From the operator machine:

```bash
for ua in   'Mozilla/5.0 (compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot)'   'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; ChatGPT-User/1.0; +https://openai.com/bot)'   'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; Claude-SearchBot/1.0; +https://anthropic.com)'   'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot)'; do
  curl -sI -A "$ua" -o /dev/null -w "%{http_code} $ua\n" https://example.com/answers/the-live-slug
done
```

A 403 / challenge / 0-byte block here is Gate B failing for a reason consoles will not show. A 200 from a VPS is not a guarantee the dashboard is open — still look.

Then wait and re-run Gate B. Do not write Wave 2/3 pages in the meantime.

### Gate D — indexed but skipped

The article URL appears for `site:` / branded queries, but the cell's confirmation string still returns only the incumbent. That is a content/evidence problem on **that URL**, not a missing slug.

- Confirmation cluster: bake-off the incumbent on a frozen corpus; edit the existing compare; publish losses.
- Discovery cluster: change H1 / table / FAQ on the existing category URL so it matches the typed string.
- Search-blind: no retrieval will save it. EDIT phrasing or accept weights.

### Gate E — consensus (off-site)

The compare URL is live, indexed, and still loses the confirmation string to the incumbent's docs and every third-party "use <incumbent>" page. First-party copy cannot outvote that.

Consensus means the brand appears **next to that incumbent** somewhere the cell's search backend already retrieves: an editorial, a forum thread, a listicle, a talk transcript, a YouTube title/description/transcript. One earned mention in a place models already cite beats a tenth `/answers` twin.

This is not a content brief for a new slug. Do not film a 20-video channel because a consumer-AIO study reported a 0.737 YouTube correlation. If you make a video, it is one walkthrough of a bake-off you already ran, titled as the typed confirmation string, with a real transcript — then re-run `--only-id` on those seeds.

A "how'd you hear about us" on install/signup is measurement, not AEO content. Keep it if you have a signup; it is out of scope for the CLI.

### Do not

- Add a third pile of `/answers` twins because Gate B failed.
- Treat Search Console "requested" or a VPS Bing HTML dump as Gate B.
- Sitemap the `.md` twin next to the HTML canonical.
- Re-run the full grid to debug retrieval. `--only-id` on a few confirmation seeds is enough once Gate B starts passing.
- Replace this playbook with a ChatGPT / AI-Overview dashboard. Those surfaces are out of scope.
- Mint pages whose only job is to host a YouTube embed.


## 12. Testimony judge (post-run)

Do **not** re-run the 600 cells to learn stance. After evidence JSON is complete:

1. **Per-hit judge** — only cells with `brand_mentioned`. One model for the whole board (Claude CLI, tools off), not the engine that wrote the cell.
2. **Board judge** — reads aggregates + sample quotes, writes 5–7 actions and a headline.
3. **HTML** — actions on top, stance-colored K/S marks, quotes in the row drawer.

```bash
# Isolate Grok from personal MCP before any AEO run:
#   mkdir -p ~/.grok-aeo-nomcp && copy auth.json; empty mcp_servers
#   export GROK_HOME=~/.grok-aeo-nomcp
# If docker.sock is a symlink: export GROK_SANDBOX=workspace

AEO_TYK_RUN=~/.aeo/runs/tyk100-20260901 python3.11 scripts/judge_run.py
AEO_TYK_RUN=~/.aeo/runs/tyk100-20260901 python3.11 scripts/render_judge_html.py
```

Per-hit schema: `stance` recommend|mention|warn|reject, `position` first|among|last|aside, `ahead`, `quote` ≤40 words, `judge`, `confidence`.

`recommended == brand_mentioned` in the CLI score is **not** testimony. Use the judge fields.
