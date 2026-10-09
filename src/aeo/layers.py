"""Google + Search Console layers: where they live, how a run fetches them, how reports show them.

Both layers sit in a layer directory next to the evidence and are rendered as their own
sections. They are never merged into the LLM mention board or its scores.

Layer directory for an evidence file `runs/<stem>.json` is `runs/<stem>.google/`;
for a run directory (scripts/render_judge_html.py) it is `<run>/google/`.
"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path
from typing import Any, Callable

from aeo import google as g
from aeo import gsc


def layer_dir_for(path: str | Path) -> Path:
    p = Path(path)
    if p.is_dir():
        return p / "google"
    return p.parent / f"{p.stem}.google"


def find_layer_dir(path: str | Path) -> Path | None:
    p = Path(path)
    # A run dir keeps layers in <run>/google; `aeo google --out-dir X` writes them straight into X.
    cands = [p / "google", p] if p.is_dir() else [p.parent / f"{p.stem}.google", p.parent / "google"]
    for c in cands:
        if (c / "google.json").is_file() or (c / "gsc.json").is_file():
            return c
    return None


def _load(path: Path, schema: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d if isinstance(d, dict) and d.get("schema_version") == schema else None


def load_layers(path: str | Path | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    if path is None:
        return None, None
    d = find_layer_dir(path)
    if d is None:
        return None, None
    return _load(d / "google.json", g.SCHEMA_VERSION), _load(d / "gsc.json", gsc.SCHEMA_VERSION)


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def run_layers(
    config_path: str | Path,
    out_dir: str | Path,
    *,
    google_client: g.DataForSEOClient | None = None,
    gsc_query: gsc.Query | None = None,
    env: dict[str, str] | None = None,
    google_overrides: dict[str, Any] | None = None,
    gsc_overrides: dict[str, Any] | None = None,
    skip_google: bool = False,
    skip_gsc: bool = False,
    propose: bool = True,
    llm: Callable[[str], str] | None = None,
    log: Callable[[str], None] = _log,
    sleep: Callable[[float], None] | None = None,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """Run whichever layers have credentials and are enabled. Never raises for layer errors."""
    raw = g.load_raw_config(config_path)
    out = Path(out_dir)
    result: dict[str, Any] = {"google": None, "gsc": None, "proposals": None}
    now = g._now_iso()
    props = g.proposals_path(config_path)

    gs = g.load_settings(raw, **(google_overrides or {}))
    if not skip_google and gs.get("enabled", True):
        client = google_client
        if client is None:
            creds = g.dataforseo_credentials(env)
            client = g.DataForSEOClient(creds) if creds else None
        if client is not None:
            targets, _watch = g.config_searches(raw)
            if not targets and propose and not props.exists():
                try:
                    sugg = g.propose_searches(raw, llm or g.claude_llm)
                    if sugg["google_targets"] or sugg["google_watch"]:
                        result["proposals"] = str(g.write_proposals(props, **sugg))
                        log(f"google: no google_targets in config; wrote UNAPPROVED suggestions to {props}")
                except Exception as e:  # LLM missing / timeout: proposals are optional
                    log(f"google: could not draft target suggestions ({type(e).__name__})")
            elif not targets:
                log(f"google: no google_targets in config; see unapproved suggestions in {props}")
            try:
                bp = g.discover_baseline(out, str(raw.get("domain") or ""), before=now)
                base = json.loads(bp.read_text(encoding="utf-8")) if bp else None
                kw: dict[str, Any] = {"settings": gs, "baseline": base, "baseline_path": str(bp) if bp else None, "log": log,
                                      "retry_failed": retry_failed}
                if sleep is not None:
                    kw["sleep"] = sleep
                doc = g.run_google_layer(raw, out, client=client, **kw)
                result["google"] = doc
            except Exception as e:
                log(f"google: layer failed ({type(e).__name__}: {e}); board unaffected")

    ss = gsc.load_settings(raw, **(gsc_overrides or {}))
    if not skip_gsc and ss.get("enabled", True):
        q = gsc_query
        if q is None:
            tokens = gsc.gsc_credentials(env)
            q = gsc.http_query(tokens) if tokens else None
        if q is not None:
            try:
                bp = g.discover_baseline(out, str(raw.get("domain") or ""), before=now, name="gsc.json")
                base = json.loads(bp.read_text(encoding="utf-8")) if bp else None
                result["gsc"] = gsc.run_gsc_layer(raw, out, query=q, settings=ss, baseline=base,
                                                  baseline_path=str(bp) if bp else None, proposals_file=props, log=log)
            except Exception as e:
                log(f"gsc: layer failed ({type(e).__name__}: {e}); board unaffected")
    return result


# ---------------------------------------------------------------- formatting helpers


def _rank_word(rank: Any) -> str:
    return f"#{rank}" if rank else "not in top 100"


def _rank_word_depth(rank: Any, depth: Any) -> str:
    return f"#{rank}" if rank else f"not in top {depth or 100}"


def _num(v: Any) -> str:
    return f"{int(v or 0):,}"


def _pos(v: Any) -> str:
    return f"{float(v):.1f}" if v else "-"


def _pct(v: Any) -> str:
    return f"{100 * float(v or 0):.1f}%"


def _signed(v: Any, fmt: str = "{:+d}") -> str:
    if v is None:
        return ""
    try:
        return fmt.format(v) if v else "±0"
    except (ValueError, TypeError):
        return str(v)


def delta_words(d: dict[str, Any] | None) -> list[str]:
    if not d:
        return []
    if d.get("new_search"):
        return ["new search (no previous run)"]
    if d.get("comparable") is False:
        return ["not comparable (a run errored)"]
    out = []
    if d.get("rank_move"):
        out.append(f"rank {'up' if d['rank_move'] > 0 else 'down'} {abs(d['rank_move'])} (#{d['rank_prev']} → #{d['rank_now']})")
    if d.get("rank_change"):
        out.append(d["rank_change"])
    if d.get("citation"):
        out.append(f"AIO citation {d['citation']}")
    if d.get("aio"):
        out.append(f"AIO {d['aio']}")
    if d.get("new_aio_domains"):
        out.append("new in AIO: " + ", ".join(d["new_aio_domains"]))
    if d.get("dropped_aio_domains"):
        out.append("dropped from AIO: " + ", ".join(d["dropped_aio_domains"]))
    if d.get("new_top10_domains"):
        out.append("new in top 10: " + ", ".join(d["new_top10_domains"]))
    if d.get("dropped_top10_domains"):
        out.append("left top 10: " + ", ".join(d["dropped_top10_domains"]))
    return out or ["no change"]


def _gsc_by_query(gsc_doc: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {g.norm_query(x.get("query", "")): x for x in (gsc_doc or {}).get("searches") or []}


def gsc_words(x: dict[str, Any] | None) -> str:
    if not x:
        return ""
    c = x.get("current")
    if not c:
        return "GSC: no impressions in window"
    s = f"GSC 28d: {_num(c['clicks'])} clicks, {_num(c['impressions'])} impr, pos {_pos(c['position'])}"
    ch = x.get("change") or {}
    if x.get("previous"):
        s += f" (vs prior 28d: clicks {_signed(ch.get('clicks'))}, impr {_signed(ch.get('impressions'))})"
    return s


# ---------------------------------------------------------------- markdown


def _md(s: Any) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def render_google_markdown(doc: dict[str, Any], gsc_doc: dict[str, Any] | None = None) -> str:
    st = doc.get("settings") or {}
    sm = doc.get("summary") or g.summarize(doc)
    t, w = sm.get(g.TARGET_TIER) or {}, sm.get(g.WATCH_TIER) or {}
    dom = doc.get("domain") or ""
    by_gsc = _gsc_by_query(gsc_doc)
    L = [
        "## Google",
        "",
        f"_Live Google via DataForSEO (location {st.get('location_code')}, {st.get('language_code')}, {st.get('device')}). "
        f"Separate layer: not part of the LLM mention scores. Generated {doc.get('generated_at')}; spent ${float(doc.get('cost_usd') or 0):.3f}._",
        "",
        f"- Targets: {t.get('searches', 0)} searches, AI Overview on {t.get('aio_present', 0)}, {dom} cited in {t.get('own_cited', 0)}, "
        f"{dom} in top 10 on {t.get('own_top10', 0)}, in top 100 on {t.get('own_top100', 0)}",
    ]
    if w.get("searches"):
        L.append(f"- Watch: {w['searches']} searches, AI Overview on {w.get('aio_present', 0)}, cited in {w.get('own_cited', 0)}, top 10 on {w.get('own_top10', 0)}, top 100 on {w.get('own_top100', 0)}")
    if doc.get("baseline"):
        L.append(f"- Compared with the run from {doc['baseline'].get('generated_at')}")
    L.append("")
    for x in [x for x in doc.get("searches") or [] if x.get("tier") == g.TARGET_TIER]:
        s = x.get("serp") or {}
        L.append(f"### {x.get('priority', '')}. {x['query']}")
        L.append("")
        if not s.get("ok"):
            L += [f"Error: {s.get('error')}", ""]
            continue
        L.append(f"- AI Overview: {'yes' if s.get('aio_present') else 'no'}; {dom} cited: {'**yes**' if s.get('own_cited') else 'no'}; our rank: {_rank_word_depth(s.get('own_rank'), st.get('depth'))}" + (" (partial results)" if s.get("partial") else ""))
        if gsc_words(by_gsc.get(g.norm_query(x["query"]))):
            L.append(f"- {gsc_words(by_gsc.get(g.norm_query(x['query'])))}")
        if x.get("delta"):
            L.append("- Since last run: " + "; ".join(delta_words(x["delta"])))
        am = x.get("ai_mode") or {}
        if am.get("ok"):
            L.append(f"- AI Mode: {len(am.get('references') or [])} sources; {dom} cited: {'**yes**' if am.get('own_cited') else 'no'}")
        if x.get("page_should_demonstrate"):
            L.append(f"- Our page should demonstrate: {x['page_should_demonstrate']}")
        if s.get("aio_references"):
            L += ["", "AIO sources:", ""]
            L += [f"{r['position']}. {'**' if r.get('own') else ''}{r['domain']}{'**' if r.get('own') else ''} — {_md(r.get('title'))} ({r['url']})" for r in s["aio_references"]]
        L += ["", "| Rank | Title | Domain |", "|---|---|---|"]
        L += [f"| {r.get('rank')} | {_md(r.get('title'))} | {'**' + r['domain'] + '**' if r.get('own') else r.get('domain')} |" for r in s.get("top10") or []]
        L.append("")
    watch = [x for x in doc.get("searches") or [] if x.get("tier") == g.WATCH_TIER]
    if watch:
        L += ["### Watch list", "", "| Search | AIO | Cited | Our rank | GSC impr | Top AIO sources | Since last run |", "|---|---|---|---|---|---|---|"]
        for x in watch:
            s = x.get("serp") or {}
            gq = (by_gsc.get(g.norm_query(x["query"])) or {}).get("current") or {}
            if not s.get("ok"):
                L.append(f"| {_md(x['query'])} | err | | | | {_md(s.get('error'))} | |")
                continue
            L.append(
                f"| {_md(x['query'])} | {'yes' if s.get('aio_present') else 'no'} | {'**yes**' if s.get('own_cited') else ''} | "
                f"{_rank_word_depth(s.get('own_rank'), st.get('watch_depth'))} | {_num(gq.get('impressions')) if gq else ''} | {', '.join((s.get('aio_ref_domains') or [])[:4])} | {_md('; '.join(delta_words(x.get('delta'))))} |"
            )
        L.append("")
    return "\n".join(L).rstrip() + "\n"


def _md_rows(rows: list[dict[str, Any]], label: str) -> list[str]:
    L = [f"| {label} | Clicks | Impr | CTR | Pos | Δ clicks | Δ impr |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        ch = r.get("change") or {}
        L.append(f"| {_md(r['key'])} | {_num(r['clicks'])} | {_num(r['impressions'])} | {_pct(r['ctr'])} | {_pos(r['position'])} | {_signed(ch.get('clicks'))} | {_signed(ch.get('impressions'))} |")
    return L


def render_gsc_markdown(doc: dict[str, Any]) -> str:
    w = doc.get("windows") or {}
    tc = (doc.get("totals") or {}).get("current") or {}
    tp = (doc.get("totals") or {}).get("previous") or {}
    ch = (doc.get("totals") or {}).get("change") or {}
    L = [
        "## Search Console",
        "",
        f"_{doc.get('site')}: {w.get('current', {}).get('start')}..{w.get('current', {}).get('end')} vs {w.get('previous', {}).get('start')}..{w.get('previous', {}).get('end')}. "
        "Separate layer: not part of the LLM mention scores._",
        "",
        "| | Last 28d | Prior 28d | Change |",
        "|---|---|---|---|",
        f"| Clicks | {_num(tc.get('clicks'))} | {_num(tp.get('clicks'))} | {_signed(ch.get('clicks'))} |",
        f"| Impressions | {_num(tc.get('impressions'))} | {_num(tp.get('impressions'))} | {_signed(ch.get('impressions'))} |",
        f"| CTR | {_pct(tc.get('ctr'))} | {_pct(tp.get('ctr'))} | {100 * float(ch.get('ctr') or 0):+.1f} pt |",
        f"| Avg position | {_pos(tc.get('position'))} | {_pos(tp.get('position'))} | {_signed(ch.get('position'), '{:+.1f}')} (+ = moved up) |",
        "",
    ]
    vr = (doc.get("totals") or {}).get("vs_previous_run")
    if vr:
        L += [f"Since the previous run ({(doc.get('baseline') or {}).get('generated_at')}): clicks {_signed(vr.get('clicks'))}, impressions {_signed(vr.get('impressions'))}.", ""]
    for title, key, label in (("Top queries", "top_queries", "Query"), ("Top pages", "top_pages", "Page"),
                              ("Striking distance (position 5-20)", "striking_distance", "Query"),
                              ("Impressions, no clicks", "impressions_no_clicks", "Query")):
        rows = doc.get(key) or []
        L += [f"### {title}", ""]
        L += (_md_rows(rows, label) if rows else ["None in this window."]) + [""]
    if doc.get("searches"):
        L += ["### Tracked searches", "", "| Search | Tier | Clicks | Impr | Pos | Δ impr vs prior 28d |", "|---|---|---|---|---|---|"]
        for x in doc["searches"]:
            c = x.get("current") or {}
            L.append(f"| {_md(x['query'])} | {x['tier']} | {_num(c.get('clicks'))} | {_num(c.get('impressions'))} | {_pos(c.get('position'))} | {_signed((x.get('change') or {}).get('impressions'))} |")
        L.append("")
    if doc.get("watch_suggestions"):
        L += [f"{len(doc['watch_suggestions'])} GSC queries with impressions are not tracked yet; they are in the proposals file as unapproved watch suggestions.", ""]
    return "\n".join(L).rstrip() + "\n"


def render_layers_markdown(google_doc: dict[str, Any] | None, gsc_doc: dict[str, Any] | None) -> str:
    parts = []
    if google_doc:
        parts.append(render_google_markdown(google_doc, gsc_doc))
    if gsc_doc:
        parts.append(render_gsc_markdown(gsc_doc))
    return "\n".join(parts)


# ---------------------------------------------------------------- HTML


_CSS = """
.aeo-layer{margin:28px 0;padding:18px 20px;border:1px solid rgba(127,127,127,.35);border-radius:12px}
.aeo-layer h2{margin:0 0 4px}.aeo-layer h3{margin:18px 0 6px;font-size:15px}
.aeo-layer .note{opacity:.75;font-size:12px;margin:0 0 12px}
.aeo-layer table{border-collapse:collapse;width:100%;font-size:13px;margin:6px 0 10px}
.aeo-layer th,.aeo-layer td{border-bottom:1px solid rgba(127,127,127,.25);padding:4px 8px;text-align:left;vertical-align:top}
.aeo-layer td.n,.aeo-layer th.n{text-align:right;font-variant-numeric:tabular-nums}
.aeo-layer a{color:inherit}
.aeo-layer .own{background:rgba(255,196,0,.22);font-weight:650}
.aeo-layer .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:8px 0 14px}
.aeo-layer .card{border:1px solid rgba(127,127,127,.3);border-radius:10px;padding:8px 10px}
.aeo-layer .card b{display:block;font-size:20px}
.aeo-layer .search{border:1px solid rgba(127,127,127,.3);border-radius:10px;padding:10px 14px;margin:12px 0}
.aeo-layer .badges span{display:inline-block;margin:2px 6px 2px 0;padding:1px 8px;border-radius:999px;border:1px solid rgba(127,127,127,.4);font-size:12px}
.aeo-layer .badges .yes{background:rgba(40,180,99,.22)}.aeo-layer .badges .no{opacity:.8}
.aeo-layer .delta{font-size:12px;opacity:.85;margin:4px 0}
.aeo-layer details{margin:6px 0}.aeo-layer summary{cursor:pointer;font-size:13px}
.aeo-layer pre{white-space:pre-wrap;word-break:break-word;font-size:12px;max-height:380px;overflow:auto;padding:8px;border:1px solid rgba(127,127,127,.25);border-radius:8px}
.aeo-layer ol{margin:4px 0 8px;padding-left:22px;font-size:13px}
"""


def _e(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _link(url: str, text: Any = None) -> str:
    return f'<a href="{_e(url)}" rel="noopener" target="_blank">{_e(text or url)}</a>'


def _badge(text: str, yes: bool | None = None) -> str:
    cls = "" if yes is None else (' class="yes"' if yes else ' class="no"')
    return f"<span{cls}>{_e(text)}</span>"


def _refs_html(refs: list[dict[str, Any]]) -> str:
    if not refs:
        return "<p class=\"note\">No cited sources.</p>"
    own_cls = ' class="own"'
    items = [f'<li{own_cls if r.get("own") else ""}>{_e(r.get("domain"))} · {_link(r["url"], r.get("title") or r["url"])}</li>' for r in refs]
    return "<ol>" + "".join(items) + "</ol>"


def _top10_html(rows: list[dict[str, Any]]) -> str:
    out = ['<table><tr><th class="n">Rank</th><th>Title</th><th>Domain</th></tr>']
    for r in rows:
        own = ' class="own"' if r.get("own") else ""
        out.append(f'<tr{own}><td class="n">{_e(r.get("rank"))}</td><td>{_link(r.get("url") or "", r.get("title") or r.get("url"))}</td><td>{_e(r.get("domain"))}</td></tr>')
    out.append("</table>")
    return "".join(out)


def render_google_html(doc: dict[str, Any], gsc_doc: dict[str, Any] | None = None) -> str:
    st = doc.get("settings") or {}
    sm = doc.get("summary") or g.summarize(doc)
    t, w = sm.get(g.TARGET_TIER) or {}, sm.get(g.WATCH_TIER) or {}
    dom = doc.get("domain") or ""
    by_gsc = _gsc_by_query(gsc_doc)
    P = ['<section class="aeo-layer google" id="google">', "<h2>Google</h2>"]
    P.append(
        f'<p class="note">Live Google via DataForSEO · location {_e(st.get("location_code"))} · {_e(st.get("language_code"))} · {_e(st.get("device"))} · '
        f'generated {_e(doc.get("generated_at"))} · spent ${float(doc.get("cost_usd") or 0):.3f}. '
        "Separate layer: these numbers are not part of the LLM mention scores."
        + (f' Compared with the run from {_e(doc["baseline"].get("generated_at"))}.' if doc.get("baseline") else "")
        + "</p>"
    )
    cards = [
        ("Target searches", t.get("searches", 0)),
        ("AI Overview shown", t.get("aio_present", 0)),
        (f"AIO cites {dom}", t.get("own_cited", 0)),
        ("We rank top 10", t.get("own_top10", 0)),
        ("We rank top 100", t.get("own_top100", 0)),
    ]
    if w.get("searches"):
        cards.append(("Watch searches", w["searches"]))
    P.append('<div class="cards">' + "".join(f'<div class="card"><b>{_e(v)}</b>{_e(k)}</div>' for k, v in cards) + "</div>")
    for x in [x for x in doc.get("searches") or [] if x.get("tier") == g.TARGET_TIER]:
        s = x.get("serp") or {}
        P.append('<article class="search">')
        P.append(f"<h3>{_e(x.get('priority'))}. {_e(x['query'])}</h3>")
        if not s.get("ok"):
            P.append(f'<p class="note">Error: {_e(s.get("error"))}</p></article>')
            continue
        badges = [
            _badge("AI Overview" if s.get("aio_present") else "no AI Overview", bool(s.get("aio_present"))),
            _badge(f"{dom} cited" if s.get("own_cited") else f"{dom} not cited", bool(s.get("own_cited"))),
            _badge(f"our rank {_rank_word_depth(s.get('own_rank'), st.get('depth'))}", bool(s.get("own_rank"))),
        ]
        am = x.get("ai_mode") or {}
        if am.get("ok"):
            badges.append(_badge(f"AI Mode {'cites ' + dom if am.get('own_cited') else 'does not cite ' + dom}", bool(am.get("own_cited"))))
        if s.get("partial"):
            badges.append(_badge("partial results"))
        P.append('<div class="badges">' + "".join(badges) + "</div>")
        gw = gsc_words(by_gsc.get(g.norm_query(x["query"])))
        if gw:
            P.append(f'<p class="delta">{_e(gw)}</p>')
        if x.get("delta"):
            P.append(f'<p class="delta">Since last run: {_e("; ".join(delta_words(x["delta"])))}</p>')
        if x.get("page_should_demonstrate"):
            P.append(f'<p class="delta">Our page should demonstrate: {_e(x["page_should_demonstrate"])}</p>')
        if s.get("aio_present"):
            P.append(f"<details><summary>AI Overview text ({len(s.get('aio_text') or '')} chars)</summary><pre>{_e(s.get('aio_text'))}</pre></details>")
            P.append(f"<p><b>AI Overview sources</b> ({len(s.get('aio_references') or [])})</p>" + _refs_html(s.get("aio_references") or []))
        P.append("<p><b>Top 10 organic</b>" + (f" · our position {_e(_rank_word_depth(s.get('own_rank'), st.get('depth')))}" ) + "</p>" + _top10_html(s.get("top10") or []))
        if am.get("ok"):
            P.append(f"<details><summary>AI Mode answer and sources ({len(am.get('references') or [])})</summary><pre>{_e(am.get('text'))}</pre>{_refs_html(am.get('references') or [])}</details>")
        P.append("</article>")
    watch = [x for x in doc.get("searches") or [] if x.get("tier") == g.WATCH_TIER]
    if watch:
        P.append("<h3>Watch list</h3>")
        P.append('<table><tr><th>Search</th><th>AIO</th><th>Cites us</th><th>Our rank</th><th class="n">GSC impr</th><th>Top AIO sources</th><th>Since last run</th></tr>')
        for x in watch:
            s = x.get("serp") or {}
            gq = (by_gsc.get(g.norm_query(x["query"])) or {}).get("current") or {}
            if not s.get("ok"):
                P.append(f"<tr><td>{_e(x['query'])}</td><td colspan=6>error: {_e(s.get('error'))}</td></tr>")
                continue
            own = ' class="own"' if s.get("own_cited") or (s.get("own_rank") or 999) <= 10 else ""
            P.append(
                f"<tr{own}><td>{_e(x['query'])}</td><td>{'yes' if s.get('aio_present') else 'no'}</td><td>{'yes' if s.get('own_cited') else ''}</td>"
                f"<td>{_e(_rank_word_depth(s.get('own_rank'), st.get('watch_depth')))}</td><td class=\"n\">{_e(_num(gq.get('impressions')) if gq else '')}</td>"
                f"<td>{_e(', '.join((s.get('aio_ref_domains') or [])[:4]))}</td><td>{_e('; '.join(delta_words(x.get('delta'))))}</td></tr>"
            )
        P.append("</table>")
    P.append("</section>")
    return "\n".join(P)


def _rows_html(rows: list[dict[str, Any]], label: str, link: bool = False) -> str:
    if not rows:
        return '<p class="note">None in this window.</p>'
    out = [f'<table><tr><th>{_e(label)}</th><th class="n">Clicks</th><th class="n">Impr</th><th class="n">CTR</th><th class="n">Pos</th><th class="n">Δ clicks</th><th class="n">Δ impr</th></tr>']
    for r in rows:
        ch = r.get("change") or {}
        key = _link(r["key"]) if link else _e(r["key"])
        out.append(f'<tr><td>{key}</td><td class="n">{_num(r["clicks"])}</td><td class="n">{_num(r["impressions"])}</td><td class="n">{_pct(r["ctr"])}</td>'
                   f'<td class="n">{_pos(r["position"])}</td><td class="n">{_e(_signed(ch.get("clicks")))}</td><td class="n">{_e(_signed(ch.get("impressions")))}</td></tr>')
    out.append("</table>")
    return "".join(out)


def render_gsc_html(doc: dict[str, Any]) -> str:
    w = doc.get("windows") or {}
    tot = doc.get("totals") or {}
    tc, tp, ch = tot.get("current") or {}, tot.get("previous") or {}, tot.get("change") or {}
    P = ['<section class="aeo-layer gsc" id="search-console">', "<h2>Search Console</h2>"]
    P.append(f'<p class="note">{_e(doc.get("site"))} · last 28 days {_e(w.get("current", {}).get("start"))}..{_e(w.get("current", {}).get("end"))} '
             f'vs prior {_e(w.get("previous", {}).get("start"))}..{_e(w.get("previous", {}).get("end"))}. Separate layer: not part of the LLM mention scores.</p>')
    cards = [
        ("Clicks", _num(tc.get("clicks")), _signed(ch.get("clicks"))),
        ("Impressions", _num(tc.get("impressions")), _signed(ch.get("impressions"))),
        ("CTR", _pct(tc.get("ctr")), f"{100 * float(ch.get('ctr') or 0):+.1f} pt"),
        ("Avg position", _pos(tc.get("position")), _signed(ch.get("position"), "{:+.1f}") + " (+ = up)"),
    ]
    P.append('<div class="cards">' + "".join(f'<div class="card"><b>{_e(v)}</b>{_e(k)} · {_e(d)} vs prior 28d</div>' for k, v, d in cards) + "</div>")
    vr = tot.get("vs_previous_run")
    if vr:
        P.append(f'<p class="delta">Since the previous run ({_e((doc.get("baseline") or {}).get("generated_at"))}): clicks {_e(_signed(vr.get("clicks")))}, impressions {_e(_signed(vr.get("impressions")))}.</p>')
    P.append(f'<p class="note">Prior 28d: {_num(tp.get("clicks"))} clicks, {_num(tp.get("impressions"))} impressions.</p>')
    if doc.get("searches"):
        P.append("<h3>Tracked searches</h3>")
        P.append('<table><tr><th>Search</th><th>Tier</th><th class="n">Clicks</th><th class="n">Impr</th><th class="n">Pos</th><th class="n">Δ impr vs prior 28d</th><th class="n">Δ impr vs last run</th></tr>')
        for x in doc["searches"]:
            c = x.get("current") or {}
            P.append(f'<tr><td>{_e(x["query"])}</td><td>{_e(x["tier"])}</td><td class="n">{_num(c.get("clicks"))}</td><td class="n">{_num(c.get("impressions"))}</td>'
                     f'<td class="n">{_pos(c.get("position"))}</td><td class="n">{_e(_signed((x.get("change") or {}).get("impressions")))}</td>'
                     f'<td class="n">{_e(_signed((x.get("vs_previous_run") or {}).get("impressions")))}</td></tr>')
        P.append("</table>")
    for title, key, label, link in (("Striking distance (position 5–20, real impressions)", "striking_distance", "Query", False),
                                    ("Impressions but no clicks", "impressions_no_clicks", "Query", False),
                                    ("Top queries", "top_queries", "Query", False), ("Top pages", "top_pages", "Page", True)):
        P.append(f"<h3>{_e(title)}</h3>" + _rows_html(doc.get(key) or [], label, link))
    if doc.get("watch_suggestions"):
        P.append(f'<p class="note">{len(doc["watch_suggestions"])} GSC queries with impressions are not tracked yet. They were added to the proposals file as unapproved watch suggestions.</p>')
    P.append("</section>")
    return "\n".join(P)


def render_layers_html(google_doc: dict[str, Any] | None, gsc_doc: dict[str, Any] | None) -> str:
    if not google_doc and not gsc_doc:
        return ""
    parts = [f"<style>{_CSS}</style>"]
    if google_doc:
        parts.append(render_google_html(google_doc, gsc_doc))
    if gsc_doc:
        parts.append(render_gsc_html(gsc_doc))
    return "\n".join(parts)


def inject_html(page: str, fragment: str) -> str:
    """Insert the layer sections before </main> (or </body>). No fragment -> page unchanged."""
    if not fragment:
        return page
    for marker in ("</main>", "</body>"):
        i = page.rfind(marker)
        if i != -1:
            return page[:i] + fragment + "\n" + page[i:]
    return page + fragment


def with_layers(page: str, evidence_or_run: str | Path | None) -> str:
    gd, sd = load_layers(evidence_or_run)
    return inject_html(page, render_layers_html(gd, sd))


def standalone_html(google_doc: dict[str, Any] | None, gsc_doc: dict[str, Any] | None, title: str = "Google layers") -> str:
    return (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title></head>'
            f'<body style="font-family:system-ui,sans-serif;max-width:1100px;margin:2em auto;padding:0 1em"><main>'
            f"{render_layers_html(google_doc, gsc_doc)}</main></body></html>\n")
