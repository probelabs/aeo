#!/usr/bin/env python3.11
"""Tyk AEO HTML: board actions on top, stance-colored K/S grid, quotes in the drawer."""
from __future__ import annotations

import html, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aeo.counts import answer_records, count_problems, headline as count_headline, summarize  # noqa: E402
from aeo.retrieval import arm_browsing  # noqa: E402
from aeo.vendors import (  # noqa: E402
    classified_query_vendors_for_arm,
    classified_vendors_for_arm,
    load_vendor_store,
    named_vendor_counts_by_origin,
    search_box_vendor_counts,
    seed_alias_map,
    workspace_from_docs,
)

ENGINES = ("claude", "codex", "grok")
ARMS = ("knowledge", "search")



# Reader-facing words for internal labels. Reports must never show snake_case
# field names (mention_k, search_likely, brand_mentioned, ...).
ARM_LABELS = {"knowledge": "Named without search", "search": "Named with search"}
QUESTION_CLASS_WORDS = {
    "knowledge_trap": "general-knowledge question",
    "search_likely": "likely to search",
    "product_fit": "product fit",
}


def plain_class(label: str) -> str:
    """Turn a roster class/why tag such as 'search_likely+product_fit' into plain words."""
    parts = [p.strip() for p in re.split(r"[+,|]", str(label or "")) if p.strip()]
    return ", ".join(QUESTION_CLASS_WORDS.get(p, p.replace("_", " ")) for p in parts)


def brand_terms_words(brand: str, aliases) -> str:
    """'Proof, ReqProof or reqproof.com' from the brand and its aliases."""
    terms: list[str] = []
    for t in [brand, *(aliases or [])]:
        t = str(t or "").strip()
        if t and t.lower() not in {x.lower() for x in terms}:
            terms.append(t)
    if len(terms) <= 1:
        return terms[0] if terms else ""
    return ", ".join(terms[:-1]) + " or " + terms[-1]


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def search_chart_surprise_set(counts, brand: str, alias_map) -> set[str]:
    """Search-chart surprise badges: not on the config seed list. Do not mix answer-side surprises."""
    return {n for n in counts if n != brand and not alias_map.is_seed(n)}


def format_named_list(items: list) -> str:
    """Join names; badge surprise records or `(surprise)` suffixes."""
    bits = []
    for item in items or []:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("normalized") or item.get("raw") or "")
            origin = item.get("origin") or ""
        else:
            name = str(item)
            origin = ""
        if not name:
            continue
        if origin == "surprise":
            bits.append(f"{esc(name)} <span class='badge-surprise'>surprise</span>")
        else:
            bits.append(esc(name))
    return ", ".join(bits)


def harness_block(
    *,
    engine: str,
    arm_name: str,
    kind: str,
    brand: str,
    stance: str = "",
    position: str = "",
    quote: str = "",
    ahead: list | None = None,
    competitors: list | None = None,
    query_vendors: list | None = None,
    arm: dict | None = None,
) -> str:
    """One drawer card: summary line + collapsible raw answer."""
    ahead = ahead or []
    competitors = competitors or []
    arm = arm if isinstance(arm, dict) else {}
    raw = arm.get("raw_response_text") or ""
    search_qs = arm.get("search_queries") or []

    browsing_note = ""
    if kind in ("hit", "miss") and arm:
        b = arm_browsing(engine, arm_name, arm)
        if arm_name == "knowledge" and b != "none":
            browsing_note = (" <span class='flag'>browsing unknown: search was not confirmed off, "
                             "so this does not count as recall from memory</span>" if b == "unknown"
                             else " <span class='flag'>searched despite search being off</span>")
    if kind == "hit":
        ahead_txt = ", ".join(str(x) for x in ahead)
        head = (
            f"<div class='quote'><b>{esc(engine)} {esc(arm_name)}</b> "
            f"<i>{esc(stance)}/{esc(position)}</i> {esc(quote or '(no quote)')}"
            + (f" <span class='ahead'>ahead: {esc(ahead_txt)}</span>" if ahead_txt else "")
            + "</div>"
        )
    elif kind == "miss":
        comps_txt = format_named_list(competitors[:10])
        named = f" Named instead: {comps_txt}." if comps_txt else ""
        searched = ""
        if arm_name == "search":
            if arm.get("searched"):
                vq = query_vendors if query_vendors is not None else (arm.get("vendors_in_search_queries") or [])
                vqs = format_named_list(vq[:8])
                searched = " Searched." + (f" Vendors in box: {vqs}." if vqs else "")
            else:
                searched = " Did not search."
        head = (
            f"<div class='quote missline'><b>{esc(engine)} {esc(arm_name)}</b> "
            f"<i>never mentioned {esc(brand)}</i>.{named}{searched}</div>"
        )
    else:
        head = (
            f"<div class='quote missline'><b>{esc(engine)} {esc(arm_name)}</b> "
            f"<i>not run</i></div>"
        )

    if browsing_note:
        head = head[: -len("</div>")] + browsing_note + "</div>"
    bits = [f"<div class='harness'>{head}"]
    if search_qs:
        qlines = "\n".join(str(q) for q in search_qs)
        bits.append(
            "<details class='raw-fold'>"
            "<summary>Search queries</summary>"
            f"<pre class='raw-body'>{esc(qlines)}</pre>"
            "</details>"
        )
    if raw:
        n = len(raw)
        bits.append(
            "<details class='raw-fold'>"
            f"<summary>Raw answer <span class='raw-meta'>{n:,} chars</span></summary>"
            f"<pre class='raw-body'>{esc(raw)}</pre>"
            "</details>"
        )
    elif kind != "none":
        bits.append("<p class='hint raw-missing'>No raw_response_text stored.</p>")
    bits.append("</div>")
    return "".join(bits)

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return raw if isinstance(raw, dict) else {}


