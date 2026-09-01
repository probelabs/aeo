#!/usr/bin/env python3.11
"""Tyk AEO HTML: board actions on top, stance-colored K/S grid, quotes in the drawer."""
from __future__ import annotations

import html, json, os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ENGINES = ("claude", "codex", "grok")
ARMS = ("knowledge", "search")


def esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def load(run: Path) -> tuple[dict[str, dict], dict, dict]:
    docs = {}
    for e in ENGINES:
        p = run / f"{e}.json"
        if p.exists():
            docs[e] = json.loads(p.read_text())
    judge = {}
    jp = run / "judge.json"
    if jp.exists():
        judge = json.loads(jp.read_text())
    board = {}
    bp = run / "board.json"
    if bp.exists():
        board = json.loads(bp.read_text())
    return docs, judge, board


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


def cell_view(arm: dict | None, j: dict | None, searched_arm: bool) -> dict:
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
        "competitors": arm.get("competitor_mentions") or [],
    }


def rates(docs, judge, rows):
    out = {}
    for e in ENGINES:
        rec = fir = wrn = hit_s = hit_k = n_s = n_k = 0
        for row in rows:
            arms = row["engines"].get(e) or {}
            for arm_name, nk in (("knowledge", "k"), ("search", "s")):
                arm = arms.get(arm_name)
                if not isinstance(arm, dict) or arm.get("error"):
                    continue
                if arm_name == "search":
                    n_s += 1
                else:
                    n_k += 1
                if not arm.get("brand_mentioned"):
                    continue
                if arm_name == "search":
                    hit_s += 1
                else:
                    hit_k += 1
                j = judge.get(f"{row['prompt_id']}|{e}|{arm_name}") or {}
                if j.get("stance") == "recommend":
                    rec += 1
                if j.get("position") == "first":
                    fir += 1
                if j.get("stance") in ("warn", "reject"):
                    wrn += 1
        doc = docs.get(e) or {}
        out[e] = {
            "n_k": n_k, "n_s": n_s, "hit_k": hit_k, "hit_s": hit_s,
            "mention_k": (hit_k / n_k) if n_k else 0,
            "mention_s": (hit_s / n_s) if n_s else 0,
            "recommend_s": (rec / hit_s) if hit_s else 0,
            "first_s": (fir / hit_s) if hit_s else 0,
            "warn_s": (wrn / hit_s) if hit_s else 0,
            "search_rate": float(doc.get("search_rate") or 0),
        }
    return out


