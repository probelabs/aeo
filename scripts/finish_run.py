#!/usr/bin/env python3.11
"""Finish a sharded board run end to end, optionally as a detached watchdog.

    python3.11 scripts/finish_run.py RUN_DIR --config CFG \
        [--wait] [--detach] \
        [--change-baseline PREV_RUN_DIR] \
        [--mapped-baseline "Oct 9=PREV_RUN_DIR" ...] \
        [--copy-to ~/Downloads/<run>]

Steps, each logged to RUN_DIR/finish_run.log with its exit code:
  1. --wait: poll every --poll seconds until no live <name>.pid remains in
     RUN_DIR (shard workers such as claude.w0.pid, and google.pid).
  2. Merge shards (<engine>.w*.json -> <engine>.json) when shards exist.
  3. Strict brand rescore in place (scripts/rescore_run.py --in-place), so
     cell hits and the summary rates agree.
  4. judge_run.py.
  5. compare_mapped.py when --mapped-baseline is given and MAPPING.json exists.
  6. change_report.py when --change-baseline is given.
  7. render_judge_html.py: ONE report, <run>-report.html, that embeds the change
     summary (change.json), the mapped comparison as a collapsed appendix and the
     Google / Search Console layers from RUN_DIR/google when present.
  8. --copy-to: copy that report and the main JSON outputs there.
Writes RUN_DIR/JUDGE_DONE.txt at the end. This replaces the per-run
watchdog_finish.py / render_full_after_judge.sh copies.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COPY_PATTERNS = ("mapped-compare.json", "board.json", "judge.json",
                 "vendors_judged.json", "claude.json", "codex.json", "grok.json", "change.json", "NEXT_STEPS.txt")


class Finisher:
    def __init__(self, args):
        self.a = args
        self.run_dir = args.run.expanduser().resolve()
        self.log_path = self.run_dir / "finish_run.log"
        self.py = args.python or sys.executable
        cfg = json.loads(args.config.expanduser().read_text())
        self.brand = args.brand or cfg.get("brand") or ""
        env = os.environ.copy()
        env.update(PYTHONPATH=str(ROOT / "src"), AEO_CONFIG=str(args.config.expanduser()), AEO_RUN=str(self.run_dir))
        if self.brand:
            env["AEO_BRAND"] = self.brand
        self.env = env

    def log(self, msg: str) -> None:
        line = time.strftime("%Y-%m-%dT%H:%M:%S%z") + " " + msg
        with self.log_path.open("a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def step(self, name: str, argv: list[str]) -> int:
        self.log(f"{name}: start")
        rc = subprocess.run(argv, cwd=str(ROOT), env=self.env).returncode
        self.log(f"{name}: rc={rc}")
        return rc

    def live_pids(self) -> list[str]:
        live = []
        for p in sorted(self.run_dir.glob("*.pid")):
            if p.name in ("finish_run.pid", "watchdog_finish.pid"):
                continue
            pid = p.read_text().strip()
            if pid.isdigit() and subprocess.run(["ps", "-p", pid], capture_output=True).returncode == 0:
                live.append(p.stem)
        return live

    def main(self) -> int:
        (self.run_dir / "finish_run.pid").write_text(f"{os.getpid()}\n")
        self.log(f"finish_run start: {self.run_dir}")
        if self.a.wait:
            while live := self.live_pids():
                self.log(f"waiting on {', '.join(live)}")
                time.sleep(self.a.poll)
        s = str(ROOT / "scripts")
        if any(self.run_dir.glob("*.w*.json")):
            if self.step("merge shards", [self.py, f"{s}/merge_shards.py", str(self.run_dir)]):
                return 1
        self.step("strict brand rescore", [self.py, f"{s}/rescore_run.py", str(self.run_dir),
                                           "--config", str(self.a.config.expanduser()), "--in-place"])
        engines = [e for e in ("claude", "codex", "grok") if (self.run_dir / f"{e}.json").exists()]
        self.step("judge", [self.py, f"{s}/judge_run.py", str(self.run_dir), *engines])
        # Change report and mapped comparison first: the single report embeds both.
        if self.a.mapped_baseline and (self.run_dir / "MAPPING.json").exists():
            argv = [self.py, f"{s}/compare_mapped.py", str(self.run_dir), "--config", str(self.a.config.expanduser())]
            for b in self.a.mapped_baseline:
                argv += ["--baseline", b]
            self.step("mapped comparison", argv)
        if self.a.change_baseline:
            argv = [self.py, f"{s}/change_report.py", "--baseline", str(Path(self.a.change_baseline).expanduser()),
                    "--current", str(self.run_dir), "--out", str(self.run_dir)]
            if self.brand:
                argv += ["--brand", self.brand]
            self.step("change report", argv)
        self.step("render", [self.py, f"{s}/render_judge_html.py", str(self.run_dir)])
        if self.a.copy_to:
            dest = Path(self.a.copy_to).expanduser()
            dest.mkdir(parents=True, exist_ok=True)
            rep = self.run_dir / f"{self.run_dir.name}-report.html"
            if rep.exists():
                shutil.copyfile(rep, dest / rep.name)
            for pat in COPY_PATTERNS:
                for f in self.run_dir.glob(pat):
                    shutil.copyfile(f, dest / f.name)
            for n in ("google.md", "gsc.md"):
                f = self.run_dir / "google" / n
                if f.exists():
                    shutil.copyfile(f, dest / n)
            self.log(f"copied outputs to {dest}")
        (self.run_dir / "JUDGE_DONE.txt").write_text(time.strftime("%Y-%m-%dT%H:%M:%S%z") + " finish_run done\n")
        self.log("DONE")
        return 0


def detach(log_path: Path) -> None:
    if os.fork() > 0:
        sys.exit(0)
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
    with open(os.devnull, "rb") as dn:
        os.dup2(dn.fileno(), 0)
    lf = open(log_path, "a", buffering=1)
    os.dup2(lf.fileno(), 1)
    os.dup2(lf.fileno(), 2)


def parse(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run", type=Path)
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--brand", default="")
    ap.add_argument("--wait", action="store_true", help="wait for live *.pid workers in RUN_DIR first")
    ap.add_argument("--poll", type=int, default=120)
    ap.add_argument("--detach", action="store_true", help="daemonize; progress goes to RUN_DIR/finish_run.log")
    ap.add_argument("--change-baseline", default="")
    ap.add_argument("--mapped-baseline", action="append", default=[], help="LABEL=RUN_DIR (repeatable)")
    ap.add_argument("--copy-to", default="")
    ap.add_argument("--python", default="", help="interpreter for the steps (default: this one)")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse(argv)
    f = Finisher(args)
    if args.detach:
        detach(f.log_path)
    return f.main()


if __name__ == "__main__":
    raise SystemExit(main())
