"""Reader-facing helpers shared by the HTML reports.

One place for plain labels, user-time (TRT) timestamps, the definition of a
"surprise" competitor (and how they are grouped), and number formatting with a
denominator. Every report imports from here so the same thing is never called
two different names or counted two different ways.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

# ------------------------------------------------------------------ labels

LABELS = {
    "knowledge": "from memory",
    "search": "with web search",
    "cell": "answer",
    "cells": "answers",
    "seed list": "competitors we track",
    "field": "all competitor mentions",
    "pp": "points",
}
ARM_SHORT = {"knowledge": "M", "search": "W"}  # chip letters: Memory / Web search
ARM_TITLE = {"knowledge": "From memory (search off)", "search": "With web search"}
ENGINE_TITLE = {"claude": "Claude", "codex": "Codex", "grok": "Grok"}

LOCATIONS = {2840: "US", 2826: "UK", 2276: "Germany", 2250: "France", 2124: "Canada", 2036: "Australia",
             2792: "Turkey", 2356: "India"}
LANGUAGES = {"en": "English", "de": "German", "fr": "French", "tr": "Turkish", "es": "Spanish"}


def engine_title(e: str) -> str:
    return ENGINE_TITLE.get(e, str(e).title())


def location_words(code: Any, lang: Any, device: Any) -> str:
    try:
        loc = LOCATIONS.get(int(code), f"location {code}")
    except (TypeError, ValueError):
        loc = str(code or "")
    bits = [loc, LANGUAGES.get(str(lang or ""), str(lang or "")), str(device or "")]
    return ", ".join(b for b in bits if b)


# ------------------------------------------------------------------ time

USER_TZ = os.environ.get("AEO_TZ") or "Europe/Istanbul"
USER_TZ_LABEL = os.environ.get("AEO_TZ_LABEL") or "TRT"


def parse_ts(ts: Any) -> datetime | None:
    if not ts:
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    for fmt in (None, "%Y%m%d-%H%M"):
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def user_time(ts: Any, *, with_day: bool = True) -> str:
    """'Fri Oct 9, 2026, 22:18 TRT' from an ISO timestamp (UTC if no zone)."""
    dt = parse_ts(ts)
    if dt is None:
        return str(ts or "")
    if ZoneInfo is not None:
        try:
            dt = dt.astimezone(ZoneInfo(USER_TZ))
        except Exception:  # pragma: no cover - missing tz database
            pass
    day = f"{dt:%a} {dt:%b} {dt.day}, {dt:%Y}" if with_day else f"{dt:%b} {dt.day}, {dt:%Y}"
    return f"{day}, {dt:%H:%M} {USER_TZ_LABEL}"


# ------------------------------------------------------------------ numbers


def n_of(a: Any, b: Any) -> str:
    return f"{int(a or 0)} of {int(b or 0)}"


def pct(rate: float | None) -> str:
    """Percent that never rounds a real non-zero value to 0.0%."""
    if rate is None:
        return "—"
    v = float(rate) * 100
    if v == 0:
        return "0%"
    if abs(v) < 1:
        return f"{v:.2g}%"
    return f"{v:.1f}%"


def points(delta: float | None) -> str:
    if delta is None:
        return "—"
    if abs(delta) < 0.05 and delta != 0:
        return f"{'+' if delta > 0 else ''}{delta:.2g} points"
    return f"{'+' if delta > 0 else ''}{delta:.1f} points"


# ------------------------------------------------------------------ surprises

# Not competitors: the assistants under test and their makers, programming
# languages, compilers/runtimes and clouds. Matching is on the lowercased name.
ASSISTANTS = {
    "claude", "claude code", "codex", "openai codex", "codex cli", "chatgpt", "openai", "anthropic",
    "grok", "xai", "gemini", "google gemini",
}
LANGUAGES_SET = {
    "python", "go", "golang", "rust", "java", "javascript", "typescript", "c", "c++", "c#", "ruby", "php",
    "kotlin", "swift", "scala", "haskell", "ocaml", "ada", "elixir", "erlang", "dart", "lua", "perl", "r",
    "sql", "bash", "shell", "node.js", "nodejs", "node", "html", "css", "yaml", "json", "f#", "clojure",
    "zig", "julia", "matlab", "objective-c", "assembly", "webassembly", "wasm",
}
COMPILERS = {"gcc", "clang", "llvm", "msvc", "rustc", "javac", "tsc", "jvm", "deno", "bun", "cargo", "npm",
             "pip", "maven", "gradle"}
CLOUDS = {"aws", "amazon web services", "azure", "microsoft azure", "gcp", "google cloud",
          "google cloud platform", "cloudflare"}
NOT_COMPETITORS = ASSISTANTS | LANGUAGES_SET | COMPILERS | CLOUDS


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()


def in_question_text(name: str, texts: Iterable[str]) -> bool:
    n = _norm(name)
    if not n:
        return False
    pat = re.compile(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])")
    return any(pat.search(_norm(t)) for t in texts)


def exclusion_reason(name: str, question_texts: Iterable[str] = (), engines: Iterable[str] = ()) -> str | None:
    n = _norm(name)
    if len(re.sub(r"[^a-z0-9]", "", n)) <= 1:
        return "too short to be a product name"
    if n in ASSISTANTS or any(n == e or n.startswith(e + " ") for e in engines):
        return "assistant under test"
    if n in LANGUAGES_SET:
        return "programming language"
    if n in COMPILERS:
        return "compiler or build tool"
    if n in CLOUDS:
        return "cloud provider"
    if in_question_text(name, question_texts):
        return "named in the question"
    return None


CATEGORIES: list[tuple[str, tuple[str, ...]]] = [
    ("Requirements & traceability", (
        "strictdoc", "sphinx-needs", "doorstop", "openfasttrac", "trlc", "lobster", "ketryx", "visure",
        "modern requirements", "helix alm", "reqtify", "spec kit", "openspec", "kiro", "jama", "polarion",
        "codebeamer", "doors", "requirement", "trace")),
    ("Testing (property, mutation, contract, fuzz)", (
        "hypothesis", "stryker", "fast-check", "pit", "mutmut", "jqwik", "proptest", "cosmic-ray",
        "mutants", "mutesting", "quickcheck", "rapid", "gremlins", "mull", "approvaltests", "pytest", "junit",
        "jacoco", "coverage", "gcov", "llvm-cov", "istanbul", "codecov", "playwright", "cucumber", "specflow",
        "reqnroll", "behave", "gauge", "concordion", "testcontainers", "wiremock", "pact", "schemathesis",
        "keploy", "diffblue", "speedscale", "goreplay", "diffy", "k6", "allure", "testrail", "zephyr", "xray",
        "afl", "fuzz", "antithesis", "jepsen", "chaos", "gremlin", "test", "scientist", "mock")),
    ("Formal methods & static analysis", (
        "kani", "verus", "spark", "astrée", "astree", "tlc", "tla", "rocq", "coq", "quint", "cbmc", "frama",
        "parasoft", "rapita", "vectorcast", "ldra", "semgrep", "codeql", "sonar", "eslint", "golangci",
        "mypy", "pyright", "archunit", "dependency-cruiser", "codescene", "infer", "lean", "isabelle",
        "dafny", "alloy", "static", "lint", "staticcheck", "go vet")),
    ("Security & supply chain", (
        "dependabot", "renovate", "socket", "gitleaks", "syft", "trivy", "sigstore", "in-toto", "owasp",
        "defectdojo", "burp", "advanced security", "code security", "attestation", "conftest",
        "open policy agent", "opa", "cedar", "snyk", "slsa", "sbom")),
    ("AI code review & agents", (
        "coderabbit", "greptile", "qodo", "copilot", "cursor", "graphite", "sourcegraph", "devin",
        "windsurf", "aider", "cline", "swe-bench", "code review", "watsonx", "transform", "tabnine")),
    ("Issue tracking & dev platforms", (
        "jira", "gitlab", "github", "azure devops", "linear", "confluence", "git", "mergify", "bazel", "nx",
        "turborepo", "pants", "develocity", "bitbucket")),
    ("Observability, release & LLM ops", (
        "datadog", "opentelemetry", "sentry", "honeycomb", "grafana", "launchdarkly", "unleash", "argo",
        "envoy", "istio", "nginx", "langfuse", "langsmith", "braintrust", "promptfoo", "great expectations",
        "dbt", "datafold")),
]
OTHER = "Other"


def category(name: str) -> str:
    n = _norm(name)
    for cat, keys in CATEGORIES:
        if any(k in n for k in keys):
            return cat
    return OTHER


def filter_surprises(counts: Counter, question_texts: Iterable[str], engines: Iterable[str] = ()) -> tuple[Counter, Counter]:
    """(kept, excluded-by-reason) using one definition of a surprise competitor."""
    texts = list(question_texts)
    engines = [_norm(e) for e in engines]
    kept: Counter = Counter()
    excluded: Counter = Counter()
    for name, n in counts.items():
        reason = exclusion_reason(name, texts, engines)
        if reason:
            excluded[reason] += n
        else:
            kept[name] += n
    return kept, excluded


def group_by_category(counts: Counter, top: int = 10) -> list[tuple[str, int, list[tuple[str, int]]]]:
    groups: dict[str, Counter] = {}
    for name, n in counts.items():
        groups.setdefault(category(name), Counter())[name] += n
    order = sorted(groups.items(), key=lambda kv: (kv[0] == OTHER, -sum(kv[1].values())))
    return [(cat, sum(c.values()), c.most_common(top)) for cat, c in order]
