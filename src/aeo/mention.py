"""Deterministic whole-word mention and vendor-in-query extraction.

A mention is a case-insensitive word-boundary match of a brand, alias, or
competitor name in answer text. Substring hits do not count. Matches that
exist only inside http(s) URLs do not count (URL-only). Overlapping hits
keep the longest term (so "xerj.org" is not also "xerj").
"""

from __future__ import annotations

import re
from typing import Iterable

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def strip_urls(text: str) -> str:
    return URL_RE.sub(" ", text or "")


def word_boundary_pattern(term: str) -> re.Pattern[str]:
    """Match `term` as a whole token. `.` and `-` in the term are literal."""
    return re.compile(rf"(?<![\w]){re.escape(term)}(?![\w])", re.IGNORECASE)


def unique_terms(terms: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in terms:
        term = (raw or "").strip()
        if not term:
            continue
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    out.sort(key=lambda t: (-len(t), t.lower()))
    return out


def find_terms(text: str, terms: Iterable[str], *, ignore_urls: bool = True) -> list[str]:
    """Return config-form terms that appear as whole words in text."""
    haystack = strip_urls(text) if ignore_urls else (text or "")
    spans: list[tuple[int, int, str]] = []
    for term in unique_terms(terms):
        for match in word_boundary_pattern(term).finditer(haystack):
            spans.append((match.start(), match.end(), term))
    # Longest first so a shorter term inside a longer hit is dropped.
    spans.sort(key=lambda s: (-(s[1] - s[0]), s[0]))
    kept: list[tuple[int, int, str]] = []
    for start, end, term in spans:
        if any(start >= k[0] and end <= k[1] and (end - start) < (k[1] - k[0]) for k in kept):
            continue
        kept.append((start, end, term))
    found: list[str] = []
    seen: set[str] = set()
    for _, _, term in sorted(kept, key=lambda s: s[0]):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            found.append(term)
    return found


def brand_terms(brand: str, aliases: Iterable[str]) -> list[str]:
    return unique_terms([brand, *aliases])


def extract_brand_mentions(text: str, brand: str, aliases: Iterable[str]) -> list[str]:
    return find_terms(text, brand_terms(brand, aliases), ignore_urls=True)


def extract_competitor_mentions(text: str, competitors: Iterable[str]) -> list[str]:
    return find_terms(text, competitors, ignore_urls=True)


def extract_vendors_in_queries(
    queries: Iterable[str],
    brand: str,
    aliases: Iterable[str],
    competitors: Iterable[str],
) -> list[str]:
    """Vendor names (brand, aliases, competitors) appearing in search-query strings."""
    blob = "\n".join(q for q in queries if q)
    return find_terms(blob, [*brand_terms(brand, aliases), *competitors], ignore_urls=True)
