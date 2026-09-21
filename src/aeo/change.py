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
    baseline: RunSnapshot, current: RunSnapshot, engines: list[str]
) -> dict[str, Any]:
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
    overall = {
        "knowledge": _rate_pair(_pool(b, "knowledge"), _pool(c, "knowledge")),
        "search": _rate_pair(_pool(b, "search"), _pool(c, "search")),
        "search_rate": _rate_pair(_pool(b, "search_rate"), _pool(c, "search_rate")),
    }
    return {"engines": by_engine, "overall": overall}


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


def vendor_counts(
    snapshot: RunSnapshot,
    *,
    brand: str,
    aliases: list[str],
    competitors: list[str],  # noqa: ARG001 — kept for call-site symmetry
    alias_map: Any,
) -> dict[str, dict[str, Any]]:
    """Normalized vendor → {name, origin, answer, search_box}. Brand excluded."""
    store = snapshot.vendors
    by_key: dict[str, dict[str, Any]] = {}

    def bump(rec: dict[str, str], *, answer: bool = False, search_box: bool = False) -> None:
        name = rec.get("name") or ""
        origin = rec.get("origin") or "known"
        key = alias_map.key_for(name)
        if not key:
            return
        slot = by_key.setdefault(
            key,
            {"key": key, "name": name, "origin": origin, "answer": 0, "search_box": 0},
        )
        slot["name"] = alias_map.display_for(name) or slot["name"]
        if origin == "surprise":
            slot["origin"] = "surprise"
        if answer:
            slot["answer"] += 1
        if search_box:
            slot["search_box"] += 1

    for row in snapshot.rows:
        pid = row.get("prompt_id")
        for engine, arms in (row.get("engines") or {}).items():
            if not isinstance(arms, dict):
                continue
            for arm_name in ARMS:
                arm = arms.get(arm_name)
                if _cell_state(arm) != "ok":
                    continue
                vkey = f"{pid}|{engine}|{arm_name}"
                cell = store.get(vkey)
                for rec in classified_vendors_for_arm(arm, cell, alias_map, brand, aliases):
                    bump(rec, answer=True)
                if arm_name == "search":
                    for rec in classified_query_vendors_for_arm(
                        arm, cell, alias_map, brand, aliases
                    ):
                        bump(rec, search_box=True)
    return by_key


def _mentions(rec: dict[str, Any] | None) -> int:
    if not rec:
        return 0
    return int(rec.get("answer") or 0) + int(rec.get("search_box") or 0)


def diff_vendors(
    baseline: RunSnapshot,
    current: RunSnapshot,
    *,
    brand: str,
) -> dict[str, Any]:
    aliases = list(dict.fromkeys([*baseline.aliases, *current.aliases]))
    competitors = list(dict.fromkeys([*baseline.competitors, *current.competitors]))
    alias_map = seed_alias_map(
        brand,
        aliases,
        competitors,
        list(baseline.vendors.values()) + list(current.vendors.values()),
    )
    b = vendor_counts(
        baseline, brand=brand, aliases=aliases, competitors=competitors, alias_map=alias_map
    )
    c = vendor_counts(
        current, brand=brand, aliases=aliases, competitors=competitors, alias_map=alias_map
    )
    keys = set(b) | set(c)
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
        if br and not cr:
            origin = br.get("origin") or origin
        if b_n == 0 and c_n > 0:
            status = "new"
        elif b_n > 0 and c_n == 0:
            status = "disappeared"
        elif c_n > b_n:
            status = "riser"
        elif c_n < b_n:
            status = "faller"
        else:
            status = "flat"
        rows.append(
            {
                "key": key,
                "name": name,
                "origin": origin,
                "surprise": origin == "surprise",
                "status": status,
                "baseline": {"answer": b_ans, "search_box": b_q, "mentions": b_n},
                "current": {"answer": c_ans, "search_box": c_q, "mentions": c_n},
                "delta": c_n - b_n,
                "delta_answer": c_ans - b_ans,
                "delta_search_box": c_q - b_q,
            }
        )

    rows.sort(key=lambda r: (-abs(r["delta"]), -r["current"]["mentions"], r["name"].lower()))
    risers = [r for r in rows if r["status"] == "riser"]
    fallers = [r for r in rows if r["status"] == "faller"]
    new = [r for r in rows if r["status"] == "new"]
    disappeared = [r for r in rows if r["status"] == "disappeared"]
    risers.sort(key=lambda r: (-r["delta"], -r["current"]["mentions"], r["name"].lower()))
    fallers.sort(key=lambda r: (r["delta"], -r["baseline"]["mentions"], r["name"].lower()))
    new.sort(key=lambda r: (-r["current"]["mentions"], r["name"].lower()))
    disappeared.sort(key=lambda r: (-r["baseline"]["mentions"], r["name"].lower()))
    surprises = [r for r in rows if r["surprise"]]
    surprises.sort(key=lambda r: (-r["current"]["mentions"], r["name"].lower()))
    return {
        "source": {"baseline": baseline.vendor_source, "current": current.vendor_source},
        "risers": risers,
        "fallers": fallers,
        "new": new,
        "disappeared": disappeared,
        "surprises": surprises,
        "all": rows,
    }


