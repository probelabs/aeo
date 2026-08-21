"""python3 -m aeo {init,run,report,board,validate}"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aeo import METHODOLOGY_VERSION
from aeo.board import (
    build_board,
    render_html,
    render_json,
    render_markdown,
    write_board_files,
    _brand_terms,
)
from aeo.config import Config, filter_prompts, load_config, starter_config, write_config
from aeo.engines import build_invocation, format_command, run_invocation
from aeo.evidence import (
    default_out_path,
    iter_evidence_files,
    load_document,
    new_document,
    new_run_id,
    write_document,
)
from aeo.parsers import parse_engine
from aeo.report import render_doc
from aeo.score import score_arm
from aeo.validate import validate_config, validate_evidence

EXAMPLE_XERJ = Path(__file__).resolve().parents[2] / "examples" / "xerj" / "aeo.config.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aeo",
        description="Local-CLI AEO measurement (knowledge vs search) for Claude, Codex, Grok.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Write a starter aeo.config.json")
    p_init.add_argument("--brand", default="Acme")
    p_init.add_argument("--domain", default="acme.example")
    p_init.add_argument("--out", default="aeo.config.json")
    p_init.add_argument(
        "--from-example",
        choices=["xerj"],
        help="Copy the XERJ example config instead of a blank starter",
    )

    p_run = sub.add_parser("run", help="Run one query or a config batch")
    p_run.add_argument("--config", help="Path to aeo.config.json")
    p_run.add_argument("--prompt", help="Single prompt text (overrides config prompts)")
    p_run.add_argument(
        "--prompt-id",
        default="adhoc",
        help="Label for --prompt (default adhoc). Does not filter the roster.",
    )
    p_run.add_argument(
        "--only-id",
        action="append",
        dest="only_ids",
        metavar="ID",
        help="Filter the config roster to this prompt id. Repeatable. Ignored with --prompt.",
    )
    p_run.add_argument(
        "--arm",
        choices=["knowledge", "search", "both"],
        default="both",
    )
    p_run.add_argument(
        "--engine",
        choices=["claude", "codex", "grok", "all"],
        default="all",
    )
    p_run.add_argument(
        "--class",
        dest="prompt_class",
        choices=["watch", "focus", "all"],
        default="all",
        help="Filter config prompts by class (default all)",
    )
    p_run.add_argument("--dry-run", action="store_true", help="Print CLI commands, do not execute")
    p_run.add_argument("--out", help="Evidence JSON path (must not already exist)")
    p_run.add_argument("--timeout", type=int, default=300)
    p_run.add_argument("--samples", type=int, help="Override samples_per_arm")

    p_rep = sub.add_parser("report", help="Print a table from evidence JSON")
    p_rep.add_argument("path", nargs="?", help="Evidence file or data dir")
    p_rep.add_argument("--config", help="Used to find data_dir when path is omitted")

    p_board = sub.add_parser("board", help="Decision board (markdown + agent JSON) from evidence")
    p_board.add_argument("path", nargs="?", help="Evidence file or data dir")
    p_board.add_argument("--config", help="Used to find data_dir when path is omitted")
    p_board.add_argument(
        "--format",
        choices=["md", "html", "json"],
        help="Stdout format. Default writes md+json and prints markdown.",
    )
    p_board.add_argument("--out-dir", help="Override boards directory")

    p_val = sub.add_parser("validate", help="Validate a config or evidence file")
    p_val.add_argument("path")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return cmd_init(args)
    if args.cmd == "run":
        return cmd_run(args)
    if args.cmd == "report":
        return cmd_report(args)
    if args.cmd == "board":
        return cmd_board(args)
    if args.cmd == "validate":
        return cmd_validate(args)
    parser.error("unknown command")
    return 2


def cmd_init(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if out.exists():
        print(f"refusing to overwrite {out}", file=sys.stderr)
        return 1
    if args.from_example == "xerj":
        if not EXAMPLE_XERJ.exists():
            print(f"example config not found: {EXAMPLE_XERJ}", file=sys.stderr)
            return 1
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(EXAMPLE_XERJ.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"wrote {out} (from examples/xerj)")
        return 0
    cfg = starter_config(args.brand, args.domain)
    write_config(cfg, out)
    print(f"wrote {out}")
    return 0


def _resolve_config(path: str | None) -> Config:
    if path:
        return load_config(path)
    for candidate in ("aeo.config.json", "examples/xerj/aeo.config.json"):
        if Path(candidate).exists():
            return load_config(candidate)
    raise FileNotFoundError("no --config given and no aeo.config.json in cwd")


def _prompt_payload(p: Any) -> dict[str, Any]:
    return {
        "id": p.id,
        "text": p.text,
        "intent": p.intent,
        "class": p.class_,
        "why": p.why,
    }


def cmd_run(args: argparse.Namespace) -> int:
    try:
        cfg = _resolve_config(args.config)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    engines = list(cfg.engines) if args.engine == "all" else [args.engine]
    arms = ["knowledge", "search"] if args.arm == "both" else [args.arm]
    samples = max(1, int(args.samples or cfg.samples_per_arm))

    if args.prompt:
        prompts = [{"id": args.prompt_id, "text": args.prompt, "intent": None, "class": None, "why": None}]
    else:
        if not cfg.prompts:
            print("config has no prompts; pass --prompt", file=sys.stderr)
            return 1
        selected = filter_prompts(
            cfg.prompts, args.prompt_class, ids=args.only_ids or None
        )
        if not selected:
            print(
                f"no prompts match --class {args.prompt_class}"
                + (f" --only-id {args.only_ids}" if args.only_ids else ""),
                file=sys.stderr,
            )
            return 1
        prompts = [_prompt_payload(p) for p in selected]

    if args.dry_run:
        for p in prompts:
            for engine in engines:
                for arm in arms:
                    for _ in range(samples):
                        inv = build_invocation(engine, arm, p["text"], cfg)
                        print(f"# {engine} {arm} ({p['id']})")
                        print(format_command(inv.argv))
                        print()
        return 0

    run_id = new_run_id()
    doc = new_document(
        cfg,
        run_id=run_id,
        engines=engines,
        samples_per_arm=samples,
    )
    for p in prompts:
        for sample_index in range(1, samples + 1):
            entry: dict[str, Any] = {
                "prompt_id": p["id"],
                "prompt_text": p["text"],
                "engines": {},
            }
            if p.get("intent"):
                entry["intent"] = p["intent"]
            if p.get("class"):
                entry["class"] = p["class"]
            if p.get("why"):
                entry["why"] = p["why"]
            if samples > 1:
                entry["sample_index"] = sample_index
            for engine in engines:
                arms_out: dict[str, Any] = {}
                for arm in arms:
                    inv = build_invocation(engine, arm, p["text"], cfg)
                    print(f"running {engine} {arm} ({p['id']}) …", file=sys.stderr)
                    result = run_invocation(inv, timeout=args.timeout)
                    parsed = parse_engine(engine, result.stdout)
                    if not parsed.raw_response_text and result.stderr and not result.error:
                        parsed.raw_response_text = result.stderr.strip()
                    arms_out[arm] = score_arm(parsed, cfg, error=result.error)
                entry["engines"][engine] = arms_out
            doc["prompts"].append(entry)

    out = Path(args.out) if args.out else default_out_path(cfg, run_id)
    try:
        write_document(doc, out)
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(out)
    return 0


def _resolve_evidence_files(args: argparse.Namespace) -> list[Path]:
    path: Path | None = Path(args.path) if getattr(args, "path", None) else None
    if path is None:
        try:
            cfg = _resolve_config(getattr(args, "config", None))
            root = Path(cfg.data_dir)
            if cfg.path and not root.is_absolute():
                root = cfg.path.parent / root
            path = root
        except FileNotFoundError:
            path = Path("aeo-data")
    files = iter_evidence_files(path)
    if not files:
        example = (
            Path(__file__).resolve().parents[2]
            / "examples"
            / "xerj"
            / "aeo-data"
            / "example-run.json"
        )
        if example.exists():
            files = [example]
    return files


def cmd_report(args: argparse.Namespace) -> int:
    files = _resolve_evidence_files(args)
    if not files:
        print("no evidence files", file=sys.stderr)
        return 1
    for i, f in enumerate(files):
        if i:
            print()
        if len(files) > 1:
            print(f"# {f}")
        doc = load_document(f)
        print(render_doc(doc))
    return 0


def cmd_board(args: argparse.Namespace) -> int:
    files = _resolve_evidence_files(args)
    if not files:
        print("no evidence files", file=sys.stderr)
        return 1
    fmt = args.format
    write_formats: tuple[str, ...]
    if fmt is None:
        write_formats = ("md", "json")
    elif fmt == "html":
        write_formats = ("md", "json", "html")
    else:
        write_formats = ("md", "json")
    stdout_fmt = fmt or "md"
    out_dir = Path(args.out_dir) if args.out_dir else None
    printed = 0
    for f in files:
        doc = load_document(f)
        board = build_board(doc)
        brand_terms = _brand_terms(doc)
        written = write_board_files(
            board,
            f,
            brand_terms=brand_terms,
            formats=write_formats,
            out_dir=out_dir,
        )
        for kind, path in written.items():
            print(f"wrote {path}", file=sys.stderr)
        if printed:
            print()
        if stdout_fmt == "json":
            print(render_json(board), end="")
        elif stdout_fmt == "html":
            print(render_html(board, brand_terms=brand_terms), end="")
        else:
            print(render_markdown(board, brand_terms=brand_terms), end="")
        printed += 1
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema_version") == "aeo-cli-evidence-v1" or "workspace" in doc:
        errors = validate_evidence(doc)
    else:
        errors = validate_config(doc)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
