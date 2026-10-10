#!/usr/bin/env python3.11
"""AEO report: verdict first, then actions, changes, assistants, competitors, search trail,
Google / Search Console, per-question answers, and the method (collapsed) at the end."""
from __future__ import annotations

import html, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aeo.counts import answer_records, count_problems, headline as count_headline, summarize  # noqa: E402
from aeo.report_ux import (  # noqa: E402
    ARM_SHORT,
    ARM_TITLE,
    LABELS,
    engine_title,
    filter_surprises,
    group_by_category,
    n_of,
    user_time,
)
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
ARM_LABELS = {"knowledge": "Named from memory", "search": "Named with web search"}
STANCE_WORDS = {"recommend": "recommended", "mention": "named", "warn": "warned against",
                "reject": "rejected", "": "named"}

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
            bits.append(f"<span class='untracked'>{esc(name.replace('**', '').strip())}</span>")
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
            browsing_note = (" <span class='flag'>search not confirmed off, "
                             "so this does not count as from memory</span>" if b == "unknown"
                             else " <span class='flag'>searched despite search being off</span>")
    if kind == "hit":
        ahead_txt = ", ".join(str(x) for x in ahead)
        head = (
            f"<div class='quote'><b>{esc(engine_title(engine))} · {esc(LABELS.get(arm_name, arm_name))}</b> "
            f"<i>{esc(STANCE_WORDS.get(stance, stance) or 'named')}{(', ' + esc(position)) if position else ''}</i> {esc(quote or '')}"
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
                searched = " Searched the web." + (f" Typed into search: {vqs}." if vqs else "")
            else:
                searched = " Did not search the web."
        head = (
            f"<div class='quote missline'><b>{esc(engine_title(engine))} · {esc(LABELS.get(arm_name, arm_name))}</b> "
            f"<i>did not name {esc(brand)}</i>.{named}{searched}</div>"
        )
    else:
        head = (
            f"<div class='quote missline'><b>{esc(engine_title(engine))} · {esc(LABELS.get(arm_name, arm_name))}</b> "
            f"<i>no answer</i></div>"
        )

    if browsing_note:
        head = head[: -len("</div>")] + browsing_note + "</div>"
    bits = [f"<div class='harness'>{head}"]
    if search_qs:
        qlines = "\n".join(str(q) for q in search_qs)
        bits.append(
            "<details class='raw-fold'>"
            "<summary>What it searched for</summary>"
            f"<pre class='raw-body'>{esc(qlines)}</pre>"
            "</details>"
        )
    if raw:
        n = len(raw)
        bits.append(
            "<details class='raw-fold'>"
            f"<summary>Full answer <span class='raw-meta'>{n:,} characters</span></summary>"
            f"<pre class='raw-body'>{esc(raw)}</pre>"
            "</details>"
        )
    elif kind != "none":
        bits.append("<p class='hint raw-missing'>The answer text was not stored.</p>")
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
         f"{t['named_unaided']} from memory · {t['named_with_search']} with web search · "
         f"{t['named_browsing_unknown']} search not confirmed off"),
        ("Linked to " + site, _n_of(t["cited"], t["answers"]), f"answers citing {site} as a source"),
        (("Described accurately", "not checked", f"{t['accuracy_not_judged']} answers naming {brand} still to review")
         if t["named"] and t["accuracy_not_judged"] >= t["named"] else
         ("Described accurately", _n_of(t["accurate"], t["named"] - t["accuracy_not_judged"]),
          f"of the checked answers naming {brand}" + (f" · {t['accuracy_not_judged']} not checked" if t["accuracy_not_judged"] else ""))),
        ("Recommended", _n_of(t["recommended"], t["named"]), f"of answers naming {brand}, suggested as something to use"),
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
        out.append(f"<article class='engine'><header><h3>{esc(engine_title(e))}</h3></header>")
        if r["status"] == "not run":
            out.append("<p class='notrun'>not run</p><p class='hint'>No answers from this engine in this run.</p></article>")
            continue
        if r["status"] == "failed":
            out.append(f"<p class='notrun'>failed</p><p class='hint'>{r['errors']} answers errored.</p></article>")
            continue
        rows = [
            ("Named from memory", r["named_unaided"], r["unaided_answers"],
             "answers where search was confirmed off"),
            ("Named, search not confirmed off", r["named_browsing_unknown"], r["browsing_unknown_answers"],
             "'from memory' answers whose search could not be confirmed off"),
            ("Named with web search", r["named_with_search"], r["search_answers"], "answers with web search allowed"),
            ("Linked to the site", r["cited"], r["answers"], "all answers"),
            ("Recommended", r["recommended"], r["answers"], "all answers (review step)"),
            ("Searched the web", r["searched"], r["search_answers"], "answers with web search allowed that actually searched"),
        ]
        for lab, a, b, tip in rows:
            if lab == "Named, search not confirmed off" and not b:
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
        return out[0] + "<p class='hint'>No answers with web search in this run.</p>"
    out.append(
        "<p class='hint'>For each answer written with web search allowed: the searches it ran, whether "
        f"{esc(site)} was among the results returned, whether a page on it was opened, whether the answer "
        f"linked to it, and whether it named {esc(brand)}.</p>"
    )
    out.append("<div class='table-wrap'><table class='funnel'><tr><th>Assistant</th><th>Answers</th><th>Searched</th>"
               "<th>Site in returned results</th><th>Site page opened</th><th>Site linked</th>"
               f"<th>{esc(brand)} named</th></tr>")
    for e, r in f["by_engine"].items():
        if r["results_recorded"]:
            res = f"{r['in_results']} of {r['results_recorded']}"
        else:
            res = "not recorded"
        opened = str(r["opened"]) if r["chain_recorded"] else "not recorded"
        out.append(f"<tr><td>{esc(engine_title(e))}</td><td>{r['answers']}</td><td>{r['searched']}</td><td>{res}</td>"
                   f"<td>{opened}</td><td>{r['cited']}</td><td>{r['named']}</td></tr>")
    out.append("</table></div>")
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
            out.append(f"<li><b>{esc(engine_title(r['engine']))}</b> · {esc(r['prompt_text'][:140])} — {esc(fu.get('stage') or '')}</li>")
        out.append("</ul>")
    return "".join(out)


