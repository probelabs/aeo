"""Normalize and merge product/vendor names from LLM extract + regex hints.

Config `competitors` (and brand/aliases) seed an alias map. They are not a
ceiling: names discovered in a post-run LLM pass join the same map.

Brand mention scoring stays in `aeo.mention` (deterministic regex). This
module is only for competitor / vendor fan-out after a run.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable

from aeo.mention import unique_terms, word_boundary_pattern

VENDOR_ROLES = ("recommend", "mention", "warn", "reject", "aside")
ARMS = ("knowledge", "search")

_SCHEME_RE = re.compile(r"^https?://", re.IGNORECASE)
_WWW_RE = re.compile(r"^www\.", re.IGNORECASE)
_DOMAIN_SUFFIX_RE = re.compile(
    r"\.(com|io|ai|org|net|dev|app|co)$",
    re.IGNORECASE,
)
_LEGAL_TRAILING = frozenset(
    {
        "inc",
        "incorporated",
        "ltd",
        "llc",
        "limited",
        "labs",
        "lab",
        "api",
        "corp",
        "corporation",
        "gmbh",
        "plc",
        "co",
    }
)
_PUNCT_RE = re.compile(r"[^\w\s.+-]+", re.UNICODE)
_SEP_RE = re.compile(r"[\s._+-]+")
# "Kong Gateway" / "Tyk API" collapse onto the product token when already known.
_GENERIC_TAILS = frozenset(
    {"gateway", "api", "platform", "service", "cloud", "hq", "app", "io"}
)


def pretty_strip(raw: str) -> str:
    """Drop URL chrome, domain suffix, and trailing Inc/Ltd/Labs/API. Keep case."""
    s = (raw or "").strip()
    if not s:
        return ""
    s = _SCHEME_RE.sub("", s)
    s = s.split("/")[0].split("?")[0]
    s = _WWW_RE.sub("", s)
    s = _DOMAIN_SUFFIX_RE.sub("", s)
    tokens = s.split()
    while tokens and tokens[-1].rstrip(".,;:").lower() in _LEGAL_TRAILING:
        tokens.pop()
    return " ".join(tokens).strip(" .,;:-")


def normalize_vendor_key(raw: str) -> str:
    """Stable merge key: lowercase, no legal/domain suffix, no whitespace."""
    stripped = pretty_strip(raw)
    s = stripped.lower()
    s = s.replace("&", " and ")
    s = _PUNCT_RE.sub(" ", s)
    s = _SEP_RE.sub("", s)
    return s


def display_score(name: str) -> int:
    """Prefer stable product spelling over domains and legal suffixes."""
    s = (name or "").strip()
    if not s:
        return -100
    score = 0
    if any(c.isupper() for c in s[1:]):
        score += 5
    if s[:1].isupper():
        score += 2
    lower = s.lower()
    if "." in s or lower.endswith((".com", ".io", ".ai", ".org")):
        score -= 4
    if any(tok.rstrip(".,").lower() in _LEGAL_TRAILING for tok in s.split()):
        score -= 2
    if " " not in s:
        score += 1
    score += min(len(s), 24) // 8
    return score


def preferred_display(raw: str, *alts: str) -> str:
    """Best-looking form among raw / normalized / already-stripped candidates."""
    candidates: list[str] = []
    for item in (raw, *alts):
        cleaned = pretty_strip(item) if item else ""
        if cleaned:
            candidates.append(cleaned)
        elif (item or "").strip():
            candidates.append(item.strip())
    if not candidates:
        return (raw or "").strip()
    best = max(candidates, key=lambda c: (display_score(c), len(c)))
    if best.islower() or best.isupper():
        titled = best.title()
        if display_score(titled) >= display_score(best):
            return titled
    return best


def brand_keys(brand: str, aliases: Iterable[str]) -> set[str]:
    keys: set[str] = set()
    for term in unique_terms([brand, *aliases]):
        raw = term.strip().lower()
        if raw:
            keys.add(raw)
        key = normalize_vendor_key(term)
        if key:
            keys.add(key)
    return keys


def is_brand_vendor(name: str, brand: str, aliases: Iterable[str]) -> bool:
    """True if name is the brand, an alias, or a longer name that contains them."""
    raw = (name or "").strip()
    if not raw:
        return False
    terms = unique_terms([brand, *aliases])
    raw_l = raw.lower()
    if raw_l in {t.lower() for t in terms}:
        return True
    key = normalize_vendor_key(raw)
    if key and key in {normalize_vendor_key(t) for t in terms if normalize_vendor_key(t)}:
        return True
    for term in terms:
        token = term.strip()
        if len(token) < 3:
            continue
        if word_boundary_pattern(token).search(raw):
            return True
    return False


class AliasMap:
    """key -> display. Seeded from config, then grown from a run."""

    def __init__(self) -> None:
        self._display: dict[str, str] = {}
        self.brand: str = ""
        self.aliases: list[str] = []

    def seed(self, brand: str, aliases: Iterable[str], competitors: Iterable[str]) -> None:
        self.brand = brand or ""
        self.aliases = [str(a) for a in aliases if a]
        for term in unique_terms([brand, *self.aliases]):
            self.observe(term)
        for term in unique_terms(competitors):
            self.observe(term)

    def key_for(self, raw: str, normalized: str | None = None) -> str:
        return self._canonical_key(normalize_vendor_key(normalized or raw))

    def _canonical_key(self, key: str) -> str:
        if not key:
            return ""
        if key in self._display:
            return key
        for known in sorted(self._display, key=len, reverse=True):
            if not known:
                continue
            if key.startswith(known) and key[len(known) :] in _GENERIC_TAILS:
                return known
            if known.startswith(key) and known[len(key) :] in _GENERIC_TAILS:
                return known
        return key

    def observe(self, raw: str, normalized: str | None = None) -> str:
        """Record a sighting and return the current display name (empty if unusable)."""
        key = self._canonical_key(normalize_vendor_key(normalized or raw))
        if not key:
            return ""
        candidate = preferred_display(raw, normalized or "")
        if not candidate:
            return self._display.get(key, "")
        prev = self._display.get(key)
        # Keep the canonical short form (Kong) over a generic-tail variant (Kong Gateway).
        if prev is None:
            self._display[key] = candidate
        elif normalize_vendor_key(prev) == key and normalize_vendor_key(candidate) != key:
            pass
        elif display_score(candidate) > display_score(prev):
            self._display[key] = candidate
        return self._display[key]

    def display_for(self, raw: str, normalized: str | None = None) -> str:
        key = self._canonical_key(normalize_vendor_key(normalized or raw))
        if not key:
            return (normalized or raw or "").strip()
        if key in self._display:
            self.observe(raw, normalized)
            return self._display[key]
        return self.observe(raw, normalized) or preferred_display(raw, normalized or "")

    def grow_from_cells(self, cells: Iterable[dict[str, Any]]) -> None:
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            for item in list(cell.get("vendors") or []) + list(cell.get("query_vendors") or []):
                if isinstance(item, str):
                    self.observe(item)
                elif isinstance(item, dict):
                    self.observe(str(item.get("raw") or ""), str(item.get("normalized") or "") or None)


def normalize_vendor_item(item: Any) -> dict[str, str] | None:
    if isinstance(item, str):
        raw = item.strip()
        if not raw:
            return None
        return {"raw": raw[:120], "normalized": preferred_display(raw)[:80], "role": "mention"}
    if not isinstance(item, dict):
        return None
    raw = str(item.get("raw") or item.get("normalized") or "").strip()
    if not raw:
        return None
    norm = str(item.get("normalized") or "").strip() or preferred_display(raw)
    role = str(item.get("role") or "mention").lower().strip()
    if role not in VENDOR_ROLES:
        role = "mention"
    return {"raw": raw[:120], "normalized": norm[:80], "role": role}


def normalize_vendor_cell(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(doc, dict):
        return None
    vendors = []
    seen: set[str] = set()
    for item in doc.get("vendors") or []:
        v = normalize_vendor_item(item)
        if not v:
            continue
        key = normalize_vendor_key(v["normalized"] or v["raw"])
        if not key or key in seen:
            continue
        seen.add(key)
        vendors.append(v)
    query_vendors = []
    qseen: set[str] = set()
    for item in doc.get("query_vendors") or []:
        v = normalize_vendor_item(item)
        if not v:
            continue
        key = normalize_vendor_key(v["normalized"] or v["raw"])
        if not key or key in qseen:
            continue
        qseen.add(key)
        query_vendors.append(v)
    try:
        conf = float(doc.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "vendors": vendors[:24],
        "query_vendors": query_vendors[:24],
        "confidence": max(0.0, min(1.0, conf)),
        "judge": str(doc.get("judge") or "claude"),
    }


def load_vendor_store(raw: Any) -> dict[str, dict[str, Any]]:
    """Accept a flat `prompt|engine|arm` map or `{cells: {...}}`."""
    if not isinstance(raw, dict):
        return {}
    blob = raw.get("cells") if isinstance(raw.get("cells"), dict) else raw
    out: dict[str, dict[str, Any]] = {}
    for key, val in blob.items():
        if not isinstance(key, str) or "|" not in key:
            continue
        if isinstance(val, dict):
            out[key] = val
    return out


def workspace_from_docs(docs: dict[str, dict[str, Any]]) -> tuple[str, list[str], list[str]]:
    brand = ""
    aliases: list[str] = []
    competitors: list[str] = []
    for doc in docs.values():
        if not isinstance(doc, dict):
            continue
        ws = doc.get("workspace") or {}
        if ws.get("brand") and not brand:
            brand = str(ws["brand"])
        for a in ws.get("aliases") or []:
            if a and str(a) not in aliases:
                aliases.append(str(a))
        for c in ws.get("competitors") or []:
            if c and str(c) not in competitors:
                competitors.append(str(c))
    return brand, aliases, competitors


def seed_alias_map(
    brand: str,
    aliases: Iterable[str],
    competitors: Iterable[str],
    cells: Iterable[dict[str, Any]] | None = None,
) -> AliasMap:
    amap = AliasMap()
    amap.seed(brand, aliases, competitors)
    if cells:
        amap.grow_from_cells(cells)
    return amap


def merge_named_vendors(
    llm_items: Iterable[Any],
    regex_names: Iterable[str],
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[str]:
    """Deduped display names. LLM first, then regex. Brand + aliases excluded."""
    out: list[str] = []
    seen: set[str] = set()
    alias_list = list(aliases)

    def _add(raw: str, normalized: str | None = None) -> None:
        if not (raw or normalized):
            return
        if is_brand_vendor(raw, brand, alias_list) or (
            normalized and is_brand_vendor(normalized, brand, alias_list)
        ):
            return
        key = alias_map.key_for(raw, normalized)
        if not key or key in seen:
            return
        if key in brand_keys(brand, alias_list):
            return
        seen.add(key)
        out.append(alias_map.display_for(raw, normalized))

    for item in llm_items or []:
        if isinstance(item, str):
            _add(item)
        elif isinstance(item, dict):
            _add(str(item.get("raw") or ""), str(item.get("normalized") or "") or None)
    for name in regex_names or []:
        _add(str(name))
    return [n for n in out if n]


def completed_cells(doc: dict[str, Any], engine: str) -> list[dict[str, Any]]:
    """Every finished knowledge/search arm: hits and misses. Skip errors/empty."""
    out: list[dict[str, Any]] = []
    for pr in doc.get("prompts") or []:
        arms = (pr.get("engines") or {}).get(engine) or {}
        for arm_name in ARMS:
            arm = arms.get(arm_name)
            if not isinstance(arm, dict) or arm.get("error"):
                continue
            text = (arm.get("raw_response_text") or "").strip()
            if not text:
                continue
            out.append(
                {
                    "key": f"{pr.get('prompt_id')}|{engine}|{arm_name}",
                    "prompt_id": pr.get("prompt_id"),
                    "prompt_text": pr.get("prompt_text") or "",
                    "engine": engine,
                    "arm": arm_name,
                    "answer": arm.get("raw_response_text") or "",
                    "search_queries": list(arm.get("search_queries") or []),
                    "competitor_mentions": list(arm.get("competitor_mentions") or []),
                    "vendors_in_search_queries": list(arm.get("vendors_in_search_queries") or []),
                    "brand_mentioned": bool(arm.get("brand_mentioned")),
                }
            )
    return out


def named_vendors_for_arm(
    arm: dict[str, Any] | None,
    vendor_cell: dict[str, Any] | None,
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[str]:
    if not isinstance(arm, dict):
        return []
    cell = vendor_cell if isinstance(vendor_cell, dict) else {}
    return merge_named_vendors(
        cell.get("vendors") or [],
        arm.get("competitor_mentions") or [],
        alias_map,
        brand,
        aliases,
    )


def query_vendors_for_arm(
    arm: dict[str, Any] | None,
    vendor_cell: dict[str, Any] | None,
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[str]:
    if not isinstance(arm, dict):
        return []
    cell = vendor_cell if isinstance(vendor_cell, dict) else {}
    return merge_named_vendors(
        cell.get("query_vendors") or [],
        arm.get("vendors_in_search_queries") or [],
        alias_map,
        brand,
        aliases,
    )


def _brand_in_search_box(
    arm: dict[str, Any],
    vendor_cell: dict[str, Any] | None,
    brand: str,
    aliases: Iterable[str],
) -> bool:
    alias_list = list(aliases)
    for v in arm.get("vendors_in_search_queries") or []:
        if is_brand_vendor(str(v), brand, alias_list):
            return True
    cell = vendor_cell if isinstance(vendor_cell, dict) else {}
    for item in cell.get("query_vendors") or []:
        raw = item if isinstance(item, str) else (item or {}).get("raw") or (item or {}).get("normalized")
        if raw and is_brand_vendor(str(raw), brand, alias_list):
            return True
        if isinstance(item, dict) and item.get("normalized"):
            if is_brand_vendor(str(item["normalized"]), brand, alias_list):
                return True
    hay = " ".join(str(q) for q in (arm.get("search_queries") or [])).lower()
    for term in unique_terms([brand, *alias_list]):
        t = term.strip().lower()
        if len(t) >= 3 and t in hay:
            return True
    return False


def who_got_named_counts(
    rows: list[dict[str, Any]],
    vendor_store: dict[str, Any],
    *,
    brand: str,
    aliases: Iterable[str],
    alias_map: AliasMap,
) -> Counter[str]:
    """Per-cell named-vendor counts. Brand counted only via `brand_mentioned`."""
    counts: Counter[str] = Counter()
    alias_list = list(aliases)
    store = load_vendor_store(vendor_store)
    for row in rows:
        pid = row.get("prompt_id")
        for engine, arms in (row.get("engines") or {}).items():
            if not isinstance(arms, dict):
                continue
            for arm_name in ARMS:
                arm = arms.get(arm_name)
                if not isinstance(arm, dict) or arm.get("error"):
                    continue
                if arm.get("brand_mentioned"):
                    counts[brand] += 1
                key = f"{pid}|{engine}|{arm_name}"
                names = named_vendors_for_arm(arm, store.get(key), alias_map, brand, alias_list)
                for name in names:
                    counts[name] += 1
    return counts


def search_box_vendor_counts(
    rows: list[dict[str, Any]],
    vendor_store: dict[str, Any],
    *,
    brand: str,
    aliases: Iterable[str],
    alias_map: AliasMap,
) -> Counter[str]:
    """Names in search tool queries: LLM query_vendors ∪ regex vendors_in_search_queries."""
    counts: Counter[str] = Counter()
    alias_list = list(aliases)
    store = load_vendor_store(vendor_store)
    for row in rows:
        pid = row.get("prompt_id")
        for engine, arms in (row.get("engines") or {}).items():
            if not isinstance(arms, dict):
                continue
            arm = arms.get("search")
            if not isinstance(arm, dict) or arm.get("error"):
                continue
            key = f"{pid}|{engine}|search"
            cell = store.get(key)
            if _brand_in_search_box(arm, cell, brand, alias_list):
                counts[brand] += 1
            for name in query_vendors_for_arm(arm, cell, alias_map, brand, alias_list):
                counts[name] += 1
    return counts