def load(run: Path) -> tuple[dict[str, dict], dict, dict, dict]:
    docs = {}
    if run.is_file():
        doc = json.loads(run.read_text())
        found: set[str] = set()
        for pr in doc.get("prompts") or []:
            found.update((pr.get("engines") or {}).keys())
        for e in ENGINES:
            if e in found or run.stem.lower() == e:
                docs[e] = doc
        out_dir = run.parent
    else:
        out_dir = run
        for e in ENGINES:
            p = run / f"{e}.json"
            if p.exists():
                docs[e] = json.loads(p.read_text())
    judge = _read_json(out_dir / "judge.json")
    board = _read_json(out_dir / "board.json")
    vendors = _read_json(out_dir / "vendors_judged.json")
    if run.is_file():
        sibling = run.with_name(run.stem + ".vendors_judged.json")
        if sibling.exists():
            vendors = _read_json(sibling)
    return docs, judge, board, vendors


def merge_rows(docs: dict[str, dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for e, doc in docs.items():
        for pr in doc.get("prompts") or []:
            pid = str(pr.get("prompt_id") or "")
            if pid not in by_id:
                by_id[pid] = {
                    "prompt_id": pid,
                    "prompt_text": pr.get("prompt_text") or pid,
                    "class": pr.get("class") or "",
                    "why": pr.get("why") or "",
                    "engines": {},
                }
                order.append(pid)
            by_id[pid]["engines"][e] = pr.get("engines", {}).get(e) or {}
    return [by_id[i] for i in order]


def cell_view(arm: dict | None, j: dict | None, searched_arm: bool, named: list | None = None) -> dict:
    if not isinstance(arm, dict) or arm.get("error"):
        return {"kind": "none"}
    mentioned = bool(arm.get("brand_mentioned"))
    stance = (j or {}).get("stance") if mentioned else None
    position = (j or {}).get("position") if mentioned else None
    return {
        "kind": "hit" if mentioned else "miss",
        "stance": stance or ("mention" if mentioned else ""),
        "position": position or "",
        "quote": (j or {}).get("quote") or "",
        "ahead": (j or {}).get("ahead") or [],
        "searched": bool(arm.get("searched")) if searched_arm else None,
        "competitors": named if named is not None else (arm.get("competitor_mentions") or []),
        "has_surprise": any(
            isinstance(x, dict) and x.get("origin") == "surprise" for x in (named or [])
        ),
    }


def _n_of(a: int, b: int) -> str:
    return f"{a} of {b}"


def summary_section(summary: dict, brand: str, domain: str) -> str:
    t = summary["total"]
    site = domain or "the brand's site"
    cards = [
        ("Answers", str(t["answers"]),
         f"{t['errors'] + t['missing']} failed and not counted" if (t["errors"] or t["missing"]) else "every planned answer came back"),
        ("Named", _n_of(t["named"], t["answers"]),
         f"{t['named_unaided']} without search · {t['named_with_search']} with search · "
         f"{t['named_browsing_unknown']} browsing unknown"),
        ("Linked to " + site, _n_of(t["cited"], t["answers"]), f"answers whose sources include {site}"),
        ("Described accurately", _n_of(t["accurate"], t["named"]),
         f"of answers naming {brand} (judge)" + (f"; {t['accuracy_not_judged']} not judged" if t["accuracy_not_judged"] else "")),
        ("Recommended", _n_of(t["recommended"], t["named"]), f"of answers naming {brand}, pushed as something to use (judge)"),
    ]
    out = ["<section class='hero'>"]
    for lab, val, hint in cards:
        out.append(f"<article class='metric'><p class='eyebrow'>{esc(lab)}</p>"
                   f"<p class='metric-n'>{esc(val)}</p><p class='hint'>{esc(hint)}</p></article>")
    out.append("</section>")
    ex = summary["groups"].get("exploratory")
    if ex and ex["planned"]:
        out.append(
            f"<p class='hint'><b>Exploratory questions</b> (new or reworded, not in the frozen measurement set, "
            f"not in the numbers above): {brand} named in {ex['named']} of {ex['answers']} answers, "
            f"linked in {ex['cited']}, recommended in {ex['recommended']}.</p>"
        )
    return "".join(out)


def engine_section(summary: dict) -> str:
    out = ["<h2>Engines</h2><section class='engine-grid'>"]
    for e, r in summary["by_engine"].items():
        out.append(f"<article class='engine'><header><h3>{esc(e)}</h3></header>")
        if r["status"] == "not run":
            out.append("<p class='notrun'>not run</p><p class='hint'>No answers from this engine in this run.</p></article>")
            continue
        if r["status"] == "failed":
            out.append(f"<p class='notrun'>failed</p><p class='hint'>{r['errors']} answers errored.</p></article>")
            continue
        rows = [
            ("Named without search", r["named_unaided"], r["unaided_answers"],
             "answers where browsing was confirmed off"),
            ("Named, browsing unknown", r["named_browsing_unknown"], r["browsing_unknown_answers"],
             "no-search answers whose browsing could not be confirmed off"),
            ("Named with search", r["named_with_search"], r["search_answers"], "search-allowed answers"),
            ("Linked to the site", r["cited"], r["answers"], "all answers"),
            ("Recommended", r["recommended"], r["answers"], "all answers (judge)"),
            ("Searched", r["searched"], r["search_answers"], "search-allowed answers that actually searched"),
        ]
        for lab, a, b, tip in rows:
            if lab == "Named, browsing unknown" and not b:
                continue
            width = (a / b * 100) if b else 0
            out.append(f"<div class='stat-line' title='{esc(tip)}'><span>{esc(lab)}</span><span>{a} of {b}</span></div>")
            out.append(f"<div class='bar'><span class='bar-fill teal' style='width:{width:.1f}%'></span></div>")
        if r["errors"] or r["missing"]:
            out.append(f"<p class='hint'>{r['errors'] + r['missing']} answers failed and are not counted.</p>")
        out.append("</article>")
    out.append("</section>")
    return "".join(out)


def retrieval_section(summary: dict, records: list[dict], brand: str, domain: str) -> str:
    f = summary["funnel"]
    site = domain or "the brand's site"
    out = [f"<h2>Where {esc(site)} drops out</h2>"]
    if not f["answers"]:
        return out[0] + "<p class='hint'>No search-allowed answers in this run.</p>"
    out.append(
        "<p class='hint'>For each answer written with search allowed: the searches it ran, whether "
        f"{esc(site)} was among the results returned, whether a page on it was opened, whether the answer "
        f"linked to it, and whether it named {esc(brand)}.</p>"
    )
    out.append("<table class='funnel'><tr><th>Engine</th><th>Answers</th><th>Searched</th>"
               "<th>Site in returned results</th><th>Site page opened</th><th>Site linked</th>"
               f"<th>{esc(brand)} named</th></tr>")
    for e, r in f["by_engine"].items():
        if r["results_recorded"]:
            res = f"{r['in_results']} of {r['results_recorded']}"
        else:
            res = "not recorded"
        opened = str(r["opened"]) if r["chain_recorded"] else "not recorded"
        out.append(f"<tr><td>{esc(e)}</td><td>{r['answers']}</td><td>{r['searched']}</td><td>{res}</td>"
                   f"<td>{opened}</td><td>{r['cited']}</td><td>{r['named']}</td></tr>")
    out.append("</table>")
    if f["stages"]:
        stages = ", ".join(f"{esc(k)}: {v}" for k, v in sorted(f["stages"].items(), key=lambda kv: -kv[1]))
        out.append(f"<p class='hint'>Furthest stage {esc(site)} reached per answer: {stages}.</p>")
    notes = []
    if any(r["chain_recorded"] < r["answers"] for r in f["by_engine"].values()):
        notes.append("Answers stored before Oct 10, 2026 kept only the search queries and the final answer, "
                     "so returned results and opened pages are not recorded for them.")
    if "codex" in f["by_engine"]:
        notes.append("Codex does not expose the result list a search returned, only its queries and opened pages.")
    for n in notes:
        out.append(f"<p class='hint'>{esc(n)}</p>")
    hits = [r for r in records if r.get("funnel") and (r["funnel"].get("in_results") or r["funnel"].get("opened")
                                                       or r["funnel"].get("cited") or r["funnel"].get("named"))]
    if hits:
        out.append("<ul class='funnel-hits'>")
        for r in hits[:20]:
            fu = r["funnel"]
            out.append(f"<li><b>{esc(r['engine'])}</b> · {esc(r['prompt_text'][:140])} — {esc(fu.get('stage') or '')}</li>")
        out.append("</ul>")
    return "".join(out)


def settings_section(docs: dict) -> str:
    out = ["<h2>Run settings</h2>"]
    rows = []
    mset = None
    for e in ENGINES:
        doc = docs.get(e)
        if not doc:
            continue
        run = doc.get("run") or {}
        mset = mset or run.get("measurement_set")
        env = (run.get("environment") or {}).get(e)
        if not env:
            rows.append(f"<tr><td>{esc(e)}</td><td colspan='3'>not recorded (run before Oct 10, 2026: no identity "
                        "isolation, CLI versions or canary)</td></tr>")
            continue
        iso = env.get("isolation") or {}
        can = env.get("canary") or {}
        can_txt = can.get("status") or "not run"
        if can.get("leaked_terms"):
            can_txt += " (named " + ", ".join(can["leaked_terms"]) + ")"
        rows.append(f"<tr><td>{esc(e)}</td><td>{esc(env.get('cli_version') or 'unknown')}</td>"
                    f"<td>{esc(iso.get('home') or 'not isolated')}; auth: {esc(iso.get('auth') or '?')}</td>"
                    f"<td>{esc(can_txt)}</td></tr>")
    if rows:
        out.append("<table class='funnel'><tr><th>Engine</th><th>CLI version</th><th>Isolation</th>"
                   "<th>Identity canary</th></tr>" + "".join(rows) + "</table>")
    if mset:
        out.append(f"<p class='hint'>Measurement set {esc(str(mset.get('id')))} v{esc(str(mset.get('version')))}, "
                   f"{mset.get('measurement_count')} frozen questions, hash {esc(str(mset.get('question_hash'))[:19])}…"
                   + ("" if mset.get("matches") else " <b>(changed since it was frozen)</b>") + "</p>")
    else:
        out.append("<p class='hint'>No frozen measurement set recorded for this run.</p>")
    return "".join(out)


def render(run: Path) -> str:
    docs, judge, board, vendors_raw = load(run)
    rows = merge_rows(docs)
    records = answer_records(docs, judge)
    summary = summarize(records)
    ws_brand, aliases, competitors, competitor_aliases = workspace_from_docs(docs)
    brand = os.environ.get("AEO_BRAND") or ws_brand or "Tyk"
    if ws_brand:
        brand = ws_brand
    domain = ""
    for doc in docs.values():
        domain = str((doc.get("workspace") or {}).get("domain") or "")
        if domain:
            break
    vendor_store = load_vendor_store(vendors_raw)
    alias_map = seed_alias_map(
        brand, aliases, competitors, vendor_store.values(), competitor_aliases=competitor_aliases
    )
    known_counts, surprise_counts = named_vendor_counts_by_origin(
        rows, vendor_store, brand=brand, aliases=aliases, alias_map=alias_map
    )
    surprise_mentions = sum(surprise_counts.values())
    parts = []
    parts.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append(f"<title>{esc(brand)} · AEO report</title><style>")
    parts.append(CSS)
    parts.append("</style></head><body>")
    parts.append(f"<header class='top'><div class='top-brand'><span class='wordmark'>{esc(brand)}</span>")
    parts.append(f"<span class='domain'>{esc(domain)}</span></div>")
    parts.append(f"<div class='top-meta'><span class='pill'>{esc(run.name)}</span>")
    parts.append(f"<span class='pill'>{summary['total']['answers']} answers</span></div></header><main>")

    parts.append("<section class='method'>")
    parts.append("<p class='eyebrow'>Methodology</p>")
    parts.append("<h1>What this board measures</h1>")
    parts.append(
        "<p>For each pain query we run <b>two arms</b> on Claude, Codex, and Grok. "
        "Seeds stay verbatim (no brand bait). Hits are regex brand mentions; stance/position "
        "come from a post-hoc judge on the raw answer, not from the assistant's own recommendation flag.</p>"
    )
    parts.append("<div class='method-grid'>")
    parts.append(
        f"<article><h3>{ARM_LABELS['knowledge']}</h3>"
        f"<p>Knowledge-only arm: web search switched <b>off</b>. Did the model name "
        f"<b>{esc(brand)}</b> from memory? Only answers whose event log confirms no search count as "
        "recall from memory; the rest are shown as <b>browsing unknown</b>.</p>"
        "<p class='ex'><b>Example.</b> Query: “rate limit partner APIs by API key.” "
        "Answer lists Kong and Apigee but never Tyk → <span class='leg-chip miss'>miss</span>. "
        "Answer says “Tyk or Kong…” → <span class='leg-chip men'>mention</span>.</p></article>"
    )
    parts.append(
        f"<article><h3>{ARM_LABELS['search']}</h3>"
        f"<p>Search-allowed arm: the model <b>may</b> use web search. Did the final answer "
        f"name <b>{esc(brand)}</b> (whether or not it searched)?</p>"
        "<p class='ex'><b>Example.</b> Same query; model searches “API gateway rate limiting”, "
        "then recommends Tyk first → <span class='leg-chip rec'>recommend</span> + first pick. "
        "Searches but only cites Cloudflare → <span class='leg-chip miss'>miss</span>.</p></article>"
    )
    parts.append(
        "<article><h3>Recommend / first / warn</h3>"
        "<p>Judge labels on hits only: <b>recommend</b> (pushed as a fit), <b>mention</b> "
        "(named, not pushed), <b>warn/reject</b>, and list <b>position</b> (first / among / last / aside).</p>"
        "<p class='ex'><b>Example.</b> “Use Tyk if you need…, otherwise Kong” with Tyk leading → "
        "recommend + first. “Also consider Tyk” after three others → mention + last.</p></article>"
    )
    parts.append(
        "<article><h3>Searched + vendors in box</h3>"
        "<p><b>Searched</b> = the search arm actually fired a search tool. "
        "<b>Vendors typed into search</b> counts names inside those tool queries "
        f"(LLM extract ∪ regex over brand/aliases/config competitors), including {esc(brand)} "
        "when an alias appears. The board ⚠ marks and the stored search-box vendor list stay regex-only.</p>"
        "<p class='ex'><b>Example.</b> Tool query “Kong vs Apigee vs Tyk rate limiting” "
        "counts all three in the search-vendor bars, even if the answer later drops Tyk. "
        "A query that only says “UserCheck email verification” still counts UserCheck "
        "as a <b>surprise</b> after the vendor pass when UserCheck was not on the seed list.</p></article>"
    )
    parts.append("</div></section>")

    actions = (board or {}).get("actions") or []
    board_headline = (board or {}).get("headline") or ""
    if count_problems([board_headline], summary):
        board_headline = ""  # the judge stated counts that disagree with the records; show ours only
    parts.append("<section class='actions'>")
    parts.append("<p class='eyebrow'>Summary</p>")
    parts.append(f"<h1>{esc(count_headline(summary, brand))}</h1>")
    if board_headline:
        parts.append(f"<p class='board-headline'>{esc(board_headline)}</p>")
    parts.append("<p class='eyebrow'>Board judge actions</p>")
    if actions:
        parts.append("<ol class='action-list'>")
        for i, a in enumerate(actions, 1):
            parts.append("<li class='action'>")
            parts.append(f"<span class='n'>{i}</span>")
            parts.append("<div>")
            parts.append(f"<p class='atitle'>{esc(a.get('title') or '')}</p>")
            parts.append(f"<p class='awhy'>{esc(a.get('why') or '')}</p>")
            parts.append(f"<p class='ado'><b>Do:</b> {esc(a.get('do') or '')}</p>")
            if a.get("evidence"):
                parts.append(f"<p class='aev'>{esc(a.get('evidence'))}</p>")
            parts.append("</div></li>")
        parts.append("</ol>")
    else:
        parts.append("<p class='hint'>Board judge has not run yet.</p>")
    parts.append("</section>")

    parts.append(summary_section(summary, brand, domain))
    parts.append(engine_section(summary))
    parts.append(retrieval_section(summary, records, brand, domain))
    parts.append(settings_section(docs))

    search_vendor_counts = search_box_vendor_counts(
        rows, vendor_store, brand=brand, aliases=aliases, alias_map=alias_map
    )

    def vendor_rows(counts, *, brand_name: str, surprise: bool = False, surprise_set: set | None = None) -> str:
        if not counts:
            return "<p class='hint'>Nothing recorded.</p>"
        top = counts.most_common(20)
        if brand_name in counts and brand_name not in {n for n, _ in top}:
            top = [(brand_name, counts[brand_name])] + top[:19]
        top = sorted(top, key=lambda x: (0 if x[0] == brand_name else 1, -x[1], x[0].lower()))
        mx = max(n for _, n in top) or 1
        bits = []
        for name, n in top:
            width = (n / mx) * 100
            is_surp = surprise or (surprise_set is not None and name in surprise_set and name != brand_name)
            cls = "vname brand" if name == brand_name else ("vname surprise" if is_surp else "vname")
            fill = "brand" if name == brand_name else ("surprise" if is_surp else "teal")
            label = esc(name)
            if is_surp:
                label += " <span class='badge-surprise'>surprise</span>"
            bits.append(
                f"<div class='vrow'><span class='{cls}'>{label}</span>"
                f"<div class='bar'><span class='bar-fill {fill}' style='width:{width:.1f}%'></span></div>"
                f"<span class='vcount'>{n}</span></div>"
            )
        return "".join(bits)

    parts.append("<h2>Who got named</h2>")
    parts.append(
        f"<p class='hint'>{esc(brand)} (strict brand match: {esc(brand_terms_words(brand, aliases))}) plus "
        "<b>known</b> competitors: config seed list, union regex + LLM extract after normalize. "
        "All engines, both arms. Surprises are not in this pile.</p>"
    )
    parts.append("<div class='vendor-bars'>")
    parts.append(vendor_rows(known_counts, brand_name=brand))
    parts.append("</div>")

    parts.append("<h2>Surprise competitors</h2>")
    parts.append(
        f"<p class='hint'>Vendors named in answers whose normalized form is <b>not</b> on the "
        f"config seed list. {surprise_mentions} mention{'' if surprise_mentions == 1 else 's'} "
        f"across {len(surprise_counts)} name{'' if len(surprise_counts) == 1 else 's'}. "
        "Discovery worth reviewing — consider adding repeats to the next run's seed list.</p>"
    )
    parts.append("<div class='vendor-bars'>")
    if surprise_counts:
        parts.append(vendor_rows(surprise_counts, brand_name=brand, surprise=True))
    else:
        parts.append("<p class='hint'>No surprises. Every named vendor was on the seed list (or the brand).</p>")
    parts.append("</div>")

    parts.append("<h2>Vendors typed into search</h2>")
    parts.append(
        f"<p class='hint'>Names inside search tool queries (search arm only): LLM extract of the "
        f"query strings, union regex over brand/aliases/config competitors, including {esc(brand)} "
        "when an alias appeared in the query box. A surprise badge here means the name is "
        "<b>not on the config seed list</b> — this chart does not reuse answer-side Surprise "
        "competitors. The stored search-box vendor list and the board ⚠ marks stay regex-only.</p>"
    )
    parts.append("<div class='vendor-bars'>")
    if search_vendor_counts:
        search_surprise = search_chart_surprise_set(search_vendor_counts, brand, alias_map)
        parts.append(vendor_rows(search_vendor_counts, brand_name=brand, surprise_set=search_surprise))
    else:
        parts.append("<p class='hint'>Nobody typed vendor names into search (or no engine searched).</p>")
    parts.append("</div>")

    parts.append("<h2>Queries</h2>")

    parts.append("<div class='chips' id='chips'>")
    for key, lab in (("recommend","recommend"), ("first","first pick"), ("last","last/aside"), ("warn","warn"), ("reject","reject"), ("miss","miss"), ("surprise","surprise")):
        parts.append(f"<button type='button' class='chip' data-f='{key}'>{lab}</button>")
    parts.append("</div>")
    parts.append("<div class='table-legend'><span class='leg-item'><b>K</b> knowledge</span> · <span class='leg-item'><b>S</b> search</span>")
    parts.append(" · <span class='leg-chip rec'>recommend</span> <span class='leg-chip men'>mention</span> <span class='leg-chip wrn'>warn</span> <span class='leg-chip rej'>reject</span> <span class='leg-chip miss'>miss</span></div>")
    parts.append("<div class='table-wrap'><table class='prompt-table'><thead><tr><th>Query</th>")
    for e in ENGINES:
        parts.append(f"<th>{e}</th>")
    parts.append("</tr></thead><tbody>")
    for i, row in enumerate(rows):
        tags = set()
        parts.append("<tr class='prompt-row' data-i='%d'>" % i)
        why = row.get("why") or row.get("class") or ""
        parts.append(f"<td class='qcell'><button type='button' class='expand'>▸</button><span class='prompt-q'>{esc(row['prompt_text'])}</span> <span class='why'>{esc(plain_class(why))}</span></td>")
        drawer = []
        for e in ENGINES:
            arms = row["engines"].get(e) or {}
            marks = []
            for arm_name, letter in (("knowledge", "K"), ("search", "S")):
                arm = arms.get(arm_name)
                j = judge.get(f"{row['prompt_id']}|{e}|{arm_name}")
                vkey = f"{row['prompt_id']}|{e}|{arm_name}"
                named = classified_vendors_for_arm(
                    arm if isinstance(arm, dict) else None,
                    vendor_store.get(vkey),
                    alias_map,
                    brand,
                    aliases,
                )
                qnamed = classified_query_vendors_for_arm(
                    arm if isinstance(arm, dict) else None,
                    vendor_store.get(vkey),
                    alias_map,
                    brand,
                    aliases,
                )
                v = cell_view(arm, j, arm_name == "search", named)
                st = v.get("stance") or ""
                kind = v.get("kind")
                cls = "miss" if kind == "miss" else {"recommend":"rec","mention":"men","warn":"wrn","reject":"rej"}.get(st, "men")
                if kind == "none":
                    cls = "none"
                if kind == "hit":
                    tags.add(st)
                    if v.get("position") == "first":
                        tags.add("first")
                    if v.get("position") in ("last", "aside"):
                        tags.add("last")
                else:
                    tags.add("miss")
                if v.get("has_surprise") or any(
                    isinstance(x, dict) and x.get("origin") == "surprise" for x in qnamed
                ):
                    tags.add("surprise")
                tip = f"{letter} {st or kind} {v.get('position') or ''}".strip()
                marks.append(f"<span class='mk {cls}' title='{esc(tip)}'>{letter}</span>")
                drawer.append(
                    harness_block(
                        engine=e,
                        arm_name=arm_name,
                        kind=kind,
                        brand=brand,
                        stance=st,
                        position=v.get("position") or "",
                        quote=v.get("quote") or "",
                        ahead=v.get("ahead") or [],
                        competitors=v.get("competitors") or [],
                        query_vendors=qnamed,
                        arm=arm if isinstance(arm, dict) else None,
                    )
                )
            parts.append("<td class='eng'><span class='marks'>" + "".join(marks) + "</span></td>")
        parts.append("</tr>")
        parts.append(
            f"<tr class='drawer' data-i='{i}' hidden><td colspan='4' data-tags='{' '.join(sorted(tags))}'>"
            f"{''.join(drawer)}</td></tr>"
        )
        # put tags on the prompt row via rewrite is hard; attach on previous via js from drawer
    parts.append("</tbody></table></div>")
    parts.append("<script>")
    parts.append(JS)
    parts.append("</script></main></body></html>")
    return "\n".join(parts)


CSS = """
:root{--bg:#0b0d10;--card:#14181e;--line:rgba(255,255,255,.08);--text:#e8edf2;--muted:#8b95a3;
--teal:#3dccc7;--rec:#6ee7b7;--men:#e8b86d;--wrn:#f0a36b;--rej:#e07a7a;--miss:#5b6570}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);
font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;line-height:1.45}
.top{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;align-items:center;
padding:14px 28px;background:rgba(11,13,16,.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.wordmark{letter-spacing:.14em;text-transform:uppercase;font-weight:650;font-size:15px}
.domain{margin-left:10px;color:var(--muted)}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--muted);margin-left:8px}
main{max-width:1200px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:22px;font-weight:620;letter-spacing:-.03em;margin:6px 0 18px}
h2{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:36px 0 12px}
.eyebrow{margin:0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.hint{color:var(--muted);font-size:13px}
.actions{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:22px}
.action-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:14px}
.action{display:grid;grid-template-columns:36px 1fr;gap:12px;align-items:start}
.action .n{width:28px;height:28px;border-radius:999px;background:rgba(61,204,199,.15);color:var(--teal);
display:flex;align-items:center;justify-content:center;font-weight:650;font-size:13px}
.atitle{margin:0;font-weight:620}.awhy,.ado,.aev{margin:4px 0 0;color:var(--muted);font-size:13px}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.metric{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;min-height:120px;display:flex;flex-direction:column}
.metric-n{margin:10px 0 6px;font-size:28px;font-weight:620;letter-spacing:-.03em}
.engine-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
.engine{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px}
.engine h3{margin:0 0 10px;text-transform:capitalize}
.stat-line{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin:8px 0 4px}
.bar{height:8px;background:#0e1116;border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.bar-fill{display:block;height:100%;background:linear-gradient(90deg,#1d9b96,var(--teal))}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.chip{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:999px;padding:4px 11px;font-size:12px;cursor:pointer}
.chip.on{border-color:var(--teal);color:var(--teal)}
.table-legend{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:11px;color:var(--muted);margin:8px 0}
.leg-chip,.mk{display:inline-flex;align-items:center;justify-content:center;border-radius:6px;padding:2px 7px;border:1px solid var(--line);font-size:11px;font-weight:650}
.mk{width:22px;height:22px;margin-right:4px}
.mk.rec{background:rgba(110,231,183,.18);color:var(--rec);border-color:transparent}
.mk.men{background:rgba(232,184,109,.16);color:var(--men);border-color:transparent}
.mk.wrn{background:rgba(240,163,107,.16);color:var(--wrn);border-color:transparent}
.mk.rej{background:rgba(224,122,122,.18);color:var(--rej);border-color:transparent}
.mk.miss{color:var(--miss)}
.mk.none{opacity:.35}
.prompt-table{width:100%;border-collapse:collapse}
.prompt-table th{text-align:left;font-size:11px;color:var(--muted);letter-spacing:.12em;text-transform:uppercase;padding:8px}
.prompt-table td{border-top:1px solid var(--line);padding:10px 8px;vertical-align:top}
.prompt-q{font-size:14px}.why{color:var(--muted);font-size:11px;margin-left:6px}
.expand{background:none;border:0;color:var(--muted);cursor:pointer}
.quote{margin:6px 0;font-size:13px;color:var(--muted)}
.quote b{color:var(--text);margin-right:6px}
.ahead{color:var(--men)}

.vendor-bars{display:flex;flex-direction:column;gap:8px;margin:12px 0 24px}
.vrow{display:grid;grid-template-columns:180px 1fr 48px;gap:10px;align-items:center}
.vname{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vcount{text-align:right;color:var(--muted);font-size:12px}
.missline{opacity:.9}
.missline i{color:var(--miss)}
.method{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:22px}
.method>p{color:var(--muted);font-size:14px;max-width:72ch}
.method-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:14px}
.method-grid article{background:#0e1116;border:1px solid var(--line);border-radius:12px;padding:14px}
.method-grid h3{margin:0 0 8px;font-size:14px}
.method-grid p{margin:0 0 8px;font-size:13px;color:var(--muted)}
.method-grid .ex{margin:0;font-size:12px;color:#a8b2bf}
.method-grid code{font-size:12px;color:var(--teal)}
.bar-fill.brand{background:linear-gradient(90deg,#b8860b,var(--men))}
.vname.brand{color:var(--men);font-weight:650}
.bar-fill.surprise{background:linear-gradient(90deg,#c45c26,var(--wrn))}
.vname.surprise{color:var(--wrn);font-weight:650}
.badge-surprise{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;
font-size:10px;letter-spacing:.04em;text-transform:uppercase;font-weight:650;
background:rgba(240,163,107,.18);color:var(--wrn);vertical-align:middle}

.harness{margin:10px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.harness:last-child{border-bottom:0}
.raw-fold{margin:6px 0 0 0}
.raw-fold>summary{cursor:pointer;color:var(--teal);font-size:12px;user-select:none;list-style:none}
.raw-fold>summary::-webkit-details-marker{display:none}
.raw-fold>summary::before{content:'▸ ';color:var(--muted)}
.raw-fold[open]>summary::before{content:'▾ '}
.raw-meta{color:var(--muted);font-weight:400;margin-left:6px}
.raw-body{margin:8px 0 0;padding:12px;background:#0e1116;border:1px solid var(--line);border-radius:10px;
max-height:420px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.5;color:#c9d2dc}
.raw-missing{margin:4px 0 0}

.qcell{max-width:520px}
.notrun{font-size:20px;color:var(--muted);margin:6px 0}
.flag{color:var(--wrn);font-style:normal}
.board-headline{color:var(--muted);margin:-8px 0 14px}
.funnel{border-collapse:collapse;font-size:13px;margin:8px 0}
.funnel td,.funnel th{border-top:1px solid var(--line);padding:6px 10px;text-align:left}
.funnel-hits{font-size:13px;color:var(--muted)}
"""

JS = """
document.querySelectorAll('.expand').forEach((b)=>{
  b.addEventListener('click',()=>{
    const tr=b.closest('tr');
    const i=tr.getAttribute('data-i');
    const d=document.querySelector('tr.drawer[data-i="'+i+'"]');
    if(d) d.hidden=!d.hidden;
  });
});
document.querySelectorAll('.chip').forEach((c)=>{
  c.addEventListener('click',()=>{
    c.classList.toggle('on');
    const on=[...document.querySelectorAll('.chip.on')].map(x=>x.dataset.f);
    document.querySelectorAll('tr.prompt-row').forEach((tr)=>{
      const i=tr.getAttribute('data-i');
      const d=document.querySelector('tr.drawer[data-i="'+i+'"]');
      const tags=(d && d.querySelector('td') && d.querySelector('td').dataset.tags)||'';
      const ok=!on.length || on.some(f=>tags.includes(f));
      tr.style.display=ok?'':'none';
      if(d && !ok) d.hidden=true;
    });
  });
});
"""


def main(argv: list[str] | None = None):
    args = list(sys.argv[1:] if argv is None else argv)
    run = None
    for a in args:
        if a in ("-h", "--help"):
            print(
                "Usage: render_judge_html.py [run_dir_or_evidence.json]\n"
                "Env: AEO_BRAND, AEO_RUN or AEO_TYK_RUN"
            )
            return
        if not a.startswith("-"):
            run = Path(a)
            break
    if run is None:
        run = Path(
            os.environ.get("AEO_RUN")
            or os.environ.get("AEO_TYK_RUN")
            or str(Path.home() / ".aeo/runs/tyk100-20260901")
        )
    out_dir = run if run.is_dir() else run.parent
    html_out = out_dir / f"{(run if run.is_dir() else run).name}-report.html"
    # Keep the historical Tyk default filename when using that run dir.
    if run.is_dir() and run.name == "tyk100-20260901":
        html_out = run / "tyk100-20260901-report.html"
    from aeo.layers import with_layers  # Google / Search Console sections, if <run>/google/ has them

    html_out.write_text(with_layers(render(run), run if run.is_dir() else run.parent))
    print("wrote", html_out)


if __name__ == "__main__":
    main()
