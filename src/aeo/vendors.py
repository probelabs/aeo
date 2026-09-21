"""Normalize and merge product/vendor names from LLM extract + regex hints.

Config `competitors` are a seed / known set (expected category map). They are
not a ceiling: names discovered in a post-run LLM pass still count. A hit
whose normalized key is not in that seed set is a **surprise** — flagged,
not quietly merged into the known pile.

Synonym groups collapse duplicate seeds: Amazon/AWS API Gateway → **Amazon API
Gateway**, Azure APIM / Azure API Management → **Azure API Management**, Apache
APISIX / APISIX → **Apache APISIX**. Config may also set `competitor_aliases`
(`canonical → [spellings]`). Duplicate seeds still collapse without that map.

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

# House-name prefixes that mean the same vendor when the rest of the product matches.
# Applied on the first token (and as a leading key prefix when the house is unambiguous).
HOUSE_SYNONYMS = (
    frozenset({"aws", "amazon", "amazonwebservices"}),
    frozenset({"gcp", "googlecloud"}),
    frozenset({"apache"}),
)

# Product-phrase abbreviations seen in seeds / extract (api management ↔ apim).
PRODUCT_ABBREVS = (("api management", "apim"),)

# Preferred display + extra phrases. Duplicate seeds still collapse via synonym_keys
# even when this table is not consulted for a name.
# Canonical picks (documented): Amazon API Gateway (AWS product title), Azure API
# Management (not "Azure APIM"), Apache APISIX (not bare APISIX).
VENDOR_SYNONYM_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Amazon API Gateway",
        ("aws api gateway", "amazon api gateway", "aws api gw", "amazon api gw"),
    ),
    (
        "Azure API Management",
        ("azure api management", "azure apim"),
    ),
    (
        "Apache APISIX",
        ("apache apisix", "apisix"),
    ),
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


def _vendor_tokens(raw: str) -> list[str]:
    stripped = pretty_strip(raw).lower()
    if not stripped:
        return []
    stripped = stripped.replace("&", " and ")
    stripped = _PUNCT_RE.sub(" ", stripped)
    return [t for t in _SEP_RE.split(stripped) if t]


def _house_key_variants(key: str) -> set[str]:
    """Swap a leading house prefix (aws/amazon/…) on an already-flattened key."""
    out = {key}
    if not key:
        return out
    for group in HOUSE_SYNONYMS:
        for house in group:
            if len(house) < 3 or not key.startswith(house) or len(key) <= len(house):
                continue
            rest = key[len(house) :]
            if len(rest) < 5:
                continue
            out.add(rest)
            for syn in group:
                out.add(syn + rest)
    return out


def synonym_keys(raw: str, normalized: str | None = None) -> set[str]:
    """All merge keys that should count as the same vendor as `raw`."""
    keys: set[str] = set()
    for item in (raw, normalized):
        if not (item or "").strip():
            continue
        keys.add(normalize_vendor_key(item))
        tokens = _vendor_tokens(item)
        if not tokens:
            continue
        for group in HOUSE_SYNONYMS:
            if tokens[0] not in group:
                continue
            for syn in group:
                keys.add(normalize_vendor_key(" ".join([syn, *tokens[1:]])))
            if len(tokens) >= 2:
                rest = normalize_vendor_key(" ".join(tokens[1:]))
                if len(rest) >= 5:
                    keys.add(rest)
        phrase = " ".join(tokens)
        for long, short in PRODUCT_ABBREVS:
            if long in phrase:
                keys.add(normalize_vendor_key(phrase.replace(long, short)))
            if re.search(rf"\b{re.escape(short)}\b", phrase):
                keys.add(normalize_vendor_key(phrase.replace(short, long)))
    extra: set[str] = set()
    for key in keys:
        extra |= _house_key_variants(key)
    keys |= extra
    return {k for k in keys if k}


def _builtin_canonical_display(keys: set[str]) -> str | None:
    for display, aliases in VENDOR_SYNONYM_GROUPS:
        group_keys = set()
        for phrase in (display, *aliases):
            group_keys |= synonym_keys(phrase)
        if keys & group_keys:
            return display
    return None


def expand_competitor_mention_terms(
    competitors: Iterable[str],
    competitor_aliases: dict[str, list[str]] | None = None,
) -> list[str]:
    """Competitors plus synonym/alias phrases so regex still hits both spellings."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(term: str) -> None:
        raw = (term or "").strip()
        if not raw:
            return
        fold = raw.lower()
        if fold in seen:
            return
        seen.add(fold)
        terms.append(raw)

    for name in competitors or []:
        add(str(name))
        nkeys = synonym_keys(str(name))
        for display, aliases in VENDOR_SYNONYM_GROUPS:
            group_keys: set[str] = set()
            for phrase in (display, *aliases):
                group_keys |= synonym_keys(phrase)
            if nkeys & group_keys:
                add(display)
                for a in aliases:
                    add(a)
    for canon, aliases in (competitor_aliases or {}).items():
        add(str(canon))
        for a in aliases or []:
            add(str(a))
    return terms


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
    """key -> display. Seeded from config, then grown from a run.

    Synonym groups (aws/amazon, azure apim/management, config competitor_aliases)
    share one canonical key so duplicate seeds do not rank as two vendors.
    """

    def __init__(self) -> None:
        self._display: dict[str, str] = {}
        self._alias_to: dict[str, str] = {}
        self._seed_keys: set[str] = set()
        self.brand: str = ""
        self.aliases: list[str] = []

    def seed(
        self,
        brand: str,
        aliases: Iterable[str],
        competitors: Iterable[str],
        competitor_aliases: dict[str, list[str]] | None = None,
    ) -> None:
        self.brand = brand or ""
        self.aliases = [str(a) for a in aliases if a]
        self._seed_keys = set()
        groups: list[tuple[str, list[str]]] = [
            (display, list(extra)) for display, extra in VENDOR_SYNONYM_GROUPS
        ]
        for canon, extra in (competitor_aliases or {}).items():
            groups.append((str(canon), [str(a) for a in extra or []]))
        for display, extra in groups:
            self._register_group(display, extra)
        for term in unique_terms([brand, *self.aliases]):
            self.observe(term)
        for term in unique_terms(competitors):
            self.observe(term)
            key = self._seed_key_for(term)
            if key:
                self._seed_keys.add(key)
        for canon, extra in (competitor_aliases or {}).items():
            for term in (canon, *[a for a in extra or [] if a]):
                self.observe(term)
                key = self.key_for(term)
                if key:
                    self._seed_keys.add(key)

    def _register_group(self, display: str, aliases: Iterable[str]) -> None:
        phrases = [display, *[a for a in aliases if a]]
        keys: set[str] = set()
        for phrase in phrases:
            keys |= synonym_keys(phrase)
        if not keys:
            return
        canon = normalize_vendor_key(display) or next(iter(sorted(keys)))
        for key in keys:
            self._alias_to[key] = canon
        if canon not in self._display:
            self._display[canon] = display.strip() or preferred_display(display)

    def _bind_synonyms(self, raw: str, normalized: str | None, canon: str) -> None:
        for key in synonym_keys(raw, normalized) | {canon}:
            if key:
                self._alias_to[key] = canon

    def _seed_key_for(self, raw: str, normalized: str | None = None) -> str:
        """Canonical key against the seed set only (not run-grown aliases)."""
        key = self.key_for(raw, normalized)
        if not key:
            return ""
        if key in self._seed_keys:
            return key
        raw_key = normalize_vendor_key(normalized or raw)
        for seed in sorted(self._seed_keys, key=len, reverse=True):
            if raw_key.startswith(seed) and raw_key[len(seed) :] in _GENERIC_TAILS:
                return seed
            if seed.startswith(raw_key) and seed[len(raw_key) :] in _GENERIC_TAILS:
                return seed
        return key

    def is_seed(self, raw: str, normalized: str | None = None) -> bool:
        key = self.key_for(raw, normalized)
        if not key:
            return False
        return self._seed_key_for(raw, normalized) in self._seed_keys

    def key_for(self, raw: str, normalized: str | None = None) -> str:
        raw_key = normalize_vendor_key(normalized or raw)
        if not raw_key:
            return ""
        for variant in (raw_key, *sorted(synonym_keys(raw, normalized))):
            mapped = self._alias_to.get(variant)
            if mapped:
                return mapped
        return self._canonical_key(raw_key)

    def _canonical_key(self, key: str) -> str:
        if not key:
            return ""
        key = self._alias_to.get(key, key)
        if key in self._display:
            return self._alias_to.get(key, key)
        pool = set(self._display) | set(self._alias_to.values())
        for known in sorted(pool, key=len, reverse=True):
            if not known:
                continue
            if key.startswith(known) and key[len(known) :] in _GENERIC_TAILS:
                return self._alias_to.get(known, known)
            if known.startswith(key) and known[len(key) :] in _GENERIC_TAILS:
                return self._alias_to.get(known, known)
        return self._alias_to.get(key, key)

    def observe(self, raw: str, normalized: str | None = None) -> str:
        """Record a sighting and return the current display name (empty if unusable)."""
        variants = synonym_keys(raw, normalized)
        raw_key = normalize_vendor_key(normalized or raw)
        if raw_key:
            variants.add(raw_key)
        if not variants:
            return ""
        canon = ""
        for variant in variants:
            mapped = self._alias_to.get(variant)
            if mapped:
                canon = mapped
                break
            if variant in self._display:
                canon = variant
                break
        if not canon:
            builtin = _builtin_canonical_display(variants)
            if builtin:
                canon = normalize_vendor_key(builtin)
                if canon not in self._display:
                    self._display[canon] = builtin
            else:
                canon = self._canonical_key(raw_key) or raw_key
        self._bind_synonyms(raw, normalized, canon)
        candidate = preferred_display(raw, normalized or "")
        if _builtin_canonical_display(variants):
            # Built-in table wins display so AWS/Amazon always show as Amazon API Gateway.
            locked = _builtin_canonical_display(variants)
            if locked:
                self._display[canon] = locked
                return locked
        if not candidate:
            return self._display.get(canon, "")
        prev = self._display.get(canon)
        if prev is None:
            self._display[canon] = candidate
        elif normalize_vendor_key(prev) == canon and normalize_vendor_key(candidate) != canon:
            pass
        elif display_score(candidate) > display_score(prev):
            self._display[canon] = candidate
        return self._display[canon]

    def display_for(self, raw: str, normalized: str | None = None) -> str:
        key = self.key_for(raw, normalized)
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


