"""Deterministic whole-word mention and vendor-in-query extraction.

This module is the one brand matcher for the whole AEO pipeline: the
runner (`aeo run` scoring, in both probelabs-aeo-main and the skill-v0
clone via a shim), the board, the change report, judge/render vendor
classification, and the per-run rescore scripts.

A mention is a case-insensitive word-boundary match of a brand, alias, or
competitor name in answer text. Substring hits do not count. Domain-style
brand/alias terms (e.g. reqproof.com, xerj.org) also count when they appear
as the host or www.host of an http(s) URL. Path-style aliases such as
``probelabs/proof`` count in prose and as a URL path (github.com/probelabs/proof).
The brand token as the first DNS label of a two-label host ({brand}.{tld})
counts when a domain alias exists. Bare words that appear only inside a URL
path or query do not count. Competitor matching still ignores URLs entirely.
Overlapping prose hits keep the longest term (so "xerj.org" is not also "xerj").

Product-form-only terms
-----------------------
Some brand names are ordinary English words. The product Proof
(reqproof.com) is the word "proof". Such terms are listed in the config::

    "brand_match": {"product_form_only": ["Proof"]}

and are never matched case-insensitively. They count only when the exact
capitalized token appears in a clear product form:

* next to an unambiguous alias: ``Proof (reqproof.com)``, ``[Proof](https://reqproof.com/)``,
  ``Proof — reqproof.com``
* after a product lead-in: ``tools like Proof``, ``such as Jama, Polarion or Proof``,
  ``Try Proof``, ``use Proof``

and never as ``proof``/``proof of``/``social proof``/``burden of proof``/
``proof point``/``mathematical proof``/``Proof-of-concept``/``Proof assistants``,
a quoted concept (``"Proof" needs…``), a glossary/definition line
(``**Proof:** a machine-checked…``, ``Proof: …``) or a table header/label
(``| Proof to retain |``).

When a config has no ``brand_match`` block, brand/alias terms that are common
English words (currently just "proof") are treated as product-form-only so
older callers that only pass (brand, aliases) stay strict.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
# Hostname-shaped: at least two labels (foo.bar). Used to decide which
# brand/alias terms are eligible for the URL-host pass.
DOMAIN_TERM_RE = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$",
    re.IGNORECASE,
)

# Brand/alias terms that are dictionary words. Used only when the config does
# not say which terms are product-form-only.
DEFAULT_PRODUCT_FORM_ONLY = frozenset({"proof"})


def strip_urls(text: str) -> str:
    return URL_RE.sub(" ", text or "")


def is_domain_term(term: str) -> bool:
    """True if term looks like a hostname (contains a dot), not a bare word."""
    return bool(DOMAIN_TERM_RE.fullmatch((term or "").strip()))


def is_path_term(term: str) -> bool:
    """True for owner/repo style aliases (probelabs/proof), not URLs or hosts."""
    t = (term or "").strip().strip("/")
    return "/" in t and "://" not in t and not t.lower().startswith("github.com/")


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


def _http_url_host_paths(text: str) -> list[str]:
    """`host/path` (lowercased, www. stripped, no query) for each http(s) URL."""
    out: list[str] = []
    for match in URL_RE.finditer(text or ""):
        raw = match.group(0).rstrip(".,;:!?")
        try:
            parsed = urlparse(raw)
            host = strip_www(parsed.hostname or "")
        except ValueError:
            continue
        if host:
            out.append(host + (parsed.path or "").lower())
    return out


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


# --------------------------------------------------------------------------
# Config: which brand terms are product-form-only
# --------------------------------------------------------------------------


def product_form_only_from_config(cfg: Any) -> list[str] | None:
    """`brand_match.product_form_only` from a config dict or Config object.

    Returns None when the config does not set it (callers then fall back to
    DEFAULT_PRODUCT_FORM_ONLY).
    """
    if cfg is None:
        return None
    if isinstance(cfg, dict):
        block = cfg.get("brand_match")
        if isinstance(block, dict) and block.get("product_form_only") is not None:
            return [str(t) for t in block.get("product_form_only") or [] if str(t).strip()]
        return None
    val = getattr(cfg, "brand_product_form_only", None)
    return None if val is None else [str(t) for t in val if str(t).strip()]


def resolve_product_form_only(
    brand: str,
    aliases: Iterable[str],
    product_form_only: Iterable[str] | None = None,
) -> list[str]:
    """Product-form-only terms, in configured capitalization."""
    if product_form_only is not None:
        return unique_terms(product_form_only)
    return [t for t in brand_terms(brand, aliases) if t.lower() in DEFAULT_PRODUCT_FORM_ONLY]


def is_bare_proof_term(term: str) -> bool:
    """True for the English word proof, not ReqProof or reqproof.com."""
    return (term or "").strip().lower() == "proof"


# --------------------------------------------------------------------------
# Product-form test for a dictionary-word brand token (Proof)
# --------------------------------------------------------------------------

_MD = r"(?:\*\*|__|\*|_|`)?"
# Noun-phrase continuations that make it the English word even after a
# lead-in: "Proof of…", "Proof that…", "Proof point", "Proof assistants",
# "Proof obligations", "Proof-of-concept". (Labels like "Proof to retain"
# are caught by the table/label rules; without a lead-in or adjacent alias
# nothing counts anyway.)
_REJECT_AFTER = re.compile(
    r"^" + _MD + r"\s*(?:"
    r"-"  # Proof-of-concept, Proof-carrying
    r"|of\b|that\b|the\b|or\b|and\s+(?:evidence|tests?|testing)\b"
    r"|points?\b|assistants?\b|obligations?\b|checkers?\b|checking\b|search\b"
    r"|sketch(?:es)?\b|steps?\b|terms?\b|scripts?\b|engineering\b|theory\b"
    r"|by\s+(?:induction|contradiction|construction|example)\b"
    r"|method\b|reading\b|objects?\b|certificates?\b|stability\b|placeholders?\b"
    r")",
    re.IGNORECASE,
)
# "mathematical Proof", "social Proof", "burden of Proof", "Tamper-Proof".
_REJECT_BEFORE = re.compile(
    r"(?:mathematical|formal|social|machine[- ]checked|burden\s+of|tamper|fool|bullet|"
    r"water|future|cryptographic|full|written|signed|retained|stronger|\bof|\bno|\bnot|"
    r"\bthe|\ba|\ban|\byour|\bour|\bthis|\bthat)\s*" + _MD + r"$",
    re.IGNORECASE,
)
# Line-start label: "Proof:", "**Proof:**", "- **Proof**:", "### Proof", "Proof —".
_LINE_LABEL_PREFIX = re.compile(r"^\s*(?:>\s*)?(?:[-*+\u2022]\s+|\d+[.)]\s+|#{1,6}\s+)?" + _MD + r"$")
_LABEL_AFTER = re.compile(r"^" + _MD + r"\s*(?::|\u2014|\u2013|\s-\s|$)")
_QUOTES = "\"'\u201c\u201d\u2018\u2019`"
# Product lead-ins right before the token (optionally after a short name list).
_LEADIN = re.compile(
    r"(?:"
    r"\b(?:tools?|products?|platforms?|options?|services?|vendors?|solutions?|"
    r"alternatives?|offerings?|apps?|frameworks?|systems?)\s+(?:like|such\s+as|including|e\.g\.,?)"
    r"|\bsuch\s+as|\blike|\bincluding|\be\.g\.,?"
    r"|\b(?:try|trying|use|using|adopt|adopting|install|installing|evaluate|evaluating|"
    r"recommend|consider|called|named|with\s+tools?\s+like)"
    r")\s+"
    # optional list of other names before Proof: "Jama, Polarion, or "
    r"(?:" + _MD + r"\[?[A-Z][\w.+/&-]*(?:\s+[A-Z][\w.+/&-]*){0,2}\]?" + _MD + r"\s*(?:\([^()]{0,40}\))?\s*,\s+)*"
    r"(?:(?:and|or)\s+)?" + _MD + r"\[?$",
    re.IGNORECASE,
)
_CTX_WINDOW = 80


def _context_alias_regex(context_terms: Iterable[str]) -> str:
    alts = []
    for t in unique_terms(context_terms):
        alts.append(re.escape(t))
    return "|".join(alts) if alts else r"(?!x)x"


def _url_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in URL_RE.finditer(text or "")]


def _alias_adjacent(after: str, ctx: str) -> bool:
    """`Proof (reqproof.com)`, `[Proof](https://reqproof.com)`, `Proof — reqproof.com`."""
    pat = re.compile(
        r"^" + _MD + r"\]?\s*(?:"
        r"\(\s*(?:by\s+|from\s+|at\s+|aka\s+|a\.k\.a\.\s+)?(?:https?://)?(?:www\.)?(?:" + ctx + r")"
        r"|\]\(\s*https?://(?:www\.)?(?:" + ctx + r")"
        r"|(?:,|:|\u2014|\u2013|-|from|at|by|via|aka)\s*" + _MD + r"\s*\(?\[?(?:https?://)?(?:www\.)?(?:" + ctx + r")"
        r")(?![\w])",
        re.IGNORECASE,
    )
    return bool(pat.match(after))


def product_form_mentioned(
    text: str,
    term: str = "Proof",
    context_terms: Iterable[str] = ("ReqProof", "reqproof", "reqproof.com", "probelabs/proof"),
) -> bool:
    """True if `term` (exact capitalization) appears in a clear product form.

    `context_terms` are unambiguous aliases (ReqProof, reqproof.com) that make
    an adjacent occurrence a product mention.
    """
    text = text or ""
    term = (term or "").strip()
    if not term:
        return False
    ctx = _context_alias_regex(context_terms)
    token = re.compile(rf"(?<![\w/.\-]){re.escape(term)}(?![\w])")
    urls = _url_spans(text)
    for match in token.finditer(text):
        s, e = match.start(), match.end()
        if any(us <= s < ue for us, ue in urls):
            continue
        line_start = text.rfind("\n", 0, s) + 1
        line_end = text.find("\n", e)
        line_end = len(text) if line_end < 0 else line_end
        prefix = text[line_start:s]
        before = text[max(line_start, s - _CTX_WINDOW) : s]
        after = text[e : min(line_end, e + _CTX_WINDOW)]

        if _REJECT_AFTER.match(after):
            continue
        # Strongest signal: an unambiguous alias right next to the token.
        if _alias_adjacent(after, ctx):
            return True
        # Markdown link text that starts with the token and points at an
        # alias host: [Proof’s Go workflow](https://reqproof.com/topics/…).
        if re.search(r"\[" + _MD + r"$", before) and re.match(
            r"^[^\]\n]{0,60}\]\(\s*https?://(?:www\.)?(?:" + ctx + r")(?![\w])", after, re.IGNORECASE
        ):
            return True
        if _REJECT_BEFORE.search(before):
            continue
        # Quoted concept: "Proof" needs a defined scope.
        if before[-1:] and before[-1] in _QUOTES and after[:1] and after[0] in _QUOTES:
            continue

        # Glossary / definition / heading label at the start of a line.
        if _LINE_LABEL_PREFIX.match(prefix) and _LABEL_AFTER.match(after):
            continue
        # Table header / label cell.
        if prefix.lstrip().startswith("|"):
            continue
        if _LEADIN.search(before):
            return True
    return False


def proof_product_mentioned(text: str) -> bool:
    """Capitalized Proof as the product (reqproof.com), not the English noun.

    Counts "Proof (reqproof.com)", "tools like Proof", "Try Proof for MC/DC".
    Does not count "proof", "proof of", "social proof", "burden of proof",
    "proof point", "mathematical proof", "Proof that…", "Proof assistants",
    "Proof-of-concept", glossary lines ("**Proof:** a machine-checked…"),
    or table labels ("| Proof to retain |").
    """
    return product_form_mentioned(text, "Proof")


def vendor_name_is_product(name: str, term: str = "Proof", context_terms: Iterable[str] = ()) -> bool:
    """For a vendor-name field (judge output), not prose.

    "Proof", "Proof CLI", "Proof (reqproof.com)" are the product; "Proof
    assistants", "proof", "Proof-of-concept" are not.
    """
    raw = (name or "").strip().strip("*_`").strip()
    if not raw or not term:
        return False
    if raw == term:
        return True
    if re.fullmatch(rf"{re.escape(term)}\s+(?:CLI|app|platform|tool|by\s+Probe\s*Labs)", raw, re.IGNORECASE) and raw.startswith(term):
        return True
    ctx = list(context_terms) or ["ReqProof", "reqproof", "reqproof.com", "probelabs/proof"]
    return product_form_mentioned(raw, term, ctx)


def _non_strict_brand_terms(brand: str, aliases: Iterable[str], strict: Iterable[str]) -> list[str]:
    """Brand terms safe for case-insensitive whole-word match."""
    strict_keys = {t.lower() for t in strict}
    return [t for t in brand_terms(brand, aliases) if t.lower() not in strict_keys]


# Kept for vendors.py / older callers.
def _generic_brand_terms(brand: str, aliases: Iterable[str]) -> list[str]:
    return _non_strict_brand_terms(brand, aliases, resolve_product_form_only(brand, aliases))


def _alias_for_host(host: str, brand: str, domain_aliases: list[str], strict_keys: set[str] | None = None) -> str | None:
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
    strict_keys = strict_keys if strict_keys is not None else set(DEFAULT_PRODUCT_FORM_ONLY)
    # Apex-label: first DNS label is the brand and host is {brand}.{tld}.
    # Skip dictionary-word brands so proof.com / proof.io are not ReqProof.
    if (
        brand_key
        and brand_key not in strict_keys
        and len(labels) == 2
        and labels[0] == brand_key
        and domain_aliases
    ):
        for alias in domain_aliases:
            if strip_www(alias).split(".")[0] == brand_key:
                return alias
        return domain_aliases[0]
    return None


def find_brand_url_host_mentions(
    text: str,
    brand: str,
    aliases: Iterable[str],
    *,
    product_form_only: Iterable[str] | None = None,
) -> list[str]:
    """Domain-style brand/alias terms that appear as http(s) URL hosts,
    plus owner/repo aliases (probelabs/proof) that appear as a URL path."""
    terms = brand_terms(brand, aliases)
    strict_keys = {t.lower() for t in resolve_product_form_only(brand, aliases, product_form_only)}
    domain_aliases = [t for t in terms if is_domain_term(t)]
    path_aliases = [t for t in terms if is_path_term(t)]
    found: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            found.append(term)

    if domain_aliases:
        for host in http_url_hosts(text):
            hit = _alias_for_host(host, brand, domain_aliases, strict_keys)
            if hit is not None:
                add(hit)
    if path_aliases:
        for hp in _http_url_host_paths(text):
            path = hp.split("/", 1)[1] if "/" in hp else ""
            for t in path_aliases:
                tk = t.strip().strip("/").lower()
                if re.match(rf"^{re.escape(tk)}(?:/|$|\.git\b)", path):
                    add(t)
    return found


def extract_brand_mentions(
    text: str,
    brand: str,
    aliases: Iterable[str],
    *,
    product_form_only: Iterable[str] | None = None,
) -> list[str]:
    """Brand/alias hits in prose, plus domain-style aliases on URL hosts.

    Product-form-only terms (config `brand_match.product_form_only`, default:
    dictionary-word terms such as Proof) use product_form_mentioned instead of
    a case-insensitive whole-word match.
    """
    strict = resolve_product_form_only(brand, aliases, product_form_only)
    safe = _non_strict_brand_terms(brand, aliases, strict)
    found = find_terms(text, safe, ignore_urls=True)
    seen = {t.lower() for t in found}
    context = [t for t in safe] or ["ReqProof", "reqproof.com"]
    for term in strict:
        if term.lower() in seen:
            continue
        if product_form_mentioned(text, term, context):
            seen.add(term.lower())
            found.append(term)
    for term in find_brand_url_host_mentions(text, brand, aliases, product_form_only=strict):
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
    *,
    product_form_only: Iterable[str] | None = None,
) -> list[str]:
    """Vendor names (brand, aliases, competitors) appearing in search-query strings."""
    blob = "\n".join(q for q in queries if q)
    found = extract_brand_mentions(blob, brand, aliases, product_form_only=product_form_only)
    seen = {t.lower() for t in found}
    for term in find_terms(blob, competitors, ignore_urls=True):
        key = term.lower()
        if key not in seen:
            seen.add(key)
            found.append(term)
    return found


# --------------------------------------------------------------------------
# Rescore stored evidence with the same matcher
# --------------------------------------------------------------------------

ARMS = ("knowledge", "search")


def rescore_brand_cells(
    doc: dict[str, Any],
    brand: str | None = None,
    aliases: Iterable[str] | None = None,
    *,
    product_form_only: Iterable[str] | None = None,
    engines: Iterable[str] | None = None,
) -> dict[str, dict[str, list[int]]]:
    """Recompute brand_mentions / brand_mentioned / recommended in place.

    Uses the evidence's own raw_response_text, so no queries are re-run.
    Brand entries in vendors_in_search_queries are recomputed from
    search_queries with the same matcher; competitor entries are kept.
    Returns {engine: {arm: [cells, old_hits, new_hits]}}.
    """
    ws = doc.get("workspace") or {}
    brand = brand if brand is not None else str(ws.get("brand") or "")
    alias_list = list(aliases) if aliases is not None else [str(a) for a in ws.get("aliases") or []]
    if product_form_only is None:
        product_form_only = product_form_only_from_config(ws)
    strict = resolve_product_form_only(brand, alias_list, product_form_only)
    bkeys = {t.lower() for t in brand_terms(brand, alias_list)} | {t.lower() for t in strict}
    only = {e for e in engines} if engines is not None else None
    stats: dict[str, dict[str, list[int]]] = {}
    for prompt in doc.get("prompts") or []:
        for eng, arms in (prompt.get("engines") or {}).items():
            if only is not None and eng not in only:
                continue
            if not isinstance(arms, dict):
                continue
            for arm in ARMS:
                cell = arms.get(arm)
                if not isinstance(cell, dict):
                    continue
                text = cell.get("raw_response_text")
                if not isinstance(text, str) or not text.strip():
                    # Nothing to re-derive from (errors, synthetic fixtures).
                    continue
                old = bool(cell.get("brand_mentioned"))
                hits = extract_brand_mentions(text, brand, alias_list, product_form_only=strict)
                cell["brand_mentions"] = hits
                cell["brand_mentioned"] = bool(hits)
                if "recommended" in cell:
                    cell["recommended"] = bool(hits)
                if isinstance(cell.get("vendors_in_search_queries"), list):
                    kept = [v for v in cell["vendors_in_search_queries"] if str(v).lower() not in bkeys]
                    qhits = extract_brand_mentions(
                        "\n".join(str(q) for q in cell.get("search_queries") or [] if q),
                        brand,
                        alias_list,
                        product_form_only=strict,
                    )
                    cell["vendors_in_search_queries"] = qhits + [v for v in kept if str(v).lower() not in {q.lower() for q in qhits}]
                st = stats.setdefault(eng, {}).setdefault(arm, [0, 0, 0])
                st[0] += 1
                st[1] += int(old)
                st[2] += int(bool(hits))
    return stats
