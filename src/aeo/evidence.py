"""Read/write aeo-cli-evidence-v1 documents. New runs never overwrite old files."""

from __future__ import annotations

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
    return {
        "brand": cfg.brand,
        "domain": cfg.domain,
        "aliases": list(cfg.aliases),
        "competitors": list(cfg.competitors),
    }


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


def write_document(doc: dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    if p.exists():
        raise FileExistsError(f"refusing to overwrite existing evidence file: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    attach_aggregates(doc)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
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
