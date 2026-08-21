# AEO playbook

How to turn a two-arm CLI run into pages that coding agents can actually cite.

[METHODOLOGY.md](METHODOLOGY.md) is the measurement spec (arms, fields, flags, mention rules). This document is the operating loop: what the numbers mean, which articles to write, and how to check that a ship moved a cell. The portable skill is [skills/aeo-playbook/SKILL.md](skills/aeo-playbook/SKILL.md).

Nouns used here are generic: **brand**, **incumbent**, **roster**, **watch**, **focus**, **fan-out**, **confirmation**, **discovery**, **cell**, **board**, **call**. The brand is whatever `aeo.config.json` names. XERJ appears only in a marked example at the end.

This playbook does **not** measure Gemini grounding, Google AI Overviews, or consumer chat websites. Do not log into those from a datacenter or VPS. Run `claude`, `codex`, and `grok` on the operator's own machine.

---

## 1. What AEO is

Answer Engine Optimization, here, means: when a coding agent is asked a realistic question, it **mentions or recommends** the brand.

There are two retrieval paths. They fail differently.

**Knowledge** (search forced off). The model answers from weights. A new brand almost never wins this path in the first year. Do not spend the content budget trying to become prior. Measure it anyway. That is how you see a trap crack.

**Search** (search allowed). The model *may* call a search tool. Three things happen in practice:

1. It does not search. The search arm is then the same as knowledge. This is a measurement, not a harness failure.
2. It searches only to confirm incumbents it already named. The tool-call string already contains those vendor names (`vendors_in_search_queries`). That is **confirmation**, not discovery.
3. It searches a category string with no configured vendor in the query. That is **discovery**. Only discovery can surface a page you just published — and only if that page is actually live and in the search backend the CLI uses.

AEO is not "write fifty blog posts." It is: measure which questions trigger search, capture the **literal** strings the model typed, write primary-source pages that can win those strings, make the pages live and crawlable, re-measure the affected cells.

The search arm is not "the internet version of the answer." It is evidence of tool use.

---

## 2. Roster

Seed queries are **verbatim everyday phrasing**. Never add the brand, a stack word, or an incumbent name to the prompt. If the model injects those into its own search tool call, record that as `vendors_in_search_queries`. That is the signal. Gaming the seed destroys it.

Keep the **full roster**. Never drop a query because you lost it. A miss is data.

Tag every query:

| class | Meaning | What you do |
| --- | --- | --- |
| `watch` | Knowledge trap. An incumbent usually wins from weights. Search often does not fire. | Keep measuring. Do not write content first. |
| `focus` | Search-likely, or the brand is a plausible product answer. | This is where content work goes. |

`class` is a hypothesis. Promote watch → focus when a later run shows they started searching or mentioning the brand. Never set a watch query to `enabled: false`.

Near-duplicate phrasings stay on the roster until you have enough samples to prove they are perfectly correlated. Do not collapse "how do I search Jupyter cells" and "I need to find a function in a notebook" after one run.

Do not rewrite the core roster mid-run. A finished engine's cells are the baseline.

After a baseline, you may add a **satellite** set (about 8–12 queries) that match strings models actually fire and that the core roster does not contain. Do not replace core queries with satellite ones. Do not inject incumbents into existing seeds to "help" the model find you.

Default `samples_per_arm` is 1. Local CLIs are slow: every cell is a cold agent start. Raise to about 20 and report a Wilson 95% CI **only** on the queries you are investing content in. Do not 20-sample the whole grid by default.

```bash
python3 -m aeo run --config aeo.config.json --class all --engine all --arm both
python3 -m aeo run --config aeo.config.json --class focus --engine all --arm both
```

---

## 3. How to analyze a run

1. Build the board. Humans read markdown. Agents read JSON (`aeo-cli-board-v1`). Do not narrate raw answers first.

   ```bash
   python3 -m aeo board aeo-data/runs/<run_id>.json
   ```

2. Read the scoreboard: `mention_rate_knowledge`, `mention_rate_search`, `search_rate`, `vendor_prebelief_rate`. One sample is a snapshot, not a rate you can bet the company on.

