"""Read/write aeo-cli-evidence-v1 documents. New runs never overwrite old files."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from aeo import EVIDENCE_SCHEMA_VERSION, METHODOLOGY_VERSION
from aeo.config import Config
from aeo.score import aggregates


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"aeo-{ts}-{uuid4().hex[:6]}"


def workspace_from_config(cfg: Config) -> dict[str, Any]:
    out: dict[str, Any] = {
        "brand": cfg.brand,
        "domain": cfg.domain,
        "aliases": list(cfg.aliases),
        "competitors": list(cfg.competitors),
    }
    if cfg.competitor_aliases:
        out["competitor_aliases"] = {k: list(v) for k, v in cfg.competitor_aliases.items()}
    return out


def new_document(
    cfg: Config,
    *,
    run_id: str | None = None,
    engines: list[str] | None = None,
    samples_per_arm: int | None = None,
    comment: str | None = None,
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "workspace": workspace_from_config(cfg),
        "run": {
            "run_id": run_id or new_run_id(),
            "timestamp": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "methodology_version": METHODOLOGY_VERSION,
            "engines": list(engines or cfg.engines),
            "samples_per_arm": int(samples_per_arm or cfg.samples_per_arm),
        },
        "prompts": [],
    }
    if comment:
        doc["run"]["comment"] = comment
    return doc


def attach_aggregates(doc: dict[str, Any]) -> dict[str, Any]:
    rates = aggregates(doc.get("prompts") or [])
    for key in (
        "mention_rate_knowledge",
        "mention_rate_search",
        "search_rate",
        "vendor_prebelief_rate",
    ):
        doc.pop(key, None)
        if key in rates:
            doc[key] = rates[key]
    return doc


def write_document(doc: dict[str, Any], path: str | Path, *, overwrite: bool = False) -> Path:
    p = Path(path)
    if p.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing evidence file: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    attach_aggregates(doc)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    return p


def load_document(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def default_out_path(cfg: Config, run_id: str) -> Path:
    root = Path(cfg.data_dir)
    if cfg.path and not root.is_absolute():
        root = cfg.path.parent / root
    return root / "runs" / f"{run_id}.json"


def iter_evidence_files(path: str | Path) -> list[Path]:
    p = Path(path)
    if p.is_file():
        return [p]
    if not p.exists():
        return []
    files = sorted(p.glob("*.json"))
    if not files:
        files = sorted((p / "runs").glob("*.json")) if (p / "runs").is_dir() else []
    return [f for f in files if f.name != "example-run.json" or True]


def prompt_entry_key(entry: dict[str, Any]) -> tuple[str, int]:
    """Identity for a prompt row: prompt_id + sample_index (missing index = 1)."""
    pid = str(entry.get("prompt_id") or "")
    raw = entry.get("sample_index")
    if raw is None:
        return (pid, 1)
    return (pid, int(raw))


def parts_dir_for(out: str | Path) -> Path:
    """Per-run shard directory next to `--out` (never written by workers as `--out`)."""
    p = Path(out)
    return p.parent / f"{p.name}.parts"


def iter_shard_files(parts_dir: str | Path) -> list[Path]:
    root = Path(parts_dir)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.json") if not p.name.endswith(".tmp"))


def cell_shard_name(prompt_id: str, sample_index: int, engine: str, arm: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in prompt_id)[:80]
    return f"{safe}__{sample_index}__{engine}__{arm}__{uuid4().hex[:8]}.json"


def cell_shard_document(
    base: dict[str, Any],
    entry: dict[str, Any],
) -> dict[str, Any]:
    """One-prompt document used as a worker shard. Same run_id as the parent."""
    return {
        "schema_version": base.get("schema_version") or EVIDENCE_SCHEMA_VERSION,
        "workspace": copy.deepcopy(base.get("workspace") or {}),
        "run": copy.deepcopy(base.get("run") or {}),
        "prompts": [copy.deepcopy(entry)],
    }


def union_documents(base: dict[str, Any], *shards: dict[str, Any]) -> dict[str, Any]:
    """Merge same-run shards into base.

    Existing prompt×engine×arm cells in *base* win (resume / skip-completed).
    Prompt order follows *base*, then first-seen extras from shards.
    Run metadata stays that of *base* (unlike report-time ``merge_docs``).
    """
    out = copy.deepcopy(base)
    prompts = list(out.get("prompts") or [])
    index: dict[tuple[str, int], int] = {}
    for i, entry in enumerate(prompts):
        if isinstance(entry, dict):
            index[prompt_entry_key(entry)] = i
    for shard in shards:
        if not isinstance(shard, dict):
            continue
        for prompt in shard.get("prompts") or []:
            if not isinstance(prompt, dict):
                continue
            key = prompt_entry_key(prompt)
            if key not in index:
                new_entry = copy.deepcopy(prompt)
                new_entry.setdefault("engines", {})
                index[key] = len(prompts)
                prompts.append(new_entry)
                continue
            dest = prompts[index[key]]
            dest.setdefault("engines", {})
            src_engines = prompt.get("engines") or {}
            if not isinstance(src_engines, dict) or not isinstance(dest["engines"], dict):
                continue
            for engine, arms in src_engines.items():
                if not isinstance(arms, dict):
                    continue
                dest_arms = dest["engines"].setdefault(engine, {})
                if not isinstance(dest_arms, dict):
                    continue
                for arm_name, arm in arms.items():
                    if arm_name not in dest_arms:
                        dest_arms[arm_name] = copy.deepcopy(arm)
    out["prompts"] = prompts
    return out
