"""Self-contained HTML AEO report from one or more evidence documents."""

from __future__ import annotations

import html
import json
from typing import Any

from aeo.board import _brand_terms, _call_for, _is_prebelief

SNIPPET_CHARS = 400


def _prompt_key(prompt: dict[str, Any]) -> tuple[str, int | None]:
    pid = str(prompt.get("prompt_id") or "")
    if prompt.get("sample_index") is None:
        return (pid, None)
    return (pid, int(prompt["sample_index"]))


def _copy_arms(arms: Any) -> Any:
    if not isinstance(arms, dict):
        return arms
    return {name: (dict(arm) if isinstance(arm, dict) else arm) for name, arm in arms.items()}


def merge_docs(docs: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge evidence docs by prompt_id (+ sample_index). Union engine arms."""
    if not docs:
        return {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {},
            "run": {"run_id": "", "engines": []},
            "prompts": [],
        }
    first = docs[0]
    run_ids: list[str] = []
    timestamps: list[str] = []
    engines: list[str] = []
    for doc in docs:
        run = doc.get("run") or {}
        rid = run.get("run_id")
        if rid:
            run_ids.append(str(rid))
        ts = run.get("timestamp")
        if ts:
            timestamps.append(str(ts))
        for engine in run.get("engines") or []:
            if engine not in engines:
                engines.append(str(engine))
        for prompt in doc.get("prompts") or []:
            for engine in (prompt.get("engines") or {}):
                if engine not in engines:
                    engines.append(str(engine))

    by_key: dict[tuple[str, int | None], dict[str, Any]] = {}
    order: list[tuple[str, int | None]] = []
    for doc in docs:
        for prompt in doc.get("prompts") or []:
            key = _prompt_key(prompt)
            if key not in by_key:
                entry = {k: v for k, v in prompt.items() if k != "engines"}
                entry["engines"] = {}
                for engine, arms in (prompt.get("engines") or {}).items():
                    entry["engines"][engine] = _copy_arms(arms)
                by_key[key] = entry
                order.append(key)
                continue
            dest = by_key[key]["engines"]
            for engine, arms in (prompt.get("engines") or {}).items():
                if engine not in dest:
                    dest[engine] = _copy_arms(arms)
                    continue
                if not isinstance(arms, dict) or not isinstance(dest[engine], dict):
                    continue
                for arm_name, arm in arms.items():
                    if arm_name not in dest[engine]:
                        dest[engine][arm_name] = dict(arm) if isinstance(arm, dict) else arm

    run = dict(first.get("run") or {})
    run["run_id"] = "+".join(run_ids)
    run["engines"] = engines
    if timestamps:
        run["timestamp"] = max(timestamps)
    return {
        "schema_version": first.get("schema_version") or "aeo-cli-evidence-v1",
        "workspace": first.get("workspace") or {},
        "run": run,
        "prompts": [by_key[k] for k in order],
    }


def _arm_ok(arm: Any) -> bool:
    return isinstance(arm, dict) and "error" not in arm


def _snippet(text: Any, n: int = SNIPPET_CHARS) -> str:
    s = " ".join(str(text or "").split())
    if len(s) <= n:
        return s
    return s[:n] + "…"


def _usage_cost(arm: dict[str, Any] | None) -> float:
    if not isinstance(arm, dict):
        return 0.0
    usage = arm.get("usage")
    if not isinstance(usage, dict):
        return 0.0
    raw = usage.get("cost_usd")
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _is_discovery(search_arm: dict[str, Any] | None, brand_terms: set[str]) -> bool:
    if not isinstance(search_arm, dict) or not search_arm.get("searched"):
        return False
    if _is_prebelief(search_arm, brand_terms):
        return False
    vendors = [str(v).lower() for v in (search_arm.get("vendors_in_search_queries") or [])]
    if not vendors:
        return True
    return not (set(vendors) - brand_terms)


def _engine_view(prompt: dict[str, Any]) -> dict[str, Any]:
    """brand_mentioned -> mentioned for every engine present (not a fixed trio)."""
    raw = prompt.get("engines") or {}
    out: dict[str, Any] = {}
    if not isinstance(raw, dict):
        return out
    for engine, arms in raw.items():
        if not isinstance(arms, dict):
            continue
        view: dict[str, Any] = {}
        k = arms.get("knowledge")
        s = arms.get("search")
        if isinstance(k, dict):
            view["knowledge"] = {"mentioned": bool(k.get("brand_mentioned"))}
        if isinstance(s, dict):
            view["search"] = {
                "mentioned": bool(s.get("brand_mentioned")),
                "searched": bool(s.get("searched")),
                "vendors_in_search_queries": list(s.get("vendors_in_search_queries") or []),
            }
        if view:
            out[str(engine)] = view
    return out


def _empty_engine_stats() -> dict[str, Any]:
    return {
        "cells": 0,
        "knowledge_cells": 0,
        "search_cells": 0,
        "knowledge_mentions": 0,
        "search_mentions": 0,
        "searched": 0,
        "never_searched": 0,
        "prebelief": 0,
        "discovery": 0,
        "mention_without_search": 0,
        "cost_usd": 0.0,
        "vendor_counts": {},
    }


def _bump_vendor(counts: dict[str, int], vendors: list[Any]) -> None:
    for v in vendors:
        name = str(v).strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + 1


def _arm_payload(arm: dict[str, Any] | None, *, search: bool, brand_terms: set[str]) -> dict[str, Any] | None:
    if not isinstance(arm, dict):
        return None
    mentioned = bool(arm.get("brand_mentioned"))
    payload: dict[str, Any] = {
        "mentioned": mentioned,
        "error": arm.get("error"),
        "snippet": _snippet(arm.get("raw_response_text")),
        "brand_mentions": list(arm.get("brand_mentions") or []),
        "competitor_mentions": list(arm.get("competitor_mentions") or []),
    }
    if not search:
        payload["knowledge_mention"] = mentioned
        return payload
    searched = bool(arm.get("searched"))
    vendors = list(arm.get("vendors_in_search_queries") or [])
    payload.update(
        {
            "searched": searched,
            "search_queries": list(arm.get("search_queries") or []),
            "vendors": vendors,
            "prebelief": _is_prebelief(arm, brand_terms),
            "discovery": _is_discovery(arm, brand_terms),
            "mention_without_search": mentioned and not searched,
        }
    )
    return payload


def build_report_payload(
    doc: dict[str, Any],
    *,
    generated_from_files: list[str] | None = None,
) -> dict[str, Any]:
    ws = doc.get("workspace") or {}
    run = doc.get("run") or {}
    brand = str(ws.get("brand") or "")
    brand_terms = _brand_terms(doc)
    prompts = list(doc.get("prompts") or [])

    engine_names: list[str] = []
    for e in run.get("engines") or []:
        if e not in engine_names:
            engine_names.append(str(e))
    for prompt in prompts:
        for e in (prompt.get("engines") or {}):
            if e not in engine_names:
                engine_names.append(str(e))

    engine_stats = {e: _empty_engine_stats() for e in engine_names}
    vendor_all: dict[str, int] = {}
    call_counts = {"win": 0, "gap": 0, "trap": 0, "search-blind": 0}
    rows: list[dict[str, Any]] = []

    for prompt in prompts:
        view = _engine_view(prompt)
        call = _call_for(prompt, view)
        call_counts[call] = call_counts.get(call, 0) + 1
        raw = prompt.get("engines") or {}
        engines_out: dict[str, Any] = {}
        search_queries: dict[str, list[str]] = {}
        vendors_by_engine: dict[str, list[str]] = {}
        competitors_by_engine: dict[str, list[str]] = {}
        issues: list[str] = []

        for engine, arms in raw.items() if isinstance(raw, dict) else []:
            if not isinstance(arms, dict):
                continue
            stats = engine_stats.setdefault(engine, _empty_engine_stats())
            if engine not in engine_names:
                engine_names.append(engine)
            k = arms.get("knowledge")
            s = arms.get("search")
            k_pay = _arm_payload(k if isinstance(k, dict) else None, search=False, brand_terms=brand_terms)
            s_pay = _arm_payload(s if isinstance(s, dict) else None, search=True, brand_terms=brand_terms)
            if k_pay is not None:
                stats["cells"] += 1
                stats["knowledge_cells"] += 1
                stats["cost_usd"] += _usage_cost(k if isinstance(k, dict) else None)
                if _arm_ok(k) and k_pay["mentioned"]:
                    stats["knowledge_mentions"] += 1
                    issues.append("knowledge-mention")
            if s_pay is not None:
                stats["cells"] += 1
                stats["search_cells"] += 1
                stats["cost_usd"] += _usage_cost(s if isinstance(s, dict) else None)
                if _arm_ok(s):
                    if s_pay["mentioned"]:
                        stats["search_mentions"] += 1
                    if s_pay["searched"]:
                        stats["searched"] += 1
                    else:
                        stats["never_searched"] += 1
                        issues.append("never-searched")
                    if s_pay["prebelief"]:
                        stats["prebelief"] += 1
                        issues.append("prebelief")
                    if s_pay["discovery"]:
                        stats["discovery"] += 1
                    if s_pay["mention_without_search"]:
                        stats["mention_without_search"] += 1
                        issues.append("mention-without-search")
                    _bump_vendor(stats["vendor_counts"], s_pay.get("vendors") or [])
                    _bump_vendor(vendor_all, s_pay.get("vendors") or [])
                search_queries[engine] = list(s_pay.get("search_queries") or [])
                vendors_by_engine[engine] = list(s_pay.get("vendors") or [])
                comps = list(s_pay.get("competitor_mentions") or [])
                if not comps and k_pay is not None:
                    comps = list(k_pay.get("competitor_mentions") or [])
                competitors_by_engine[engine] = comps
            elif k_pay is not None:
                competitors_by_engine[engine] = list(k_pay.get("competitor_mentions") or [])
            engines_out[engine] = {}
            if k_pay is not None:
                engines_out[engine]["knowledge"] = k_pay
            if s_pay is not None:
                engines_out[engine]["search"] = s_pay
            if (k_pay and k_pay.get("mentioned")) or (s_pay and s_pay.get("mentioned")):
                issues.append("mention")

        row = {
            "prompt_id": prompt.get("prompt_id") or "",
            "prompt_text": prompt.get("prompt_text") or "",
            "class": prompt.get("class") or "",
            "why": prompt.get("why") or "",
            "call": call,
            "engines": engines_out,
            "search_queries": search_queries,
            "vendors": vendors_by_engine,
            "competitor_mentions": competitors_by_engine,
            "issues": sorted(set(issues)),
        }
        rows.append(row)

    search_cells = sum(s["search_cells"] for s in engine_stats.values())
    search_mentions = sum(s["search_mentions"] for s in engine_stats.values())
    searched = sum(s["searched"] for s in engine_stats.values())
    knowledge_mentions = sum(s["knowledge_mentions"] for s in engine_stats.values())
    never_searched = sum(s["never_searched"] for s in engine_stats.values())
    prebelief = sum(s["prebelief"] for s in engine_stats.values())
    discovery = sum(s["discovery"] for s in engine_stats.values())
    mws = sum(s["mention_without_search"] for s in engine_stats.values())
    cells = sum(s["cells"] for s in engine_stats.values())
    cost = sum(s["cost_usd"] for s in engine_stats.values())
    knowledge_cells = sum(s["knowledge_cells"] for s in engine_stats.values())

    generated_at = str(run.get("timestamp") or "")

    payload: dict[str, Any] = {
        "brand": brand,
        "domain": str(ws.get("domain") or ""),
        "aliases": list(ws.get("aliases") or []),
        "run_ids": str(run.get("run_id") or ""),
        "generated_at": generated_at,
        "schema_version": str(doc.get("schema_version") or "aeo-cli-evidence-v1"),
        "engines": engine_stats,
        "engine_order": engine_names,
        "overall": {
            "cells": cells,
            "knowledge_cells": knowledge_cells,
            "search_cells": search_cells,
            "knowledge_mentions": knowledge_mentions,
            "search_mentions": search_mentions,
            "mention_rate_search": (search_mentions / search_cells) if search_cells else 0.0,
            "search_rate": (searched / search_cells) if search_cells else 0.0,
            "searched": searched,
            "never_searched": never_searched,
            "prebelief": prebelief,
            "discovery": discovery,
            "search_blind": never_searched,
            "mention_without_search": mws,
            "cost_usd": cost,
            "vendor_counts": vendor_all,
        },
        "calls": call_counts,
        "rows": rows,
    }
    if generated_from_files:
        payload["generated_from_files"] = list(generated_from_files)
    return payload


def _is_payload(obj: dict[str, Any]) -> bool:
    return "rows" in obj and "overall" in obj and "brand" in obj


def render_html_report(
    payload_or_docs: dict[str, Any] | list[dict[str, Any]],
    generated_from_files: list[str] | None = None,
) -> str:
    if isinstance(payload_or_docs, list):
        payload = build_report_payload(
            merge_docs(payload_or_docs),
            generated_from_files=generated_from_files,
        )
    elif _is_payload(payload_or_docs):
        payload = payload_or_docs
        if generated_from_files and "generated_from_files" not in payload:
            payload = dict(payload)
            payload["generated_from_files"] = list(generated_from_files)
    else:
        payload = build_report_payload(
            payload_or_docs,
            generated_from_files=generated_from_files,
        )
    return _render(payload)


def _pct(n: int, d: int) -> float:
    return (100.0 * n / d) if d else 0.0


def _fmt_pct(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _fmt_money(v: float) -> str:
    return f"${v:.2f}"


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _bar(pct: float, kind: str = "teal") -> str:
    width = max(0.0, min(100.0, pct))
    return (
        f'<div class="bar"><i class="bar-fill {kind}" style="width:{width:.2f}%"></i>'
        f'<span class="bar-n">{width:.0f}%</span></div>'
    )


CALL_COPY = {
    "win": ("Named the brand", "Named the brand (check contamination)"),
    "gap": ("Searched, no mention", "Searched, still no brand"),
    "trap": ("Watch", "Watch query; miss is expected"),
    "search-blind": ("Never searched", "Search allowed, nobody searched"),
}


def _engine_label(name: str) -> str:
    raw = str(name or "").replace("_", " ").strip()
    return raw[:1].upper() + raw[1:] if raw else ""


def _mention_word(ok: bool | None) -> str:
    if ok is None:
        return "—"
    return "named" if ok else "miss"


def _search_used_word(s_pay: dict[str, Any] | None) -> str:
    if not s_pay:
        return "not run"
    if not s_pay.get("searched"):
        return "did not search"
    if s_pay.get("prebelief"):
        return "confirmation"
    if s_pay.get("discovery"):
        return "discovery"
    return "searched"


def _verdict_label(call: str) -> str:
    pair = CALL_COPY.get(call)
    return pair[0] if pair else str(call or "")


_SVG_DISC = (
    '<svg class="ico" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">'
    '<circle cx="7" cy="7" r="5" fill="currentColor"/></svg>'
)
_SVG_MISS = (
    '<svg class="ico" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">'
    '<circle cx="7" cy="7" r="5" fill="none" stroke="currentColor" stroke-width="1.35"/>'
    '<path d="M5.1 5.1l3.8 3.8M8.9 5.1l-3.8 3.8" stroke="currentColor" '
    'stroke-width="1.35" stroke-linecap="round"/></svg>'
)
_SVG_DASH = (
    '<svg class="ico" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">'
    '<path d="M3.2 7h7.6" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
)
_SVG_MAG = (
    '<svg class="ico mag" viewBox="0 0 14 14" width="14" height="14" aria-hidden="true">'
    '<circle cx="6.1" cy="6.1" r="3.5" fill="none" stroke="currentColor" stroke-width="1.45"/>'
    '<path d="M8.7 8.7L12 12" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>'
)


def _mark_mention(kind: str, pay: dict[str, Any] | None) -> str:
    letter = "K" if kind == "Knowledge" else "S"
    extra = ""
    if pay is None:
        state, tip, inner = "none", f"{kind}: not run", _SVG_DASH
    elif pay.get("mentioned"):
        state, tip, inner = "named", f"{kind}: named", letter
    else:
        state, tip, inner = "miss", f"{kind}: miss", letter
    if letter == "S" and pay and pay.get("mention_without_search"):
        extra = " mws"
        tip = f"{kind}: named (without search)" if pay.get("mentioned") else tip
    return f'<span class="mk {state}{extra}" title="{_esc(tip)}">{inner}</span>'


def _mark_search_used(s: dict[str, Any] | None) -> str:
    if s is None:
        state, tip, icon = "none", "Search: not run", _SVG_DASH
    elif not s.get("searched"):
        state, tip, icon = "nosearch", "Search: did not search", _SVG_MAG
    elif s.get("prebelief"):
        state, tip, icon = "confirm", "Search: confirmation (typed incumbent)", _SVG_MAG
    elif s.get("discovery"):
        state, tip, icon = "discover", "Search: discovery", _SVG_MAG
    else:
        state, tip, icon = "searched", "Search: searched", _SVG_MAG
    return f'<span class="mk q {state}" title="{_esc(tip)}">{icon}</span>'


def _engine_marks_html(k: dict[str, Any] | None, s: dict[str, Any] | None) -> str:
    return (
        f'<span class="marks">'
        f"{_mark_mention('Knowledge', k)}"
        f"{_mark_mention('Search mention', s)}"
        f"{_mark_search_used(s)}"
        f"</span>"
    )


def _row_vendors(row: dict[str, Any]) -> list[str]:
    vendors_map = row.get("vendors") or {}
    vendors_flat: list[str] = []
    seen: set[str] = set()
    for vs in vendors_map.values():
        for v in vs:
            key = str(v).lower()
            if key in seen:
                continue
            seen.add(key)
            vendors_flat.append(str(v))
    return vendors_flat


def _ranked_vendors(counts: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))


def _render(payload: dict[str, Any]) -> str:
    brand = payload.get("brand") or "brand"
    domain = payload.get("domain") or ""
    run_ids = payload.get("run_ids") or ""
    overall = payload.get("overall") or {}
    engines: dict[str, Any] = payload.get("engines") or {}
    order = list(payload.get("engine_order") or engines.keys())
    rows = payload.get("rows") or []
    calls = payload.get("calls") or {}
    files = payload.get("generated_from_files") or []
    generated_at = payload.get("generated_at") or ""
    schema = payload.get("schema_version") or "aeo-cli-evidence-v1"

    mention_rate = float(overall.get("mention_rate_search") or 0)
    search_rate = float(overall.get("search_rate") or 0)
    cells = int(overall.get("cells") or 0)
    cost = float(overall.get("cost_usd") or 0)
    has_cost = cost > 0

    title = f"{brand} · AEO report"
    vendor_max = max([n for _, n in _ranked_vendors(overall.get("vendor_counts") or {})] or [1])

    engine_vendor_json = {
        "all": dict(overall.get("vendor_counts") or {}),
        **{e: dict((engines.get(e) or {}).get("vendor_counts") or {}) for e in order},
    }

    classes = sorted({str(r.get("class")) for r in rows if r.get("class")})
    call_names = ("win", "gap", "trap", "search-blind")

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append(f"<title>{_esc(title)}</title>")
    parts.append("<style>")
    parts.append(_CSS)
    parts.append("</style></head><body>")

    parts.append('<header class="top">')
    parts.append('<div class="top-brand">')
    parts.append(f'<span class="wordmark">{_esc(brand)}</span>')
    if domain:
        parts.append(f'<span class="domain">{_esc(domain)}</span>')
    parts.append("</div>")
    parts.append('<div class="top-meta">')
    parts.append(f'<span class="pill">{_esc(run_ids or "run")}</span>')
    parts.append(f'<span class="pill tabular">{cells} cells</span>')
    if generated_at:
        parts.append(f'<span class="pill muted">{_esc(generated_at)}</span>')
    parts.append("</div></header>")

    parts.append("<main>")
    parts.append('<section class="hero">')
    sm = int(overall.get("search_mentions") or 0)
    sc = int(overall.get("search_cells") or 0)
    metric_cards = [
        ("Mention (S)", _fmt_pct(mention_rate), f"{sm} / {sc} search-arm mentions · all engines"),
        ("Search", _fmt_pct(search_rate), f"{int(overall.get('searched') or 0)} searched"),
        ("Confirm", str(int(overall.get("prebelief") or 0)), "typed an incumbent into the search box"),
        ("Discover", str(int(overall.get("discovery") or 0)), "no incumbent in the box"),
        ("Blind", str(int(overall.get("search_blind") or 0)), "search arm, did not search"),
        ("Dirty", str(int(overall.get("mention_without_search") or 0)), "mention without search · possible contamination"),
    ]
    if has_cost:
        metric_cards.append(("Spend", _fmt_money(cost), "sum of usage.cost_usd"))
    for label, value, hint in metric_cards:
        parts.append(
            f'<article class="metric"><p class="eyebrow">{_esc(label)}</p>'
            f'<p class="metric-n tabular">{_esc(value)}</p>'
            f'<p class="hint">{_esc(hint)}</p></article>'
        )
    parts.append("</section>")

    parts.append('<section class="engines">')
    parts.append("<h2>Engines</h2>")
    parts.append('<div class="engine-grid">')
    for engine in order:
        st = engines.get(engine) or _empty_engine_stats()
        kc = int(st.get("knowledge_cells") or 0)
        sc_e = int(st.get("search_cells") or 0)
        km = int(st.get("knowledge_mentions") or 0)
        sm_e = int(st.get("search_mentions") or 0)
        sr = int(st.get("searched") or 0)
        pb = int(st.get("prebelief") or 0)
        parts.append('<article class="card engine">')
        parts.append(f'<header><h3>{_esc(engine)}</h3><span class="pill tabular">{int(st.get("cells") or 0)} cells</span></header>')
        parts.append(
            f'<div class="stat-line"><span>Knowledge mention</span>'
            f'<span class="tabular">{km}/{kc}</span></div>'
        )
        parts.append(_bar(_pct(km, kc), "amber"))
        parts.append(
            f'<div class="stat-line"><span>Search mention</span>'
            f'<span class="tabular">{sm_e}/{sc_e}</span></div>'
        )
        parts.append(_bar(_pct(sm_e, sc_e), "teal"))
        parts.append(
            f'<div class="stat-line"><span>Search fired</span>'
            f'<span class="tabular">{sr}/{sc_e}</span></div>'
        )
        parts.append(_bar(_pct(sr, sc_e), "teal"))
        parts.append(
            f'<div class="stat-line"><span>Confirmation share</span>'
            f'<span class="tabular">{pb}/{sr or sc_e}</span></div>'
        )
        parts.append(_bar(_pct(pb, sr) if sr else 0.0, "amber"))
        extras = [
            f'{int(st.get("discovery") or 0)} discovery',
            f'{int(st.get("never_searched") or 0)} never searched',
            f'{int(st.get("mention_without_search") or 0)} mention w/o search',
        ]
        if float(st.get("cost_usd") or 0) > 0:
            extras.append(_fmt_money(float(st["cost_usd"])))
        parts.append(f'<p class="hint">{_esc(" · ".join(extras))}</p>')
        parts.append("</article>")
    parts.append("</div></section>")

    parts.append('<section class="vendors">')
    parts.append("<h2>Vendor fan-out</h2>")
    parts.append('<p class="blurb">Names typed into the search box (search arm <code>vendors_in_search_queries</code>), ranked.</p>')
    parts.append('<div class="chips" id="vendor-engines">')
    parts.append('<button type="button" class="chip on" data-engine="all">all</button>')
    for engine in order:
        parts.append(f'<button type="button" class="chip" data-engine="{_esc(engine)}">{_esc(engine)}</button>')
    parts.append("</div>")
    parts.append('<div id="vendor-bars" class="vendor-bars">')
    for name, n in _ranked_vendors(overall.get("vendor_counts") or {}):
        w = 100.0 * n / vendor_max
        parts.append(
            f'<div class="vrow" data-n="{n}"><span class="vname">{_esc(name)}</span>'
            f'<div class="bar"><i class="bar-fill teal" style="width:{w:.2f}%"></i></div>'
            f'<span class="tabular vcount">{n}</span></div>'
        )
    if not (overall.get("vendor_counts") or {}):
        parts.append('<p class="hint">No vendors recorded in search queries.</p>')
    parts.append("</div></section>")

    parts.append('<section class="calls">')
    parts.append("<h2>Calls</h2>")
    parts.append('<div class="call-strip">')
    for key in call_names:
        n = int(calls.get(key) or 0)
        title, _meaning = CALL_COPY[key]
        parts.append(
            f'<article class="call {key}"><p class="metric-n tabular">{n}</p>'
            f'<p class="hint">{_esc(title)}</p></article>'
        )
    parts.append("</div></section>")

    parts.append('<section class="prompts">')
    parts.append("<h2>Prompts</h2>")
    parts.append('<div class="toolbar">')
    parts.append('<input type="search" id="q" placeholder="Filter prompts…" autocomplete="off">')
    parts.append('<div class="chips" id="call-chips">')
    for key in call_names:
        parts.append(
            f'<button type="button" class="chip" data-call="{_esc(key)}">{_esc(_verdict_label(key))}</button>'
        )
    parts.append("</div>")
    parts.append('<div class="chips" id="class-chips">')
    for cls in classes:
        parts.append(f'<button type="button" class="chip" data-class="{_esc(cls)}">{_esc(cls)}</button>')
    parts.append("</div>")
    parts.append('<div class="chips" id="issue-chips">')
    for key, label in (
        ("never-searched", "never searched"),
        ("prebelief", "confirmation"),
        ("mention", "mention"),
        ("mention-without-search", "mention without search"),
        ("knowledge-mention", "knowledge mention"),
    ):
        parts.append(f'<button type="button" class="chip" data-issue="{key}">{label}</button>')
    parts.append("</div>")
    parts.append('<div class="chips" id="sort-chips">')
    parts.append('<button type="button" class="chip" data-sort="query">Sort: query</button>')
    parts.append('<button type="button" class="chip" data-sort="call">Sort: verdict</button>')
    parts.append("</div></div>")
    parts.append('<p class="hint" id="shown-count"></p>')

    parts.append('<div class="table-legend" role="note">')
    parts.append('<span class="leg-item"><b class="leg-k">K</b> knowledge mention</span>')
    parts.append('<span class="leg-dot">·</span>')
    parts.append('<span class="leg-item"><b class="leg-k">S</b> search mention</span>')
    parts.append('<span class="leg-dot">·</span>')
    parts.append('<span class="leg-item"><b class="leg-k">Q</b> searched</span>')
    parts.append('<span class="leg-gap"></span>')
    parts.append('<span class="leg-chip named">named</span>')
    parts.append('<span class="leg-chip miss">miss</span>')
    parts.append('<span class="leg-chip confirm">confirmation</span>')
    parts.append('<span class="leg-chip discover">discovery</span>')
    parts.append('<span class="leg-chip nosearch">no search</span>')
    parts.append('<span class="leg-gap"></span>')
    parts.append('<span class="leg-calls">Call')
    for key in call_names:
        parts.append(f'<span class="leg-call"><i class="cdot {key}"></i> {_esc(key)}</span>')
    parts.append("</span></div>")

    ncol = 1 + len(order) + 2
    parts.append('<div class="table-wrap" id="prompt-list">')
    parts.append('<table class="prompt-table">')
    parts.append("<thead><tr>")
    parts.append("<th>Query</th>")
    for engine in order:
        parts.append(f"<th>{_esc(_engine_label(engine))}</th>")
    parts.append("<th>Vendors</th>")
    parts.append("<th>Call</th>")
    parts.append("</tr></thead><tbody>")
    for i, row in enumerate(rows):
        issues = " ".join(row.get("issues") or [])
        vendors_flat = _row_vendors(row)
        vendor_txt = ", ".join(vendors_flat)
        qtext = row.get("prompt_text") or row.get("prompt_id") or ""
        call = row.get("call") or ""
        cls = row.get("class") or ""
        parts.append(
            f'<tr class="prompt-row { _esc(call) }" data-i="{i}" data-call="{_esc(call)}" '
            f'data-class="{_esc(cls)}" data-issues="{_esc(issues)}" '
            f'data-q="{_esc(qtext.lower())}" data-vendors="{_esc(vendor_txt.lower())}">'
        )
        parts.append(
            f'<td class="qcell"><button type="button" class="expand" aria-expanded="false" '
            f'title="search_queries">▸</button>'
            f'<span class="prompt-q">{_esc(qtext)}</span></td>'
        )
        for engine in order:
            ev = (row.get("engines") or {}).get(engine) or {}
            parts.append(f'<td class="eng">{_engine_marks_html(ev.get("knowledge"), ev.get("search"))}</td>')
        parts.append(
            f'<td class="vendors" title="{_esc(vendor_txt)}">{_esc(vendor_txt)}</td>'
        )
        parts.append(
            f'<td class="callcell"><span class="call-badge {_esc(call)}">{_esc(_verdict_label(call))}</span></td>'
        )
        parts.append("</tr>")
        parts.append(f'<tr class="detail-row" hidden data-i="{i}"><td colspan="{ncol}">')
        parts.append('<div class="detail-grid">')
        for engine in order:
            ev = (row.get("engines") or {}).get(engine) or {}
            s = ev.get("search") or {}
            k = ev.get("knowledge") or {}
            qs = s.get("search_queries") or (row.get("search_queries") or {}).get(engine) or []
            comps = (row.get("competitor_mentions") or {}).get(engine) or s.get("competitor_mentions") or []
            parts.append(f'<div class="detail-col"><h4>{_esc(_engine_label(engine))}</h4>')
            if qs:
                parts.append("<p class='k'>search_queries</p><ul>")
                for qv in qs:
                    parts.append(f"<li><code>{_esc(qv)}</code></li>")
                parts.append("</ul>")
            else:
                parts.append("<p class='hint'>no search_queries</p>")
            if comps:
                parts.append(
                    f"<p class='k'>competitor_mentions</p><p>{_esc(', '.join(str(c) for c in comps))}</p>"
                )
            bm = list((s.get("brand_mentions") or []) or (k.get("brand_mentions") or []))
            if bm:
                parts.append(f"<p class='k'>brand_mentions</p><p>{_esc(', '.join(str(x) for x in bm))}</p>")
            snippet = s.get("snippet") or k.get("snippet") or ""
            if snippet:
                parts.append(f"<p class='k'>answer (truncated)</p><p class='snip'>{_esc(snippet)}</p>")
            parts.append("</div>")
        parts.append("</div></td></tr>")
    parts.append("</tbody></table></div></section>")


    parts.append("<footer>")
    parts.append(f"<p>schema {_esc(schema)}</p>")
    if files:
        parts.append(f"<p>generated from {_esc(', '.join(files))}</p>")
    parts.append(
        "<p>Methodology: knowledge-only vs search-allowed. Confirmation (prebelief) is a search "
        "whose tool-call vendors are nonempty and none match brand aliases. Discovery is a search "
        "with no incumbent in the box (empty vendors, or only the brand). "
        "Search-blind is a search arm that ran but did not search. "
        "Do not treat mention-without-search as a win.</p>"
    )
    parts.append("</footer></main>")
    parts.append('<script type="application/json" id="vendor-data">')
    dumped = json.dumps(engine_vendor_json, ensure_ascii=False)
    parts.append(dumped.replace("<", "\\u003c"))
    parts.append("</script>")
    parts.append("<script>")
    parts.append(_JS)
    parts.append("</script></body></html>\n")
    return "\n".join(parts)


_CSS = """
:root{
  --bg:#0b0d10;--card:#14181e;--card2:#1a2028;--line:rgba(255,255,255,.08);
  --text:#e8edf2;--muted:#8b95a3;--teal:#3dccc7;--amber:#e8b86d;--rose:#e07a7a;
  --win:#6ee7b7;--gap:#7eb6ff;--trap:#c4b5fd;--blind:#94a3b8;
  --shadow:0 20px 60px rgba(0,0,0,.35);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
  font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-feature-settings:"tnum" 1;font-variant-numeric:tabular-nums;
  line-height:1.45;overflow-x:clip}
.tabular{font-variant-numeric:tabular-nums}
.top{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;
  align-items:center;gap:16px;padding:14px 28px;background:rgba(11,13,16,.88);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--line)}
