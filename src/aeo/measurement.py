"""Frozen measurement set: which questions are compared across runs.

The config's ``measurement_set`` freezes the questions used to measure change:

    "measurement_set": {"id": "proof-v3", "version": 1, "frozen": "2026-10-10",
                        "question_hash": "sha256:...", "count": 108}

Every prompt is in the ``measurement`` group unless it says
``"group": "exploratory"``. New or reworded questions go in the exploratory group:
they are run and reported separately and never change the frozen hash. The hash
covers the id and exact (whitespace-normalised) text of every measurement
question, so editing one is detected before a run starts.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

GROUPS = ("measurement", "exploratory")


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def prompt_group(prompt: Any) -> str:
    raw = prompt.get("group") if isinstance(prompt, dict) else getattr(prompt, "group", None)
    return "exploratory" if str(raw or "").strip().lower() == "exploratory" else "measurement"


def _id_text(prompt: Any) -> tuple[str, str]:
    if isinstance(prompt, dict):
        return str(prompt.get("id") or prompt.get("prompt_id") or ""), normalize_text(prompt.get("text") or prompt.get("prompt_text") or "")
    return str(prompt.id), normalize_text(prompt.text)


def question_hash(prompts: Iterable[Any]) -> str:
    rows = sorted(_id_text(p) for p in prompts if prompt_group(p) == "measurement")
    blob = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def measurement_record(cfg: Any, selected: Iterable[Any] | None = None) -> dict[str, Any] | None:
    """What the run measured, for run metadata. None when the config declares no set."""
    declared = getattr(cfg, "measurement_set", None)
    prompts = list(getattr(cfg, "prompts", None) or [])
    if not declared:
        return None
    actual = question_hash(prompts)
    rec: dict[str, Any] = {
        "id": str(declared.get("id") or ""),
        "version": declared.get("version"),
        "frozen": declared.get("frozen"),
        "declared_hash": declared.get("question_hash"),
        "question_hash": actual,
        "matches": (declared.get("question_hash") in (None, "", actual)),
        "measurement_count": sum(1 for p in prompts if prompt_group(p) == "measurement"),
        "exploratory_count": sum(1 for p in prompts if prompt_group(p) == "exploratory"),
    }
    if selected is not None:
        sel = list(selected)
        rec["run_measurement_count"] = sum(1 for p in sel if prompt_group(p) == "measurement")
        rec["run_exploratory_count"] = sum(1 for p in sel if prompt_group(p) == "exploratory")
    return rec


def mismatch_message(rec: dict[str, Any]) -> str:
    return (
        f"measurement set {rec.get('id')} v{rec.get('version')} changed: config questions hash to "
        f"{rec.get('question_hash')} but the frozen set is {rec.get('declared_hash')}. "
        "Put new or reworded questions in the exploratory group (\"group\": \"exploratory\"), "
        "or freeze a new version on purpose."
    )