def workspace_from_docs(
    docs: dict[str, dict[str, Any]],
) -> tuple[str, list[str], list[str], dict[str, list[str]]]:
    brand = ""
    aliases: list[str] = []
    competitors: list[str] = []
    competitor_aliases: dict[str, list[str]] = {}
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
        raw_aliases = ws.get("competitor_aliases") or {}
        if isinstance(raw_aliases, dict):
            for canon, extra in raw_aliases.items():
                slot = competitor_aliases.setdefault(str(canon), [])
                for item in extra or []:
                    if item and str(item) not in slot:
                        slot.append(str(item))
    return brand, aliases, competitors, competitor_aliases


def seed_alias_map(
    brand: str,
    aliases: Iterable[str],
    competitors: Iterable[str],
    cells: Iterable[dict[str, Any]] | None = None,
    competitor_aliases: dict[str, list[str]] | None = None,
) -> AliasMap:
    amap = AliasMap()
    amap.seed(brand, aliases, competitors, competitor_aliases=competitor_aliases)
    if cells:
        amap.grow_from_cells(cells)
    return amap


def vendor_origin(
    raw: str,
    normalized: str | None,
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> str | None:
    """`known` if in the config seed set, `surprise` otherwise. None if brand."""
    alias_list = list(aliases)
    if is_brand_vendor(raw, brand, alias_list) or (
        normalized and is_brand_vendor(normalized, brand, alias_list)
    ):
        return None
    key = alias_map.key_for(raw, normalized)
    if key and key in brand_keys(brand, alias_list):
        return None
    if alias_map.is_seed(raw, normalized):
        return "known"
    return "surprise"


def merge_classified_vendors(
    llm_items: Iterable[Any],
    regex_names: Iterable[str],
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[dict[str, str]]:
    """Deduped `{name, origin}` records. LLM first, then regex. Brand excluded."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    alias_list = list(aliases)

    def _add(raw: str, normalized: str | None = None) -> None:
        if not (raw or normalized):
            return
        origin = vendor_origin(raw, normalized, alias_map, brand, alias_list)
        if origin is None:
            return
        key = alias_map.key_for(raw, normalized)
        if not key or key in seen:
            return
        seen.add(key)
        out.append({"name": alias_map.display_for(raw, normalized), "origin": origin})

    for item in llm_items or []:
        if isinstance(item, str):
            _add(item)
        elif isinstance(item, dict):
            _add(str(item.get("raw") or ""), str(item.get("normalized") or "") or None)
    for name in regex_names or []:
        _add(str(name))
    return [rec for rec in out if rec.get("name")]


def merge_named_vendors(
    llm_items: Iterable[Any],
    regex_names: Iterable[str],
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[str]:
    """Deduped display names. LLM first, then regex. Brand + aliases excluded."""
    return [rec["name"] for rec in merge_classified_vendors(llm_items, regex_names, alias_map, brand, aliases)]


def annotate_vendor_cell(
    cell: dict[str, Any],
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> dict[str, Any]:
    """Stamp `origin` known|surprise on each vendor item. Drops brand rows."""
    alias_list = list(aliases)
    for key in ("vendors", "query_vendors"):
        kept = []
        for item in cell.get(key) or []:
            if isinstance(item, str):
                item = normalize_vendor_item(item)
            if not isinstance(item, dict):
                continue
            origin = vendor_origin(
                str(item.get("raw") or ""),
                str(item.get("normalized") or "") or None,
                alias_map,
                brand,
                alias_list,
            )
            if origin is None:
                continue
            item = dict(item)
            item["origin"] = origin
            kept.append(item)
        cell[key] = kept
    return cell


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


def classified_vendors_for_arm(
    arm: dict[str, Any] | None,
    vendor_cell: dict[str, Any] | None,
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[dict[str, str]]:
    if not isinstance(arm, dict):
        return []
    cell = vendor_cell if isinstance(vendor_cell, dict) else {}
    return merge_classified_vendors(
        cell.get("vendors") or [],
        arm.get("competitor_mentions") or [],
        alias_map,
        brand,
        aliases,
    )


def named_vendors_for_arm(
    arm: dict[str, Any] | None,
    vendor_cell: dict[str, Any] | None,
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[str]:
    return [rec["name"] for rec in classified_vendors_for_arm(arm, vendor_cell, alias_map, brand, aliases)]


def classified_query_vendors_for_arm(
    arm: dict[str, Any] | None,
    vendor_cell: dict[str, Any] | None,
    alias_map: AliasMap,
    brand: str,
    aliases: Iterable[str],
) -> list[dict[str, str]]:
    if not isinstance(arm, dict):
        return []
    cell = vendor_cell if isinstance(vendor_cell, dict) else {}
    return merge_classified_vendors(
        cell.get("query_vendors") or [],
        arm.get("vendors_in_search_queries") or [],
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
    return [rec["name"] for rec in classified_query_vendors_for_arm(arm, vendor_cell, alias_map, brand, aliases)]


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


def named_vendor_counts_by_origin(
    rows: list[dict[str, Any]],
    vendor_store: dict[str, Any],
    *,
    brand: str,
    aliases: Iterable[str],
    alias_map: AliasMap,
) -> tuple[Counter[str], Counter[str]]:
    """Answer-name counts split into known (incl. brand_mentioned) vs surprise."""
    known: Counter[str] = Counter()
    surprise: Counter[str] = Counter()
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
                    known[brand] += 1
                key = f"{pid}|{engine}|{arm_name}"
                for rec in classified_vendors_for_arm(arm, store.get(key), alias_map, brand, alias_list):
                    if rec["origin"] == "surprise":
                        surprise[rec["name"]] += 1
                    else:
                        known[rec["name"]] += 1
    return known, surprise


def who_got_named_counts(
    rows: list[dict[str, Any]],
    vendor_store: dict[str, Any],
    *,
    brand: str,
    aliases: Iterable[str],
    alias_map: AliasMap,
) -> Counter[str]:
    """Known seed competitors + brand. Surprises are not in this pile."""
    known, _ = named_vendor_counts_by_origin(
        rows, vendor_store, brand=brand, aliases=aliases, alias_map=alias_map
    )
    return known


def surprise_named_counts(
    rows: list[dict[str, Any]],
    vendor_store: dict[str, Any],
    *,
    brand: str,
    aliases: Iterable[str],
    alias_map: AliasMap,
) -> Counter[str]:
    _, surprise = named_vendor_counts_by_origin(
        rows, vendor_store, brand=brand, aliases=aliases, alias_map=alias_map
    )
    return surprise


def surprise_frequencies(
    docs: dict[str, dict[str, Any]],
    vendor_store: dict[str, Any],
    *,
    brand: str,
    aliases: Iterable[str],
    competitors: Iterable[str],
    competitor_aliases: dict[str, list[str]] | None = None,
) -> list[tuple[str, int]]:
    """High-frequency surprises across a run, for the board judge."""
    store = load_vendor_store(vendor_store)
    amap = seed_alias_map(
        brand, aliases, competitors, store.values(), competitor_aliases=competitor_aliases
    )
    counts: Counter[str] = Counter()
    for engine, doc in docs.items():
        if not isinstance(doc, dict):
            continue
        for cell in completed_cells(doc, str(engine)):
            recs = merge_classified_vendors(
                (store.get(cell["key"]) or {}).get("vendors") or [],
                cell.get("competitor_mentions") or [],
                amap,
                brand,
                aliases,
            )
            for rec in recs:
                if rec["origin"] == "surprise":
                    counts[rec["name"]] += 1
    return counts.most_common(16)


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