.wordmark{font-size:15px;letter-spacing:.14em;text-transform:uppercase;font-weight:650}
.domain{margin-left:10px;color:var(--muted);font-size:13px}
.top-meta{display:flex;gap:8px;flex-wrap:wrap}
.pill{border:1px solid var(--line);background:var(--card);border-radius:999px;
  padding:3px 10px;font-size:12px;color:var(--muted)}
main{max-width:1280px;margin:0 auto;padding:36px 28px 80px}
h2{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);
  font-weight:600;margin:40px 0 14px}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(132px,1fr));gap:15px;
  align-items:stretch;margin:0}
.metric,.card,.call{background:var(--card);border:1px solid var(--line);
  border-radius:16px;box-shadow:var(--shadow)}
.eyebrow{margin:0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted);
  white-space:nowrap}
.sub,.hint,.blurb{color:var(--muted);font-size:13px}
.hint{overflow-wrap:anywhere;white-space:normal}
.metric{padding:16px 18px;min-height:132px;display:flex;flex-direction:column}
.metric-n{margin:10px 0 6px;font-size:28px;font-weight:620;letter-spacing:-.03em;line-height:1}
.metric .hint{margin:auto 0 0}
.engine-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}
.engine{padding:18px}
.engine header{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.engine h3{margin:0;font-size:16px;font-weight:620;text-transform:capitalize}
.stat-line{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin:10px 0 4px}
.bar{position:relative;height:8px;background:#0e1116;border-radius:99px;overflow:hidden;border:1px solid var(--line)}
.bar-fill{display:block;height:100%;border-radius:99px}
.bar-fill.teal{background:linear-gradient(90deg,#1d9b96,var(--teal))}
.bar-fill.amber{background:linear-gradient(90deg,#b8863a,var(--amber))}
.bar-n{display:none}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.chip{border:1px solid var(--line);background:transparent;color:var(--text);
  border-radius:999px;padding:4px 11px;font-size:12px;cursor:pointer}
.chip.on{background:rgba(61,204,199,.14);border-color:rgba(61,204,199,.45);color:var(--teal)}
.vendor-bars{display:flex;flex-direction:column;gap:8px;margin-top:12px}
.vrow{display:grid;grid-template-columns:160px 1fr 40px;gap:10px;align-items:center}
.vname{font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.vcount{text-align:right;color:var(--muted);font-size:12px}
.call-strip{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.call{padding:16px}
.call-meaning{margin:8px 0 4px;font-size:14px;line-height:1.35;font-weight:560}
.table-legend{position:sticky;top:52px;z-index:16;display:flex;flex-wrap:nowrap;
  align-items:center;gap:8px;margin:0 0 8px;padding:7px 12px;overflow-x:auto;
  background:rgba(20,24,30,.96);backdrop-filter:blur(12px);border:1px solid var(--line);
  border-radius:10px;font-size:11px;color:var(--muted);white-space:nowrap}
.table-legend .leg-item{display:inline-flex;align-items:center;gap:5px}
.table-legend .leg-dot{opacity:.5}
.table-legend .leg-gap{width:8px;flex:0 0 8px}
.leg-chip{display:inline-flex;align-items:center;gap:4px;padding:1px 7px;border-radius:999px;
  border:1px solid var(--line);font-size:11px}
.leg-chip.named{color:#0d2b28;background:var(--teal);border-color:transparent}
.leg-chip.miss{color:var(--rose);background:rgba(224,122,122,.12)}
.leg-chip.confirm{color:#2a1f0a;background:var(--amber);border-color:transparent}
.leg-chip.discover{color:#0d2b28;background:rgba(61,204,199,.28);border-color:rgba(61,204,199,.45)}
.leg-chip.nosearch{color:var(--muted)}
.leg-calls{display:inline-flex;align-items:center;gap:8px;margin-left:4px}
.leg-call{display:inline-flex;align-items:center;gap:4px}
.cdot{display:inline-block;width:7px;height:7px;border-radius:50%}
.cdot.win{background:var(--win)}
.cdot.gap{background:var(--gap)}
.cdot.trap{background:var(--trap)}
.cdot.search-blind{background:var(--blind)}
.call.win{border-color:rgba(110,231,183,.25)}
.call.gap{border-color:rgba(126,182,255,.25)}
.call.trap{border-color:rgba(196,181,253,.25)}
.call.search-blind{border-color:rgba(148,163,184,.25)}
.toolbar{display:flex;flex-direction:column;gap:8px;margin-bottom:12px}
#q{width:100%;max-width:420px;background:#0e1116;border:1px solid var(--line);
  color:var(--text);border-radius:10px;padding:9px 12px;font:inherit}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}
.prompt-table{width:100%;border-collapse:collapse;font-size:12.5px}
.prompt-table th{text-align:left;font-size:11px;letter-spacing:.08em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding:7px 8px;border-bottom:1px solid var(--line);
  background:#12161c}
.prompt-table td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
.prompt-table td.eng{width:1%;white-space:nowrap;padding:5px 6px}
.prompt-q{display:inline;font-size:12.5px;font-weight:520;line-height:1.35;overflow-wrap:anywhere}
.qcell{min-width:16em;max-width:32em}
.vendors{max-width:12em;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--muted)}
.callcell{white-space:nowrap;width:1%}
tr.prompt-row.hidden,tr.detail-row.hidden{display:none}
tr.prompt-row.win td:first-child{box-shadow:inset 3px 0 0 var(--win)}
tr.prompt-row.gap td:first-child{box-shadow:inset 3px 0 0 var(--gap)}
tr.prompt-row.trap td:first-child{box-shadow:inset 3px 0 0 var(--trap)}
tr.prompt-row.search-blind td:first-child{box-shadow:inset 3px 0 0 var(--blind)}
tr.prompt-row:hover td{background:rgba(255,255,255,.02)}
.marks{display:inline-flex;align-items:center;gap:4px}
.mk{position:relative;display:inline-flex;align-items:center;justify-content:center;
  width:14px;height:14px;border-radius:50%;font-size:8px;font-weight:750;line-height:1;
  color:var(--muted)}
.mk.named{background:var(--teal);color:#042220}
.mk.miss{background:rgba(224,122,122,.18);color:#c46b6b}
.mk.none{color:#5a6470}
.mk.mws{box-shadow:0 0 0 1.5px var(--rose)}
.leg-k{display:inline-flex;align-items:center;justify-content:center;width:14px;height:14px;
  border-radius:50%;background:var(--teal);color:#042220;font-size:8px;font-weight:750}
.mk.q.discover,.mk.q.searched{color:var(--teal)}
.mk.q.confirm{color:var(--amber)}
.mk.q.nosearch{color:#5a6470}
.mk.q.none{color:#5a6470}
.ico{display:block}
button.expand{border:0;background:transparent;color:var(--muted);padding:0 6px 0 0;
  font-size:10px;cursor:pointer;line-height:1}
button.expand[aria-expanded="true"]{color:var(--teal)}
.call-badge{display:inline-block;flex:0 0 auto;padding:2px 8px;border-radius:999px;font-size:11px;
  border:1px solid var(--line);text-transform:none;white-space:nowrap}
.call-badge.win{color:var(--win)}
.call-badge.gap{color:var(--gap)}
.call-badge.trap{color:var(--trap)}
.call-badge.search-blind{color:var(--blind)}
.detail-row td{padding:10px 12px;background:#10141a}
.detail-grid{display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:16px;padding:4px 0 4px}
.detail-col h4{margin:0 0 8px;text-transform:capitalize}
.detail-col .k{margin:10px 0 4px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.snip{white-space:pre-wrap;color:#c7d0da;font-size:12.5px}
code{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
footer p{max-width:78ch}
@media (max-width:800px){
  .hero{grid-template-columns:repeat(2,1fr)}
  .call-strip{grid-template-columns:1fr 1fr}
  .vrow{grid-template-columns:1fr 2fr 32px}
}
"""

_JS = r"""
(function(){
  const q = document.getElementById('q');
  const cards = Array.from(document.querySelectorAll('tr.prompt-row'));
  const list = document.querySelector('#prompt-list tbody');
  const shown = document.getElementById('shown-count');
  const callOn = new Set();
  const classOn = new Set();
  const issueOn = new Set();

  function apply(){
    const needle = (q && q.value || '').trim().toLowerCase();
    let n = 0;
    cards.forEach(function(card){
      const okQ = !needle || (card.getAttribute('data-q')||'').indexOf(needle) >= 0
        || (card.getAttribute('data-vendors')||'').indexOf(needle) >= 0;
      const call = card.getAttribute('data-call') || '';
      const cls = card.getAttribute('data-class') || '';
      const issues = (card.getAttribute('data-issues') || '').split(/\s+/);
      const okCall = !callOn.size || callOn.has(call);
      const okCls = !classOn.size || classOn.has(cls);
      const okIss = !issueOn.size || issues.some(function(i){ return issueOn.has(i); });
      const vis = okQ && okCall && okCls && okIss;
      card.classList.toggle('hidden', !vis);
      const d = card.nextElementSibling;
      if(d && d.classList.contains('detail-row')){
        d.classList.toggle('hidden', !vis);
        if(!vis) d.hidden = true;
      }
      if(vis) n++;
    });
    if(shown) shown.textContent = n + ' / ' + cards.length + ' prompts';
  }

  function toggleSet(set, value, btn){
    if(set.has(value)){ set.delete(value); btn.classList.remove('on'); }
    else { set.add(value); btn.classList.add('on'); }
    apply();
  }
  document.querySelectorAll('#call-chips .chip').forEach(function(btn){
    btn.addEventListener('click', function(){ toggleSet(callOn, btn.getAttribute('data-call'), btn); });
  });
  document.querySelectorAll('#class-chips .chip').forEach(function(btn){
    btn.addEventListener('click', function(){ toggleSet(classOn, btn.getAttribute('data-class'), btn); });
  });
  document.querySelectorAll('#issue-chips .chip').forEach(function(btn){
    btn.addEventListener('click', function(){ toggleSet(issueOn, btn.getAttribute('data-issue'), btn); });
  });
  if(q) q.addEventListener('input', apply);

  document.querySelectorAll('button.expand').forEach(function(btn){
    btn.addEventListener('click', function(){
      const card = btn.closest('tr.prompt-row');
      const d = card && card.nextElementSibling;
      if(!d || !d.classList.contains('detail-row')) return;
      const open = d.hidden;
      d.hidden = !open;
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  });

  document.querySelectorAll('#sort-chips .chip').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('#sort-chips .chip').forEach(function(b){ b.classList.remove('on'); });
      btn.classList.add('on');
      const key = btn.getAttribute('data-sort');
      const dir = btn.dataset.dir === 'asc' ? -1 : 1;
      btn.dataset.dir = dir === 1 ? 'asc' : 'desc';
      const ordered = cards.slice().sort(function(a,b){
        let va, vb;
        if(key === 'call'){ va = a.getAttribute('data-call'); vb = b.getAttribute('data-call'); }
        else { va = a.getAttribute('data-q'); vb = b.getAttribute('data-q'); }
        return String(va||'').localeCompare(String(vb||'')) * dir;
      });
      ordered.forEach(function(card){
        const det = card.nextElementSibling;
        list.appendChild(card);
        if(det && det.classList.contains('detail-row')) list.appendChild(det);
      });
    });
  });

  const vendorHost = document.getElementById('vendor-bars');
  const vendorDataEl = document.getElementById('vendor-data');
  let vendorData = {};
  try { vendorData = JSON.parse(vendorDataEl && vendorDataEl.textContent || '{}'); } catch(e) { vendorData = {}; }
  document.querySelectorAll('#vendor-engines .chip').forEach(function(btn){
    btn.addEventListener('click', function(){
      document.querySelectorAll('#vendor-engines .chip').forEach(function(b){ b.classList.remove('on'); });
      btn.classList.add('on');
      const key = btn.getAttribute('data-engine');
      const counts = vendorData[key] || {};
      const items = Object.keys(counts).map(function(k){ return [k, counts[k]]; })
        .sort(function(a,b){ return b[1]-a[1] || a[0].localeCompare(b[0]); });
      const max = items.reduce(function(m, it){ return Math.max(m, it[1]); }, 1);
      if(!vendorHost) return;
      if(!items.length){ vendorHost.innerHTML = '<p class="hint">No vendors recorded in search queries.</p>'; return; }
      vendorHost.innerHTML = items.map(function(it){
        const w = (100 * it[1] / max).toFixed(2);
        return '<div class="vrow"><span class="vname"></span><div class="bar"><i class="bar-fill teal" style="width:'+w+'%"></i></div><span class="tabular vcount">'+it[1]+'</span></div>';
      }).join('');
      Array.from(vendorHost.querySelectorAll('.vname')).forEach(function(el, i){ el.textContent = items[i][0]; });
    });
  });
  apply();
})();
"""
