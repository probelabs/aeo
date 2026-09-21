"""Diff two completed AEO runs: brand rates, prompt transitions, vendor fan-out.

Deterministic. `vendors_judged.json` is preferred when present; otherwise
regex `competitor_mentions` / `vendors_in_search_queries` are the fallback.
One side may lack the vendor store (typical for an older baseline).
"""

from __future__ import annotations

import html
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aeo.vendors import (
    ARMS,
    classified_query_vendors_for_arm,
    classified_vendors_for_arm,
    load_vendor_store,
    seed_alias_map,
    workspace_from_docs,
)

SCHEMA_VERSION = "aeo-change-v1"
DEFAULT_ENGINES = ("claude", "codex", "grok")
SKIP_JSON = frozenset(
    {
        "judge.json",
        "board.json",
        "vendors_judged.json",
        "change.json",
    }
)

TRANSITIONS = ("miss_to_hit", "hit_to_miss", "hit_to_hit", "still_miss")
TRANSITION_LABELS = {
    "miss_to_hit": "miss → hit",
    "hit_to_miss": "hit → miss",
    "hit_to_hit": "hit → hit",
    "still_miss": "still miss",
}

# Competitor dynamics. A name is absent / OUT when mentions < floor (default 1 = count==0).
# `--floor 2` treats a single leftover mention as near-zero, not still ranking.
DEFAULT_FLOOR = 1
DEFAULT_TOP_N = 15
DEFAULT_TOP_MOVERS = 10
# HTML / scannable lists. change.json keeps the full NEW/OUT arrays.
DEFAULT_TOP_LIST = 20

# Search-index hint for practical takeaways (deterministic, not a live lookup).
ENGINE_SEARCH_HINT = {
    "claude": "Brave/Claude",
    "codex": "Bing/Codex",
    "grok": "Grok",
}


def _esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return raw if isinstance(raw, dict) else {}


def _docs_from_evidence(doc: dict[str, Any], hint: str = "") -> dict[str, dict[str, Any]]:
    found: set[str] = set()
    for pr in doc.get("prompts") or []:
        found.update((pr.get("engines") or {}).keys())
    for e in (doc.get("run") or {}).get("engines") or []:
        found.add(str(e))
    docs: dict[str, dict[str, Any]] = {}
    for e in found:
        docs[e] = doc
    stem = hint.lower()
    if stem in found or stem in DEFAULT_ENGINES:
        docs[stem] = doc
    if not docs and stem:
        docs[stem] = doc
    return docs


