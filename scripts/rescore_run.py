#!/usr/bin/env python3.11
"""Rescore a finished run's brand hits with the one strict matcher, into NEW files.

    PYTHONPATH=src python3.11 scripts/rescore_run.py RUN_DIR --config CFG [--suffix rescored]

Reads RUN_DIR/{claude,codex,grok}.json, re-derives brand_mentioned /
brand_mentions / recommended and the brand part of vendors_in_search_queries
from each cell's stored raw_response_text (no queries are re-run), and writes
RUN_DIR/<engine>.<suffix>.json plus RUN_DIR/board.<suffix>.json (counts by
engine x arm, every flipped cell, and the deterministic aeo board on the
rescored evidence). Originals are never modified.

With --in-place the rescored evidence replaces RUN_DIR/<engine>.json (the
original is first copied to <engine>.json.bak-prerescore, once), so later
steps (judge, render, change report) read strict hits AND matching summary
rates (mention_rate_knowledge / mention_rate_search / search_rate). The
old per-run rescore_brand.py flipped the cell bits but left those totals
stale.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aeo import mention  # noqa: E402
from aeo.board import build_board  # noqa: E402
from aeo.mention import product_form_only_from_config, rescore_brand_cells  # noqa: E402
from aeo.score import aggregates  # noqa: E402

ENGINES = ("claude", "codex", "grok")
ARMS = ("knowledge", "search")


def cell_counts(doc: dict, engine: str) -> dict:
    out = {}
    for arm in ARMS:
        n = hits = searched = typed = 0
        for p in doc.get("prompts") or []:
            c = ((p.get("engines") or {}).get(engine) or {}).get(arm)
            if not isinstance(c, dict) or c.get("error"):
                continue
            n += 1
            hits += bool(c.get("brand_mentioned"))
            searched += bool(c.get("searched"))
        out[arm] = {"cells": n, "hits": hits, "searched": searched}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run", type=Path)
    ap.add_argument("--config", required=True, help="aeo config JSON (brand, aliases, brand_match)")
    ap.add_argument("--suffix", default="rescored")
    ap.add_argument("--in-place", action="store_true",
                    help="overwrite <engine>.json (backup kept as <engine>.json.bak-prerescore)")
    args = ap.parse_args()
    run = args.run.expanduser().resolve()
    cfg = json.loads(Path(args.config).expanduser().read_text())
    brand, aliases = cfg["brand"], list(cfg.get("aliases") or [])
    pfo = product_form_only_from_config(cfg)

    report: dict = {
        "schema": "aeo-brand-rescore-v1",
        "run": run.name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "matcher": {
            "module": str(Path(mention.__file__).resolve()),
            "brand": brand,
            "aliases": aliases,
            "product_form_only": mention.resolve_product_form_only(brand, aliases, pfo),
            "config": str(Path(args.config).expanduser()),
        },
        "by_engine_arm": {},
        "totals": {"old_hits": 0, "new_hits": 0},
        "flipped": [],
        "new_hits": [],
    }
    merged: dict | None = None
    for eng in ENGINES:
        src = run / f"{eng}.json"
        if not src.exists():
            continue
        orig = json.loads(src.read_text())
        doc = copy.deepcopy(orig)
        old_counts = cell_counts(orig, eng)
        rescore_brand_cells(doc, brand, aliases, product_form_only=pfo, engines=[eng])
        doc.update(aggregates(doc.get("prompts") or []))
        doc.setdefault("run", {})["brand_rescore"] = {
            "source": src.name,
            "matcher": report["matcher"]["module"],
            "product_form_only": report["matcher"]["product_form_only"],
            "at": report["generated_at"],
        }
        new_counts = cell_counts(doc, eng)
        for arm in ARMS:
            report["by_engine_arm"].setdefault(eng, {})[arm] = {
                "cells": new_counts[arm]["cells"],
                "old_hits": old_counts[arm]["hits"],
                "new_hits": new_counts[arm]["hits"],
                "searched": new_counts[arm]["searched"],
            }
            report["totals"]["old_hits"] += old_counts[arm]["hits"]
            report["totals"]["new_hits"] += new_counts[arm]["hits"]
        for po, pn in zip(orig.get("prompts") or [], doc.get("prompts") or []):
            for arm in ARMS:
                co = ((po.get("engines") or {}).get(eng) or {}).get(arm)
                cn = ((pn.get("engines") or {}).get(eng) or {}).get(arm)
                if not isinstance(co, dict) or not isinstance(cn, dict):
                    continue
                if bool(co.get("brand_mentioned")) != bool(cn.get("brand_mentioned")):
                    report["flipped"].append({
                        "prompt_id": pn.get("prompt_id"), "engine": eng, "arm": arm,
                        "old": co.get("brand_mentions"), "new": cn.get("brand_mentions"),
                    })
                if cn.get("brand_mentioned"):
                    report["new_hits"].append({
                        "prompt_id": pn.get("prompt_id"), "engine": eng, "arm": arm,
                        "mentions": cn.get("brand_mentions"),
                    })
        if args.in_place:
            bak = run / f"{eng}.json.bak-prerescore"
            if not bak.exists():
                bak.write_text(src.read_text())
            dst = src
        else:
            dst = run / f"{eng}.{args.suffix}.json"
            if dst.resolve() == src.resolve():
                raise SystemExit("refusing to overwrite the original evidence")
        dst.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {dst}  {eng}: " + ", ".join(
            f"{a} {report['by_engine_arm'][eng][a]['old_hits']}->{report['by_engine_arm'][eng][a]['new_hits']}"
            f"/{report['by_engine_arm'][eng][a]['cells']}" for a in ARMS))
        if merged is None:
            merged = copy.deepcopy(doc)
        else:
            by_id = {p.get("prompt_id"): p for p in merged.get("prompts") or []}
            for p in doc.get("prompts") or []:
                tgt = by_id.get(p.get("prompt_id"))
                if tgt is None:
                    merged.setdefault("prompts", []).append(copy.deepcopy(p))
                else:
                    tgt.setdefault("engines", {})[eng] = copy.deepcopy((p.get("engines") or {}).get(eng))
    if merged is not None:
        report["deterministic_board"] = build_board(merged)
    out = run / f"board.{args.suffix}.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"wrote {out}  total {report['totals']['old_hits']} -> {report['totals']['new_hits']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
