"""Every summary number in a report, built from one list of per-answer records.

A record is one question × engine × arm. Reports, the board judge's input and the
headline all read ``summarize(records)``; nothing else counts answers, so the
numbers cannot disagree with each other.

Separate outcomes per answer:

- named: the strict brand matcher found the brand in the answer
- cited: a link to the brand's domain is among the answer's sources
- described accurately: the judge says what the answer says about the brand is
  right (only judged when named; ``None`` = not judged)
- recommended: the judge says the answer pushes the brand as something to use

Named answers are split by browsing status: "unaided" (no-search arm and browsing
confirmed off), "with search" (search arm) and "browsing unknown" (no-search arm
where browsing could not be confirmed off or search was observed). Only unaided
answers count as recall from the model's own knowledge.

An engine with no stored answers is "not run", never 0.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from aeo.measurement import prompt_group
from aeo.retrieval import arm_browsing, brand_funnel, host_matches, urls_in

ENGINES = ("claude", "codex", "grok")
ARMS = ("knowledge", "search")


def _cited(arm: dict[str, Any], domain: str) -> bool:
    if "brand_cited" in arm and arm.get("retrieval") is None:
        return bool(arm["brand_cited"])
    urls = list(urls_in(arm.get("raw_response_text") or ""))
    chain = arm.get("retrieval") or {}
    urls += [u for u in chain.get("cited_urls") or [] if u not in urls]
    return any(host_matches(u, domain) for u in urls)


def answer_records(docs: dict[str, dict[str, Any]], judge: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    judge = judge or {}
    out: list[dict[str, Any]] = []
    for engine in ENGINES:
        doc = docs.get(engine)
        if not doc:
            continue
        domain = str((doc.get("workspace") or {}).get("domain") or "")
        for pr in doc.get("prompts") or []:
            arms = (pr.get("engines") or {}).get(engine) or {}
            for arm_name in ARMS:
                arm = arms.get(arm_name)
                rec: dict[str, Any] = {
                    "prompt_id": str(pr.get("prompt_id") or ""),
                    "prompt_text": pr.get("prompt_text") or "",
                    "group": prompt_group(pr),
                    "engine": engine,
                    "arm": arm_name,
                }
                if not isinstance(arm, dict):
                    rec["status"] = "missing"
                    out.append(rec)
                    continue
                if arm.get("error"):
                    rec["status"] = "error"
                    rec["error"] = str(arm.get("error"))[:200]
                    out.append(rec)
                    continue
                named = bool(arm.get("brand_mentioned"))
                j = judge.get(f"{rec['prompt_id']}|{engine}|{arm_name}") if named else None
                j = j if isinstance(j, dict) else {}
                browsing = arm_browsing(engine, arm_name, arm)
                acc = j.get("accurate") if named else None
                rec.update(
                    status="ok",
                    browsing=browsing,
                    browsing_recorded="browsing" in arm,
                    named=named,
                    cited=_cited(arm, domain),
                    judged=bool(j.get("stance")),
                    accurate=acc if isinstance(acc, bool) else None,
                    recommended=(j.get("stance") == "recommend") if j.get("stance") else None,
                    stance=j.get("stance"),
                    position=j.get("position"),
                    searched=browsing == "searched",
                )
                rec["recall"] = (
                    "with search" if arm_name == "search"
                    else ("unaided" if browsing == "none" else "browsing unknown")
                )
                if arm_name == "search":
                    chain = arm.get("retrieval")
                    if chain is not None:
                        rec["funnel"] = brand_funnel(chain, domain, named=named,
                                                     cited_urls=list(chain.get("cited_urls") or urls_in(arm.get("raw_response_text") or "")))
                    else:
                        legacy = {"steps": [{"kind": "search", "query": q, "results": None} for q in arm.get("search_queries") or []],
                                  "results_exposed": False}
                        rec["funnel"] = brand_funnel(legacy, domain, named=named,
                                                     cited_urls=urls_in(arm.get("raw_response_text") or ""))
                        rec["funnel"]["recorded"] = False
                out.append(rec)
    return out


def _blank() -> dict[str, Any]:
    return {
        "planned": 0, "answers": 0, "errors": 0, "missing": 0,
        "named": 0, "named_unaided": 0, "named_with_search": 0, "named_browsing_unknown": 0,
        "cited": 0, "accurate": 0, "inaccurate": 0, "accuracy_not_judged": 0, "recommended": 0,
        "knowledge_answers": 0, "unaided_answers": 0, "browsing_unknown_answers": 0,
        "search_answers": 0, "searched": 0,
    }


def _add(acc: dict[str, Any], r: dict[str, Any]) -> None:
    acc["planned"] += 1
    if r["status"] == "error":
        acc["errors"] += 1
        return
    if r["status"] == "missing":
        acc["missing"] += 1
        return
    acc["answers"] += 1
    if r["arm"] == "knowledge":
        acc["knowledge_answers"] += 1
        if r["recall"] == "unaided":
            acc["unaided_answers"] += 1
        else:
            acc["browsing_unknown_answers"] += 1
    else:
        acc["search_answers"] += 1
        acc["searched"] += bool(r.get("searched"))
    if r["named"]:
        acc["named"] += 1
        key = {"unaided": "named_unaided", "with search": "named_with_search"}.get(r["recall"], "named_browsing_unknown")
        acc[key] += 1
        if r.get("accurate") is True:
            acc["accurate"] += 1
        elif r.get("accurate") is False:
            acc["inaccurate"] += 1
        else:
            acc["accuracy_not_judged"] += 1
        acc["recommended"] += bool(r.get("recommended"))
    acc["cited"] += bool(r.get("cited"))


def summarize(records: Iterable[dict[str, Any]], *, engines: Iterable[str] = ENGINES) -> dict[str, Any]:
    records = list(records)
    total = _blank()
    by_engine: dict[str, dict[str, Any]] = {}
    groups: dict[str, dict[str, Any]] = {}
    for e in engines:
        by_engine[e] = _blank()
    for r in records:
        by_engine.setdefault(r["engine"], _blank())
        groups.setdefault(r["group"], _blank())
        _add(groups[r["group"]], r)
        if r["group"] == "measurement":
            _add(total, r)
            _add(by_engine[r["engine"]], r)
    for e, rec in by_engine.items():
        if rec["planned"] == 0:
            rec["status"] = "not run"
        elif rec["answers"] == 0:
            rec["status"] = "failed"
        else:
            rec["status"] = "ran"
    funnel = funnel_summary(r for r in records if r["group"] == "measurement")
    return {
        "total": total,
        "by_engine": by_engine,
        "groups": groups,
        "engines_run": [e for e, rec in by_engine.items() if rec["status"] != "not run"],
        "engines_not_run": [e for e, rec in by_engine.items() if rec["status"] == "not run"],
        "funnel": funnel,
    }


def funnel_summary(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Where the brand's site appeared or dropped out, across search-arm answers."""
    out: dict[str, Any] = {"answers": 0, "searched": 0, "results_recorded": 0, "in_results": 0,
                           "opened": 0, "cited": 0, "named": 0, "chain_recorded": 0, "stages": {}, "by_engine": {}}
    for r in records:
        if r.get("status") != "ok" or r.get("arm") != "search":
            continue
        f = r.get("funnel") or {}
        eng = out["by_engine"].setdefault(r["engine"], {"answers": 0, "searched": 0, "results_recorded": 0,
                                                          "in_results": 0, "opened": 0, "cited": 0, "named": 0,
                                                          "chain_recorded": 0})
        for acc in (out, eng):
            acc["answers"] += 1
            acc["chain_recorded"] += f.get("recorded", True) is not False
            if f.get("searches") or f.get("fetches"):
                acc["searched"] += 1
            if f.get("in_results") is not None:
                acc["results_recorded"] += 1
                acc["in_results"] += bool(f.get("in_results"))
            acc["opened"] += bool(f.get("opened"))
            acc["cited"] += bool(f.get("cited"))
            acc["named"] += bool(f.get("named"))
        stage = f.get("stage") or "unknown"
        out["stages"][stage] = out["stages"].get(stage, 0) + 1
    return out


