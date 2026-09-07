"""Plan remaining cells and run them, optionally in parallel.

Design (safer than lock + reload-before-write):

- One ``aeo run`` process owns ``--out``. Do not share that file across processes.
- ``--concurrency N`` (default 1) runs up to N remaining cells
  (prompt × sample × engine × arm) in this process. ``--engine all`` stays
  one process; engines share the same pool.
- Workers never write ``--out``. Each completed cell is a shard under
  ``<out>.parts/``. The parent unions shards into ``--out`` after every cell
  (existing cells win). Leftover shards from a killed run are merged on the
  next resume before new work is planned.
"""

from __future__ import annotations

import copy
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aeo.config import Config
from aeo.engines import build_invocation, run_invocation
from aeo.evidence import (
    cell_shard_document,
    cell_shard_name,
    iter_shard_files,
    load_document,
    parts_dir_for,
    prompt_entry_key,
    union_documents,
    write_document,
)
from aeo.parsers import parse_engine
from aeo.score import score_arm


@dataclass(frozen=True)
class CellWork:
    prompt_id: str
    prompt_text: str
    intent: str | None
    class_: str | None
    why: str | None
    sample_index: int
    engine: str
    arm: str


def find_entry(
    doc: dict[str, Any],
    prompt_id: str,
    sample_index: int,
    samples: int,
) -> dict[str, Any] | None:
    want = (prompt_id, sample_index if samples > 1 else 1)
    for entry in doc.get("prompts") or []:
        if not isinstance(entry, dict):
            continue
        if samples > 1:
            if prompt_entry_key(entry) != want:
                continue
            return entry
        if entry.get("prompt_id") == prompt_id:
            return entry
    return None


def cell_done(entry: dict[str, Any], engine: str, arm: str) -> bool:
    got = ((entry.get("engines") or {}).get(engine) or {})
    return arm in got


def prompt_entry(
    prompt: dict[str, Any],
    sample_index: int,
    samples: int,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "prompt_id": prompt["id"],
        "prompt_text": prompt["text"],
        "engines": {},
    }
    if prompt.get("intent"):
        entry["intent"] = prompt["intent"]
    if prompt.get("class"):
        entry["class"] = prompt["class"]
    if prompt.get("why"):
        entry["why"] = prompt["why"]
    if samples > 1:
        entry["sample_index"] = sample_index
    return entry


def ensure_prompt_entry(
    doc: dict[str, Any],
    prompt: dict[str, Any],
    sample_index: int,
    samples: int,
) -> dict[str, Any]:
    entry = find_entry(doc, prompt["id"], sample_index, samples)
    if entry is None:
        entry = prompt_entry(prompt, sample_index, samples)
        doc.setdefault("prompts", []).append(entry)
    return entry


def plan_remaining(
    doc: dict[str, Any],
    prompts: list[dict[str, Any]],
    engines: list[str],
    arms: list[str],
    samples: int,
) -> tuple[int, list[CellWork]]:
    """Ensure roster-order entries exist; return (skipped, remaining jobs)."""
    skipped = 0
    jobs: list[CellWork] = []
    for prompt in prompts:
        for sample_index in range(1, samples + 1):
            entry = ensure_prompt_entry(doc, prompt, sample_index, samples)
            for engine in engines:
                for arm in arms:
                    if cell_done(entry, engine, arm):
                        skipped += 1
                        print(
                            f"skip {engine} {arm} ({prompt['id']}) already stored",
                            file=sys.stderr,
                            flush=True,
                        )
                        continue
                    jobs.append(
                        CellWork(
                            prompt_id=prompt["id"],
                            prompt_text=prompt["text"],
                            intent=prompt.get("intent"),
                            class_=prompt.get("class"),
                            why=prompt.get("why"),
                            sample_index=sample_index,
                            engine=engine,
                            arm=arm,
                        )
                    )
    return skipped, jobs


def recover_shards(doc: dict[str, Any], out: Path) -> tuple[dict[str, Any], int]:
    """Union leftover ``<out>.parts/*.json`` into *doc*. Existing cells win."""
    parts = parts_dir_for(out)
    recovered = 0
    for path in iter_shard_files(parts):
        try:
            shard = load_document(path)
        except (OSError, ValueError) as exc:
            print(f"skip corrupt shard {path}: {exc}", file=sys.stderr, flush=True)
            continue
        doc = union_documents(doc, shard)
        recovered += 1
        try:
            path.unlink()
        except OSError:
            pass
    if recovered:
        print(f"merged {recovered} leftover shard(s) into {out}", file=sys.stderr, flush=True)
    _rmdir_if_empty(parts)
    return doc, recovered