3. Per query, one **call** (deterministic, see [METHODOLOGY.md](METHODOLOGY.md)):

   | Call | When |
   | --- | --- |
   | `win` | Brand mentioned on search or knowledge, any engine. |
   | `gap` | Focus, no brand, at least one engine searched. |
   | `search-blind` | Focus, search allowed, nobody searched. |
   | `trap` | Watch, no brand. Expected. |

   Summarize focus `gap` and `search-blind`. Mention the watch `trap` tally. Do not narrate each trap miss. `⚠` on the search column is confirmation (vendors already in the tool-call strings, brand was not).

4. Build the **fan-out map**. Take every `search_queries` string on search-allowed arms. Frequency-count them. Rare strings are noise. Repeating strings are the titles and FAQ `q:` lines the next pages must win. This list is usually *not* your slugs. It is incumbent names, category phrases, and GitHub URLs.

5. Split searched cells:

   | Kind | Test | What a page must do |
   | --- | --- | --- |
   | Confirmation | `vendors_in_search_queries` names an incumbent, not the brand | Rank for that incumbent's name (`Foo vs …`, `Foo alternative`) |
   | Discovery | category query, no configured vendor in the tool call | Rank for the category string the model typed |

   A knowledge-only miss is not a content bug. There was nothing to retrieve.

6. Before blaming content, confirm candidate pages are **retrievable**:

   - HTTP 200 with the *article* body, not a homepage fallback of the same byte size.
   - Canonical == `og:url` == sitemap `<loc>`, extensionless, no `.html` hop.
   - Fetched by Bing, Brave, or whatever backend the CLI search tool uses.

   A perfect page that 200s as the homepage is invisible. A miss you attribute to "they don't know us" is then a deploy bug.

7. Compare run N to run N−1 on the **same prompt ids**. Every cycle ask only two questions: did any watch leave the trap? Did any new page move a focus cell from miss → mention, or mention → a cited URL?

8. Never invent checkmarks. Recompute the board from evidence (`python3 -m aeo board` again if unsure).

---

## 4. Which articles to create

A page ships only if **all three** are true:

1. Models already fire this string (fan-out), or the board call is `gap` with discovery search.
2. No incumbent page owns it in agent language. What currently gets cited: GitHub READMEs with a one-line H1 and a tool table, comparison tables, copy-paste MCP/CLI JSON, numbered how-tos, FAQ blocks that reuse the seed phrasing. What does not: marketing landing pages, protocol-compatibility claims, changelogs.
3. You can run the product against the incumbent on one **disclosed** corpus and publish the losses.

### Write

- Compare pages versus names that appear in `vendors_in_search_queries`.
- Category pages whose H1 matches a repeating discovery string, not your internal slug.
- One mixed-job page when the roster asks a mixed question and you only have single-format answers.
- Query-shaped front doors that link to existing capability docs. A recipe at `/docs/recipes/…` is not the URL a WebSearch returns.

### Do not write

- Another fifty templated long-tail pages (doorway / scaled-content risk).
- A rewrite of an existing answer that only inserts the word "agent".
- Listicles with no run.
- Pages for clusters the product cannot win (missing format family, no OCR, a live SaaS API you do not ship). Missing is correct.
- Pages you could draft from the homepage without a terminal.
- `FAQPage`, `HowTo`, or `aggregateRating` JSON-LD.
- Numbers from a stale scorecard that later docs contradict.

### Evidence rule

If an article could have been written without running the product, it does not ship.

Every page carries a measured number, a real transcript, or a published failure. Kill the page if you did not run both sides, if you hide losses, or if the only win is a slogan.

### Page factory

1. Freeze a corpus (file list + tree SHA) and a task list **before** anyone indexes. Commit that protocol first.
2. Install incumbents on the same machine as the CLIs. Record versions. Without the extractor a tool needs (for example `pdftotext` for PDF search), you are not comparing that format.
3. Run the same tasks. Reduce each side to a **set of files**. Use returned hits, not aggregations, not GUI impressions. Then an agent arm: can the incumbent be driven without a human? Token counts only from a real `claude -p --output-format json` (or equivalent) capture.
4. Write the article from those files only.

   - `**TL;DR**` with agree / disagree counts (30–60 words).
   - One comparison table (property × tool, or use-case × winner).
   - Numbered setup with a copy-paste block an agent can run.
   - Named clients in the H1 or H2: Claude, Codex, Grok — not "LLMs".
   - FAQ of 4–8 questions. Every `q` ends with `?`. Use seed phrasing **and** fan-out strings.
   - A "when not to use us" section. Models prefer pages that concede.
   - A machine twin (`llms.txt` block or `.md`) that is **not** a competing sitemap document. Reference the twin with `link rel="alternate"`, never an ordinary `href` to the same content.