def settings_section(docs: dict) -> str:
    out = ["<h3>Run settings</h3>"]
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
            rows.append(f"<tr><td>{esc(engine_title(e))}</td><td colspan='3'>not recorded (run before Oct 10, 2026: no identity "
                        "isolation, versions or identity check)</td></tr>")
            continue
        iso = env.get("isolation") or {}
        can = env.get("canary") or {}
        can_txt = can.get("status") or "not run"
        if can.get("leaked_terms"):
            can_txt += " (named " + ", ".join(can["leaked_terms"]) + ")"
        rows.append(f"<tr><td>{esc(engine_title(e))}</td><td>{esc(env.get('cli_version') or 'unknown')}</td>"
                    f"<td>{esc(iso.get('home') or 'not isolated')}; auth: {esc(iso.get('auth') or '?')}</td>"
                    f"<td>{esc(can_txt)}</td></tr>")
    if rows:
        out.append("<div class='table-wrap'><table class='funnel'><tr><th>Assistant</th><th>Version</th><th>Isolation</th>"
                   "<th>Identity check</th></tr>" + "".join(rows) + "</table></div>")
    if mset:
        out.append(f"<p class='hint'>Question set {esc(str(mset.get('id')))} v{esc(str(mset.get('version')))}, "
                   f"{mset.get('measurement_count')} frozen questions, hash {esc(str(mset.get('question_hash'))[:19])}…"
                   + ("" if mset.get("matches") else " <b>(changed since it was frozen)</b>") + "</p>")
    else:
        out.append("<p class='hint'>No frozen question set recorded for this run.</p>")
    return "".join(out)


def _load_json_opt(path: Path) -> dict:
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _config() -> dict:
    p = os.environ.get("AEO_CONFIG")
    return _load_json_opt(Path(p).expanduser()) if p else {}


def run_started(docs: dict) -> str:
    stamps = sorted(str((d.get("run") or {}).get("timestamp") or "") for d in docs.values() if (d.get("run") or {}).get("timestamp"))
    return stamps[0] if stamps else ""


def verdict_section(summary: dict, brand: str, domain: str, *, change: dict, actions: list, layers: tuple,
                    caveats: list[str]) -> str:
    t = summary["total"]
    site = domain or "our site"
    tiles = [
        ("Named", n_of(t["named"], t["answers"]),
         f"{t['named_unaided']} from memory · {t['named_with_search']} with web search"
         + (f" · {t['named_browsing_unknown']} search not confirmed off" if t["named_browsing_unknown"] else "")),
        ("Recommended", n_of(t["recommended"], t["named"]), f"of the answers naming {brand}"),
        ("Linked to " + site, n_of(t["cited"], t["answers"]), "answers citing the site as a source"),
        (("Described accurately", "not checked", f"{t['accuracy_not_judged']} answers naming {brand} still to review")
         if t["named"] and t["accuracy_not_judged"] >= t["named"] else
         ("Described accurately", n_of(t["accurate"], t["named"] - t["accuracy_not_judged"]),
          f"of the checked answers naming {brand}" + (f" · {t['accuracy_not_judged']} not checked" if t["accuracy_not_judged"] else ""))),
    ]
    gdoc, sdoc = layers
    if gdoc:
        from aeo import google as gmod

        tsum = ((gdoc.get("summary") or gmod.summarize(gdoc)).get(gmod.TARGET_TIER) or {})
        tiles.append(("Google AI Overviews cite us", n_of(tsum.get("own_cited"), tsum.get("searches")), "target searches"))
    if sdoc:
        tot = (sdoc.get("totals") or {})
        cur, prev = tot.get("current") or {}, tot.get("previous") or {}
        tiles.append(("Search Console clicks", str(int(cur.get("clicks") or 0)), f"last 28 days · was {int(prev.get('clicks') or 0)}"))
    out = ["<section class='verdict' id='verdict'>"]
    out.append("<div class='tiles'>")
    for lab, val, sub in tiles:
        out.append(f"<article class='tile'><p class='eyebrow'>{esc(lab)}</p><p class='tile-n'>{esc(val)}</p>"
                   f"<p class='hint'>{esc(sub)}</p></article>")
    out.append("</div>")
    if change:
        comp = change.get("comparability") or {}
        when = user_time(((change.get("baseline") or {}).get("timestamps") or [""])[0], with_day=False)
        if comp and not comp.get("comparable", True):
            line = f"<b>Not comparable</b> with the last run ({esc(when)}): {esc('; '.join(comp.get('reasons') or []))}."
        else:
            br = (change.get("brand_rates") or {}).get("overall") or {}
            k, s = br.get("knowledge") or {}, br.get("search") or {}
            def ns(x):
                return f"{(x.get('baseline') or {}).get('hits', 0)}→{(x.get('current') or {}).get('hits', 0)} of {(x.get('current') or {}).get('n', 0)}"
            line = (f"<b>Vs last comparable run</b> ({esc(when)}): named from memory {esc(ns(k))}, "
                    f"with web search {esc(ns(s))}. <a href='#changes'>What changed</a>")
        out.append(f"<p class='vline'>{line}</p>")
    else:
        out.append("<p class='vline'><b>Vs last run:</b> no earlier comparable run.</p>")
    if actions:
        a = actions[0]
        out.append(f"<p class='vline'><b>This week's #1:</b> {esc(a.get('title') or '')}"
                   + (f" — {esc(a.get('do') or '')}" if a.get("do") else "") + " <a href='#actions'>All actions</a></p>")
    if caveats:
        out.append("<div class='caveats'><p class='eyebrow'>Caveats for this run</p><ul>"
                   + "".join(f"<li>{esc(c)}</li>" for c in caveats) + "</ul></div>")
    out.append("</section>")
    return "".join(out)


