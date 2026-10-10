#!/usr/bin/env python3.11
"""Merge sharded engine outputs (<engine>.w0.json, <engine>.w1.json, ...) into <engine>.json.

    python3.11 scripts/merge_shards.py RUN_DIR [claude codex ...] [--dry-run]

For each prompt_id the completed, non-error cell wins per arm (knowledge /
search); when both shards completed it, the longer answer wins. The union of
prompts is kept in first-seen order, and the summary rates are recomputed from
the merged cells. An existing <engine>.json is copied to
<engine>.json.pre_merge_bak first.

Stop all shard workers before merging. Run this before rescore/judge/render.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from aeo.score import aggregates  # noqa: E402

ARMS = ("knowledge", "search")


def cell_ok(c) -> bool:
    return isinstance(c, dict) and not c.get("error") and bool(c.get("raw_response_text") or "brand_mentioned" in c)


def prefer_cell(a, b):
    a_ok, b_ok = cell_ok(a), cell_ok(b)
    if a_ok != b_ok:
        return a if a_ok else b
    if a_ok and b_ok:
        return b if len(b.get("raw_response_text") or "") > len(a.get("raw_response_text") or "") else a
    if isinstance(a, dict):
        return a
    return b if isinstance(b, dict) else a


def merge_prompt(pa: dict, pb: dict, engine: str) -> dict:
    out = dict(pa)
    for k in ("prompt_text", "class", "why"):
        if not out.get(k) and pb.get(k):
            out[k] = pb[k]
    ea = (pa.get("engines") or {}).get(engine) or {}
    eb = (pb.get("engines") or {}).get(engine) or {}
    merged = {arm: prefer_cell(ea.get(arm), eb.get(arm)) for arm in ARMS}
    for src in (ea, eb):
        for k, v in src.items():
            merged.setdefault(k, v)
    engines = dict(out.get("engines") or {})
    engines[engine] = merged
    for ek, ev in (pb.get("engines") or {}).items():
        engines.setdefault(ek, ev)
    out["engines"] = engines
    out["prompt_id"] = pa.get("prompt_id") or pb.get("prompt_id")
    return out


def shard_files(run: Path, engine: str) -> list[Path]:
    return sorted(run.glob(f"{engine}.w*.json"), key=lambda p: p.name)


def merge_engine(run: Path, engine: str, dry_run: bool = False) -> dict:
    shards = shard_files(run, engine)
    if not shards:
        raise SystemExit(f"no shards for {engine} in {run} (expected {engine}.w0.json ...)")
    docs = [json.loads(p.read_text()) for p in shards]
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for d in docs:
        for p in d.get("prompts") or []:
            pid = p.get("prompt_id")
            if not pid:
                continue
            if pid in by_id:
                by_id[pid] = merge_prompt(by_id[pid], p, engine)
            else:
                by_id[pid] = p
                order.append(pid)
    merged = dict(docs[0])
    for d in docs[1:]:
        for k in ("schema_version", "workspace", "run"):
            if not merged.get(k) and d.get(k):
                merged[k] = d[k]
    merged["prompts"] = [by_id[pid] for pid in order]
    merged.update(aggregates(merged["prompts"]))
    complete = sum(
        1 for p in merged["prompts"]
        if all(cell_ok(((p.get("engines") or {}).get(engine) or {}).get(a)) for a in ARMS)
    )
    out = run / f"{engine}.json"
    stats = {"engine": engine, "shards": [p.name for p in shards], "prompts": len(merged["prompts"]),
             "complete_both_arms": complete, "out": str(out)}
    if dry_run:
        print("[dry-run]", stats)
        return stats
    if out.exists():
        (run / f"{engine}.json.pre_merge_bak").write_text(out.read_text())
    out.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n")
    print("merged", stats)
    return stats


def engines_with_shards(run: Path) -> list[str]:
    return sorted({p.name.split(".")[0] for p in run.glob("*.w*.json")})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("engines", nargs="*", help="default: every engine that has shard files")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    run = args.run.expanduser().resolve()
    for e in args.engines or engines_with_shards(run):
        merge_engine(run, e, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
