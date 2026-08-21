# AEO playbook

How to turn a two-arm CLI run into pages that coding agents can actually cite.

[METHODOLOGY.md](METHODOLOGY.md) is the measurement spec (arms, fields, flags, mention rules). This document is the operating loop. The portable skill is [skills/aeo-playbook/SKILL.md](skills/aeo-playbook/SKILL.md).

Nouns: **brand**, **incumbent**, **roster**, **watch**, **focus**, **fan-out**, **confirmation**, **discovery**, **cell**, **board**, **call**. The brand is whatever `aeo.config.json` names. XERJ appears only in a marked example at the end.

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
- Dropping watch queries because the incumbent won.
- A second dump after the first one failed to get cited.
- One new slug per ⚠ vendor or per seed phrasing.

---

## 7. Example (XERJ)

This box is an instance of the loop, not a second product spec.

- Core roster: 84 verbatim questions, 12 watch / 72 focus, none disabled.
- Baseline (Claude, 1 sample, both arms): mention rate 0 / 0, search rate ~0.81, vendor pre-belief ~0.49. Zero brand mentions. Search-tool strings named Recoll, ripgrep-all, Omnisearch, Pagefind, Elasticsearch, Meilisearch — not the brand's slugs.
- Planned `/answers/*` and `/compare/*` were not live (every URL 200ed the homepage). Sitemap had no `/answers` entries. A miss could not be blamed on "they read the article and passed."
- Article rule: if you could write it without running the binary, it does not ship. Two compare pages existed in the unpublished branch (vs ripgrep, vs a vector database). None vs Recoll, ripgrep-all, or DocFetcher.
- Next batch, after a frozen mixed-docs corpus and real incumbent installs: at most one compare URL per repeating incumbent cluster, plus one mixed-folder answer if that cluster is distinct. Satellite *probes* (`local file search for AI agents`, `ripgrep-all vs Recoll`) wait until the three-engine board lands; the `vs` probes are confirmation checks, not new core seeds. Email / OCR / live Jira were refused — surfaces this product does not win.

Another brand copies the *shape*, not the slugs, not MCP, not Recoll.