def run_caveats(docs: dict, summary: dict, brand: str) -> list[str]:
    out = []
    if not any((d.get("run") or {}).get("environment") for d in docs.values()):
        out.append("These answers were collected before identity isolation (Oct 10, 2026): the assistants ran "
                   "as a logged-in account, and Claude could see the account email, which contains the product's "
                   f"domain. Mentions of {brand} may be inflated.")
    else:
        for e, d in docs.items():
            can = (((d.get("run") or {}).get("environment") or {}).get(e) or {}).get("canary") or {}
            if can.get("status") not in (None, "pass"):
                out.append(f"{engine_title(e)} identity check: {can.get('status')}.")
    for e, r in summary["by_engine"].items():
        if r["status"] == "ran" and r["browsing_unknown_answers"]:
            out.append(f"{engine_title(e)}: {r['browsing_unknown_answers']} of {r['knowledge_answers']} 'from memory' "
                       "answers could not be confirmed as search-free, so they are not counted as recall from memory.")
    if summary["engines_not_run"]:
        out.append("Not run: " + ", ".join(engine_title(e) for e in summary["engines_not_run"]) + ".")
    return out


def actions_section(actions: list, board_headline: str) -> str:
    out = ["<section class='actions' id='actions'><h2>Actions</h2>"]
    if board_headline:
        out.append(f"<p class='board-headline'>{esc(board_headline)}</p>")
    if not actions:
        out.append("<p class='hint'>No actions yet (the review step has not run).</p></section>")
        return "".join(out)
    out.append("<ol class='action-list'>")
    for i, a in enumerate(actions, 1):
        out.append(f"<li class='action'><span class='n'>{i}</span><div>")
        out.append(f"<p class='atitle'>{esc(a.get('title') or '')}" + (" <span class='tag'>this week</span>" if i == 1 else "") + "</p>")
        if a.get("do"):
            out.append(f"<p class='ado'><b>Do:</b> {esc(a.get('do'))}</p>")
        why = " ".join(x for x in (a.get("why"), a.get("evidence")) if x)
        if why:
            out.append(f"<details class='raw-fold'><summary>Why we think this</summary><p class='awhy'>{esc(why)}</p></details>")
        out.append("</div></li>")
    out.append("</ol></section>")
    return "".join(out)


def changes_section(change: dict, brand: str) -> str:
    out = ["<section id='changes'><h2>What changed</h2>"]
    if not change:
        out.append("<p class='hint'>No earlier run to compare with.</p></section>")
        return "".join(out)
    comp = change.get("comparability") or {}
    cmpn = change.get("comparison") or {}
    base_when = user_time(((change.get("baseline") or {}).get("timestamps") or [""])[0])
    ok = comp.get("comparable", True)
    if not ok:
        out.append(f"<p class='notcomp'><b>Not comparable</b> with the run from {esc(base_when)}: "
                   f"{esc('; '.join(comp.get('reasons') or []))}. Nothing below is a trend.</p>")
    else:
        out.append(f"<p class='hint'>Compared with the run from {esc(base_when)} on "
                   f"{int(cmpn.get('overlap') or 0)} questions with identical wording.</p>")
    for w in change.get("warnings") or []:
        out.append(f"<p class='hint'>⚠ {esc(w)}</p>")
    br = change.get("brand_rates") or {}
    rows = []
    for e, er in (br.get("engines") or {}).items():
        for arm in ("knowledge", "search"):
            x = er.get(arm) or {}
            b, c = x.get("baseline") or {}, x.get("current") or {}
            if not c.get("n") and not b.get("n"):
                continue
            rows.append(f"<tr><td>{esc(engine_title(e))}</td><td>{esc(ARM_TITLE[arm])}</td>"
                        f"<td>{esc(n_of(b.get('hits'), b.get('n')))}</td><td>{esc(n_of(c.get('hits'), c.get('n')))}</td></tr>")
    if rows:
        out.append(f"<div class='table-wrap'><table class='plain'><tr><th>Assistant</th><th>Mode</th>"
                   f"<th>{esc(brand)} named before</th><th>{esc(brand)} named now</th></tr>" + "".join(rows) + "</table></div>")
        label = "identical questions" if ok else "the identical questions only (for reference)"
        out.append(f"<p class='hint'>Counts cover {label}.</p>")
    tc = (change.get("transitions") or {}).get("counts") or {}
    if tc and ok:
        words = {"miss_to_hit": "newly named", "hit_to_miss": "no longer named", "hit_to_hit": "still named", "miss_to_miss": "still not named"}
        bits = [f"{words.get(k, k.replace('_', ' '))}: {v}" for k, v in tc.items()]
        out.append(f"<p class='hint'>Per answer: {esc(', '.join(bits))}.</p>")
    if ok:
        comps = change.get("competitors") or {}
        ris = [r for r in comps.get("risers") or [] if not r.get("is_brand")][:5]
        fal = [r for r in comps.get("fallers") or [] if not r.get("is_brand")][:5]
        if ris or fal:
            fmt = lambda r: f"{r['name']} {r['baseline']['mentions']}→{r['current']['mentions']}"
            out.append("<p class='hint'>Competitors mentioned more: " + esc(", ".join(fmt(r) for r in ris) or "none")
                       + ". Mentioned less: " + esc(", ".join(fmt(r) for r in fal) or "none") + ".</p>")
    out.append("</section>")
    return "".join(out)