def headline(summary: dict[str, Any], brand: str) -> str:
    """One deterministic sentence with the run's core counts."""
    t = summary["total"]
    if not t["answers"]:
        return f"No answers were collected for {brand}."
    parts = [f"{brand} was named in {t['named']} of {t['answers']} answers"]
    split = []
    if t["named_unaided"]:
        split.append(f"{t['named_unaided']} from memory")
    if t["named_with_search"]:
        split.append(f"{t['named_with_search']} with web search")
    if t["named_browsing_unknown"]:
        split.append(f"{t['named_browsing_unknown']} where search was not confirmed off")
    if split:
        parts[0] += " (" + ", ".join(split) + ")"
    parts.append(f"linked as a source in {t['cited']}")
    parts.append(f"recommended in {t['recommended']}")
    if t["named"]:
        if t["accuracy_not_judged"] and t["accuracy_not_judged"] >= t["named"]:
            acc = "accuracy not checked yet"
        else:
            acc = f"described accurately in {t['accurate']}"
            if t["accuracy_not_judged"]:
                acc += f" ({t['accuracy_not_judged']} not checked)"
        parts.append(acc)
    s = ", ".join(parts[:-1]) + " and " + parts[-1] + "."
    if t["errors"] or t["missing"]:
        s += f" {t['errors'] + t['missing']} more answers failed and are not counted."
    return s


