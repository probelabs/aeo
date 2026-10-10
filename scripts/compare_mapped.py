#!/usr/bin/env python3.11
"""Compare a run on a new roster against earlier runs on the old roster, question by question.

    PYTHONPATH=src python3.11 scripts/compare_mapped.py RUN_DIR --config CFG \
        --baseline "Oct 2=~/.aeo/runs/proof93-20261002" --baseline "Oct 9=~/.aeo/runs/proof93-20261009" \
        [--mapping RUN_DIR/MAPPING.json] [--label v3]

MAPPING.json maps each new prompt id to the old prompt ids it replaces:
{"new-id": ["old-id-1", "old-id-2"], ...}. Every side is scored with the same
strict brand matcher (brand, aliases and brand_match.product_form_only from
CFG), so a loose match in an old run cannot inflate it. Writes
RUN_DIR/mapped-compare.json and RUN_DIR/<run>-mapped-compare.html.

Each pair is labelled "exact" when the old and new wording are identical
(whitespace aside) and "approximate" when they only mean something similar.
Approximate pairs are a rough guide, not like for like; the change report
(scripts/change_report.py) compares exact wording only.
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aeo.measurement import normalize_text  # noqa: E402
from aeo.mention import extract_brand_mentions, product_form_only_from_config  # noqa: E402

ARMS = ("knowledge", "search")
ARM_WORDS = {"knowledge": "without search", "search": "with search"}


def load_cells(run: Path, engines) -> tuple[dict, dict]:
    cells: dict = {}
    for e in engines:
        f = run / f"{e}.json"
        if not f.exists():
            continue
        for p in json.loads(f.read_text()).get("prompts") or []:
            for a in ARMS:
                c = ((p.get("engines") or {}).get(e) or {}).get(a)
                if isinstance(c, dict) and not c.get("error"):
                    cells[(p.get("prompt_id"), e, a)] = c
    vf = run / "vendors_judged.json"
    vend = json.loads(vf.read_text()) if vf.exists() else {}
    return cells, vend


def load_texts(run: Path, engines) -> dict:
    texts: dict = {}
    for e in engines:
        f = run / f"{e}.json"
        if f.exists():
            for p in json.loads(f.read_text()).get("prompts") or []:
                if p.get("prompt_id") and p.get("prompt_text"):
                    texts.setdefault(p["prompt_id"], p["prompt_text"])
    return texts


def summarize(cells, vend, pid, e, a, brand, aliases, pfo, max_vendors=8):
    c = cells.get((pid, e, a))
    if c is None:
        return None
    hit = bool(extract_brand_mentions(c.get("raw_response_text") or "", brand, aliases, product_form_only=pfo))
    entry = vend.get(f"{pid}|{e}|{a}") if isinstance(vend, dict) else None
    vs = [v.get("normalized") or v.get("raw") for v in ((entry or {}).get("vendors") or []) if isinstance(v, dict)]
    return {"brand": hit, "searched": bool(c.get("searched")),
            "queries": c.get("search_queries") or [], "vendors": [v for v in vs if v][:max_vendors]}


def compare(run: Path, cfg: dict, mapping: dict, baselines: dict[str, Path], label: str, engines) -> dict:
    brand, aliases = cfg["brand"], list(cfg.get("aliases") or [])
    pfo = product_form_only_from_config(cfg)
    text = {p["id"]: p.get("text", "") for p in cfg.get("prompts") or [] if "id" in p}
    cur = load_cells(run, engines)
    base = {k: load_cells(v, engines) for k, v in baselines.items()}
    old_texts: dict = {}
    for v in baselines.values():
        for k2, t in load_texts(v, engines).items():
            old_texts.setdefault(k2, t)
    rows = []
    for nid, olds in mapping.items():
        for oid in (olds if isinstance(olds, list) else [olds]):
            new_t = text.get(nid, "")
            old_t = old_texts.get(oid, "")
            match = "exact" if new_t and old_t and normalize_text(new_t) == normalize_text(old_t) else "approximate"
            r = {"new_id": nid, "new_text": new_t, "old_id": oid, "old_text": old_t, "match": match, "cells": {}}
            for e in engines:
                for a in ARMS:
                    cell = {label: summarize(*cur, nid, e, a, brand, aliases, pfo)}
                    for k, (bc, bv) in base.items():
                        cell[k] = summarize(bc, bv, oid, e, a, brand, aliases, pfo)
                    r["cells"][f"{e}|{a}"] = cell
            rows.append(r)
    summary = {}
    by_match: dict = {"exact": {}, "approximate": {}}
    for k in [*baselines, label]:
        h = t = 0
        for r in rows:
            for c in r["cells"].values():
                s = c.get(k)
                if s:
                    t += 1
                    h += s["brand"]
                    m = by_match[r["match"]].setdefault(k, [0, 0])
                    m[0] += s["brand"]
                    m[1] += 1
        summary[k] = [h, t]
    return {"brand": brand, "labels": [*baselines, label], "summary": summary, "summary_by_match": by_match,
            "pairs": len(rows), "exact_pairs": sum(r["match"] == "exact" for r in rows),
            "approximate_pairs": sum(r["match"] == "approximate" for r in rows), "rows": rows}


def render_html(doc: dict, run_name: str) -> str:
    brand, labels = doc["brand"], doc["labels"]
    cur = labels[-1]
    prev = labels[-2] if len(labels) > 1 else None

    def mark(s):
        if s is None:
            return "–"
        return ("<b>" + html.escape(brand) + "</b>" if s["brand"] else "no") + (" · searched" if s["searched"] else "")

    head = "".join(f"<th>{html.escape(k)}</th>" for k in labels)
    out = [
        "<html><head><meta charset=utf-8>",
        f"<title>{html.escape(run_name)}: mapped questions (approximate)</title>",
        "<style>body{font:14px system-ui;margin:24px}table{border-collapse:collapse}"
        "td,th{border:1px solid #ddd;padding:4px 6px;vertical-align:top}.v{color:#666;font-size:12px}</style></head><body>",
        f"<h1>Mapped questions (approximate): {html.escape(cur)} vs {html.escape(', '.join(labels[:-1]))}</h1>",
        f"<p>{doc['pairs']} old→new question pairs: <b>{doc.get('exact_pairs', 0)} with identical wording</b> and "
        f"<b>{doc.get('approximate_pairs', doc['pairs'])} approximate</b> (similar meaning, different wording). "
        f"Strict {html.escape(brand)} matching on every side. Approximate pairs are a rough guide, not like for like.</p>",
        f"<p>{html.escape(brand)} named: " + ", ".join(
            f"{html.escape(k)} {h} of {t} answers" for k, (h, t) in doc["summary"].items()) + "</p>",
        f"<table><tr><th>New question</th><th>Old id</th><th>Match</th><th>Assistant</th>{head}"
        f"<th>{html.escape(cur)} vendors named</th>"
        + (f"<th>{html.escape(prev)} vendors named</th>" if prev else "") + "</tr>",
    ]
    for r in doc["rows"]:
        for k, c in r["cells"].items():
            e, a = k.split("|")
            row = (f"<tr><td>{html.escape(r['new_id'])}<div class=v>{html.escape(r['new_text'])}</div></td>"
                   f"<td>{html.escape(r['old_id'])}</td><td>{html.escape(r.get('match', 'approximate'))}</td>"
                   f"<td>{html.escape(e)} {ARM_WORDS.get(a, a)}</td>"
                   + "".join(f"<td>{mark(c.get(l))}</td>" for l in labels)
                   + f"<td class=v>{html.escape(', '.join((c.get(cur) or {}).get('vendors') or []))}</td>")
            if prev:
                row += f"<td class=v>{html.escape(', '.join((c.get(prev) or {}).get('vendors') or []))}</td>"
            out.append(row + "</tr>")
    out.append("</table></body></html>")
    return "\n".join(out)


def parse_baseline(s: str) -> tuple[str, Path]:
    if "=" not in s:
        p = Path(s).expanduser()
        return p.name, p
    k, v = s.split("=", 1)
    return k.strip(), Path(v.strip()).expanduser()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--baseline", action="append", required=True, help="LABEL=RUN_DIR (repeatable, oldest first)")
    ap.add_argument("--mapping", type=Path, help="default RUN_DIR/MAPPING.json")
    ap.add_argument("--label", default="", help="label for RUN_DIR (default: its folder name)")
    ap.add_argument("--engines", nargs="+", default=["claude", "codex"])
    args = ap.parse_args(argv)
    run = args.run.expanduser().resolve()
    cfg = json.loads(args.config.expanduser().read_text())
    mapping = json.loads((args.mapping or run / "MAPPING.json").expanduser().read_text())
    baselines = dict(parse_baseline(b) for b in args.baseline)
    label = args.label or run.name
    doc = compare(run, cfg, mapping, baselines, label, args.engines)
    (run / "mapped-compare.json").write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    out = run / f"{run.name}-mapped-compare.html"
    out.write_text(render_html(doc, run.name))
    print("mapped pairs", doc["pairs"], doc["summary"], "->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