def mapped_appendix(mapped: dict, brand: str) -> str:
    rows = mapped.get("rows") or []
    if not rows:
        return ""
    labels = list(mapped.get("labels") or [])
    changed = []
    for r in rows:
        for ck, by in (r.get("cells") or {}).items():
            vals = [bool((by.get(lab) or {}).get("brand", (by.get(lab) or {}).get("proof"))) for lab in by]
            if len(set(vals)) > 1:
                changed.append((r, ck, by))
    named_any = sum(1 for r in rows for by in (r.get("cells") or {}).values()
                    for v in by.values() if (v or {}).get("brand", (v or {}).get("proof")))
    out = ["<details class='appendix' id='mapped'><summary>Appendix: reworded questions vs older runs (approximate)</summary>"]
    exact = sum(1 for r in rows if r.get("match") == "exact")
    out.append(f"<p class='hint'>{len(rows)} old→new question pairs ({exact} identical wording, {len(rows) - exact} reworded, "
               f"so approximate). {brand} was named in {named_any} answers across all of them; "
               f"{len(changed)} answer{'s' if len(changed) != 1 else ''} changed between runs.</p>")
    if changed:
        out.append("<div class='table-wrap'><table class='plain'><tr><th>New question</th><th>Assistant · mode</th>"
                   + "".join(f"<th>{esc(lab)}</th>" for lab in (labels or sorted({lab for _, _, by in changed for lab in by})))
                   + "</tr>")
        for r, ck, by in changed[:60]:
            e, _, a = ck.partition("|")
            cells = "".join(f"<td>{'named' if (by.get(lab) or {}).get('brand', (by.get(lab) or {}).get('proof')) else ('—' if lab not in by else 'not named')}</td>"
                            for lab in (labels or sorted(by)))
            out.append(f"<tr><td>{esc(r.get('new_text') or r.get('new_id') or '')}</td><td>{esc(engine_title(e))} · {esc(LABELS.get(a, a))}</td>{cells}</tr>")
        out.append("</table></div>")
    out.append("</details>")
    return "".join(out)


def methodology_section(brand: str, domain: str, rows: list, judge: dict, docs: dict, competitors: list) -> str:
    # A real example from this run: the first answer that named the brand, else the first question.
    example = None
    for row in rows:
        for e, arms in row["engines"].items():
            for arm_name in ARMS:
                arm = (arms or {}).get(arm_name)
                if isinstance(arm, dict) and arm.get("brand_mentioned"):
                    example = (row, e, arm_name, judge.get(f"{row['prompt_id']}|{e}|{arm_name}") or {})
                    break
            if example:
                break
        if example:
            break
    comp_words = ", ".join(competitors[:3]) if competitors else "the competitors we track"
    out = ["<details class='method' id='method'><summary>How this is measured</summary>"]
    out.append(
        f"<p>Each question is asked to each AI assistant twice: once <b>from memory</b> (web search switched off) and "
        f"once <b>with web search</b> allowed. Questions are asked as written, without mentioning {esc(brand)}. "
        f"An answer counts as naming {esc(brand)} only on an exact match of the brand or its domain ({esc(domain)}). "
        "A separate review step reads each answer that names it and records whether it recommends it, where it "
        "appears in the list, and whether the description is accurate.</p>")
    out.append(f"<p>Every answer runs in a fresh, empty assistant profile with no account details, memory, or tools "
               f"beyond search, after a check that the assistant cannot tell who is asking. 'From memory' only counts "
               f"when the assistant's own event log shows it did not search.</p>")
    if example:
        row, e, arm_name, j = example
        out.append(f"<p class='ex'><b>Example from this run.</b> “{esc(row['prompt_text'][:200])}” — "
                   f"{esc(engine_title(e))} {esc(LABELS[arm_name])} named {esc(brand)}"
                   + (f" ({esc(j.get('stance'))}, {esc(j.get('position') or '')})" if j.get("stance") else "")
                   + (f": “{esc((j.get('quote') or '')[:200])}”" if j.get("quote") else "") + ".</p>")
    elif rows:
        out.append(f"<p class='ex'><b>Example.</b> “{esc(rows[0]['prompt_text'][:200])}” — an answer that lists "
                   f"{esc(comp_words)} but not {esc(brand)} counts as <i>not named</i>.</p>")
    out.append(settings_section(docs))
    out.append("</details>")
    return "".join(out)


