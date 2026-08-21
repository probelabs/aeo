"""Validate evidence/config documents. jsonschema if installed, else structural."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_with_jsonschema(doc: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
    except ImportError:
        return []
    validator = Draft202012Validator(schema)
    return [e.message for e in validator.iter_errors(doc)]


def structural_evidence(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["document is not an object"]
    if doc.get("schema_version") != "aeo-cli-evidence-v1":
        errors.append("schema_version must be aeo-cli-evidence-v1")
    for key in ("workspace", "run", "prompts"):
        if key not in doc:
            errors.append(f"missing {key}")
    ws = doc.get("workspace") or {}
    for key in ("brand", "domain", "aliases", "competitors"):
        if key not in ws:
            errors.append(f"workspace missing {key}")
    run = doc.get("run") or {}
    for key in ("run_id", "timestamp", "methodology_version", "engines", "samples_per_arm"):
        if key not in run:
            errors.append(f"run missing {key}")
    if run.get("methodology_version") not in {None, "aeo-cli-v1"}:
        errors.append("methodology_version must be aeo-cli-v1")
    prompts = doc.get("prompts")
    if not isinstance(prompts, list):
        errors.append("prompts must be an array")
        return errors
    arm_keys = (
        "raw_response_text",
        "brand_mentioned",
        "brand_mentions",
        "competitor_mentions",
        "searched",
        "search_queries",
        "vendors_in_search_queries",
        "recommended",
    )
    for i, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            errors.append(f"prompts[{i}] not an object")
            continue
        for key in ("prompt_id", "prompt_text", "engines"):
            if key not in prompt:
                errors.append(f"prompts[{i}] missing {key}")
        engines = prompt.get("engines") or {}
        if not isinstance(engines, dict):
            errors.append(f"prompts[{i}].engines not an object")
            continue
        for engine, arms in engines.items():
            if engine not in {"claude", "codex", "grok"}:
                errors.append(f"unknown engine {engine}")
            if not isinstance(arms, dict):
                continue
            for arm_name, arm in arms.items():
                if arm_name not in {"knowledge", "search"}:
                    errors.append(f"{engine}.{arm_name} is not an arm")
                    continue
                if not isinstance(arm, dict):
                    errors.append(f"{engine}.{arm_name} not an object")
                    continue
                for key in arm_keys:
                    if key not in arm:
                        errors.append(f"{engine}.{arm_name} missing {key}")
    return errors


def structural_config(doc: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(doc, dict):
        return ["document is not an object"]
    for key in ("brand", "domain", "aliases", "competitors", "engines", "prompts"):
        if key not in doc:
            errors.append(f"missing {key}")
    prompts = doc.get("prompts")
    if isinstance(prompts, list):
        for i, p in enumerate(prompts):
            if not isinstance(p, dict) or "id" not in p or "text" not in p:
                errors.append(f"prompts[{i}] needs id and text")
    return errors


def validate_evidence(doc: dict[str, Any]) -> list[str]:
    errors = structural_evidence(doc)
    schema_path = SCHEMA_DIR / "aeo-cli-evidence-v1.json"
    if schema_path.exists():
        errors.extend(validate_with_jsonschema(doc, _load_schema("aeo-cli-evidence-v1.json")))
    # Dedupe
    seen: set[str] = set()
    out: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def validate_config(doc: dict[str, Any]) -> list[str]:
    errors = structural_config(doc)
    schema_path = SCHEMA_DIR / "aeo-cli-config-v1.json"
    if schema_path.exists():
        errors.extend(validate_with_jsonschema(doc, _load_schema("aeo-cli-config-v1.json")))
    seen: set[str] = set()
    out: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out
