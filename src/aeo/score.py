"""Score a parsed CLI run into an ArmResult."""

from __future__ import annotations

from typing import Any

from aeo.config import Config
from aeo.mention import (
    extract_brand_mentions,
    extract_competitor_mentions,
    extract_vendors_in_queries,
)
from aeo.parsers import ParsedRun


def score_arm(
    parsed: ParsedRun,
    cfg: Config,
    *,
    error: str | None = None,
) -> dict[str, Any]:
    text = parsed.raw_response_text
    brand_mentions = extract_brand_mentions(text, cfg.brand, cfg.aliases)
    competitor_mentions = extract_competitor_mentions(text, cfg.competitors)
    vendors = extract_vendors_in_queries(
        parsed.search_queries, cfg.brand, cfg.aliases, cfg.competitors
    )
    brand_mentioned = bool(brand_mentions)
    arm: dict[str, Any] = {
        "raw_response_text": text,
        "brand_mentioned": brand_mentioned,
        "brand_mentions": brand_mentions,
        "competitor_mentions": competitor_mentions,
        "searched": bool(parsed.searched),
        "search_queries": list(parsed.search_queries),
        "vendors_in_search_queries": vendors,
        # v1: recommended == brand mentioned in answer text (not URL-only).
        "recommended": brand_mentioned,
    }
    if error:
        arm["error"] = error
    if parsed.usage:
        arm["usage"] = parsed.usage
    return arm


def aggregates(prompts: list[dict[str, Any]]) -> dict[str, float]:
    know_hits = know_n = search_hits = search_n = searched_n = prebelief = 0
    for prompt in prompts:
        engines = prompt.get("engines") or {}
        for arms in engines.values():
            if not isinstance(arms, dict):
                continue
            k = arms.get("knowledge")
            s = arms.get("search")
            if isinstance(k, dict) and "error" not in k:
                know_n += 1
                if k.get("brand_mentioned"):
                    know_hits += 1
            if isinstance(s, dict) and "error" not in s:
                search_n += 1
                if s.get("brand_mentioned"):
                    search_hits += 1
                if s.get("searched"):
                    searched_n += 1
                    if s.get("vendors_in_search_queries"):
                        prebelief += 1
    out: dict[str, float] = {}
    if know_n:
        out["mention_rate_knowledge"] = know_hits / know_n
    if search_n:
        out["mention_rate_search"] = search_hits / search_n
        out["search_rate"] = searched_n / search_n
        out["vendor_prebelief_rate"] = (prebelief / searched_n) if searched_n else 0.0
    spend = spend_totals(prompts)
    if spend["cells_with_usage"]:
        out["tokens_input"] = float(spend["input_tokens"])
        out["tokens_output"] = float(spend["output_tokens"])
        out["tokens_total"] = float(spend["total_tokens"])
        out["cost_usd"] = float(spend["cost_usd"])
    return out


def spend_totals(prompts: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum usage across every arm. Missing usage is skipped, not treated as zero cost."""
    acc = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "cells_with_usage": 0,
        "by_engine": {},
    }
    for prompt in prompts:
        engines = prompt.get("engines") or {}
        for engine, arms in engines.items():
            if not isinstance(arms, dict):
                continue
            bucket = acc["by_engine"].setdefault(
                engine,
                {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0, "cells": 0},
            )
            for arm in arms.values():
                if not isinstance(arm, dict):
                    continue
                u = arm.get("usage")
                if not isinstance(u, dict):
                    continue
                acc["cells_with_usage"] += 1
                bucket["cells"] += 1
                for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"):
                    acc[k] += int(u.get(k) or 0)
                bucket["input_tokens"] += int(u.get("input_tokens") or 0)
                bucket["output_tokens"] += int(u.get("output_tokens") or 0)
                bucket["total_tokens"] += int(u.get("total_tokens") or 0)
                if u.get("cost_usd") is not None:
                    acc["cost_usd"] += float(u["cost_usd"])
                    bucket["cost_usd"] += float(u["cost_usd"])
    acc["cost_usd"] = round(acc["cost_usd"], 6)
    return acc