def render(run: Path) -> str:
    docs, judge, board, vendors_raw = load(run)
    rows = merge_rows(docs)
    records = answer_records(docs, judge)
    summary = summarize(records)
    ws_brand, aliases, competitors, competitor_aliases = workspace_from_docs(docs)
    brand = ws_brand or os.environ.get("AEO_BRAND") or "the brand"
    domain = ""
    for doc in docs.values():
        domain = str((doc.get("workspace") or {}).get("domain") or "")
        if domain:
            break
    out_dir = run if run.is_dir() else run.parent
    change = _load_json_opt(out_dir / "change.json")
    if change and not change.get("comparability") and change.get("schema_version"):
        change = {**change, "comparability": {"comparable": False, "reasons": ["made by an older report version"]}}
    mapped = _load_json_opt(out_dir / "mapped-compare.json")
    from aeo.layers import LAYER_MARKER, load_layers

    layers = load_layers(out_dir)
    vendor_store = load_vendor_store(vendors_raw)
    alias_map = seed_alias_map(
        brand, aliases, competitors, vendor_store.values(), competitor_aliases=competitor_aliases
    )
    known_counts, surprise_raw = named_vendor_counts_by_origin(
        rows, vendor_store, brand=brand, aliases=aliases, alias_map=alias_map
    )
    question_texts = [r["prompt_text"] for r in rows]
    surprise_counts, excluded = filter_surprises(surprise_raw, question_texts, docs.keys())
    surprise_mentions = sum(surprise_counts.values())
    engines_run = [e for e in ENGINES if summary["by_engine"].get(e, {}).get("status") != "not run"]
    started = run_started(docs)
    n_questions = len({r["prompt_id"] for r in rows})

    actions = (board or {}).get("actions") or []
    board_headline = (board or {}).get("headline") or ""
    if count_problems([board_headline] + [str(a.get(k) or "") for a in actions for k in ("title", "why", "do", "evidence")], summary):
        board_headline = ""

    parts = []
    parts.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append(f"<title>{esc(brand)} · AEO report</title><style>")
    parts.append(CSS)
    parts.append("</style></head><body>")
    eng_chips = "".join(
        f"<span class='pill {'ok' if e in engines_run else 'off'}'>{esc(engine_title(e))} {'✓' if e in engines_run else 'not run'}</span>"
        for e in ENGINES)
    parts.append(f"<header class='top'><div class='top-row'><div class='top-brand'><span class='wordmark'>{esc(brand)}</span>"
                 f"<span class='domain'>{esc(domain)}</span></div>"
                 f"<div class='top-meta'><span class='pill'>AI answers report · {esc(user_time(started))}</span>"
                 f"<span class='pill'>{n_questions} questions · {summary['total']['answers']} answers</span>{eng_chips}</div></div>")
    nav = [("verdict", "Verdict"), ("actions", "Actions"), ("changes", "Changes"), ("engines", "Assistants"),
           ("competitors", "Competitors"), ("retrieval", "Search trail")]
    if layers[0]:
        nav.append(("google", "Google"))
    if layers[1]:
        nav.append(("search-console", "Search Console"))
    nav += [("questions", "Questions"), ("method", "Method")]
    parts.append("<nav class='secnav'>" + "".join(f"<a href='#{a}'>{esc(b)}</a>" for a, b in nav) + "</nav></header><main>")

    parts.append(f"<h1 class='headline'>{esc(count_headline(summary, brand))}</h1>")
    parts.append(verdict_section(summary, brand, domain, change=change, actions=actions, layers=layers,
                                 caveats=run_caveats(docs, summary, brand)))
    parts.append(actions_section(actions, board_headline))
    parts.append(changes_section(change, brand))
    parts.append("<section id='engines'><h2>Assistants</h2>")
    parts.append(summary_section(summary, brand, domain))
    parts.append(engine_section(summary).replace("<h2>Engines</h2>", ""))
    parts.append("</section>")

    search_vendor_counts = search_box_vendor_counts(
        rows, vendor_store, brand=brand, aliases=aliases, alias_map=alias_map
    )

    def vendor_rows(counts, *, brand_name: str, surprise_set: set | None = None, limit: int = 20) -> str:
        if not counts:
            return "<p class='hint'>Nothing recorded.</p>"
        top = sorted(counts.most_common(limit), key=lambda x: (-x[1], x[0].lower()))
        rank = {n: i for i, (n, _) in enumerate(sorted(counts.items(), key=lambda x: (-x[1], x[0].lower())), 1)}
        if brand_name in counts and brand_name not in {n for n, _ in top}:
            top.append((brand_name, counts[brand_name]))
        mx = max(n for _, n in top) or 1
        bits = []
        for name, n in top:
            width = max((n / mx) * 100, 1.5)
            is_brand = name == brand_name
            untracked = surprise_set is not None and name in surprise_set and not is_brand
            cls = "vname brand" if is_brand else ("vname untracked" if untracked else "vname")
            fill = "brand" if is_brand else ("surprise" if untracked else "teal")
            label = esc(name) + (f" <span class='you'>you · #{rank.get(name)}</span>" if is_brand else "")
            if untracked:
                label += " <span class='badge-surprise'>not tracked</span>"
            bits.append(
                f"<div class='vrow'><span class='{cls}'>{label}</span>"
                f"<div class='bar'><span class='bar-fill {fill}' style='width:{width:.1f}%'></span></div>"
                f"<span class='vcount'>{n}</span></div>"
            )
        return "".join(bits)

    parts.append("<section id='competitors'><h2>Competitors</h2>")
    parts.append("<h3>Who got named</h3>")
    parts.append(
        f"<p class='hint'>Answers naming {esc(brand)} (exact match: {esc(brand_terms_words(brand, aliases))}) or a "
        "competitor we track. All assistants, both modes. Counts are answers that named each one.</p>"
    )
    parts.append("<div class='vendor-bars'>" + vendor_rows(known_counts, brand_name=brand) + "</div>")
    parts.append("<h3>Surprise competitors</h3>")
    ex_txt = ", ".join(f"{k} ({v})" for k, v in excluded.most_common())
    parts.append(
        f"<p class='hint'>Products named in answers that are not on our tracked list: {surprise_mentions} mentions of "
        f"{len(surprise_counts)} names. Left out as not competitors: {esc(ex_txt) or 'nothing'}. "
        "Top 10 per category; add repeat names to the tracked list.</p>"
    )
    if surprise_counts:
        parts.append("<div class='cat-grid'>")
        for cat, total, top in group_by_category(surprise_counts):
            items = "".join(f"<li><span>{esc(n)}</span><span class='vcount'>{c}</span></li>" for n, c in top)
            parts.append(f"<article class='cat'><p class='eyebrow'>{esc(cat)} · {total}</p><ol>{items}</ol></article>")
        parts.append("</div>")
    else:
        parts.append("<p class='hint'>None: every product named was one we track.</p>")
    parts.append("<h3>Typed into search</h3>")
    parts.append(
        f"<p class='hint'>Names the assistants typed into their own web searches (with web search only), sorted by count. "
        f"{esc(brand)} is highlighted in place.</p>"
    )
    if search_vendor_counts:
        search_surprise = search_chart_surprise_set(search_vendor_counts, brand, alias_map)
        parts.append("<div class='vendor-bars'>" + vendor_rows(search_vendor_counts, brand_name=brand, surprise_set=search_surprise) + "</div>")
    else:
        parts.append("<p class='hint'>No product names were typed into search.</p>")
    parts.append("</section>")

    parts.append("<section id='retrieval'>" + retrieval_section(summary, records, brand, domain).replace("<h2>", "<h2>Search trail: ", 1) + "</section>")
    parts.append(LAYER_MARKER)

    # ---------------------------------------------------------------- questions
    shown = [e for e in ENGINES if e in engines_run]
    def named_any(row):
        return any(isinstance(a, dict) and a.get("brand_mentioned") for arms in row["engines"].values() for a in (arms or {}).values())
    ordered = sorted(enumerate(rows), key=lambda ir: (0 if named_any(ir[1]) else 1, ir[0]))
    parts.append("<section id='questions'><h2>Questions</h2>")
    parts.append("<div class='chips' id='chips'>")
    for key, lab in (("named", f"{brand} named"), ("notnamed", f"{brand} not named"), ("recommend", "recommended"),
                     ("first", "listed first"), ("warn", "warned against"), ("untracked", "names an untracked product")):
        parts.append(f"<button type='button' class='chip' data-f='{key}'>{esc(lab)}</button>")
    parts.append("</div>")
    parts.append("<div class='table-legend'><b>M</b> from memory · <b>W</b> with web search · "
                 "<span class='leg-chip rec'>recommended</span> <span class='leg-chip men'>named</span> "
                 "<span class='leg-chip wrn'>warned</span> <span class='leg-chip rej'>rejected</span> "
                 "<span class='leg-chip miss'>not named</span></div>")
    parts.append("<div class='table-wrap'><table class='prompt-table'><thead><tr><th>Question</th>")
    for e in shown:
        parts.append(f"<th>{esc(engine_title(e))}</th>")
    parts.append("</tr></thead><tbody>")
    for i, row in ordered:
        tags = set()
        hit_row = named_any(row)
        tags.add("named" if hit_row else "notnamed")
        drawer = []
        cells_html = []
        for e in shown:
            arms = row["engines"].get(e) or {}
            marks = []
            for arm_name in ARMS:
                letter = ARM_SHORT[arm_name]
                arm = arms.get(arm_name)
                j = judge.get(f"{row['prompt_id']}|{e}|{arm_name}")
                vkey = f"{row['prompt_id']}|{e}|{arm_name}"
                named = classified_vendors_for_arm(arm if isinstance(arm, dict) else None, vendor_store.get(vkey),
                                                   alias_map, brand, aliases)
                qnamed = classified_query_vendors_for_arm(arm if isinstance(arm, dict) else None, vendor_store.get(vkey),
                                                          alias_map, brand, aliases)
                v = cell_view(arm, j, arm_name == "search", named)
                st = v.get("stance") or ""
                kind = v.get("kind")
                cls = "miss" if kind == "miss" else {"recommend": "rec", "mention": "men", "warn": "wrn", "reject": "rej"}.get(st, "men")
                if kind == "none":
                    cls = "none"
                if kind == "hit":
                    tags.add(st)
                    if v.get("position") == "first":
                        tags.add("first")
                if any(isinstance(x, dict) and x.get("origin") == "surprise" and x.get("name") in surprise_counts
                       for x in list(named or []) + list(qnamed or [])):
                    tags.add("untracked")
                word = {"hit": st or "named", "miss": "not named", "none": "no answer"}.get(kind, kind)
                tip = f"{engine_title(e)} {LABELS[arm_name]}: {word} {v.get('position') or ''}".strip()
                marks.append(f"<span class='mk {cls}' title='{esc(tip)}'>{letter}</span>")
                drawer.append(harness_block(engine=e, arm_name=arm_name, kind=kind, brand=brand, stance=st,
                                            position=v.get("position") or "", quote=v.get("quote") or "",
                                            ahead=v.get("ahead") or [], competitors=v.get("competitors") or [],
                                            query_vendors=qnamed, arm=arm if isinstance(arm, dict) else None))
            cells_html.append(f"<td class='eng' data-eng='{esc(engine_title(e))}'><span class='marks'>{''.join(marks)}</span></td>")
        tag_attr = " ".join(sorted(tags))
        parts.append(f"<tr class='prompt-row{' hitrow' if hit_row else ''}' data-i='{i}' data-tags='{tag_attr}'>")
        parts.append(f"<td class='qcell'><button type='button' class='expand' aria-label='Show answers'>▸</button>"
                     f"<span class='prompt-q'>{esc(row['prompt_text'])}</span>"
                     + (f"<span class='qclass'>{esc(plain_class(row.get('class') or ''))}</span>" if row.get('class') and row.get('class') != 'focus' else "")
                     + "</td>")
        parts.extend(cells_html)
        parts.append("</tr>")
        parts.append(f"<tr class='drawer' data-i='{i}' hidden><td colspan='{len(shown) + 1}'>{''.join(drawer)}</td></tr>")
    parts.append("</tbody></table></div></section>")
    parts.append(mapped_appendix(mapped, brand))
    parts.append(methodology_section(brand, domain, rows, judge, docs, list(competitors or [])))
    parts.append("<p class='hint backtop'><a href='#verdict'>Back to top</a></p>")
    parts.append("<script>")
    parts.append(JS)
    parts.append("</script></main></body></html>")
    return "\n".join(parts)


