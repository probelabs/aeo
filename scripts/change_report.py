#!/usr/bin/env python3.11
"""Diff two completed AEO boards: brand Δ, prompt transitions, competitor fan-out.

Writes change.json + *-change-report.html. Deterministic Python; no LLM required.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aeo.change import diff_runs, load_run, write_change_report  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Diff two AEO run directories (or a baseline evidence file vs a current run). "
            "Writes change.json and a human HTML change report."
        )
    )
    parser.add_argument(
        "--baseline",
        required=True,
        help="Baseline run directory or evidence JSON (run N−1)",
    )
    parser.add_argument(
        "--current",
        required=True,
        help="Current run directory (run N)",
    )
    parser.add_argument(
        "--brand",
        default=os.environ.get("AEO_BRAND") or "",
        help="Brand to score (default: AEO_BRAND or workspace.brand)",
    )
    parser.add_argument(
        "--out",
        default="",
        help="Output directory (default: current run dir, or its parent if a file)",
    )
    parser.add_argument(
        "--floor",
        type=int,
        default=1,
        help="OUT/NEW floor (default 1 = count==0). Mentions below this are not ranking.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        help="Top-N vendors in the rank table by either run (default 15)",
    )
    parser.add_argument(
        "--movers",
        type=int,
        default=10,
        help="How many risers and fallers to keep (default 10)",
    )
    args = parser.parse_args(argv)

    baseline = load_run(Path(args.baseline))
    current = load_run(Path(args.current))
    brand = (args.brand or current.brand or baseline.brand or "").strip()
    if not brand:
        parser.error("brand is required: pass --brand or set AEO_BRAND / workspace.brand")

    if args.floor < 1:
        parser.error("--floor must be >= 1")
    if args.movers < 1:
        parser.error("--movers must be >= 1")
    if args.top < 1:
        parser.error("--top must be >= 1")
    payload = diff_runs(
        baseline,
        current,
        brand=brand,
        floor=args.floor,
        top_n=args.top,
        top_movers=args.movers,
    )
    if args.out:
        out_dir = Path(args.out)
    elif current.path.is_dir():
        out_dir = current.path
    else:
        out_dir = current.path.parent
    json_path, html_path = write_change_report(payload, out_dir)
    print("wrote", json_path)
    print("wrote", html_path)
    print(payload["summary"]["headline"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