def _biggest_mover(vendors: dict[str, Any]) -> dict[str, Any] | None:
    candidates = [
        r
        for r in vendors.get("all") or []
        if r.get("delta") and r.get("status") in ("riser", "faller", "new", "disappeared")
    ]
    if not candidates:
        return None
    best = max(
        candidates,
        key=lambda r: (abs(int(r["delta"])), abs(int(r.get("delta_answer") or 0)), r["name"].lower()),
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


def _headline(
    brand: str,
    rates: dict[str, Any],
    mover: dict[str, Any] | None,
    surprise: dict[str, Any] | None,
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
    if mover:
        verb = {
            "riser": "rose",
            "new": "appeared",
            "faller": "fell",
            "disappeared": "disappeared",
        }.get(mover["status"], "moved")
        if mover["status"] == "new":
            bits.append(f"{mover['name']} appeared with {mover['current']} mentions.")
        elif mover["status"] == "disappeared":
            bits.append(f"{mover['name']} disappeared ({mover['baseline']} → 0).")
        else:
            n = abs(int(mover["delta"]))
            unit = "mention" if n == 1 else "mentions"
            bits.append(f"{mover['name']} {verb} {n} {unit}.")
    if surprise:
        bits.append(f"New surprise: {surprise['name']} ({surprise['current']}).")
    elif not mover:
        bits.append("No competitor movement.")
    return " ".join(bits)


def diff_runs(
    baseline: RunSnapshot,
    current: RunSnapshot,
    *,
    brand: str,
) -> dict[str, Any]:
    brand = (brand or current.brand or baseline.brand or "").strip()
    if not brand:
        raise ValueError("brand is required (--brand or workspace.brand)")
    engines = _engine_order(baseline.docs, current.docs)
    rates = diff_brand_rates(baseline, current, engines)
    transitions = prompt_transitions(baseline, current, engines)
    vendors = diff_vendors(baseline, current, brand=brand)
    mover = _biggest_mover(vendors)
    surprise = _new_surprise(vendors)
    headline = _headline(brand, rates, mover, surprise)
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
        "summary": {
            "headline": headline,
            "brand_delta_pp": {
                "knowledge": (rates["overall"]["knowledge"] or {}).get("delta_pp"),
                "search": (rates["overall"]["search"] or {}).get("delta_pp"),
                "search_rate": (rates["overall"]["search_rate"] or {}).get("delta_pp"),
            },
            "biggest_competitor_mover": mover,
            "new_surprise": surprise,
        },
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
            "notes": [
                "Same roster is assumed; unmatched prompt_ids are listed, not scored in transitions.",
                "Mention rates skip error / missing cells. Those cells are listed as incomplete.",
                "Brand hits are deterministic brand_mentioned. Stance/position only when judge.json exists on both sides of a hit→hit.",
                "Vendor counts prefer vendors_judged.json (LLM ∪ regex) per cell; otherwise regex competitor_mentions / vendors_in_search_queries.",
                "A side without vendors_judged cannot surface surprises that were never on the seed list.",
                "Names are merged with aeo.vendors normalize (Kong Gateway ≡ Kong when Kong is seeded).",
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


def _vendor_table(rows: list[dict[str, Any]], empty: str) -> str:
    if not rows:
        return f"<p class='hint'>{_esc(empty)}</p>"
    bits = [
        "<div class='table-wrap'><table class='data'><thead><tr>",
        "<th>Name</th><th>Origin</th><th class='num'>Baseline</th><th class='num'>Current</th>",
        "<th class='num'>Δ</th><th class='num'>Answer Δ</th><th class='num'>Search-box Δ</th>",
        "</tr></thead><tbody>",
    ]
    for r in rows:
        badge = (
            " <span class='badge-surprise'>surprise</span>" if r.get("surprise") else ""
        )
        cls = _delta_class(r.get("delta"), invert=True)
        bits.append("<tr>")
        bits.append(f"<td>{_esc(r.get('name'))}{badge}</td>")
        bits.append(f"<td class='muted'>{_esc(r.get('origin'))}</td>")
        bits.append(f"<td class='num'>{int((r.get('baseline') or {}).get('mentions') or 0)}</td>")
        bits.append(f"<td class='num'>{int((r.get('current') or {}).get('mentions') or 0)}</td>")
        bits.append(f"<td class='num delta {cls}'>{_esc(_pp_int(r.get('delta')))}</td>")
        bits.append(f"<td class='num'>{_esc(_pp_int(r.get('delta_answer')))}</td>")
        bits.append(f"<td class='num'>{_esc(_pp_int(r.get('delta_search_box')))}</td>")
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
    bsrc = (methodology.get("vendor_source") or {}).get("baseline") or baseline.get("vendor_source")
    csrc = (methodology.get("vendor_source") or {}).get("current") or current.get("vendor_source")
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
        "<article><h3>Δpp</h3>"
        "<p>Percentage-point change: (current rate − baseline rate) × 100. "
        "Vendor Δ is a mention-count change (answer + search-box names), after normalize.</p></article>"
    )
    parts.append("</div></section>")

    parts.append("<section class='actions'>")
    parts.append("<p class='eyebrow'>Summary</p>")
    parts.append(f"<h1>{_esc(summary.get('headline') or '')}</h1>")
    parts.append("<div class='hero'>")
    bd = summary.get("brand_delta_pp") or {}
    for lab, key, hint in (
        ("Brand Δ (S)", "search", "search-arm mention, all engines"),
        ("Brand Δ (K)", "knowledge", "knowledge-arm mention, all engines"),
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
    parts.append("</div></section>")

    parts.append("<h2>Brand mention rates</h2>")
    parts.append(
        "<p class='hint'>Absolute rates plus Δpp per engine × arm. Search rate is the share of "
        "completed search arms that fired a search tool.</p>"
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

    parts.append("<h2>Competitor / vendor fan-out</h2>")
    parts.append(
        "<p class='hint'>Mention count = cells that named the vendor in the answer plus cells "
        "that typed it into the search box. Names are normalized. Surprises are off the config seed list.</p>"
    )
    parts.append("<h3>Risers</h3>")
    parts.append(_vendor_table(vendors.get("risers") or [], "No risers."))
    parts.append("<h3>Fallers</h3>")
    parts.append(_vendor_table(vendors.get("fallers") or [], "No fallers."))
    parts.append("<h3>New entrants</h3>")
    parts.append(_vendor_table(vendors.get("new") or [], "No new vendors."))
    parts.append("<h3>Disappeared</h3>")
    parts.append(_vendor_table(vendors.get("disappeared") or [], "Nobody disappeared."))

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
h3{font-size:14px;margin:18px 0 8px}
.eyebrow{margin:0;font-size:11px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.hint{color:var(--muted);font-size:13px}
.muted{color:var(--muted)}
.actions{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:22px 24px;margin-bottom:22px}
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
.badge-surprise{display:inline-block;margin-left:6px;padding:0 6px;border-radius:999px;
font-size:10px;letter-spacing:.04em;text-transform:uppercase;font-weight:650;
background:rgba(240,163,107,.18);color:var(--wrn);vertical-align:middle}
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
  const table = document.getElementById('transitions');
  if(!table) return;
  const tbody = table.tBodies[0];
  table.querySelectorAll('th').forEach(function(th, idx){
    th.addEventListener('click', function(){
      const rows = Array.from(tbody.rows);
      const dir = th.dataset.dir === 'asc' ? -1 : 1;
      table.querySelectorAll('th').forEach(function(h){ h.dataset.dir = ''; });
      th.dataset.dir = dir === 1 ? 'asc' : 'desc';
      rows.sort(function(a,b){
        const va = (a.cells[idx] && a.cells[idx].innerText || '').toLowerCase();
        const vb = (b.cells[idx] && b.cells[idx].innerText || '').toLowerCase();
        if(va < vb) return -1 * dir;
        if(va > vb) return 1 * dir;
        return 0;
      });
      rows.forEach(function(r){ tbody.appendChild(r); });
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