CSS = """
:root{--bg:#0b0d10;--card:#14181e;--line:rgba(255,255,255,.10);--text:#e8edf2;--muted:#9aa4b2;
--teal:#3dccc7;--rec:#6ee7b7;--men:#e8b86d;--wrn:#f0a36b;--rej:#e07a7a;--miss:#8b95a3}
*{box-sizing:border-box}html{scroll-behavior:smooth;scroll-padding-top:110px}
html,body{margin:0;background:var(--bg);color:var(--text);font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;line-height:1.5;overflow-x:hidden}
a{color:var(--teal)}
.top{position:sticky;top:0;z-index:20;padding:10px 24px 0;background:rgba(11,13,16,.94);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.top-row{display:flex;flex-wrap:wrap;gap:8px 16px;justify-content:space-between;align-items:center}
.wordmark{letter-spacing:.14em;text-transform:uppercase;font-weight:650;font-size:15px}
.domain{margin-left:10px;color:var(--muted);font-size:13px}
.top-meta{display:flex;flex-wrap:wrap;gap:6px}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--muted);white-space:nowrap}
.pill.ok{color:var(--rec)}.pill.off{opacity:.75}
.secnav{display:flex;gap:4px;overflow-x:auto;padding:8px 0 8px;scrollbar-width:none}
.secnav a{color:var(--muted);text-decoration:none;font-size:13px;padding:4px 10px;border-radius:999px;white-space:nowrap}
.secnav a:hover{color:var(--text);background:rgba(255,255,255,.06)}
main{max-width:1200px;margin:0 auto;padding:24px 24px 80px}
h1.headline{font-size:22px;font-weight:620;letter-spacing:-.02em;margin:6px 0 14px}
h2{font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);margin:40px 0 12px}
h3{font-size:15px;margin:22px 0 8px}
.eyebrow{margin:0;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.hint{color:var(--muted);font-size:13px}
.verdict{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px 20px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.tile{background:#0e1116;border:1px solid var(--line);border-radius:12px;padding:12px 14px}
.tile-n{margin:6px 0 2px;font-size:24px;font-weight:640;letter-spacing:-.02em}
.tile .hint{margin:0;font-size:12px}
.vline{margin:12px 0 0;font-size:14px}
.caveats{margin-top:14px;padding:10px 14px;border-left:3px solid var(--wrn);background:rgba(240,163,107,.07);border-radius:8px}
.caveats ul{margin:6px 0 0;padding-left:18px;font-size:13px;color:#d6dde6}
.notcomp{padding:10px 14px;border-left:3px solid var(--wrn);background:rgba(240,163,107,.07);border-radius:8px}
.actions{margin-top:8px}
.action-list{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:14px}
.action{display:grid;grid-template-columns:36px 1fr;gap:12px;align-items:start;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.action .n{width:28px;height:28px;border-radius:999px;background:rgba(61,204,199,.15);color:var(--teal);
display:flex;align-items:center;justify-content:center;font-weight:650;font-size:13px}
.atitle{margin:0;font-weight:620}.ado{margin:6px 0 0;font-size:14px}.awhy{margin:6px 0 0;color:var(--muted);font-size:13px}
.tag{font-size:11px;border-radius:999px;padding:1px 8px;background:rgba(110,231,183,.16);color:var(--rec);margin-left:6px;vertical-align:middle}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.metric{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;display:flex;flex-direction:column}
.metric-n{margin:8px 0 4px;font-size:24px;font-weight:620}
.engine-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:12px}
.engine{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}
.engine h3{margin:0 0 8px}
.stat-line{display:flex;justify-content:space-between;gap:8px;font-size:13px;color:var(--muted);margin:8px 0 4px}
.bar{height:8px;background:#0e1116;border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.bar-fill{display:block;height:100%;background:linear-gradient(90deg,#1d9b96,var(--teal))}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.chip{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:999px;padding:5px 12px;font-size:13px;cursor:pointer}
.chip.on{border-color:var(--teal);color:var(--teal)}
.table-legend{display:flex;flex-wrap:wrap;gap:8px;align-items:center;font-size:12px;color:var(--muted);margin:8px 0}
.leg-chip,.mk{display:inline-flex;align-items:center;justify-content:center;border-radius:6px;padding:2px 7px;border:1px solid var(--line);font-size:12px;font-weight:650}
.mk{width:24px;height:24px;margin-right:4px}
.marks{display:inline-flex;gap:4px;white-space:nowrap}.mk{margin-right:0}
.mk.rec,.leg-chip.rec{background:rgba(110,231,183,.18);color:var(--rec);border-color:transparent}
.mk.men,.leg-chip.men{background:rgba(232,184,109,.16);color:var(--men);border-color:transparent}
.mk.wrn,.leg-chip.wrn{background:rgba(240,163,107,.16);color:var(--wrn);border-color:transparent}
.mk.rej,.leg-chip.rej{background:rgba(224,122,122,.18);color:var(--rej);border-color:transparent}
.mk.miss,.leg-chip.miss{color:var(--miss)}
.mk.none{opacity:.4}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;max-width:100%}
table.plain,.funnel{border-collapse:collapse;font-size:13px;margin:8px 0;width:100%}
table.plain td,table.plain th,.funnel td,.funnel th{border-top:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
.prompt-table{width:100%;border-collapse:collapse}
.prompt-table th{text-align:left;font-size:12px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;padding:8px}
.prompt-table td{border-top:1px solid var(--line);padding:10px 8px;vertical-align:top}
.prompt-row.hitrow td{background:rgba(232,184,109,.07)}
.prompt-q{font-size:14px}
.qclass{display:block;color:var(--muted);font-size:12px;margin-left:20px}
.expand{background:none;border:0;color:var(--muted);cursor:pointer;font-size:14px;padding:0 6px 0 0}
.quote{margin:6px 0;font-size:13px;color:#c9d2dc}
.quote b{color:var(--text);margin-right:6px}
.ahead{color:var(--men)}
.vendor-bars{display:flex;flex-direction:column;gap:8px;margin:12px 0 24px}
.vrow{display:grid;grid-template-columns:minmax(120px,220px) 1fr 48px;gap:10px;align-items:center}
.vname{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vcount{text-align:right;color:var(--muted);font-size:13px}
.missline i{color:var(--miss)}
.bar-fill.brand{background:linear-gradient(90deg,#b8860b,var(--men))}
.vname.brand{color:var(--men);font-weight:650}
.bar-fill.surprise{background:linear-gradient(90deg,#4d5866,#7b8796)}
.you{font-size:11px;border-radius:999px;padding:0 6px;background:rgba(232,184,109,.18);color:var(--men);margin-left:4px}
.badge-surprise{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;font-size:11px;border:1px dashed var(--muted);color:var(--muted)}
.untracked{border-bottom:1px dashed var(--muted)}
.cat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:10px;margin:10px 0 20px}
.cat{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px}
.cat ol{margin:8px 0 0;padding-left:20px;font-size:13px}
.cat li span:first-child{display:inline-block;max-width:80%}
.cat li .vcount{float:right}
.harness{margin:10px 0 14px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.harness:last-child{border-bottom:0}
.raw-fold{margin:6px 0 0 0}
.raw-fold>summary,details>summary{cursor:pointer;color:var(--teal);font-size:13px}
.raw-meta{color:var(--muted);font-weight:400;margin-left:6px}
.raw-body{margin:8px 0 0;padding:12px;background:#0e1116;border:1px solid var(--line);border-radius:10px;
max-height:420px;overflow:auto;white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.5;color:#c9d2dc}
.notrun{font-size:20px;color:var(--muted);margin:6px 0}
.flag{color:var(--wrn);font-style:normal}
.board-headline{color:var(--muted);margin:0 0 12px}
.funnel-hits{font-size:13px;color:var(--muted)}
details.method,details.appendix{margin-top:28px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 18px}
details.method>summary,details.appendix>summary{font-size:15px;color:var(--text);font-weight:600}
details.method p{color:#c9d2dc;font-size:14px;max-width:80ch}
.ex{color:var(--muted)}
.backtop{margin-top:24px}
.aeo-layer{background:var(--card)}
@media (max-width:640px){
 .top{padding:8px 12px 0}.domain{display:none}main{padding:16px 12px 60px}
 h1.headline{font-size:18px}
 .tile-n{font-size:19px}
 .tiles{grid-template-columns:1fr 1fr;gap:8px}.tile{padding:10px}.verdict{padding:12px}
 h1.headline{font-size:16px}
 .top-meta .pill{font-size:11px;padding:2px 8px}.wordmark{font-size:13px}
 .vrow{grid-template-columns:110px 1fr 36px}
 .prompt-table thead{display:none}
 .prompt-table,.prompt-table tbody,.prompt-table tr,.prompt-table td{display:block;width:100%}
 .prompt-table tr.prompt-row{border-top:1px solid var(--line);padding:8px 0}
 .prompt-table td{border:0;padding:4px 0}
 .prompt-table td.eng::before{content:attr(data-eng);display:inline-block;width:70px;color:var(--muted);font-size:12px}
 .prompt-table tr.drawer td{padding:0 0 10px}
 .prompt-table tr[hidden]{display:none}
}
"""

JS = """
document.querySelectorAll('.expand').forEach((b)=>{
  b.addEventListener('click',()=>{
    const tr=b.closest('tr');
    const d=document.querySelector('tr.drawer[data-i="'+tr.getAttribute('data-i')+'"]');
    if(d){d.hidden=!d.hidden;b.textContent=d.hidden?'▸':'▾';}
  });
});
document.querySelectorAll('.chip').forEach((c)=>{
  c.addEventListener('click',()=>{
    c.classList.toggle('on');
    const on=[...document.querySelectorAll('.chip.on')].map(x=>x.dataset.f);
    document.querySelectorAll('tr.prompt-row').forEach((tr)=>{
      const tags=(tr.dataset.tags||'').split(' ');
      const ok=!on.length || on.every(f=>tags.includes(f));
      tr.style.display=ok?'':'none';
      const d=document.querySelector('tr.drawer[data-i="'+tr.getAttribute('data-i')+'"]');
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