def _engine_order(*doc_maps: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for e in DEFAULT_ENGINES:
        if any(e in m for m in doc_maps):
            out.append(e)
            seen.add(e)
    extras: list[str] = []
    for m in doc_maps:
        for e in m:
            if e not in seen:
                extras.append(e)
                seen.add(e)
    extras.sort()
    return out + extras


def merge_rows(docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per prompt_id; union engine arms (same shape as the judge HTML)."""
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for engine, doc in docs.items():
        for pr in doc.get("prompts") or []:
            pid = str(pr.get("prompt_id") or "")
            if not pid:
                continue
            if pid not in by_id:
                by_id[pid] = {
                    "prompt_id": pid,
                    "prompt_text": pr.get("prompt_text") or pid,
                    "class": pr.get("class") or "",
                    "why": pr.get("why") or "",
                    "engines": {},
                }
                order.append(pid)
            elif not by_id[pid].get("prompt_text") or by_id[pid]["prompt_text"] == pid:
                if pr.get("prompt_text"):
                    by_id[pid]["prompt_text"] = pr["prompt_text"]
            if pr.get("class") and not by_id[pid].get("class"):
                by_id[pid]["class"] = pr["class"]
            by_id[pid]["engines"][engine] = pr.get("engines", {}).get(engine) or {}
    return [by_id[i] for i in order]


def _cell_state(arm: Any) -> str:
    if not isinstance(arm, dict):
        return "missing"
    if arm.get("error"):
        return "error"
    return "ok"


def _is_hit(arm: dict[str, Any]) -> bool:
    return bool(arm.get("brand_mentioned"))


def _rate(hits: int, n: int) -> float | None:
    if n <= 0:
        return None
    return hits / n


def _delta_pp(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round((after - before) * 100.0, 2)


def _pct_label(rate: float | None) -> str:
    if rate is None:
        return "—"
    return f"{rate * 100:.1f}%"


def _pp_label(delta: float | None) -> str:
    if delta is None:
        return "—"
    sign = "+" if delta > 0 else ""
    return f"{sign}{delta:.1f}pp"


def _vendor_source(vendors: dict[str, Any]) -> str:
    return "vendors_judged" if vendors else "regex"


@dataclass
class RunSnapshot:
    path: Path
    label: str
    docs: dict[str, dict[str, Any]]
    rows: list[dict[str, Any]]
    judge: dict[str, Any]
    vendors: dict[str, Any]
    brand: str
    aliases: list[str]
    competitors: list[str]
    run_ids: list[str] = field(default_factory=list)
    timestamps: list[str] = field(default_factory=list)

    @property
    def vendor_source(self) -> str:
        return _vendor_source(self.vendors)

    @property
    def prompt_ids(self) -> set[str]:
        return {str(r["prompt_id"]) for r in self.rows}


def load_run(path: Path) -> RunSnapshot:
    """Load a run directory or a single evidence JSON (+ sibling judge/vendors)."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"run not found: {path}")

    docs: dict[str, dict[str, Any]] = {}
    if path.is_file():
        doc = json.loads(path.read_text())
        if not isinstance(doc, dict):
            raise ValueError(f"not an evidence object: {path}")
        docs = _docs_from_evidence(doc, hint=path.stem)
        out_dir = path.parent
        label = path.stem
    else:
        out_dir = path
        label = path.name
        for engine in DEFAULT_ENGINES:
            p = path / f"{engine}.json"
            if p.exists():
                raw = json.loads(p.read_text())
                if isinstance(raw, dict):
                    docs[engine] = raw
        if not docs:
            for p in sorted(path.glob("*.json")):
                if p.name in SKIP_JSON or "vendors_judged" in p.name:
                    continue
                raw = json.loads(p.read_text())
                if isinstance(raw, dict) and raw.get("prompts") is not None:
                    docs.update(_docs_from_evidence(raw, hint=p.stem))
                    if docs:
                        break

    judge = _read_json(out_dir / "judge.json")
    vendors_raw = _read_json(out_dir / "vendors_judged.json")
    if path.is_file():
        sibling = path.with_name(path.stem + ".vendors_judged.json")
        if sibling.exists():
            vendors_raw = _read_json(sibling)

    vendors = load_vendor_store(vendors_raw)
    rows = merge_rows(docs)
    ws_brand, aliases, competitors = workspace_from_docs(docs)
    run_ids: list[str] = []
    timestamps: list[str] = []
    for doc in docs.values():
        run = doc.get("run") or {}
        rid = run.get("run_id")
        if rid and str(rid) not in run_ids:
            run_ids.append(str(rid))
        ts = run.get("timestamp")
        if ts and str(ts) not in timestamps:
            timestamps.append(str(ts))
    return RunSnapshot(
        path=path,
        label=label,
        docs=docs,
        rows=rows,
        judge=judge,
        vendors=vendors,
        brand=ws_brand,
        aliases=aliases,
        competitors=competitors,
        run_ids=run_ids,
        timestamps=timestamps,
    )


def _arm_of(row: dict[str, Any], engine: str, arm_name: str) -> Any:
    return (row.get("engines") or {}).get(engine, {}).get(arm_name)


def brand_rates(snapshot: RunSnapshot, engines: list[str]) -> dict[str, Any]:
    """Per-engine × arm mention rates plus search_rate, computed from cells."""
    out: dict[str, Any] = {}
    for engine in engines:
        rec: dict[str, Any] = {}
        for arm_name in ARMS:
            hits = n = 0
            for row in snapshot.rows:
                arm = _arm_of(row, engine, arm_name)
                if _cell_state(arm) != "ok":
                    continue
                n += 1
                if _is_hit(arm):
                    hits += 1
            rec[arm_name] = {"hits": hits, "n": n, "rate": _rate(hits, n)}
        searched = search_n = 0
        for row in snapshot.rows:
            arm = _arm_of(row, engine, "search")
            if _cell_state(arm) != "ok":
                continue
            search_n += 1
            if arm.get("searched"):
                searched += 1
        rec["search_rate"] = {
            "searched": searched,
            "n": search_n,
            "rate": _rate(searched, search_n),
        }
        out[engine] = rec
    return out


def _pool(engine_rates: dict[str, Any], field: str) -> dict[str, Any]:
    hits = n = 0
    if field == "search_rate":
        for rec in engine_rates.values():
            sr = rec.get("search_rate") or {}
            hits += int(sr.get("searched") or 0)
            n += int(sr.get("n") or 0)
        return {"searched": hits, "n": n, "rate": _rate(hits, n)}
    for rec in engine_rates.values():
        arm = rec.get(field) or {}
        hits += int(arm.get("hits") or 0)
        n += int(arm.get("n") or 0)
    return {"hits": hits, "n": n, "rate": _rate(hits, n)}


def _rate_pair(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "baseline": before,
        "current": after,
        "delta_pp": _delta_pp(before.get("rate"), after.get("rate")),
    }


def diff_brand_rates(
    baseline: RunSnapshot,
    current: RunSnapshot,
    engines: list[str],
    *,
    comparable: list[str] | None = None,
) -> dict[str, Any]:
    """Per-engine rates for every listed engine; blended overall uses comparable only.

    When `comparable` is omitted, overall still pools `engines` (legacy). Pass the
    coverage intersection so a baseline-only engine cannot fake a blended drop.
    """
    b = brand_rates(baseline, engines)
    c = brand_rates(current, engines)
    by_engine: dict[str, Any] = {}
    for engine in engines:
        br = b.get(engine) or {}
        cr = c.get(engine) or {}
        by_engine[engine] = {
            "knowledge": _rate_pair(
                br.get("knowledge") or {"hits": 0, "n": 0, "rate": None},
                cr.get("knowledge") or {"hits": 0, "n": 0, "rate": None},
            ),
            "search": _rate_pair(
                br.get("search") or {"hits": 0, "n": 0, "rate": None},
                cr.get("search") or {"hits": 0, "n": 0, "rate": None},
            ),
            "search_rate": _rate_pair(
                br.get("search_rate") or {"searched": 0, "n": 0, "rate": None},
                cr.get("search_rate") or {"searched": 0, "n": 0, "rate": None},
            ),
        }
    pool = list(comparable) if comparable is not None else list(engines)
    pool_set = set(pool)
    b_pool = {e: rec for e, rec in b.items() if e in pool_set}
    c_pool = {e: rec for e, rec in c.items() if e in pool_set}
    skipped = [e for e in engines if e not in pool_set]
    overall = {
        "knowledge": _rate_pair(_pool(b_pool, "knowledge"), _pool(c_pool, "knowledge")),
        "search": _rate_pair(_pool(b_pool, "search"), _pool(c_pool, "search")),
        "search_rate": _rate_pair(_pool(b_pool, "search_rate"), _pool(c_pool, "search_rate")),
    }
    return {
        "engines": by_engine,
        "overall": overall,
        "compared_engines": pool,
        "skipped_engines": skipped,
        "blend_note": (
            "Overall / blended rates use engines present on both sides only. "
            "Per-engine rows still show a skipped engine."
            if skipped
            else "Overall rates pool every listed engine (coverage matches)."
        ),
    }


def _judge_fields(judge: dict[str, Any], key: str) -> dict[str, Any] | None:
    rec = judge.get(key)
    if not isinstance(rec, dict):
        return None
    stance = rec.get("stance") or ""
    position = rec.get("position") or ""
    if not stance and not position:
        return None
    return {"stance": stance, "position": position, "quote": rec.get("quote") or ""}


def prompt_transitions(
    baseline: RunSnapshot,
    current: RunSnapshot,
    engines: list[str],
) -> dict[str, Any]:
    base_by = {r["prompt_id"]: r for r in baseline.rows}
    cur_by = {r["prompt_id"]: r for r in current.rows}
    matched = [pid for pid in (r["prompt_id"] for r in current.rows) if pid in base_by]
    # keep baseline-only order for unmatched listing
    unmatched = {
        "baseline_only": sorted(baseline.prompt_ids - current.prompt_ids),
        "current_only": sorted(current.prompt_ids - baseline.prompt_ids),
    }
    rows: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    stance_changed = position_changed = 0
    judge_both = 0

    for pid in matched:
        brow = base_by[pid]
        crow = cur_by[pid]
        for engine in engines:
            for arm_name in ARMS:
                barm = _arm_of(brow, engine, arm_name)
                carm = _arm_of(crow, engine, arm_name)
                bstate = _cell_state(barm)
                cstate = _cell_state(carm)
                if bstate != "ok" or cstate != "ok":
                    if bstate != "missing" or cstate != "missing":
                        incomplete.append(
                            {
                                "prompt_id": pid,
                                "engine": engine,
                                "arm": arm_name,
                                "baseline": bstate,
                                "current": cstate,
                            }
                        )
                    continue
                bhit = _is_hit(barm)
                chit = _is_hit(carm)
                if not bhit and chit:
                    kind = "miss_to_hit"
                elif bhit and not chit:
                    kind = "hit_to_miss"
                elif bhit and chit:
                    kind = "hit_to_hit"
                else:
                    kind = "still_miss"
                counts[kind] += 1
                key = f"{pid}|{engine}|{arm_name}"
                bj = _judge_fields(baseline.judge, key)
                cj = _judge_fields(current.judge, key)
                stance = position = None
                if kind == "hit_to_hit" and bj and cj:
                    judge_both += 1
                    stance = {
                        "baseline": bj.get("stance") or "",
                        "current": cj.get("stance") or "",
                        "changed": (bj.get("stance") or "") != (cj.get("stance") or ""),
                    }
                    position = {
                        "baseline": bj.get("position") or "",
                        "current": cj.get("position") or "",
                        "changed": (bj.get("position") or "") != (cj.get("position") or ""),
                    }
                    if stance["changed"]:
                        stance_changed += 1
                    if position["changed"]:
                        position_changed += 1
                rows.append(
                    {
                        "prompt_id": pid,
                        "prompt_text": crow.get("prompt_text") or brow.get("prompt_text") or pid,
                        "class": crow.get("class") or brow.get("class") or "",
                        "engine": engine,
                        "arm": arm_name,
                        "transition": kind,
                        "baseline": {
                            "kind": "hit" if bhit else "miss",
                            "judge": bj,
                        },
                        "current": {
                            "kind": "hit" if chit else "miss",
                            "judge": cj,
                        },
                        "stance": stance,
                        "position": position,
                    }
                )

    rank = {k: i for i, k in enumerate(TRANSITIONS)}
    rows.sort(
        key=lambda r: (
            rank.get(r["transition"], 99),
            0 if (r.get("stance") or {}).get("changed") else 1,
            0 if (r.get("position") or {}).get("changed") else 1,
            r["engine"],
            r["arm"],
            r["prompt_id"],
        )
    )
    return {
        "counts": {k: int(counts.get(k) or 0) for k in TRANSITIONS},
        "stance_changed": stance_changed,
        "position_changed": position_changed,
        "hit_to_hit_with_judge_both": judge_both,
        "unmatched_prompt_ids": unmatched,
        "incomplete_cells": incomplete,
        "rows": rows,
    }


def engines_with_cells(snapshot: RunSnapshot) -> list[str]:
    """Engines that have at least one completed (non-error) cell."""
    found: list[str] = []
    for engine in _engine_order(snapshot.docs):
        for row in snapshot.rows:
            for arm_name in ARMS:
                if _cell_state(_arm_of(row, engine, arm_name)) == "ok":
                    found.append(engine)
                    break
            else:
                continue
            break
    return found


def engine_coverage(baseline: RunSnapshot, current: RunSnapshot) -> dict[str, Any]:
    """Comparable engines = intersection. A skipped engine is not a market drop."""
    b = engines_with_cells(baseline)
    c = engines_with_cells(current)
    bs, cs = set(b), set(c)
    comparable = [e for e in _engine_order(baseline.docs, current.docs) if e in bs and e in cs]
    return {
        "baseline": b,
        "current": c,
        "comparable": comparable,
        "missing_in_current": [e for e in b if e not in cs],
        "missing_in_baseline": [e for e in c if e not in bs],
    }


def brand_cell_counts(snapshot: RunSnapshot, engines: list[str]) -> dict[str, Any]:
    """Deterministic brand_mentioned cells (K+S) on the given engines."""
    out: dict[str, Any] = {
        "cells": 0,
        "n": 0,
        "knowledge": {"hits": 0, "n": 0, "rate": None},
        "search": {"hits": 0, "n": 0, "rate": None},
        "engines": {},
    }
    for engine in engines:
        rec: dict[str, Any] = {
            "cells": 0,
            "n": 0,
            "knowledge": {"hits": 0, "n": 0, "rate": None},
            "search": {"hits": 0, "n": 0, "rate": None},
        }
        for row in snapshot.rows:
            for arm_name in ARMS:
                arm = _arm_of(row, engine, arm_name)
                if _cell_state(arm) != "ok":
                    continue
                rec["n"] += 1
                rec[arm_name]["n"] += 1
                if _is_hit(arm):
                    rec["cells"] += 1
                    rec[arm_name]["hits"] += 1
        for arm_name in ARMS:
            rec[arm_name]["rate"] = _rate(rec[arm_name]["hits"], rec[arm_name]["n"])
        out["engines"][engine] = rec
        out["cells"] += rec["cells"]
        out["n"] += rec["n"]
        for arm_name in ARMS:
            out[arm_name]["hits"] += rec[arm_name]["hits"]
            out[arm_name]["n"] += rec[arm_name]["n"]
    for arm_name in ARMS:
        out[arm_name]["rate"] = _rate(out[arm_name]["hits"], out[arm_name]["n"])
    return out


def vendor_counts(
    snapshot: RunSnapshot,
    *,
    brand: str,
    aliases: list[str],
    alias_map: Any,
    engines: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Normalized vendor → {name, origin, answer, search_box, by_engine}. Brand excluded."""
    store = snapshot.vendors
    allow = set(engines) if engines is not None else None
    by_key: dict[str, dict[str, Any]] = {}

    def bump(
        rec: dict[str, str],
        engine: str,
        *,
        answer: bool = False,
        search_box: bool = False,
    ) -> None:
        name = rec.get("name") or ""
        origin = rec.get("origin") or "known"
        key = alias_map.key_for(name)
        if not key:
            return
        slot = by_key.setdefault(
            key,
            {
                "key": key,
                "name": name,
                "origin": origin,
                "answer": 0,
                "search_box": 0,
                "by_engine": {},
            },
        )
        slot["name"] = alias_map.display_for(name) or slot["name"]
        if origin == "surprise":
            slot["origin"] = "surprise"
        eng = slot["by_engine"].setdefault(engine, {"answer": 0, "search_box": 0})
        if answer:
            slot["answer"] += 1
            eng["answer"] += 1
        if search_box:
            slot["search_box"] += 1
            eng["search_box"] += 1

    for row in snapshot.rows:
        pid = row.get("prompt_id")
        for engine, arms in (row.get("engines") or {}).items():
            if allow is not None and engine not in allow:
                continue
            if not isinstance(arms, dict):
                continue
            for arm_name in ARMS:
                arm = arms.get(arm_name)
                if _cell_state(arm) != "ok":
                    continue
                vkey = f"{pid}|{engine}|{arm_name}"
                cell = store.get(vkey)
                for rec in classified_vendors_for_arm(arm, cell, alias_map, brand, aliases):
                    bump(rec, engine, answer=True)
                if arm_name == "search":
                    for rec in classified_query_vendors_for_arm(
                        arm, cell, alias_map, brand, aliases
                    ):
                        bump(rec, engine, search_box=True)
    return by_key


def _mentions(rec: dict[str, Any] | None) -> int:
    if not rec:
        return 0
    return int(rec.get("answer") or 0) + int(rec.get("search_box") or 0)


def _engine_mentions(rec: dict[str, Any] | None, engine: str) -> int:
    if not rec:
        return 0
    slot = (rec.get("by_engine") or {}).get(engine) or {}
    return int(slot.get("answer") or 0) + int(slot.get("search_box") or 0)


def _share(n: int, total: int) -> float | None:
    if total <= 0:
        return None
    return n / total


def _rank_map(counts: dict[str, int], *, floor: int) -> dict[str, int]:
    """1-based rank among names with mentions >= floor. Tie-break: name."""
    ranked = sorted(
        ((k, n) for k, n in counts.items() if n >= floor),
        key=lambda kn: (-kn[1], kn[0]),
    )
    return {k: i + 1 for i, (k, _n) in enumerate(ranked)}


def _rank_label(baseline_rank: int | None, current_rank: int | None) -> str:
    if baseline_rank is None and current_rank is not None:
        return "NEW"
    if baseline_rank is not None and current_rank is None:
        return "OUT"
    if baseline_rank is None or current_rank is None:
        return "—"
    if baseline_rank == current_rank:
        return "—"
    delta = baseline_rank - current_rank
    if delta > 0:
        return f"↑{delta}"
    return f"↓{abs(delta)}"


def _status_for(b_n: int, c_n: int, floor: int) -> str:
    b_on = b_n >= floor
    c_on = c_n >= floor
    if not b_on and c_on:
        return "new"
    if b_on and not c_on:
        return "disappeared"
    if b_on and c_on and c_n > b_n:
        return "riser"
    if b_on and c_on and c_n < b_n:
        return "faller"
    if b_on and c_on:
        return "flat"
    return "noise"


def diff_vendors(
    baseline: RunSnapshot,
    current: RunSnapshot,
    *,
    brand: str,
    engines: list[str] | None = None,
    floor: int = DEFAULT_FLOOR,
    top_n: int = DEFAULT_TOP_N,
    top_movers: int = DEFAULT_TOP_MOVERS,
) -> dict[str, Any]:
    aliases = list(dict.fromkeys([*baseline.aliases, *current.aliases]))
    competitors = list(dict.fromkeys([*baseline.competitors, *current.competitors]))
    alias_map = seed_alias_map(
        brand,
        aliases,
        competitors,
        list(baseline.vendors.values()) + list(current.vendors.values()),
    )
    use_engines = list(engines or _engine_order(baseline.docs, current.docs))
    b = vendor_counts(baseline, brand=brand, aliases=aliases, alias_map=alias_map, engines=use_engines)
    c = vendor_counts(current, brand=brand, aliases=aliases, alias_map=alias_map, engines=use_engines)
    keys = set(b) | set(c)
    field_b = sum(_mentions(b.get(k)) for k in keys)
    field_c = sum(_mentions(c.get(k)) for k in keys)
    rows: list[dict[str, Any]] = []
    for key in keys:
        br = b.get(key)
        cr = c.get(key)
        b_ans = int((br or {}).get("answer") or 0)
        c_ans = int((cr or {}).get("answer") or 0)
        b_q = int((br or {}).get("search_box") or 0)
        c_q = int((cr or {}).get("search_box") or 0)
        b_n = b_ans + b_q
        c_n = c_ans + c_q
        name = (cr or br or {}).get("name") or key
        origin = "known"
        if (cr and cr.get("origin") == "surprise") or (br and br.get("origin") == "surprise"):
            origin = "surprise"
        if cr:
            origin = cr.get("origin") or origin
        elif br:
            origin = br.get("origin") or origin
        engines_seen = sorted(
            {
                e
                for rec in (br, cr)
                if rec
                for e, slot in (rec.get("by_engine") or {}).items()
                if (int(slot.get("answer") or 0) + int(slot.get("search_box") or 0)) > 0
            }
        )
        by_engine = {
            e: {
                "baseline": _engine_mentions(br, e),
                "current": _engine_mentions(cr, e),
                "delta": _engine_mentions(cr, e) - _engine_mentions(br, e),
            }
            for e in use_engines
        }
        rows.append(
            {
                "key": key,
                "name": name,
                "origin": origin,
                "surprise": origin == "surprise",
                "is_brand": False,
                "status": _status_for(b_n, c_n, floor),
                "baseline": {
                    "answer": b_ans,
                    "search_box": b_q,
                    "mentions": b_n,
                    "share": _share(b_n, field_b),
                },
                "current": {
                    "answer": c_ans,
                    "search_box": c_q,
                    "mentions": c_n,
                    "share": _share(c_n, field_c),
                },
                "delta": c_n - b_n,
                "delta_answer": c_ans - b_ans,
                "delta_search_box": c_q - b_q,
                "share_delta_pp": _delta_pp(_share(b_n, field_b), _share(c_n, field_c)),
                "engines": engines_seen,
                "by_engine": by_engine,
            }
        )

    brand_b = brand_cell_counts(baseline, use_engines)
    brand_c = brand_cell_counts(current, use_engines)
    b_brand_n = int(brand_b["cells"])
    c_brand_n = int(brand_c["cells"])
    named_b = field_b + b_brand_n
    named_c = field_c + c_brand_n
    brand_row = {
        "key": "__brand__",
        "name": brand,
        "origin": "brand",
        "surprise": False,
        "is_brand": True,
        "status": _status_for(b_brand_n, c_brand_n, floor),
        "baseline": {
            "answer": b_brand_n,
            "search_box": 0,
            "mentions": b_brand_n,
            "share": _share(b_brand_n, named_b),
        },
        "current": {
            "answer": c_brand_n,
            "search_box": 0,
            "mentions": c_brand_n,
            "share": _share(c_brand_n, named_c),
        },
        "delta": c_brand_n - b_brand_n,
        "delta_answer": c_brand_n - b_brand_n,
        "delta_search_box": 0,
        "share_delta_pp": _delta_pp(_share(b_brand_n, named_b), _share(c_brand_n, named_c)),
        "engines": use_engines,
        "by_engine": {
            e: {
                "baseline": int((brand_b["engines"].get(e) or {}).get("cells") or 0),
                "current": int((brand_c["engines"].get(e) or {}).get("cells") or 0),
                "delta": int((brand_c["engines"].get(e) or {}).get("cells") or 0)
                - int((brand_b["engines"].get(e) or {}).get("cells") or 0),
            }
            for e in use_engines
        },
    }

    rank_b = _rank_map(
        {r["key"]: r["baseline"]["mentions"] for r in rows} | {brand_row["key"]: b_brand_n},
        floor=floor,
    )
    rank_c = _rank_map(
        {r["key"]: r["current"]["mentions"] for r in rows} | {brand_row["key"]: c_brand_n},
        floor=floor,
    )
    for r in (*rows, brand_row):
        r["baseline_rank"] = rank_b.get(r["key"])
        r["current_rank"] = rank_c.get(r["key"])
        r["rank_delta"] = (
            (r["baseline_rank"] - r["current_rank"])
            if r["baseline_rank"] is not None and r["current_rank"] is not None
            else None
        )
        r["rank_label"] = _rank_label(r["baseline_rank"], r["current_rank"])

    rows.sort(key=lambda r: (-abs(r["delta"]), -r["current"]["mentions"], r["name"].lower()))
    scored = [r for r in rows if r["status"] != "noise"]
    risers = sorted(
        [r for r in scored if r["status"] == "riser"],
        key=lambda r: (-r["delta"], -r["current"]["mentions"], r["name"].lower()),
    )
    fallers = sorted(
        [r for r in scored if r["status"] == "faller"],
        key=lambda r: (r["delta"], -r["baseline"]["mentions"], r["name"].lower()),
    )
    new = sorted(
        [r for r in scored if r["status"] == "new"],
        key=lambda r: (-r["current"]["mentions"], r["name"].lower()),
    )
    disappeared = sorted(
        [r for r in scored if r["status"] == "disappeared"],
        key=lambda r: (-r["baseline"]["mentions"], r["name"].lower()),
    )
    new_known = [r for r in new if not r["surprise"]]
    new_surprise = [r for r in new if r["surprise"]]
    surprises = sorted(
        [r for r in scored if r["surprise"]],
        key=lambda r: (-r["current"]["mentions"], r["name"].lower()),
    )

    def _in_top(r: dict[str, Any]) -> bool:
        brk = r.get("baseline_rank")
        crk = r.get("current_rank")
        return (brk is not None and brk <= top_n) or (crk is not None and crk <= top_n)

    rank_rows = [brand_row, *rows]
    rank = [r for r in rank_rows if _in_top(r)]
    rank.sort(
        key=lambda r: (
            r.get("current_rank") is None,
            r.get("current_rank") or 999,
            r.get("baseline_rank") or 999,
            r["name"].lower(),
        )
    )

    def _leader(side: str) -> dict[str, Any] | None:
        pool = [r for r in rows if (r.get(side) or {}).get("mentions", 0) >= floor]
        if not pool:
            return None
        best = min(pool, key=lambda r: (-r[side]["mentions"], r["name"].lower()))
        return {"name": best["name"], "mentions": best[side]["mentions"], "origin": best["origin"]}

    brand_vs_field = {
        "compared_engines": use_engines,
        "floor": floor,
        "brand": {
            "name": brand,
            "baseline_mentions": b_brand_n,
            "current_mentions": c_brand_n,
            "delta": c_brand_n - b_brand_n,
            "baseline_cells": brand_b["n"],
            "current_cells": brand_c["n"],
            "knowledge": _rate_pair(brand_b["knowledge"], brand_c["knowledge"]),
            "search": _rate_pair(brand_b["search"], brand_c["search"]),
            "share": {
                "baseline": _share(b_brand_n, named_b),
                "current": _share(c_brand_n, named_c),
                "delta_pp": _delta_pp(_share(b_brand_n, named_b), _share(c_brand_n, named_c)),
            },
            "baseline_rank": brand_row.get("baseline_rank"),
            "current_rank": brand_row.get("current_rank"),
            "rank_delta": brand_row.get("rank_delta"),
            "rank_label": brand_row.get("rank_label"),
        },
        "field": {
            "baseline_mentions": field_b,
            "current_mentions": field_c,
            "delta": field_c - field_b,
            "baseline_names": sum(1 for r in rows if r["baseline"]["mentions"] >= floor),
            "current_names": sum(1 for r in rows if r["current"]["mentions"] >= floor),
        },
        "leader": {"baseline": _leader("baseline"), "current": _leader("current")},
        "brand_rank": {
            "baseline": brand_row.get("baseline_rank"),
            "current": brand_row.get("current_rank"),
            "delta": brand_row.get("rank_delta"),
            "label": brand_row.get("rank_label"),
        },
    }

    return {
        "source": {"baseline": baseline.vendor_source, "current": current.vendor_source},
        "floor": floor,
        "floor_note": (
            f"A name is NEW when baseline mentions < {floor} and current ≥ {floor}; "
            f"OUT / disappeared when current < {floor} and baseline ≥ {floor}. "
            f"Default floor is 1 (count == 0). Raise --floor to treat leftover singles as gone."
        ),
        "top_n": top_n,
        "top_movers": top_movers,
        "top_list": DEFAULT_TOP_LIST,
        "compared_engines": use_engines,
        "brand_vs_field": brand_vs_field,
        "rank": rank,
        "risers": risers[:top_movers],
        "fallers": fallers[:top_movers],
        "new": new,
        "new_known": new_known,
        "new_surprise": new_surprise,
        "disappeared": disappeared,
        "surprises": surprises,
        "all": scored,
    }


def _biggest_mover(vendors: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        r
        for r in vendors.get("all") or []
        if r.get("delta") and r.get("status") in ("riser", "faller", "new", "disappeared")
    ]
    if not candidates:
        return None
    # Prefer volume over a swarm of +1 new names; then incumbents that actually moved.
    best = min(
        candidates,
        key=lambda r: (
            -abs(int(r["delta"])),
            -max(int(r["current"]["mentions"]), int(r["baseline"]["mentions"])),
            0 if r.get("status") in ("riser", "faller") else 1,
            r["name"].lower(),
        ),
    )
    return {
        "name": best["name"],
        "delta": best["delta"],
        "status": best["status"],
        "origin": best.get("origin") or "known",
        "surprise": bool(best.get("surprise")),
        "baseline": best["baseline"]["mentions"],
        "current": best["current"]["mentions"],
    }


def _new_surprise(vendors: dict[str, Any]) -> dict[str, Any] | None:
    news = [r for r in vendors.get("new") or [] if r.get("surprise")]
    if not news:
        return None
    best = news[0]
    return {
        "name": best["name"],
        "current": best["current"]["mentions"],
        "origin": "surprise",
    }


def _title_engine(engine: str) -> str:
    raw = (engine or "").strip()
    if not raw:
        return "engine"
    return raw[0].upper() + raw[1:]


def _join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{', '.join(names[:-1])}, and {names[-1]}"


def _moved_pp(delta: float | None) -> str:
    if delta is None:
        return "could not be compared"
    if delta == 0:
        return "was unchanged"
    if delta > 0:
        return f"rose {delta:.1f}pp"
    return f"fell {abs(delta):.1f}pp"


def _mention_clause(delta: int | None) -> str:
    if delta is None:
        return "comparable absolute mentions could not be compared"
    if delta == 0:
        return "comparable absolute mentions unchanged"
    adverb = "slightly " if 0 < abs(delta) < 5 else ""
    direction = "up" if delta > 0 else "down"
    return f"comparable absolute mentions {adverb}{direction}"


def _engine_score(mentions_delta: int, k_delta: float | None, s_delta: float | None) -> float:
    return mentions_delta * 10.0 + (k_delta or 0.0) + (s_delta or 0.0)


def _direction_from_score(score: float) -> str:
    if score > 1:
        return "up"
    if score < -1:
        return "down"
    return "flat"


def _share_pattern(brand_delta: int, share_delta: float | None) -> str:
    share_up = share_delta is not None and share_delta > 0
    share_down = share_delta is not None and share_delta < 0
    abs_up = brand_delta > 0
    abs_down = brand_delta < 0
    if abs_up and share_down:
        return "absolute_up_share_down"
    if abs_down and share_up:
        return "absolute_down_share_up"
    if abs_up and share_up:
        return "both_up"
    if abs_down and share_down:
        return "both_down"
    if abs_up:
        return "absolute_up"
    if abs_down:
        return "absolute_down"
    if share_down:
        return "share_down"
    if share_up:
        return "share_up"
    return "flat"


def _share_note(
    brand: str,
    *,
    brand_b: int,
    brand_c: int,
    brand_delta: int,
    field_b: int,
    field_c: int,
    field_delta: int,
    share_b: float | None,
    share_c: float | None,
    share_delta: float | None,
    pattern: str,
    field_names_b: int | None = None,
    field_names_c: int | None = None,
) -> str:
    share_bit = (
        f"share {_share_label(share_b)} → {_share_label(share_c)} "
        f"({_pp_label(share_delta)})"
    )
    if pattern == "absolute_up_share_down":
        extra = ""
        if field_names_b is not None and field_names_c is not None and field_names_c > field_names_b:
            extra = f" Named vendors {field_names_b}→{field_names_c}."
        return (
            f"{brand} named cells rose {brand_b}→{brand_c} ({_pp_int(brand_delta)}) while "
            f"{share_bit} — the vendor field grew ({field_b}→{field_c} mentions, "
            f"{_pp_int(field_delta)}). Share can fall while the brand rises when the field "
            f"grows; a share drop ≠ the brand vanishing.{extra}"
        )
    if pattern == "absolute_down_share_up":
        return (
            f"{brand} named cells fell {brand_b}→{brand_c} ({_pp_int(brand_delta)}) while "
            f"{share_bit}. Share rose because the field shrank "
            f"({field_b}→{field_c}, {_pp_int(field_delta)}), not because the brand gained volume."
        )
    if pattern == "both_up":
        return (
            f"{brand} named cells rose {brand_b}→{brand_c} and {share_bit}. "
            f"Field {field_b}→{field_c}."
        )
    if pattern == "both_down":
        return (
            f"{brand} named cells fell {brand_b}→{brand_c} and {share_bit}. "
            f"Field {field_b}→{field_c}."
        )
    return (
        f"{brand} named cells {brand_b}→{brand_c} ({_pp_int(brand_delta)}); "
        f"field {field_b}→{field_c} ({_pp_int(field_delta)}); {share_bit}."
    )


def interpret_change(
    brand: str,
    rates: dict[str, Any],
    vendors: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Deterministic executive narrative from the same stats as the tables. No LLM."""
    comparable = list(
        coverage.get("comparable") or rates.get("compared_engines") or []
    )
    skipped = list(coverage.get("missing_in_current") or [])
    added = list(coverage.get("missing_in_baseline") or [])
    overall = rates.get("overall") or {}
    by_engine = rates.get("engines") or {}
    bvf = vendors.get("brand_vs_field") or {}
    brand_blk = bvf.get("brand") or {}
    field_blk = bvf.get("field") or {}

    split: list[dict[str, Any]] = []
    for engine in comparable:
        rec = by_engine.get(engine) or {}
        k = rec.get("knowledge") or {}
        s = rec.get("search") or {}
        sr = rec.get("search_rate") or {}
        k_hits_b = int((k.get("baseline") or {}).get("hits") or 0)
        k_hits_c = int((k.get("current") or {}).get("hits") or 0)
        s_hits_b = int((s.get("baseline") or {}).get("hits") or 0)
        s_hits_c = int((s.get("current") or {}).get("hits") or 0)
        mentions_b = k_hits_b + s_hits_b
        mentions_c = k_hits_c + s_hits_c
        mentions_delta = mentions_c - mentions_b
        k_delta = k.get("delta_pp")
        s_delta = s.get("delta_pp")
        score = _engine_score(mentions_delta, k_delta, s_delta)
        split.append(
            {
                "engine": engine,
                "label": _title_engine(engine),
                "score": round(score, 2),
                "direction": _direction_from_score(score),
                "role": "flat",
                "knowledge": {
                    "baseline_rate": (k.get("baseline") or {}).get("rate"),
                    "current_rate": (k.get("current") or {}).get("rate"),
                    "delta_pp": k_delta,
                    "baseline": k.get("baseline") or {},
                    "current": k.get("current") or {},
                },
                "search": {
                    "baseline_rate": (s.get("baseline") or {}).get("rate"),
                    "current_rate": (s.get("current") or {}).get("rate"),
                    "delta_pp": s_delta,
                    "baseline": s.get("baseline") or {},
                    "current": s.get("current") or {},
                },
                "search_rate": {
                    "baseline_rate": (sr.get("baseline") or {}).get("rate"),
                    "current_rate": (sr.get("current") or {}).get("rate"),
                    "delta_pp": sr.get("delta_pp"),
                    "baseline": sr.get("baseline") or {},
                    "current": sr.get("current") or {},
                },
                "mentions": {
                    "baseline": mentions_b,
                    "current": mentions_c,
                    "delta": mentions_delta,
                },
            }
        )

    ups = [e for e in split if e["direction"] == "up"]
    downs = [e for e in split if e["direction"] == "down"]
    if ups:
        best_up = max(ups, key=lambda e: e["score"])
        best_up["role"] = "main_win"
        for e in ups:
            if e is not best_up:
                e["role"] = "win"
    if downs:
        worst = min(downs, key=lambda e: e["score"])
        worst["role"] = "main_loss"
        for e in downs:
            if e is not worst:
                e["role"] = "loss"
    disagree = bool(ups and downs)

    brand_delta = int(brand_blk.get("delta") or 0)
    field_delta = int(field_blk.get("delta") or 0)
    share = brand_blk.get("share") or {}
    share_delta = share.get("delta_pp")
    pattern = _share_pattern(brand_delta, share_delta)

    up_labels = [_title_engine(e["engine"]) for e in ups]
    down_labels = [_title_engine(e["engine"]) for e in downs]
    if disagree:
        prefix = "mixed"
        mid = f"{_join_names(up_labels)} up, {_join_names(down_labels)} down"
    elif ups and not downs:
        prefix = "up"
        mid = f"{_join_names(up_labels)} improved"
    elif downs and not ups:
        prefix = "down"
        mid = f"{_join_names(down_labels)} declined"
    elif comparable:
        prefix = "flat"
        mid = "comparable engines unchanged"
    else:
        prefix = "incomplete"
        mid = "no overlapping engines to compare"

    mention_bit = _mention_clause(brand_delta)
    if pattern == "absolute_up_share_down":
        mention_bit += "; share of field fell (field grew)"
    verdict = f"{prefix}: {mid}; {mention_bit}"

    eng_list = _join_names([_title_engine(e) for e in comparable]) or "no overlapping engines"
    k_pair = overall.get("knowledge") or {}
    s_pair = overall.get("search") or {}
    k_b = (k_pair.get("baseline") or {}).get("rate")
    k_c = (k_pair.get("current") or {}).get("rate")
    s_b = (s_pair.get("baseline") or {}).get("rate")
    s_c = (s_pair.get("current") or {}).get("rate")
    named_b = int(brand_blk.get("baseline_mentions") or 0)
    named_c = int(brand_blk.get("current_mentions") or 0)
    rank_b = brand_blk.get("baseline_rank")
    rank_c = brand_blk.get("current_rank")
    if not comparable:
        comparable_story = (
            f"No engine appears on both runs, so {brand} rates and rank cannot be compared."
        )
    else:
        comparable_story = (
            f"On {eng_list} (engines in both runs), {brand} knowledge "
            f"{_moved_pp(k_pair.get('delta_pp'))} ({_pct_label(k_b)} → {_pct_label(k_c)}), "
            f"search {_moved_pp(s_pair.get('delta_pp'))} ({_pct_label(s_b)} → {_pct_label(s_c)}). "
            f"Named cells {named_b}→{named_c}."
        )
        if rank_b is not None or rank_c is not None:
            comparable_story += f" Rank {rank_b or '—'}→{rank_c or '—'}."
        if disagree:
            comparable_story += (
                " Overall looks flat only because engines moved in opposite directions."
            )

    share_vs_absolute = {
        "brand_mentions_baseline": named_b,
        "brand_mentions_current": named_c,
        "brand_delta": brand_delta,
        "field_mentions_baseline": int(field_blk.get("baseline_mentions") or 0),
        "field_mentions_current": int(field_blk.get("current_mentions") or 0),
        "field_delta": field_delta,
        "field_names_baseline": int(field_blk.get("baseline_names") or 0),
        "field_names_current": int(field_blk.get("current_names") or 0),
        "share_baseline": share.get("baseline"),
        "share_current": share.get("current"),
        "share_delta_pp": share_delta,
        "field_grew": field_delta > 0,
        "pattern": pattern,
        "note": _share_note(
            brand,
            brand_b=named_b,
            brand_c=named_c,
            brand_delta=brand_delta,
            field_b=int(field_blk.get("baseline_mentions") or 0),
            field_c=int(field_blk.get("current_mentions") or 0),
            field_delta=field_delta,
            share_b=share.get("baseline"),
            share_c=share.get("current"),
            share_delta=share_delta,
            pattern=pattern,
            field_names_b=int(field_blk.get("baseline_names") or 0),
            field_names_c=int(field_blk.get("current_names") or 0),
        ),
    }

    caveats: list[str] = []
    if skipped:
        caveats.append(
            f"{_join_names([_title_engine(e) for e in skipped])} in baseline only — "
            f"do not blend that engine into the headline. Prefer comparable-engine rates "
            f"({eng_list or 'none'}) over a blended figure."
        )
    if added:
        caveats.append(
            f"{_join_names([_title_engine(e) for e in added])} in current only — "
            "those cells are shown per-engine, not folded into blended Δ or ranks."
        )
    if skipped or added:
        caveats.append(
            "Prefer comparable-engine rates over a blended headline when coverage differs."
        )
    if not comparable:
        caveats.append("No overlapping engines; rates and ranks cannot be compared.")
    src = vendors.get("source") or {}
    bsrc = src.get("baseline")
    csrc = src.get("current")
    if bsrc and csrc and bsrc != csrc:
        caveats.append(
            f"Vendor source changed ({bsrc} → {csrc}); field-size jumps can be extract "
            "coverage, not only more vendors in the answers."
        )
    if not caveats:
        caveats.append(
            f"Comparable engines: {eng_list or 'none'}. Blended rates use these only."
        )

    practical: list[str] = []

    def _add(line: str) -> None:
        if line and line not in practical and len(practical) < 4:
            practical.append(line)

    if skipped:
        _add(
            f"Ignore blended headlines that mix {_join_names([_title_engine(e) for e in skipped])}; "
            f"read {eng_list or 'overlapping engines'} only."
        )
    if disagree:
        win = next((e for e in split if e["role"] == "main_win"), None)
        loss = next((e for e in split if e["role"] == "main_loss"), None)
        if win and loss:
            w_hint = ENGINE_SEARCH_HINT.get(win["engine"], win["label"])
            l_hint = ENGINE_SEARCH_HINT.get(loss["engine"], loss["label"])
            _add(
                f"Engine split: treat {win['label']} ({w_hint}) and {loss['label']} ({l_hint}) "
                "as separate fires — overall flat hides opposing moves."
            )
    main_loss = next((e for e in split if e["role"] == "main_loss"), None)
    main_win = next((e for e in split if e["role"] == "main_win"), None)
    if main_loss:
        s_delta = main_loss["search"]["delta_pp"]
        k_delta = main_loss["knowledge"]["delta_pp"]
        sr_now = main_loss["search_rate"]["current_rate"]
        label = main_loss["label"]
        hint = ENGINE_SEARCH_HINT.get(main_loss["engine"], label)
        if s_delta is not None and s_delta < 0 and sr_now is not None and sr_now >= 0.95:
            _add(
                f"Search-arm loss on {label} with {sr_now * 100:.0f}% search_rate → "
                f"retrieval/citation problem on that engine's index ({hint}), "
                "not a 'didn't search' miss."
            )
        elif s_delta is not None and s_delta < 0:
            _add(
                f"Search-arm loss on {label} ({hint}) — treat that index as a separate fire."
            )
        elif k_delta is not None and k_delta < 0:
            _add(
                f"Knowledge-arm loss on {label} — model prior weakened; "
                "that is not a retrieval-only fix."
            )
    if pattern == "absolute_up_share_down":
        _add(
            "Do not treat the share drop as the brand vanishing; absolute mentions rose "
            "while the field grew."
        )
    if main_win:
        s_delta = main_win["search"]["delta_pp"]
        k_delta = main_win["knowledge"]["delta_pp"]
        label = main_win["label"]
        hint = ENGINE_SEARCH_HINT.get(main_win["engine"], label)
        if s_delta is not None and s_delta > 0:
            _add(f"Search-arm gain on {label} — invest in the {hint} retrieval/citation path.")
        elif k_delta is not None and k_delta > 0:
            _add(f"Knowledge-arm gain on {label} — keep that prior/training path warm.")
    rank_now = rank_c if isinstance(rank_c, int) else None
    rank_was = rank_b if isinstance(rank_b, int) else None
    if rank_now is not None and rank_now >= 5:
        was = rank_was if rank_was is not None else "—"
        _add(
            f"Don't overread mid-pack rank ({was}→{rank_now}); "
            "engine-level hit rates are the decision."
        )
    surprises = vendors.get("new_surprise") or []
    if surprises:
        top = surprises[0]
        _add(
            f"New off-seed name {top.get('name')} appeared; check extract coverage vs a real rival."
        )
    if added and not comparable:
        _add("Re-run the same roster on overlapping engines before calling a market move.")
    if len(practical) < 2 and comparable:
        _add(
            f"Re-check {eng_list} cells that flipped miss→hit or hit→miss before changing the plan."
        )
    if not practical:
        _add("No actionable engine split — read the rank table and prompt transitions.")

    return {
        "verdict": verdict,
        "comparable_story": comparable_story,
        "engines_disagree": disagree,
        "compared_engines": comparable,
        "engine_split": split,
        "share_vs_absolute": share_vs_absolute,
        "caveats": caveats,
        "practical": practical,
    }


def _headline(
    brand: str,
    rates: dict[str, Any],
    vendors: dict[str, Any],
    mover: dict[str, Any] | None,
    surprise: dict[str, Any] | None,
    coverage: dict[str, Any],
) -> str:
    search = (rates.get("overall") or {}).get("search") or {}
    delta = search.get("delta_pp")
    before = (search.get("baseline") or {}).get("rate")
    after = (search.get("current") or {}).get("rate")
    bits: list[str] = []
    if delta is None:
        bits.append(f"{brand} search mention rate could not be compared (incomplete cells).")
    elif delta == 0:
        bits.append(f"{brand} search mention was unchanged at {_pct_label(after)}.")
    elif delta > 0:
        bits.append(
            f"{brand} search mention rose {delta:.1f}pp ({_pct_label(before)} → {_pct_label(after)})."
        )
    else:
        bits.append(
            f"{brand} search mention fell {abs(delta):.1f}pp ({_pct_label(before)} → {_pct_label(after)})."
        )
    bvf = vendors.get("brand_vs_field") or {}
    brand_blk = bvf.get("brand") or {}
    field_blk = bvf.get("field") or {}
    if brand_blk and field_blk:
        bits.append(
            f"{brand} {brand_blk.get('baseline_mentions')}→{brand_blk.get('current_mentions')} "
            f"named cells; field {field_blk.get('baseline_mentions')}→{field_blk.get('current_mentions')}."
        )
    if mover:
        verb = {
            "riser": "rose",
            "new": "appeared",
            "faller": "fell",
            "disappeared": "left the ranking",
        }.get(mover["status"], "moved")
        if mover["status"] == "new":
            bits.append(f"{mover['name']} appeared with {mover['current']} mentions.")
        elif mover["status"] == "disappeared":
            bits.append(f"{mover['name']} left the ranking ({mover['baseline']} → {mover['current']}).")
        else:
            n = abs(int(mover["delta"]))
            unit = "mention" if n == 1 else "mentions"
            bits.append(f"{mover['name']} {verb} {n} {unit}.")
    new_n = len(vendors.get("new") or [])
    out_n = len(vendors.get("disappeared") or [])
    if new_n or out_n:
        extra = []
        if new_n:
            extra.append(f"{new_n} new")
        if out_n:
            extra.append(f"{out_n} OUT")
        bits.append("Competitors: " + ", ".join(extra) + ".")
    if surprise:
        bits.append(f"New surprise: {surprise['name']} ({surprise['current']}).")
    skipped = coverage.get("missing_in_current") or []
    added = coverage.get("missing_in_baseline") or []
    if skipped or added:
        used = ", ".join(coverage.get("comparable") or []) or "the overlapping engines"
        if skipped:
            bits.append(
                f"Note: {', '.join(skipped)} in baseline only — headline and blended rates "
                f"use {used}."
            )
        if added:
            bits.append(
                f"Note: {', '.join(added)} in current only — not folded into blended Δ."
            )
    elif not mover and not new_n and not out_n:
        bits.append("No competitor movement.")
    return " ".join(bits)


def diff_runs(
    baseline: RunSnapshot,
    current: RunSnapshot,
    *,
    brand: str,
    floor: int = DEFAULT_FLOOR,
    top_n: int = DEFAULT_TOP_N,
    top_movers: int = DEFAULT_TOP_MOVERS,
) -> dict[str, Any]:
    brand = (brand or current.brand or baseline.brand or "").strip()
    if not brand:
        raise ValueError("brand is required (--brand or workspace.brand)")
    if floor < 1:
        raise ValueError("floor must be >= 1")
    if top_n < 1:
        raise ValueError("top_n must be >= 1")
    if top_movers < 1:
        raise ValueError("top_movers must be >= 1")
    engines = _engine_order(baseline.docs, current.docs)
    coverage = engine_coverage(baseline, current)
    comparable = coverage["comparable"]
    rank_engines = comparable or engines
    rates = diff_brand_rates(baseline, current, engines, comparable=comparable)
    transitions = prompt_transitions(baseline, current, engines)
    vendors = diff_vendors(
        baseline,
        current,
        brand=brand,
        engines=rank_engines,
        floor=floor,
        top_n=top_n,
        top_movers=top_movers,
    )
    vendors["engine_coverage"] = coverage
    mover = _biggest_mover(vendors)
    surprise = _new_surprise(vendors)
    headline = _headline(brand, rates, vendors, mover, surprise, coverage)
    interpretation = interpret_change(brand, rates, vendors, coverage)
    bvf = vendors.get("brand_vs_field") or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "brand": brand,
        "baseline": {
            "path": str(baseline.path),
            "label": baseline.label,
            "run_ids": list(baseline.run_ids),
            "timestamps": list(baseline.timestamps),
            "vendor_source": baseline.vendor_source,
        },
        "current": {
            "path": str(current.path),
            "label": current.label,
            "run_ids": list(current.run_ids),
            "timestamps": list(current.timestamps),
            "vendor_source": current.vendor_source,
        },
        "engines": engines,
        "engine_coverage": coverage,
        "summary": {
            "headline": headline,
            "verdict": interpretation.get("verdict"),
            "brand_delta_pp": {
                "knowledge": (rates["overall"]["knowledge"] or {}).get("delta_pp"),
                "search": (rates["overall"]["search"] or {}).get("delta_pp"),
                "search_rate": (rates["overall"]["search_rate"] or {}).get("delta_pp"),
            },
            "brand_vs_field": {
                "brand_delta": (bvf.get("brand") or {}).get("delta"),
                "field_delta": (bvf.get("field") or {}).get("delta"),
                "share_delta_pp": ((bvf.get("brand") or {}).get("share") or {}).get("delta_pp"),
            },
            "biggest_competitor_mover": mover,
            "new_count": len(vendors.get("new") or []),
            "disappeared_count": len(vendors.get("disappeared") or []),
            "new_surprise": surprise,
        },
        "interpretation": interpretation,
        "brand_rates": rates,
        "transitions": transitions,
        "competitors": vendors,
        "methodology": {
            "same_roster_assumed": True,
            "match": "prompt_id + engine + arm",
            "unmatched_prompt_ids": transitions["unmatched_prompt_ids"],
            "incomplete_cells": transitions["incomplete_cells"],
            "incomplete_cell_count": len(transitions["incomplete_cells"]),
            "vendor_source": vendors["source"],
            "floor": floor,
            "floor_note": vendors.get("floor_note"),
            "compared_engines": comparable,
            "engine_coverage": coverage,
            "notes": [
                "Same roster is assumed; unmatched prompt_ids are listed, not scored in transitions.",
                "Mention rates skip error / missing cells. Those cells are listed as incomplete.",
                "Brand hits are deterministic brand_mentioned. Stance/position only when judge.json exists on both sides of a hit→hit.",
                "Vendor counts prefer vendors_judged.json (LLM ∪ regex) per cell; otherwise regex competitor_mentions / vendors_in_search_queries.",
                "A side without vendors_judged cannot surface surprises that were never on the seed list.",
                "Names are merged with aeo.vendors normalize (Kong Gateway ≡ Kong when Kong is seeded).",
                vendors.get("floor_note") or "",
                "Competitor ranks use engines present on both sides. A skipped engine (e.g. Grok in baseline only) is labelled — do not read that as a market drop.",
                "Blended brand rates, headline Δpp, and the executive narrative use comparable engines only. Per-engine rows still show a skipped engine.",
                "The interpretation / executive block is template + numbers. No LLM wrote it.",
            ],
        },
    }


def _delta_class(delta: float | None, *, invert: bool = False) -> str:
    if delta is None or delta == 0:
        return "flat"
    up = delta > 0
    if invert:
        up = not up
    return "up" if up else "down"


def _rate_cell(pair: dict[str, Any], *, invert: bool = False) -> str:
    b = pair.get("baseline") or {}
    c = pair.get("current") or {}
    delta = pair.get("delta_pp")
    bhits = b.get("hits", b.get("searched"))
    bn = b.get("n")
    chits = c.get("hits", c.get("searched"))
    cn = c.get("n")
    frac_b = f"{bhits}/{bn}" if bhits is not None and bn is not None else "—"
    frac_c = f"{chits}/{cn}" if chits is not None and cn is not None else "—"
    cls = _delta_class(delta, invert=invert)
    return (
        f"<td class='num'>{_esc(frac_b)} <span class='muted'>{_esc(_pct_label(b.get('rate')))}</span></td>"
        f"<td class='num'>{_esc(frac_c)} <span class='muted'>{_esc(_pct_label(c.get('rate')))}</span></td>"
        f"<td class='num delta {cls}'>{_esc(_pp_label(delta))}</td>"
    )


def _share_label(share: float | None) -> str:
    if share is None:
        return "—"
    return f"{share * 100:.1f}%"


def _origin_badge(r: dict[str, Any]) -> str:
    bits = ""
    if r.get("is_brand"):
        bits += " <span class='badge-brand'>brand</span>"
    if r.get("surprise"):
        bits += " <span class='badge-surprise'>surprise</span>"
    return bits


def _engines_attr(r: dict[str, Any]) -> str:
    return " ".join(r.get("engines") or [])


def _vendor_table(
    rows: list[dict[str, Any]],
    empty: str,
    *,
    limit: int | None = None,
    order_note: str = "",
) -> str:
    if not rows:
        return f"<p class='hint'>{_esc(empty)}</p>"
    shown = rows[:limit] if limit and limit > 0 else rows
    bits: list[str] = []
    if limit and len(rows) > limit:
        bits.append(
            f"<p class='hint'>Showing {len(shown)} of {len(rows)}"
            + (f" — {order_note}." if order_note else ".")
            + "</p>"
        )
    bits.append("<div class='table-wrap'><table class='data sortable'><thead><tr>")
    bits.append(
        "<th>Name</th><th>Origin</th><th class='num'>Baseline</th><th class='num'>Current</th>"
        "<th class='num'>Δ</th><th class='num'>Share Δ</th><th class='num'>Answer Δ</th>"
    )
    bits.append("</tr></thead><tbody>")
    for r in shown:
        # Competitor dynamics: + is a rise, − is a fall (not brand-inverted).
        cls = _delta_class(r.get("delta"))
        bits.append(f"<tr data-engines='{_esc(_engines_attr(r))}'>")
        bits.append(f"<td>{_esc(r.get('name'))}{_origin_badge(r)}</td>")
        bits.append(f"<td class='muted'>{_esc(r.get('origin'))}</td>")
        bits.append(f"<td class='num'>{int((r.get('baseline') or {}).get('mentions') or 0)}</td>")
        bits.append(f"<td class='num'>{int((r.get('current') or {}).get('mentions') or 0)}</td>")
        bits.append(f"<td class='num delta {cls}'>{_esc(_pp_int(r.get('delta')))}</td>")
        bits.append(
            f"<td class='num delta {_delta_class(r.get('share_delta_pp'))}'>"
            f"{_esc(_pp_label(r.get('share_delta_pp')))}</td>"
        )
        bits.append(f"<td class='num'>{_esc(_pp_int(r.get('delta_answer')))}</td>")
        bits.append("</tr>")
    bits.append("</tbody></table></div>")
    return "".join(bits)


def _rank_chip(label: str) -> str:
    raw = label or "—"
    cls = "flat"
    if raw == "NEW":
        cls = "new"
    elif raw == "OUT":
        cls = "out"
    elif raw.startswith("↑"):
        cls = "up"
    elif raw.startswith("↓"):
        cls = "down"
    return f"<span class='rank-chip {cls}'>{_esc(raw)}</span>"


def _rank_table(rows: list[dict[str, Any]], engines: list[str]) -> str:
    if not rows:
        return "<p class='hint'>No vendors on either side at this floor.</p>"
    show_eng = len(engines) > 1
    bits = [
        "<div class='table-wrap'><table class='data sortable' id='rank-table'><thead><tr>",
        "<th>Name</th><th class='num'>Base rank</th><th class='num'>Now rank</th><th>Rank Δ</th>",
        "<th class='num'>Baseline</th><th class='num'>Current</th><th class='num'>Δ</th>",
        "<th class='num'>Share now</th>",
    ]
    if show_eng:
        for e in engines:
            bits.append(f"<th class='num'>{_esc(e)}</th>")
    bits.append("</tr></thead><tbody>")
    for r in rows:
        cls = "brand-row" if r.get("is_brand") else ""
        bits.append(f"<tr class='{cls}' data-engines='{_esc(_engines_attr(r))}'>")
        bits.append(f"<td>{_esc(r.get('name'))}{_origin_badge(r)}</td>")
        bits.append(f"<td class='num'>{r.get('baseline_rank') or '—'}</td>")
        bits.append(f"<td class='num'>{r.get('current_rank') or '—'}</td>")
        bits.append(f"<td>{_rank_chip(str(r.get('rank_label') or '—'))}</td>")
        bits.append(f"<td class='num'>{int((r.get('baseline') or {}).get('mentions') or 0)}</td>")
        bits.append(f"<td class='num'>{int((r.get('current') or {}).get('mentions') or 0)}</td>")
        bits.append(
            f"<td class='num delta {_delta_class(r.get('delta'))}'>"
            f"{_esc(_pp_int(r.get('delta')))}</td>"
        )
        bits.append(
            f"<td class='num'>{_esc(_share_label((r.get('current') or {}).get('share')))}</td>"
        )
        if show_eng:
            by_e = r.get("by_engine") or {}
            for e in engines:
                slot = by_e.get(e) or {}
                bits.append(
                    f"<td class='num'>{int(slot.get('baseline') or 0)}→{int(slot.get('current') or 0)}</td>"
                )
        bits.append("</tr>")
    bits.append("</tbody></table></div>")
    return "".join(bits)


def _pp_int(n: Any) -> str:
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "—"
    sign = "+" if v > 0 else ""
    return f"{sign}{v}"


def _rate_arrow(delta: float | None, before: float | None, after: float | None) -> str:
    return f"{_pct_label(before)} → {_pct_label(after)} ({_pp_label(delta)})"


def _render_executive(payload: dict[str, Any]) -> str:
    brand = payload.get("brand") or "brand"
    interp = payload.get("interpretation") or {}
    if not interp:
        return ""
    split = list(interp.get("engine_split") or [])
    disagree = bool(interp.get("engines_disagree"))
    share = interp.get("share_vs_absolute") or {}
    parts: list[str] = []
    parts.append("<section class='executive' id='what-this-means'>")
    parts.append(f"<p class='eyebrow'>What this means for {_esc(brand)}</p>")
    parts.append(f"<h1>{_esc(interp.get('verdict') or '')}</h1>")
    story = interp.get("comparable_story") or ""
    if story:
        parts.append(f"<p class='exec-story'>{_esc(story)}</p>")
    if split:
        heading = "Engine split" if disagree else "Comparable engines"
        cls = "exec-split disagree" if disagree else "exec-split"
        parts.append(f"<h2 class='exec-split-h'>{heading}</h2>")
        if disagree:
            parts.append(
                "<p class='split-banner'>Opposite moves — a blended 'overall' hides this.</p>"
            )
        parts.append(f"<div class='{cls}'>")
        for e in split:
            role = e.get("role") or "flat"
            direction = e.get("direction") or "flat"
            card_cls = "split-card"
            if role == "main_win" or direction == "up":
                card_cls += " win"
            elif role == "main_loss" or direction == "down":
                card_cls += " loss"
            else:
                card_cls += " flat"
            role_lab = {
                "main_win": "Main win",
                "win": "Win",
                "main_loss": "Main loss",
                "loss": "Loss",
                "flat": "Flat",
            }.get(role, role)
            k = e.get("knowledge") or {}
            s = e.get("search") or {}
            mentions = e.get("mentions") or {}
            parts.append(f"<article class='{card_cls}'>")
            parts.append(f"<p class='eyebrow'>{_esc(role_lab)}</p>")
            parts.append(f"<p class='split-name'>{_esc(e.get('label') or e.get('engine'))}</p>")
            parts.append(
                f"<p class='split-line'><span>K</span> "
                f"{_esc(_rate_arrow(k.get('delta_pp'), k.get('baseline_rate'), k.get('current_rate')))}</p>"
            )
            parts.append(
                f"<p class='split-line'><span>S</span> "
                f"{_esc(_rate_arrow(s.get('delta_pp'), s.get('baseline_rate'), s.get('current_rate')))}</p>"
            )
            parts.append(
                f"<p class='split-line'><span>Mentions</span> "
                f"{int(mentions.get('baseline') or 0)}→{int(mentions.get('current') or 0)} "
                f"({_esc(_pp_int(mentions.get('delta')))})</p>"
            )
            parts.append("</article>")
        parts.append("</div>")
    note = share.get("note") or ""
    if note:
        parts.append(f"<p class='exec-share' id='share-vs-absolute'>{_esc(note)}</p>")
    practical = list(interp.get("practical") or [])
    caveats = list(interp.get("caveats") or [])
    if practical or caveats:
        parts.append("<div class='exec-cols'>")
        if practical:
            parts.append("<article><h3>What to do</h3><ul class='practical'>")
            for item in practical:
                parts.append(f"<li>{_esc(item)}</li>")
            parts.append("</ul></article>")
        if caveats:
            parts.append("<article><h3>Caveats</h3><ul class='caveats'>")
            for item in caveats:
                parts.append(f"<li>{_esc(item)}</li>")
            parts.append("</ul></article>")
        parts.append("</div>")
    parts.append("</section>")
    return "\n".join(parts)


def render_change_html(payload: dict[str, Any]) -> str:
    brand = payload.get("brand") or "brand"
    baseline = payload.get("baseline") or {}
    current = payload.get("current") or {}
    rates = payload.get("brand_rates") or {}
    transitions = payload.get("transitions") or {}
    vendors = payload.get("competitors") or {}
    summary = payload.get("summary") or {}
    methodology = payload.get("methodology") or {}
    engines = list(payload.get("engines") or (rates.get("engines") or {}).keys())
    unmatched = methodology.get("unmatched_prompt_ids") or transitions.get("unmatched_prompt_ids") or {}
    incomplete = methodology.get("incomplete_cells") or transitions.get("incomplete_cells") or []
    title = f"{brand} · AEO change report"

    parts: list[str] = []
    parts.append("<!DOCTYPE html><html lang='en'><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append(f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>")
    parts.append("<header class='top'><div class='top-brand'>")
    parts.append(f"<span class='wordmark'>{_esc(brand)}</span>")
    parts.append("<span class='domain'>change report</span></div>")
    parts.append("<div class='top-meta'>")
    parts.append(f"<span class='pill'>{_esc(baseline.get('label') or 'baseline')}</span>")
    parts.append("<span class='arrow'>→</span>")
    parts.append(f"<span class='pill'>{_esc(current.get('label') or 'current')}</span>")
    n_rows = len(transitions.get("rows") or [])
    parts.append(f"<span class='pill'>{n_rows} matched cells</span>")
    parts.append("</div></header><main>")

    coverage = payload.get("engine_coverage") or methodology.get("engine_coverage") or {}
    skipped = coverage.get("missing_in_current") or []
    added_eng = coverage.get("missing_in_baseline") or []
    comparable = coverage.get("comparable") or vendors.get("compared_engines") or engines
    floor = methodology.get("floor") or vendors.get("floor") or DEFAULT_FLOOR
    bsrc = (methodology.get("vendor_source") or {}).get("baseline") or baseline.get("vendor_source")
    csrc = (methodology.get("vendor_source") or {}).get("current") or current.get("vendor_source")

    if skipped or added_eng:
        parts.append("<aside class='gap-banner'>")
        if skipped:
            parts.append(
                f"<p><b>Engine gap.</b> Baseline has <b>{_esc(', '.join(skipped))}</b> "
                "and current does not. Competitor ranks and NEW/OUT use only "
                f"<b>{_esc(', '.join(comparable) or 'overlapping engines')}</b>. "
                "A drop-off that existed only on the missing engine is not a market change.</p>"
            )
        if added_eng:
            parts.append(
                f"<p>Current also has <b>{_esc(', '.join(added_eng))}</b> which baseline lacked. "
                "Those cells are shown per-engine, not folded into blended brand Δ or ranks.</p>"
            )
        if skipped:
            parts.append(
                "<p>Blended brand Δ, the headline, and the executive narrative use only "
                f"<b>{_esc(', '.join(comparable) or 'overlapping engines')}</b>.</p>"
            )
        parts.append("</aside>")

    parts.append(_render_executive(payload))

    parts.append("<section class='actions'>")
    parts.append("<p class='eyebrow'>Summary</p>")
    parts.append(f"<h1>{_esc(summary.get('headline') or '')}</h1>")
    parts.append("<div class='hero'>")
    bd = summary.get("brand_delta_pp") or {}
    for lab, key, hint in (
        ("Brand Δ (S)", "search", "search-arm mention, comparable engines"),
        ("Brand Δ (K)", "knowledge", "knowledge-arm mention, comparable engines"),
        ("Search-rate Δ", "search_rate", "share of search arms that actually searched"),
    ):
        delta = bd.get(key)
        cls = _delta_class(delta)
        parts.append(
            f"<article class='metric'><p class='eyebrow'>{lab}</p>"
            f"<p class='metric-n delta {cls}'>{_esc(_pp_label(delta))}</p>"
            f"<p class='hint'>{_esc(hint)}</p></article>"
        )
    mover = summary.get("biggest_competitor_mover")
    if mover:
        parts.append(
            "<article class='metric'><p class='eyebrow'>Biggest competitor mover</p>"
            f"<p class='metric-n'>{_esc(mover.get('name'))}</p>"
            f"<p class='hint'>{_esc(mover.get('status'))} {_esc(_pp_int(mover.get('delta')))} "
            f"({mover.get('baseline')} → {mover.get('current')})</p></article>"
        )
    else:
        parts.append(
            "<article class='metric'><p class='eyebrow'>Biggest competitor mover</p>"
            "<p class='metric-n'>None</p><p class='hint'>No vendor count changed</p></article>"
        )
    surprise = summary.get("new_surprise")
    if surprise:
        parts.append(
            "<article class='metric'><p class='eyebrow'>New surprise</p>"
            f"<p class='metric-n'>{_esc(surprise.get('name'))} "
            "<span class='badge-surprise'>surprise</span></p>"
            f"<p class='hint'>{int(surprise.get('current') or 0)} mentions in current, absent in baseline</p></article>"
        )
    else:
        parts.append(
            "<article class='metric'><p class='eyebrow'>New surprise</p>"
            "<p class='metric-n'>None</p>"
            "<p class='hint'>No new off-seed vendor in current</p></article>"
        )
    parts.append(
        "<article class='metric'><p class='eyebrow'>New / OUT</p>"
        f"<p class='metric-n'>{int(summary.get('new_count') or 0)}"
        f"<span class='slash'>/</span>{int(summary.get('disappeared_count') or 0)}</p>"
        f"<p class='hint'>names crossing the floor ({int(floor)})</p></article>"
    )
    parts.append("</div></section>")

    parts.append("<section class='method'>")
    parts.append("<p class='eyebrow'>Methodology</p>")
    parts.append("<h1>What this diff measures</h1>")
    parts.append(
        "<p>Same roster assumed. Cells match on <code>prompt_id</code> + engine + arm. "
        "Brand hits are the deterministic <code>brand_mentioned</code> bit. "
        "Competitor names prefer <code>vendors_judged.json</code> (LLM ∪ regex) and fall back "
        "to evidence <code>competitor_mentions</code> / <code>vendors_in_search_queries</code>. "
        "No LLM wrote this page — the numbers are Python.</p>"
    )
    parts.append("<div class='method-grid'>")
    parts.append(
        "<article><h3>Vendor source</h3>"
        f"<p>Baseline: <b>{_esc(bsrc)}</b>. Current: <b>{_esc(csrc)}</b>. "
        "A side without <code>vendors_judged</code> cannot list surprises that were never on the seed list.</p></article>"
    )
    bo = unmatched.get("baseline_only") or []
    co = unmatched.get("current_only") or []
    parts.append(
        "<article><h3>Unmatched prompt ids</h3>"
        f"<p>Baseline only: <b>{len(bo)}</b>. Current only: <b>{len(co)}</b>. "
        "They are listed, not scored in transitions.</p>"
        + (
            f"<p class='ex'>{_esc(', '.join(bo[:12] + co[:12]))}</p>"
            if (bo or co)
            else "<p class='ex'>None — every prompt_id appears on both sides.</p>"
        )
        + "</article>"
    )
    parts.append(
        "<article><h3>Incomplete cells</h3>"
        f"<p><b>{len(incomplete)}</b> matched prompt×engine×arm pairs are error or missing on one side "
        "and are excluded from rates and transitions.</p></article>"
    )
    parts.append(
        "<article><h3>Floor + Δ</h3>"
        f"<p>OUT / disappeared when current mentions &lt; <b>{int(floor)}</b> "
        f"(default 1 = count == 0). NEW when baseline &lt; {int(floor)}. "
        "Δpp is (current rate − baseline rate) × 100. Vendor Δ is mention count "
        "(answer + search-box), after normalize. Share is of the competitor field "
        "(brand share is brand / (brand + field)).</p></article>"
    )
    parts.append("</div></section>")

    bvf = vendors.get("brand_vs_field") or summary.get("brand_vs_field") or {}
    brand_blk = bvf.get("brand") or {}
    field_blk = bvf.get("field") or {}
    parts.append("<h2>Brand vs field</h2>")
    parts.append(
        f"<p class='hint'>{_esc(brand)} named-cell count next to the competitor field "
        "(sum of vendor mentions on comparable engines). Did we rise while Kong fell?</p>"
    )
    parts.append("<div class='table-wrap'><table class='data'><thead><tr>")
    parts.append(
        "<th></th><th class='num'>Baseline</th><th class='num'>Current</th><th class='num'>Δ</th>"
    )
    parts.append("</tr></thead><tbody>")
    parts.append(
        "<tr class='brand-row'><td>"
        f"{_esc(brand)} <span class='badge-brand'>brand</span> named cells</td>"
        f"<td class='num'>{int(brand_blk.get('baseline_mentions') or 0)}</td>"
        f"<td class='num'>{int(brand_blk.get('current_mentions') or 0)}</td>"
        f"<td class='num delta {_delta_class(brand_blk.get('delta'))}'>"
        f"{_esc(_pp_int(brand_blk.get('delta')))}</td></tr>"
    )
    parts.append(
        "<tr><td>Competitor field (mentions)</td>"
        f"<td class='num'>{int(field_blk.get('baseline_mentions') or 0)}</td>"
        f"<td class='num'>{int(field_blk.get('current_mentions') or 0)}</td>"
        f"<td class='num delta {_delta_class(field_blk.get('delta'), invert=True)}'>"
        f"{_esc(_pp_int(field_blk.get('delta')))}</td></tr>"
    )
    share = brand_blk.get("share") or {}
    parts.append(
        f"<tr><td>{_esc(brand)} share of named</td>"
        f"<td class='num'>{_esc(_share_label(share.get('baseline')))}</td>"
        f"<td class='num'>{_esc(_share_label(share.get('current')))}</td>"
        f"<td class='num delta {_delta_class(share.get('delta_pp'))}'>"
        f"{_esc(_pp_label(share.get('delta_pp')))}</td></tr>"
    )
    for arm, lab in (("search", "Mention S"), ("knowledge", "Mention K")):
        pair = brand_blk.get(arm) or {}
        parts.append(f"<tr><td>{lab}</td>{_rate_cell(pair)}</tr>")
    lead_b = (bvf.get("leader") or {}).get("baseline") or {}
    lead_c = (bvf.get("leader") or {}).get("current") or {}
    parts.append(
        "<tr><td>Field leader (ex-brand)</td>"
        f"<td>{_esc(lead_b.get('name') or '—')} "
        f"<span class='muted'>{lead_b.get('mentions') or ''}</span></td>"
        f"<td>{_esc(lead_c.get('name') or '—')} "
        f"<span class='muted'>{lead_c.get('mentions') or ''}</span></td>"
        f"<td class='muted'>{'same' if lead_b.get('name') == lead_c.get('name') else 'changed'}</td></tr>"
    )
    parts.append("</tbody></table></div>")

    compared = list(vendors.get("compared_engines") or comparable)
    if len(compared) > 1:
        parts.append("<div class='chips' id='engine-chips'>")
        parts.append("<button type='button' class='chip on' data-engine='all'>all engines</button>")
        for e in compared:
            parts.append(
                f"<button type='button' class='chip' data-engine='{_esc(e)}'>{_esc(e)}</button>"
            )
        parts.append("</div>")
        parts.append(
            "<p class='hint'>Filter NEW / OUT / movers / rank to names that appeared on that engine.</p>"
        )

    parts.append("<h2>Rank table</h2>")
    parts.append(
        f"<p class='hint'>Top {int(vendors.get('top_n') or DEFAULT_TOP_N)} by either run "
        "(brand included). Rank Δ is ↑ better / ↓ worse / NEW / OUT. "
        f"Compared engines: {_esc(', '.join(compared) or '—')}.</p>"
    )
    parts.append(_rank_table(vendors.get("rank") or [], compared))

    new_n = len(vendors.get("new") or [])
    out_n = len(vendors.get("disappeared") or [])
    list_cap = int(vendors.get("top_list") or DEFAULT_TOP_LIST)
    parts.append(f"<h2>New competitors <span class='count'>{new_n}</span></h2>")
    parts.append(
        f"<p class='hint'>Named in current, below floor ({int(floor)}) in baseline. "
        "Split known-seed vs surprise when vendors_judged exists on current. "
        f"HTML shows the top {list_cap} by current mentions; change.json has the full list.</p>"
    )
    if vendors.get("source", {}).get("current") == "vendors_judged" or vendors.get("new_surprise"):
        parts.append(f"<h3>Known seed ({len(vendors.get('new_known') or [])})</h3>")
        parts.append(
            _vendor_table(
                vendors.get("new_known") or [],
                "No new seed-list competitors.",
                limit=list_cap,
                order_note="ordered by current mentions",
            )
        )
        parts.append(f"<h3>Surprise ({len(vendors.get('new_surprise') or [])})</h3>")
        parts.append(
            _vendor_table(
                vendors.get("new_surprise") or [],
                "No new off-seed names.",
                limit=list_cap,
                order_note="ordered by current mentions",
            )
        )
    else:
        parts.append(
            _vendor_table(
                vendors.get("new") or [],
                "No new competitors. (Regex-only sides cannot invent surprises.)",
                limit=list_cap,
                order_note="ordered by current mentions",
            )
        )

    parts.append(f"<h2>No longer ranking <span class='count'>{out_n}</span></h2>")
    parts.append(
        f"<p class='hint'>Present in baseline (≥ floor {int(floor)}), gone or near-zero in current. "
        "Do not read these as market change if an engine was skipped (see banner). "
        f"HTML shows the top {list_cap} by baseline mentions.</p>"
    )
    parts.append(
        _vendor_table(
            vendors.get("disappeared") or [],
            "Nobody left the ranking.",
            limit=list_cap,
            order_note="ordered by baseline mentions",
        )
    )

    movers_n = int(vendors.get("top_movers") or DEFAULT_TOP_MOVERS)
    parts.append("<h2>Risers / fallers</h2>")
    parts.append(
        f"<p class='hint'>Largest mention-count Δ among names still ranking on both sides "
        f"(top {movers_n}). Share Δ is percentage points of the competitor field.</p>"
    )
    parts.append(f"<h3>Risers ({len(vendors.get('risers') or [])})</h3>")
    parts.append(_vendor_table(vendors.get("risers") or [], "No risers."))
    parts.append(f"<h3>Fallers ({len(vendors.get('fallers') or [])})</h3>")
    parts.append(_vendor_table(vendors.get("fallers") or [], "No fallers."))

    parts.append("<h2>Brand mention rates</h2>")
    skipped_rates = rates.get("skipped_engines") or []
    compared_rates = rates.get("compared_engines") or comparable
    blend_note = rates.get("blend_note") or ""
    parts.append(
        "<p class='hint'>Absolute rates plus Δpp per engine × arm. The <b>all</b> row pools "
        f"comparable engines only ({_esc(', '.join(compared_rates) or 'none')}). "
        "Search rate is the share of completed search arms that fired a search tool."
        + (
            f" Skipped from the blend: {_esc(', '.join(skipped_rates))}."
            if skipped_rates
            else ""
        )
        + (f" {_esc(blend_note)}" if blend_note else "")
        + "</p>"
    )
    parts.append("<div class='table-wrap'><table class='data'><thead><tr>")
    parts.append(
        "<th>Engine</th><th>Arm</th><th class='num'>Baseline</th><th class='num'>Current</th><th class='num'>Δpp</th>"
    )
    parts.append("</tr></thead><tbody>")
    overall = rates.get("overall") or {}
    for arm, label in (("knowledge", "K"), ("search", "S"), ("search_rate", "search rate")):
        pair = overall.get(arm) or {}
        parts.append(f"<tr class='overall'><td>all</td><td>{label}</td>{_rate_cell(pair)}</tr>")
    for engine in engines:
        rec = (rates.get("engines") or {}).get(engine) or {}
        for arm, label in (("knowledge", "K"), ("search", "S"), ("search_rate", "search rate")):
            parts.append(
                f"<tr><td>{_esc(engine)}</td><td>{label}</td>"
                f"{_rate_cell(rec.get(arm) or {})}</tr>"
            )
    parts.append("</tbody></table></div>")

    counts = transitions.get("counts") or {}
    parts.append("<h2>Prompt-level brand transitions</h2>")
    parts.append("<div class='hero slim'>")
    for key in TRANSITIONS:
        parts.append(
            f"<article class='metric'><p class='eyebrow'>{_esc(TRANSITION_LABELS[key])}</p>"
            f"<p class='metric-n'>{int(counts.get(key) or 0)}</p></article>"
        )
    parts.append(
        "<article class='metric'><p class='eyebrow'>stance changed</p>"
        f"<p class='metric-n'>{int(transitions.get('stance_changed') or 0)}</p>"
        "<p class='hint'>hit→hit with judge.json on both sides</p></article>"
    )
    parts.append("</div>")
    parts.append(
        "<p class='hint'>Click a column header to sort. Stance/position only when both runs have judge.json on a hit→hit.</p>"
    )
    trows = list(transitions.get("rows") or [])
    parts.append("<div class='table-wrap'><table class='data sortable' id='transitions'><thead><tr>")
    for col in (
        "Query",
        "Engine",
        "Arm",
        "Transition",
        "Baseline",
        "Current",
        "Stance",
        "Position",
    ):
        parts.append(f"<th data-sort='{col.lower()}'>{col}</th>")
    parts.append("</tr></thead><tbody>")
    for r in trows:
        st = r.get("stance") or {}
        pos = r.get("position") or {}
        stance_txt = "—"
        if st:
            stance_txt = f"{st.get('baseline') or '—'} → {st.get('current') or '—'}"
            if st.get("changed"):
                stance_txt += " ✱"
        pos_txt = "—"
        if pos:
            pos_txt = f"{pos.get('baseline') or '—'} → {pos.get('current') or '—'}"
            if pos.get("changed"):
                pos_txt += " ✱"
        bkind = (r.get("baseline") or {}).get("kind") or ""
        ckind = (r.get("current") or {}).get("kind") or ""
        q = r.get("prompt_text") or r.get("prompt_id")
        parts.append(
            "<tr "
            f"data-transition='{_esc(r.get('transition'))}' "
            f"data-engine='{_esc(r.get('engine'))}' "
            f"data-arm='{_esc(r.get('arm'))}'>"
            f"<td class='qcell'><span class='prompt-q'>{_esc(q)}</span> "
            f"<span class='why'>{_esc(r.get('prompt_id'))}</span></td>"
            f"<td>{_esc(r.get('engine'))}</td><td>{_esc(r.get('arm'))}</td>"
            f"<td><span class='chip-t { _esc(r.get('transition')) }'>"
            f"{_esc(TRANSITION_LABELS.get(r.get('transition'), r.get('transition')))}</span></td>"
            f"<td class='{_esc(bkind)}'>{_esc(bkind)}</td>"
            f"<td class='{_esc(ckind)}'>{_esc(ckind)}</td>"
            f"<td>{_esc(stance_txt)}</td><td>{_esc(pos_txt)}</td></tr>"
        )
    if not trows:
        parts.append("<tr><td colspan='8' class='hint'>No matched completed cells.</td></tr>")
    parts.append("</tbody></table></div>")

    if incomplete:
        parts.append("<h2>Incomplete cells</h2>")
        parts.append("<div class='table-wrap'><table class='data'><thead><tr>")
        parts.append("<th>prompt_id</th><th>Engine</th><th>Arm</th><th>Baseline</th><th>Current</th>")
        parts.append("</tr></thead><tbody>")
        for cell in incomplete[:80]:
            parts.append(
                "<tr>"
                f"<td>{_esc(cell.get('prompt_id'))}</td>"
                f"<td>{_esc(cell.get('engine'))}</td>"
                f"<td>{_esc(cell.get('arm'))}</td>"
                f"<td>{_esc(cell.get('baseline'))}</td>"
                f"<td>{_esc(cell.get('current'))}</td></tr>"
            )
        parts.append("</tbody></table></div>")
        if len(incomplete) > 80:
            parts.append(f"<p class='hint'>Showing 80 of {len(incomplete)}.</p>")

    parts.append("<footer>")
    parts.append(f"<p>schema {_esc(payload.get('schema_version') or SCHEMA_VERSION)}</p>")
    parts.append(
        f"<p>baseline {_esc(baseline.get('path'))} ({_esc(bsrc)}) → "
        f"current {_esc(current.get('path'))} ({_esc(csrc)})</p>"
    )
    parts.append("</footer></main>")
    parts.append(f"<script>{_JS}</script></body></html>\n")
    return "\n".join(parts)


_CSS = """
:root{--bg:#0b0d10;--card:#14181e;--line:rgba(255,255,255,.08);--text:#e8edf2;--muted:#8b95a3;
--teal:#3dccc7;--rec:#6ee7b7;--men:#e8b86d;--wrn:#f0a36b;--rej:#e07a7a;--miss:#5b6570}
*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--text);
font-family:ui-sans-serif,system-ui,-apple-system,sans-serif;line-height:1.45}
.top{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;align-items:center;
padding:14px 28px;background:rgba(11,13,16,.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}
.wordmark{letter-spacing:.14em;text-transform:uppercase;font-weight:650;font-size:15px}
.domain{margin-left:10px;color:var(--muted)}
.pill{border:1px solid var(--line);border-radius:999px;padding:3px 10px;font-size:12px;color:var(--muted);margin-left:8px}
.arrow{color:var(--muted);margin-left:8px}
main{max-width:1200px;margin:0 auto;padding:32px 24px 80px}
h1{font-size:22px;font-weight:620;letter-spacing:-.03em;margin:6px 0 18px}
h2{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:var(--muted);margin:36px 0 12px}
h2 .count{margin-left:8px;letter-spacing:0;text-transform:none;color:var(--text);font-size:13px;font-weight:650}
h3{font-size:14px;margin:18px 0 8px}
.eyebrow{margin:0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.hint{color:var(--muted);font-size:13px}
.muted{color:var(--muted)}
.actions{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:22px}
.executive{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:22px}
.executive h1{font-size:24px;margin:8px 0 14px;max-width:70ch}
.exec-story{color:var(--text);font-size:15px;max-width:78ch;margin:0 0 16px;line-height:1.5}
.exec-share{color:var(--muted);font-size:14px;max-width:78ch;margin:12px 0 16px;line-height:1.5}
.exec-split-h{margin:8px 0 8px;font-size:13px;letter-spacing:.12em}
.split-banner{margin:0 0 12px;color:var(--wrn);font-size:14px;font-weight:620}
.exec-split{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:8px 0 16px}
.exec-split.disagree{gap:14px}
.split-card{background:#0e1116;border:1px solid var(--line);border-radius:14px;padding:16px 16px 14px;
border-left-width:5px}
.split-card.win{border-color:rgba(110,231,183,.55);border-left-color:var(--rec);
background:rgba(110,231,183,.06)}
.split-card.loss{border-color:rgba(224,122,122,.55);border-left-color:var(--rej);
background:rgba(224,122,122,.06)}
.split-card.flat{border-color:var(--line)}
.exec-split.disagree .split-card{min-height:168px}
.split-name{margin:8px 0 12px;font-size:28px;font-weight:650;letter-spacing:-.03em}
.split-card.win .split-name{color:var(--rec)}
.split-card.loss .split-name{color:var(--rej)}
.split-line{margin:0 0 6px;font-size:13px;font-variant-numeric:tabular-nums}
.split-line span{display:inline-block;min-width:72px;color:var(--muted);font-size:11px;
letter-spacing:.08em;text-transform:uppercase}
.exec-cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-top:8px}
.exec-cols h3{margin:0 0 8px;font-size:14px}
.exec-cols ul{margin:0;padding-left:18px}
.exec-cols li{margin:0 0 8px;font-size:14px;color:var(--text)}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.hero.slim{margin:8px 0 16px}
.metric{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:16px;min-height:110px;display:flex;flex-direction:column}
.metric-n{margin:10px 0 6px;font-size:28px;font-weight:620;letter-spacing:-.03em}
.method{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:22px}
.method>p{color:var(--muted);font-size:14px;max-width:72ch}
.method-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin-top:14px}
.method-grid article{background:#0e1116;border:1px solid var(--line);border-radius:12px;padding:14px}
.method-grid h3{margin:0 0 8px;font-size:14px}
.method-grid p{margin:0 0 8px;font-size:13px;color:var(--muted)}
.method-grid .ex{margin:0;font-size:12px;color:#a8b2bf}
.method-grid code,code{font-size:12px;color:var(--teal)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card);margin:8px 0 20px}
.data{width:100%;border-collapse:collapse;font-size:13px}
.data th{text-align:left;font-size:11px;color:var(--muted);letter-spacing:.08em;text-transform:uppercase;
padding:8px;border-bottom:1px solid var(--line);background:#12161c;cursor:default}
.data.sortable th{cursor:pointer}
.data td{padding:8px;border-top:1px solid var(--line);vertical-align:top}
.data tr.overall td{background:rgba(61,204,199,.05);font-weight:620}
.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.delta.up{color:var(--rec)}
.delta.down{color:var(--rej)}
.delta.flat{color:var(--muted)}
.badge-surprise,.badge-brand{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;
font-size:10px;letter-spacing:.04em;text-transform:uppercase;font-weight:650;vertical-align:middle}
.badge-surprise{background:rgba(240,163,107,.18);color:var(--wrn)}
.badge-brand{background:rgba(232,184,109,.16);color:var(--men)}
.gap-banner{background:rgba(240,163,107,.1);border:1px solid rgba(240,163,107,.35);border-radius:14px;
padding:14px 18px;margin:0 0 22px;color:var(--wrn)}
.gap-banner p{margin:0 0 8px}.gap-banner p:last-child{margin:0}
.slash{color:var(--muted);margin:0 6px;font-weight:400}
.chips{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.chip{border:1px solid var(--line);background:transparent;color:var(--text);border-radius:999px;
padding:4px 11px;font-size:12px;cursor:pointer}
.chip.on{border-color:var(--teal);color:var(--teal)}
tr.brand-row td{background:rgba(232,184,109,.06)}
.rank-chip{display:inline-block;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:650;
border:1px solid var(--line)}
.rank-chip.up{color:var(--rec);background:rgba(110,231,183,.12)}
.rank-chip.down{color:var(--rej);background:rgba(224,122,122,.12)}
.rank-chip.new{color:var(--men);background:rgba(232,184,109,.14)}
.rank-chip.out{color:var(--miss)}
.rank-chip.flat{color:var(--muted)}
.chip-t{display:inline-block;border-radius:999px;padding:2px 8px;font-size:11px;border:1px solid var(--line)}
.chip-t.miss_to_hit{color:var(--rec);background:rgba(110,231,183,.12)}
.chip-t.hit_to_miss{color:var(--rej);background:rgba(224,122,122,.12)}
.chip-t.hit_to_hit{color:var(--men);background:rgba(232,184,109,.12)}
.chip-t.still_miss{color:var(--miss)}
td.hit{color:var(--rec)}
td.miss{color:var(--miss)}
.qcell{max-width:420px}
.prompt-q{font-size:13px}
.why{color:var(--muted);font-size:11px;margin-left:6px}
footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);color:var(--muted);font-size:12px}
"""

_JS = r"""
(function(){
  document.querySelectorAll('table.sortable').forEach(function(table){
    const tbody = table.tBodies[0];
    if(!tbody) return;
    table.querySelectorAll('th').forEach(function(th, idx){
      th.addEventListener('click', function(){
        const rows = Array.from(tbody.rows);
        const dir = th.dataset.dir === 'asc' ? -1 : 1;
        table.querySelectorAll('th').forEach(function(h){ h.dataset.dir = ''; });
        th.dataset.dir = dir === 1 ? 'asc' : 'desc';
        rows.sort(function(a,b){
          const va = (a.cells[idx] && a.cells[idx].innerText || '').toLowerCase();
          const vb = (b.cells[idx] && b.cells[idx].innerText || '').toLowerCase();
          const na = parseFloat(va.replace(/[^0-9.+-]/g,''));
          const nb = parseFloat(vb.replace(/[^0-9.+-]/g,''));
          if(!isNaN(na) && !isNaN(nb) && va.search(/[0-9]/) >= 0){
            return (na - nb) * dir;
          }
          if(va < vb) return -1 * dir;
          if(va > vb) return 1 * dir;
          return 0;
        });
        rows.forEach(function(r){ tbody.appendChild(r); });
      });
    });
  });
  const chips = document.querySelectorAll('#engine-chips .chip');
  chips.forEach(function(chip){
    chip.addEventListener('click', function(){
      chips.forEach(function(c){ c.classList.remove('on'); });
      chip.classList.add('on');
      const eng = chip.getAttribute('data-engine') || 'all';
      document.querySelectorAll('tr[data-engines]').forEach(function(tr){
        const list = (tr.getAttribute('data-engines') || '').split(/\s+/);
        const ok = eng === 'all' || list.indexOf(eng) >= 0;
        tr.style.display = ok ? '' : 'none';
      });
    });
  });
})();
"""


def write_change_report(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    html_name: str | None = None,
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "change.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    label = (payload.get("current") or {}).get("label") or "run"
    html_path = out_dir / (html_name or f"{label}-change-report.html")
    html_path.write_text(render_change_html(payload))
    return json_path, html_path