5. Ship a **small** batch (about 3–8 pages), not a second dump. Reuse one corpus across the compare pages in that batch.

Formats that get cited, in order: comparison table, copy-paste JSON or CLI, numbered how-to, FAQ using the seed phrasing, then a concession.

---

## 5. Validation loop

```
measure full roster (1 sample × two arms × all engines)
        │
        ▼
board + fan-out map
        │
        ▼
pick 3–8 pages that pass the three tests
        │
        ▼
run evidence (frozen corpus + incumbents + agent arm)
        │
        ▼
write from the capture only → generate → gates
        │
        ▼
deploy (real article 200s, not homepage)
        │
        ▼
search-engine fetch (Bing / Brave or the CLI's backend)
        │
        ▼
re-run ONLY the affected focus queries
(the ones whose fan-out named that incumbent or category)
        │
        ▼
compare boards (same prompt ids)
        │
        ▼
raise n ≈ 20 + Wilson 95% CI on invested queries only
watch queries stay on a slower cadence — still on the roster
```

Do not wait for a monthly ritual. Re-run after each ship and index. Do not restart an in-flight full-grid run.

Cold CLI starts are expensive (system prompt and tools reload every cell). Track tokens and spend so a 20-sample pass has a budget before you start it. The spend is the method, not a leak. It is still a reason not to 20-sample eighty queries.

Commands for the measure and re-measure steps:

```bash
python3 -m aeo run --config aeo.config.json --class all --engine all --arm both
python3 -m aeo board aeo-data/runs/<run_id>.json
# after a ship: re-run only the prompt ids whose fan-out named the new page's incumbent
python3 -m aeo run --config aeo.config.json --prompt-id <id> --engine all --arm both
```

(If `--prompt-id` is not implemented, pass `--prompt` with the verbatim seed text.)

---

## 6. Anti-patterns

- Injecting the brand or incumbents into seeds so the model "finds" you.
- Treating `searched = false` as a harness failure.
- Treating confirmation search as discovery.
- Attributing a miss to content when the page is not live or not indexed.
- Writing the article before the capture exists.
- Hand-editing generated HTML, inventing numbers, or citing a URL that 308s or serves the homepage.
- Logging consumer LLM accounts in from a datacenter.
- Dropping watch queries because the incumbent won.
- A second 50-page dump after the first one failed to get cited.

---

## 7. Example (XERJ)

This box is an instance of the loop, not a second product spec.

- Core roster: 84 verbatim questions, 12 watch / 72 focus, none disabled.
- Baseline (Claude, 1 sample, both arms): mention rate 0 / 0, search rate ~0.81, vendor pre-belief ~0.49. Zero brand mentions. Search-tool strings named Recoll, ripgrep-all, Omnisearch, Pagefind, Elasticsearch, Meilisearch — not the brand's slugs.
- Planned `/answers/*` and `/compare/*` were not live (every URL 200ed the homepage). Sitemap had no `/answers` entries. So a miss could not be blamed on "they read the article and passed."
- Article rule already in force: if you could write it without running the binary, it does not ship. Two compare pages existed in the unpublished branch (vs ripgrep, vs a vector database). None vs Recoll, ripgrep-all, or DocFetcher.
- Next batch, after a frozen mixed-docs corpus and real incumbent installs: three compare pages plus one mixed-folder answer. Satellite seeds (`local file search for AI agents`, `ripgrep-all vs Recoll`, …) wait until the full three-engine board lands. Email / OCR / live Jira pages were refused — the product cannot win those clusters.

Another brand should copy the *shape* (roster, fan-out, live-URL check, small evidence-backed batch), not the slugs.