def render(run: Path) -> str:
    docs, judge, board = load(run)
    rows = merge_rows(docs)
    eng_rates = rates(docs, judge, rows)
    brand = os.environ.get("AEO_BRAND") or "Tyk"
    for _e, doc in docs.items():
        ws = doc.get("workspace") or {}
        if ws.get("brand"):
            brand = str(ws["brand"])
            break
    n_cells = sum(len((docs.get(e) or {}).get("prompts") or []) * 2 for e in ENGINES)
    # overall search mention / recommend / first among search hits
    sm = sc = rec = fir = wrn = 0
    for e, r in eng_rates.items():
        sc += r["n_s"]; sm += r["hit_s"]
        # recompute rec/first from judge for search only
    for row in rows:
        for e in ENGINES:
            arm = (row["engines"].get(e) or {}).get("search")
            if not isinstance(arm, dict) or not arm.get("brand_mentioned"):
                continue
            j = judge.get(f"{row['prompt_id']}|{e}|search") or {}
            if j.get("stance") == "recommend":
                rec += 1
            if j.get("position") == "first":
                fir += 1
            if j.get("stance") in ("warn", "reject"):
                wrn += 1
    mention_s = (sm / sc) if sc else 0
    recommend_s = (rec / sm) if sm else 0
    first_s = (fir / sm) if sm else 0
    warn_s = (wrn / sm) if sm else 0

    parts = []
    parts.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append("<title>Tyk · AEO report</title><style>")
    parts.append(CSS)
    parts.append("</style></head><body>")
    parts.append("<header class='top'><div class='top-brand'><span class='wordmark'>Tyk</span>")
    parts.append("<span class='domain'>tyk.io</span></div>")
    parts.append(f"<div class='top-meta'><span class='pill'>tyk100-20260901</span>")
    parts.append(f"<span class='pill'>{n_cells} cells</span></div></header><main>")

    actions = (board or {}).get("actions") or []
    headline = (board or {}).get("headline") or ""
    parts.append("<section class='actions'>")
    parts.append("<p class='eyebrow'>Board judge</p>")
    if headline:
        parts.append(f"<h1>{esc(headline)}</h1>")
    else:
        parts.append("<h1>Top actions from this board</h1>")
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

    def pct(x):
        return f"{x*100:.0f}%"

    parts.append("<section class='hero'>")
    for lab, val, hint in (
        ("Mention (S)", pct(mention_s), f"{sm} / {sc} search-arm names"),
        ("Recommend (S)", pct(recommend_s), "of Tyk hits that were actually pushed"),
        ("First pick (S)", pct(first_s), "of Tyk hits that led the list"),
        ("Warn/reject (S)", pct(warn_s), "of Tyk hits with a caveat or no"),
    ):
        parts.append(f"<article class='metric'><p class='eyebrow'>{lab}</p>")
        parts.append(f"<p class='metric-n'>{val}</p><p class='hint'>{esc(hint)}</p></article>")
    parts.append("</section>")

    parts.append("<h2>Engines</h2><section class='engine-grid'>")
    for e in ENGINES:
        r = eng_rates.get(e) or {}
        parts.append(f"<article class='engine'><header><h3>{e}</h3></header>")
        for lab, key in (("Mention K", "mention_k"), ("Mention S", "mention_s"), ("Recommend among hits", "recommend_s"), ("First pick among hits", "first_s"), ("Searched", "search_rate")):
            v = r.get(key) or 0
            parts.append(f"<div class='stat-line'><span>{lab}</span><span>{pct(v)}</span></div>")
            parts.append(f"<div class='bar'><span class='bar-fill teal' style='width:{v*100:.1f}%'></span></div>")
        parts.append("</article>")
    parts.append("</section>")


    # competitor mention fan-out across all arms
    from collections import Counter
    comp_counts = Counter()
    search_vendor_counts = Counter()
    for row in rows:
        for e in ENGINES:
            arms = row["engines"].get(e) or {}
            for arm_name in ARMS:
                arm = arms.get(arm_name)
                if not isinstance(arm, dict) or arm.get("error"):
                    continue
                for c in arm.get("competitor_mentions") or []:
                    comp_counts[str(c)] += 1
                if arm_name == "search":
                    for v in arm.get("vendors_in_search_queries") or []:
                        search_vendor_counts[str(v)] += 1

    parts.append("<h2>Who got named instead</h2>")
    parts.append("<p class='hint'>Competitor mentions in answer text (all engines, both arms). Not the same as search-box prebelief.</p>")
    parts.append("<div class='vendor-bars'>")
    for name, n in comp_counts.most_common(20):
        width = (n / (comp_counts.most_common(1)[0][1] if comp_counts else 1)) * 100
        parts.append(
            f"<div class='vrow'><span class='vname'>{esc(name)}</span>"
            f"<div class='bar'><span class='bar-fill teal' style='width:{width:.1f}%'></span></div>"
            f"<span class='vcount'>{n}</span></div>"
        )
    if not comp_counts:
        parts.append("<p class='hint'>No competitor mentions recorded.</p>")
    parts.append("</div>")

    parts.append("<h2>Vendors typed into search</h2>")
    parts.append("<p class='hint'>Names inside search tool queries (search arm only).</p>")
    parts.append("<div class='vendor-bars'>")
    for name, n in search_vendor_counts.most_common(20):
        width = (n / (search_vendor_counts.most_common(1)[0][1] if search_vendor_counts else 1)) * 100
        parts.append(
            f"<div class='vrow'><span class='vname'>{esc(name)}</span>"
            f"<div class='bar'><span class='bar-fill teal' style='width:{width:.1f}%'></span></div>"
            f"<span class='vcount'>{n}</span></div>"
        )
    if not search_vendor_counts:
        parts.append("<p class='hint'>Nobody typed vendor names into search (or no engine searched).</p>")
    parts.append("</div>")

    parts.append("<h2>Queries</h2>")

    parts.append("<div class='chips' id='chips'>")
    for key, lab in (("recommend","recommend"), ("first","first pick"), ("last","last/aside"), ("warn","warn"), ("reject","reject"), ("miss","miss")):
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
        parts.append(f"<td class='qcell'><button type='button' class='expand'>▸</button><span class='prompt-q'>{esc(row['prompt_text'])}</span> <span class='why'>{esc(why)}</span></td>")
        drawer = []
        for e in ENGINES:
            arms = row["engines"].get(e) or {}
            marks = []
            for arm_name, letter in (("knowledge", "K"), ("search", "S")):
                arm = arms.get(arm_name)
                j = judge.get(f"{row['prompt_id']}|{e}|{arm_name}")
                v = cell_view(arm, j, arm_name == "search")
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
                tip = f"{letter} {st or kind} {v.get('position') or ''}".strip()
                marks.append(f"<span class='mk {cls}' title='{esc(tip)}'>{letter}</span>")
                comps = v.get("competitors") or []
                comps_txt = ", ".join(str(c) for c in comps[:10])
                if kind == "hit":
                    ahead = ", ".join(v.get("ahead") or [])
                    quote = v.get("quote") or "(no quote)"
                    drawer.append(
                        f"<div class='quote'><b>{esc(e)} {arm_name}</b> "
                        f"<i>{esc(st)}/{esc(v.get('position') or '')}</i> {esc(quote)}"
                        + (f" <span class='ahead'>ahead: {esc(ahead)}</span>" if ahead else "")
                        + "</div>"
                    )
                elif kind == "miss":
                    named = f" Named instead: {esc(comps_txt)}." if comps_txt else ""
                    searched = ""
                    if arm_name == "search" and isinstance(arm, dict):
                        if arm.get("searched"):
                            vq = arm.get("vendors_in_search_queries") or []
                            vqs = ", ".join(str(x) for x in vq[:8])
                            searched = " Searched." + (f" Vendors in box: {esc(vqs)}." if vqs else "")
                        else:
                            searched = " Did not search."
                    drawer.append(
                        f"<div class='quote missline'><b>{esc(e)} {arm_name}</b> "
                        f"<i>never mentioned {esc(brand)}</i>.{named}{searched}</div>"
                    )
                elif kind == "none":
                    drawer.append(
                        f"<div class='quote missline'><b>{esc(e)} {arm_name}</b> "
                        f"<i>not run</i></div>"
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

.qcell{max-width:520px}
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


def main():
    run = Path(os.environ.get("AEO_TYK_RUN") or str(Path.home() / ".aeo/runs/tyk100-20260901"))
    html_out = run / "tyk100-20260901-report.html"
    html_out.write_text(render(run))
    print("wrote", html_out)


if __name__ == "__main__":
    main()
