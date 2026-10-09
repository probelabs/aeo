"""python3 -m aeo {init,run,report,board,validate}"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from aeo import METHODOLOGY_VERSION
from aeo.board import (
    boards_dir_for,
    build_board,
    render_html,
    render_json,
    render_markdown,
    write_board_files,
    _brand_terms,
)
from aeo.config import Config, filter_prompts, load_config, starter_config, write_config
from aeo.engines import build_invocation, format_command
from aeo.evidence import (
    default_out_path,
    iter_evidence_files,
    load_document,
    new_document,
    new_run_id,
    write_document,
)
from aeo.layers import load_layers, render_layers_markdown, with_layers
from aeo.report import render_doc
from aeo.runner import plan_remaining, recover_shards, run_jobs
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
    p_run.add_argument("--out", help="Evidence JSON path. Reuses the file and skips completed prompt×engine×arm cells.")
    p_run.add_argument("--timeout", type=int, default=300)
    p_run.add_argument("--retries", type=int, default=2, help="Retries per cell after timeout or CLI error (default 2)")
    p_run.add_argument("--samples", type=int, help="Override samples_per_arm")
    p_run.add_argument(
        "--concurrency",
        type=_concurrency_type,
        default=1,
        metavar="N",
        help=(
            "Max parallel cells in this process (default 1). Workers write temp shards; "
            "the parent merges them into --out so completed cells are not lost. "
            "--engine all stays one process. Do not share --out across processes."
        ),
    )

    p_run.add_argument(
        "--no-google",
        action="store_true",
        help="Skip the Google (DataForSEO) and Search Console layers for this run",
    )
    p_run.add_argument("--google-max-cost", type=float, help="Spending cap in USD for the Google layer (overrides google.max_cost_usd)")

    p_google = sub.add_parser("google", help="Google layers only: live SERP/AI Overviews (DataForSEO) + Search Console")
    p_google.add_argument("--config", help="Path to aeo.config.json")
    p_google.add_argument("--out-dir", help="Layer directory (default <data_dir>/google/<timestamp>; use <run>/google for a run dir)")
    p_google.add_argument("--estimate", action="store_true", help="Print the cost estimate and exit; no API calls")
    p_google.add_argument("--propose", action="store_true", help="Draft unapproved google_targets/google_watch suggestions and exit")
    p_google.add_argument("--max-cost", type=float, help="Spending cap in USD (overrides google.max_cost_usd)")
    p_google.add_argument("--no-ai-mode", action="store_true", help="Skip Google AI Mode for targets")
    p_google.add_argument("--mobile", action="store_true", help="Also take a mobile snapshot of each target")
    p_google.add_argument("--no-serp", action="store_true", help="Skip the DataForSEO layer")
    p_google.add_argument("--no-gsc", action="store_true", help="Skip the Search Console layer")
    p_google.add_argument("--reuse-raw", action="store_true", help="Rebuild google.json from raw/ in --out-dir; no API calls")

    p_rep = sub.add_parser("report", help="Print a table from evidence JSON")
    p_rep.add_argument("path", nargs="*", help="Evidence file(s) or data dir")
    p_rep.add_argument("--config", help="Used to find data_dir when path is omitted")
    p_rep.add_argument("--html", action="store_true", help="Write a standalone HTML report (merges multiple files)")
    p_rep.add_argument("--out", help="HTML output path (with --html). Default: <first-stem>-report.html")

    p_board = sub.add_parser("board", help="Decision board (markdown + agent JSON) from evidence")
    p_board.add_argument("path", nargs="*", help="Evidence file(s) or data dir")
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
    if args.cmd == "google":
        return cmd_google(args)
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


def _concurrency_type(value: str) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("concurrency must be an integer") from exc
    if n < 1:
        raise argparse.ArgumentTypeError("concurrency must be >= 1")
    return n


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
    retries = int(getattr(args, "retries", 2) or 0)

    if args.prompt:
        prompts = [{"id": args.prompt_id, "text": args.prompt, "intent": None, "class": None, "why": None}]
    else:
        if not cfg.prompts:
            print("config has no prompts; pass --prompt", file=sys.stderr)
            return 1
        only_ids = getattr(args, "only_ids", None)
        try:
            selected = filter_prompts(cfg.prompts, args.prompt_class, ids=only_ids or None)
        except TypeError:
            selected = filter_prompts(cfg.prompts, args.prompt_class)
        if not selected:
            print(
                f"no prompts match --class {args.prompt_class}"
                + (f" --only-id {only_ids}" if only_ids else ""),
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

    out = Path(args.out) if args.out else None
    if out and out.exists():
        doc = load_document(out)
        run_id = (doc.get("run") or {}).get("run_id") or new_run_id()
        print(f"resume {out} ({len(doc.get('prompts') or [])} prompts already stored)", file=sys.stderr, flush=True)
    else:
        run_id = new_run_id()
        doc = new_document(
            cfg,
            run_id=run_id,
            engines=engines,
            samples_per_arm=samples,
        )
    if out is None:
        out = default_out_path(cfg, run_id)

    concurrency = int(getattr(args, "concurrency", 1) or 1)
    doc, _recovered = recover_shards(doc, out)
    skipped, jobs = plan_remaining(doc, prompts, engines, arms, samples)
    write_document(doc, out, overwrite=True)
    if concurrency > 1 and jobs:
        print(
            f"concurrency {concurrency} ({len(jobs)} remaining cell(s))",
            file=sys.stderr,
            flush=True,
        )
    doc, ran = run_jobs(
        cfg=cfg,
        doc=doc,
        out=out,
        jobs=jobs,
        samples=samples,
        timeout=args.timeout,
        retries=retries,
        concurrency=concurrency,
    )

    print(f"done ran={ran} skipped={skipped} -> {out}", file=sys.stderr, flush=True)
    _run_layers_after(cfg, out, args)
    print(out)
    return 0


def _resolve_evidence_files(args: argparse.Namespace) -> list[Path]:
    raw = getattr(args, "path", None)
    if isinstance(raw, (list, tuple)):
        given = [Path(p) for p in raw if p]
    elif raw:
        given = [Path(raw)]
    else:
        given = []
    explicit = bool(given)

    if not given:
        try:
            cfg = _resolve_config(getattr(args, "config", None))
            root = Path(cfg.data_dir)
            if cfg.path and not root.is_absolute():
                root = cfg.path.parent / root
            given = [root]
        except FileNotFoundError:
            given = [Path("aeo-data")]

    files: list[Path] = []
    for path in given:
        files.extend(iter_evidence_files(path))
    if not files and not explicit:
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
    if getattr(args, "html", False):
        from aeo.html_report import render_html_report

        docs = [load_document(f) for f in files]
        out = Path(args.out) if getattr(args, "out", None) else files[0].with_name(f"{files[0].stem}-report.html")
        html = render_html_report(docs, generated_from_files=[f.name for f in files])
        html = with_layers(html, files[0])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html, encoding="utf-8")
        print(out)
        return 0
    for i, f in enumerate(files):
        if i:
            print()
        if len(files) > 1:
            print(f"# {f}")
        doc = load_document(f)
        print(render_doc(doc))
        layers_md = render_layers_markdown(*load_layers(f))
        if layers_md:
            print()
            print(layers_md, end="")
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
    docs = [load_document(f) for f in files]
    disk_formats = tuple(x for x in write_formats if x != "html")
    printed = 0
    for f, doc in zip(files, docs):
        board = build_board(doc)
        brand_terms = _brand_terms(doc)
        written = write_board_files(
            board,
            f,
            brand_terms=brand_terms,
            formats=disk_formats,
            out_dir=out_dir,
            doc=doc,
        )
        _add_layers_to_board_files(written, f)
        for kind, path in written.items():
            print(f"wrote {path}", file=sys.stderr)
        if stdout_fmt == "html":
            continue
        if printed:
            print()
        if stdout_fmt == "json":
            print(render_json(board), end="")
        else:
            print(render_markdown(board, brand_terms=brand_terms), end="")
            layers_md = render_layers_markdown(*load_layers(f))
            if layers_md:
                print()
                print(layers_md, end="")
        printed += 1
    if "html" in write_formats:
        from aeo.html_report import merge_docs, render_html_report

        html = render_html_report(docs, generated_from_files=[f.name for f in files])
        html = with_layers(html, files[0])
        dest = out_dir or boards_dir_for(files[0])
        dest.mkdir(parents=True, exist_ok=True)
        run_id = (merge_docs(docs).get("run") or {}).get("run_id") or files[0].stem
        path = dest / f"{run_id}.html"
        path.write_text(html, encoding="utf-8")
        print(f"wrote {path}", file=sys.stderr)
        if stdout_fmt == "html":
            print(html, end="")
    return 0


def _add_layers_to_board_files(written: dict[str, Path], evidence: Path) -> None:
    """Append the Google / Search Console sections to board md and add them to board json
    under separate keys. Board scores are not touched."""
    gdoc, sdoc = load_layers(evidence)
    if not gdoc and not sdoc:
        return
    if "md" in written:
        p = written["md"]
        p.write_text(p.read_text(encoding="utf-8").rstrip() + "\n\n" + render_layers_markdown(gdoc, sdoc), encoding="utf-8")
    if "json" in written:
        p = written["json"]
        board = json.loads(p.read_text(encoding="utf-8"))
        if gdoc:
            board["google"] = gdoc
        if sdoc:
            board["search_console"] = sdoc
        p.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_layers_after(cfg: Config, out: Path, args: argparse.Namespace) -> None:
    """Google + Search Console layers after a full config run. Skipped silently without credentials."""
    if getattr(args, "no_google", False) or args.prompt or getattr(args, "only_ids", None) or cfg.path is None:
        return
    from aeo.layers import find_layer_dir, layer_dir_for, run_layers

    if find_layer_dir(out) is not None:  # resume: already fetched for this evidence file
        return
    try:
        run_layers(cfg.path, layer_dir_for(out), google_overrides={"max_cost_usd": getattr(args, "google_max_cost", None)})
    except Exception as exc:  # layers must never fail the board run
        print(f"google layers skipped: {type(exc).__name__}: {exc}", file=sys.stderr)


def cmd_google(args: argparse.Namespace) -> int:
    from datetime import datetime, timezone

    from aeo import google as g
    from aeo.layers import load_layers, run_layers, standalone_html

    try:
        cfg = _resolve_config(args.config)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1
    raw = g.load_raw_config(cfg.path)
    overrides = {"max_cost_usd": args.max_cost, "ai_mode": False if args.no_ai_mode else None, "mobile": True if args.mobile else None}
    if args.estimate:
        targets, watch = g.config_searches(raw)
        print(json.dumps(g.estimate_cost(targets, watch, g.load_settings(raw, **overrides)), indent=2))
        return 0
    if args.propose:
        sugg = g.propose_searches(raw)
        path = g.write_proposals(g.proposals_path(cfg.path), **sugg)
        print(f"wrote unapproved suggestions: {path}")
        return 0
    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        root = Path(cfg.data_dir)
        if not root.is_absolute():
            root = cfg.path.parent / root
        out_dir = root / "google" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if args.reuse_raw:
        doc = g.run_google_layer(raw, out_dir, client=g.DataForSEOClient(transport=lambda *_: {}), settings=g.load_settings(raw, **overrides), reuse_raw=True)
        res = {"google": doc, "gsc": None}
    else:
        res = run_layers(cfg.path, out_dir, google_overrides=overrides, skip_google=args.no_serp, skip_gsc=args.no_gsc)
    if not res.get("google") and not res.get("gsc"):
        print("no Google layer ran (no credentials, nothing approved to check, or over the cap)", file=sys.stderr)
        return 0
    gdoc, sdoc = load_layers(out_dir)
    (out_dir / "google.html").write_text(standalone_html(gdoc, sdoc, f"{cfg.brand} · Google"), encoding="utf-8")
    print(out_dir)
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
