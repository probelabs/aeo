"""Identity canary: before a run, ask each engine who the user is.

Each engine gets the exact isolated invocation used for real answers (no-search
arm, fresh engine home, empty working directory) and the question below. If the
answer contains the user's name, handle, company or product, the isolation leaked
and the run must not be used as measurement.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

from aeo.config import Config
from aeo.engines import build_invocation
from aeo.parsers import parse_engine

CANARY_PROMPT = "What is my name or email, and what do you know about me?"
# Leonid's identity, company and product. Config `identity_terms` adds more.
DEFAULT_IDENTITY_TERMS = ["leonid", "bugaev", "buger", "probelabs", "probe labs", "reqproof"]


def identity_terms(cfg: Config | None) -> list[str]:
    terms = list(DEFAULT_IDENTITY_TERMS)
    extra = list(getattr(cfg, "identity_terms", None) or [])
    env = os.environ.get("AEO_IDENTITY_TERMS") or ""
    extra += [t for t in env.split(",") if t.strip()]
    for t in extra:
        t = str(t).strip().lower()
        if t and t not in terms:
            terms.append(t)
    return terms


def leaked_terms(answer: str, terms: list[str]) -> list[str]:
    low = (answer or "").lower()
    found = []
    for t in terms:
        if re.search(r"(?<![a-z0-9])" + re.escape(t.lower()) + r"(?![a-z0-9])", low):
            found.append(t)
    return found


def run_canary(engine: str, cfg: Config, *, timeout: int = 180, runner=None) -> dict[str, Any]:
    """Run the canary on one engine. Returns a record for run metadata.

    status: pass (answer shows no identity), leak (identity terms found),
    error (the engine did not answer, so isolation could not be verified).
    """
    if runner is None:
        from aeo import runner as runner_mod

        runner = runner_mod.run_invocation
    inv = build_invocation(engine, "knowledge", CANARY_PROMPT, cfg, raw_prompt=True)
    res = runner(inv, timeout=timeout)
    answer = parse_engine(engine, res.stdout).raw_response_text if not res.error else ""
    terms = identity_terms(cfg)
    found = leaked_terms(answer, terms) if answer else []
    if res.error or not answer.strip():
        status = "error"
    elif found:
        status = "leak"
    else:
        status = "pass"
    return {
        "status": status,
        "prompt": CANARY_PROMPT,
        "answer": (answer or "")[:600],
        "leaked_terms": found,
        "checked_terms": terms,
        "error": res.error,
        "at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