def board_count_lines(summary: dict[str, Any], brand: str) -> list[str]:
    """Counts handed to the board judge. Same numbers as the report."""
    t = summary["total"]
    lines = [
        f"TOTAL: {t['answers']} answers. {brand} named in {t['named']} of {t['answers']} answers "
        f"({t['named_unaided']} without search, {t['named_with_search']} with search, "
        f"{t['named_browsing_unknown']} without search but browsing could not be ruled out). "
        f"Linked to {brand}'s site in {t['cited']}. Recommended in {t['recommended']}. "
        f"Described accurately in {t['accurate']}, inaccurately in {t['inaccurate']}.",
    ]
    for e, rec in summary["by_engine"].items():
        if rec["status"] == "not run":
            lines.append(f"{e}: not run in this report.")
            continue
        lines.append(
            f"{e}: {rec['answers']} answers. Named {rec['named']} times "
            f"({rec['named_unaided']} without search of {rec['unaided_answers']} answers confirmed not browsing; "
            f"{rec['named_with_search']} with search of {rec['search_answers']}; "
            f"{rec['named_browsing_unknown']} of {rec['browsing_unknown_answers']} answers whose browsing is unknown). "
            f"Searched the web on {rec['searched']} of {rec['search_answers']} search-allowed answers."
        )
    ex = summary["groups"].get("exploratory")
    if ex and ex["planned"]:
        lines.append(f"Exploratory questions (reported separately, not in TOTAL): {brand} named in {ex['named']} of {ex['answers']} answers.")
    return lines


ANSWERS_RE = re.compile(r"\b(\d+) of (\d+)\b(?: [A-Za-z]+)? answers\b")


def allowed_count_pairs(summary: dict[str, Any]) -> set[tuple[int, int]]:
    pairs: set[tuple[int, int]] = set()
    recs = [summary["total"], *summary["by_engine"].values(), *summary.get("groups", {}).values()]
    for r in recs:
        for num in ("named", "named_unaided", "named_with_search", "named_browsing_unknown", "cited",
                    "recommended", "accurate", "searched", "errors"):
            for den in ("answers", "planned", "search_answers", "knowledge_answers", "unaided_answers",
                        "browsing_unknown_answers"):
                pairs.add((int(r.get(num) or 0), int(r.get(den) or 0)))
    return pairs


def count_problems(texts: Iterable[str], summary: dict[str, Any]) -> list[str]:
    """'N of M answers' phrases that do not match the shared counts."""
    ok = allowed_count_pairs(summary)
    bad = []
    for text in texts:
        for m in ANSWERS_RE.finditer(text or ""):
            if (int(m.group(1)), int(m.group(2))) not in ok:
                bad.append(m.group(0))
    return sorted(set(bad))
