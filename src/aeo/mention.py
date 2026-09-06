"""Deterministic whole-word mention and vendor-in-query extraction.

A mention is a case-insensitive word-boundary match of a brand, alias, or
competitor name in answer text. Substring hits do not count. Domain-style
brand/alias terms (e.g. reqproof.com, xerj.org) also count when they appear
as the host or www.host of an http(s) URL. The brand token as the first
DNS label of a two-label host ({brand}.{tld}) counts when a domain alias
exists. Bare words that appear only inside a URL path or query do not
count. Competitor matching still ignores URLs entirely. Overlapping prose
hits keep the longest term (so "xerj.org" is not also "xerj").
"""

from __future__ import annotations

import re
from typing import Iterable
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
# Hostname-shaped: at least two labels (foo.bar). Used to decide which
# brand/alias terms are eligible for the URL-host pass.
DOMAIN_TERM_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)


def strip_urls(text: str) -> str:
    return URL_RE.sub(" ", text or "")


def is_domain_term(term: str) -> bool:
    """True if term looks like a hostname (contains a dot), not a bare word."""
    return bool(DOMAIN_TERM_RE.fullmatch((term or "").strip()))


def strip_www(host: str) -> str:
    h = (host or "").strip().lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def http_url_hosts(text: str) -> list[str]:
    """Lowercased hosts of http(s) URLs in text, in appearance order."""
    hosts: list[str] = []
    for match in URL_RE.finditer(text or ""):
        raw = match.group(0).rstrip(".,;:!?")
        try:
            host = (urlparse(raw).hostname or "").lower().rstrip(".")
        except ValueError:
            continue
        if host:
            hosts.append(host)
    return hosts


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


def _alias_for_host(host: str, brand: str, domain_aliases: list[str]) -> str | None:
    """Config-form domain alias for an http(s) host, or None."""
    norm = strip_www(host)
    if not norm:
        return None
    alias_by_norm = {strip_www(a): a for a in reversed(domain_aliases)}
    if norm in alias_by_norm:
        return alias_by_norm[norm]
    for alias_norm, alias in alias_by_norm.items():
        if norm.endswith("." + alias_norm):
            return alias
    brand_key = (brand or "").strip().lower()
    labels = norm.split(".")
    # Apex-label: first DNS label is the brand and host is {brand}.{tld}.
    if brand_key and len(labels) == 2 and labels[0] == brand_key and domain_aliases:
        for alias in domain_aliases:
            if strip_www(alias).split(".")[0] == brand_key:
                return alias
        return domain_aliases[0]
    return None


def find_brand_url_host_mentions(text: str, brand: str, aliases: Iterable[str]) -> list[str]:
    """Domain-style brand/alias terms that appear as http(s) URL hosts."""
    domain_aliases = [t for t in brand_terms(brand, aliases) if is_domain_term(t)]
    if not domain_aliases:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for host in http_url_hosts(text):
        hit = _alias_for_host(host, brand, domain_aliases)
        if hit is None:
            continue
        key = hit.lower()
        if key not in seen:
            seen.add(key)
            found.append(hit)
    return found


def extract_brand_mentions(text: str, brand: str, aliases: Iterable[str]) -> list[str]:
    """Brand/alias hits in prose, plus domain-style aliases on URL hosts."""
    found = find_terms(text, brand_terms(brand, aliases), ignore_urls=True)
    seen = {t.lower() for t in found}
    for term in find_brand_url_host_mentions(text, brand, aliases):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            found.append(term)
    return found


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