def should_retry(error: str | None) -> bool:
    if not error:
        return False
    if "CLI not found" in error or "failed to start" in error:
        return False
    return True


def run_cell(engine: str, arm: str, prompt_id: str, inv: Any, timeout: int, retries: int) -> Any:
    last = None
    attempts = max(0, int(retries)) + 1
    for attempt in range(attempts):
        if attempt:
            print(
                f"retry {attempt}/{retries} {engine} {arm} ({prompt_id}) after {last.error}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(min(8, 2 ** (attempt - 1)))
        last = run_invocation(inv, timeout=timeout)
        if not last.error:
            return last
        if not should_retry(last.error):
            return last
    return last


def execute_cell(
    work: CellWork,
    cfg: Config,
    *,
    samples: int,
    timeout: int,
    retries: int,
) -> dict[str, Any]:
    inv = build_invocation(work.engine, work.arm, work.prompt_text, cfg)
    print(f"running {work.engine} {work.arm} ({work.prompt_id}) …", file=sys.stderr, flush=True)
    result = run_cell(work.engine, work.arm, work.prompt_id, inv, timeout, retries)
    parsed = parse_engine(work.engine, result.stdout)
    if not parsed.raw_response_text and result.stderr and not result.error:
        parsed.raw_response_text = result.stderr.strip()
    scored = score_arm(parsed, cfg, error=result.error)
    entry = prompt_entry(
        {
            "id": work.prompt_id,
            "text": work.prompt_text,
            "intent": work.intent,
            "class": work.class_,
            "why": work.why,
        },
        work.sample_index,
        samples,
    )
    entry["engines"] = {work.engine: {work.arm: scored}}
    return entry


def _write_shard(base: dict[str, Any], out: Path, work: CellWork, entry: dict[str, Any]) -> Path:
    parts = parts_dir_for(out)
    parts.mkdir(parents=True, exist_ok=True)
    path = parts / cell_shard_name(work.prompt_id, work.sample_index, work.engine, work.arm)
    write_document(cell_shard_document(base, entry), path, overwrite=True)
    return path


def _rmdir_if_empty(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def apply_shard(doc: dict[str, Any], out: Path, shard: dict[str, Any], shard_path: Path | None) -> dict[str, Any]:
    doc = union_documents(doc, shard)
    write_document(doc, out, overwrite=True)
    if shard_path is not None:
        try:
            shard_path.unlink()
        except OSError:
            pass
    print(f"checkpoint {len(doc.get('prompts') or [])} prompts -> {out}", file=sys.stderr, flush=True)
    return doc


def run_jobs(
    *,
    cfg: Config,
    doc: dict[str, Any],
    out: Path,
    jobs: list[CellWork],
    samples: int,
    timeout: int,
    retries: int,
    concurrency: int,
) -> tuple[dict[str, Any], int]:
    """Execute *jobs* with up to *concurrency* workers. Returns (doc, ran)."""
    if not jobs:
        return doc, 0
    workers = max(1, min(int(concurrency), len(jobs)))
    ran = 0
    # Snapshot metadata so workers never read `--out` while the parent writes it.
    base_meta = {
        "schema_version": doc.get("schema_version"),
        "workspace": copy.deepcopy(doc.get("workspace") or {}),
        "run": copy.deepcopy(doc.get("run") or {}),
        "prompts": [],
    }

    def _one(work: CellWork) -> tuple[CellWork, dict[str, Any], Path]:
        entry = execute_cell(work, cfg, samples=samples, timeout=timeout, retries=retries)
        path = _write_shard(base_meta, out, work, entry)
        return work, cell_shard_document(base_meta, entry), path

    if workers == 1:
        for work in jobs:
            _work, shard, path = _one(work)
            doc = apply_shard(doc, out, shard, path)
            ran += 1
        _rmdir_if_empty(parts_dir_for(out))
        return doc, ran

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, work) for work in jobs]
        for fut in as_completed(futures):
            _work, shard, path = fut.result()
            doc = apply_shard(doc, out, shard, path)
            ran += 1
    _rmdir_if_empty(parts_dir_for(out))
    return doc, ran
