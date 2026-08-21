"""Print a table: query, class, engine, knowledge hit, search hit, searched?, vendors, brand."""

from __future__ import annotations

from typing import Any


def _yes(v: bool | None) -> str:
    if v is None:
        return "—"
    return "yes" if v else "no"


def _short(text: str, n: int = 48) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _vendors(arm: dict[str, Any] | None) -> str:
    if not arm:
        return "—"
    names = arm.get("vendors_in_search_queries") or []
    return ", ".join(names) if names else "—"


def rows_from_doc(doc: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for prompt in doc.get("prompts") or []:
        query = prompt.get("prompt_text") or prompt.get("prompt_id") or ""
        cls = prompt.get("class") or "—"
        engines = prompt.get("engines") or {}
        for engine in ("claude", "codex", "grok"):
            arms = engines.get(engine)
            if not arms:
                continue
            k = arms.get("knowledge")
            s = arms.get("search")
            brand = False
            if isinstance(k, dict) and k.get("brand_mentioned"):
                brand = True
            if isinstance(s, dict) and s.get("brand_mentioned"):
                brand = True
            rows.append(
                {
                    "query": query,
                    "class": cls,
                    "engine": engine,
                    "knowledge": _yes(k.get("brand_mentioned") if isinstance(k, dict) else None),
                    "search": _yes(s.get("brand_mentioned") if isinstance(s, dict) else None),
                    "searched": _yes(s.get("searched") if isinstance(s, dict) else None),
                    "vendors": _vendors(s if isinstance(s, dict) else None),
                    "brand": _yes(brand),
                }
            )
    return rows


def render_table(rows: list[dict[str, str]]) -> str:
    headers = [
        ("query", "query"),
        ("class", "class"),
        ("engine", "engine"),
        ("knowledge", "knowledge hit"),
        ("search", "search hit"),
        ("searched", "searched?"),
        ("vendors", "vendors in search queries"),
        ("brand", "brand in answer"),
    ]
    data = [{k: r.get(k, "") for k, _ in headers} for r in rows]
    widths = {k: len(label) for k, label in headers}
    display: list[dict[str, str]] = []
    for row in data:
        shown = dict(row)
        shown["query"] = _short(row["query"], 52)
        shown["vendors"] = _short(row["vendors"], 36)
        display.append(shown)
        for k, _ in headers:
            widths[k] = max(widths[k], len(shown[k]))

    def fmt(row: dict[str, str]) -> str:
        return "  ".join(row[k].ljust(widths[k]) for k, _ in headers)

    header_row = fmt({k: label for k, label in headers})
    rule = "  ".join("-" * widths[k] for k, _ in headers)
    lines = [header_row, rule]
    for row in display:
        lines.append(fmt(row))
    if not display:
        lines.append("(no samples)")
    return "\n".join(lines)


def _arm_ok(arm: Any) -> bool:
    return isinstance(arm, dict) and "error" not in arm


def class_tally(doc: dict[str, Any]) -> str:
    """One-line N watch / N focus plus mention and search rates split by class."""
    stats: dict[str, dict[str, float]] = {}
    prompt_ids: dict[str, set[str]] = {}
    for prompt in doc.get("prompts") or []:
        cls = prompt.get("class") or "unclassed"
        pid = prompt.get("prompt_id") or prompt.get("prompt_text") or ""
        prompt_ids.setdefault(cls, set()).add(pid)
        bucket = stats.setdefault(
            cls, {"know_n": 0, "know_hits": 0, "search_n": 0, "search_hits": 0, "searched": 0}
        )
        for arms in (prompt.get("engines") or {}).values():
            if not isinstance(arms, dict):
                continue
            k = arms.get("knowledge")
            s = arms.get("search")
            if _arm_ok(k):
                bucket["know_n"] += 1
                if k.get("brand_mentioned"):
                    bucket["know_hits"] += 1
            if _arm_ok(s):
                bucket["search_n"] += 1
                if s.get("brand_mentioned"):
                    bucket["search_hits"] += 1
                if s.get("searched"):
                    bucket["searched"] += 1

    n_watch = len(prompt_ids.get("watch") or [])
    n_focus = len(prompt_ids.get("focus") or [])
    parts = [f"{n_watch} watch, {n_focus} focus"]
    extra = len(prompt_ids.get("unclassed") or [])
    if extra:
        parts[0] += f", {extra} unclassed"
    for cls in ("watch", "focus"):
        b = stats.get(cls)
        if not b:
            parts.append(f"{cls} mention_k=— mention_s=— search=—")
            continue
        mk = (b["know_hits"] / b["know_n"]) if b["know_n"] else 0.0
        ms = (b["search_hits"] / b["search_n"]) if b["search_n"] else 0.0
        sr = (b["searched"] / b["search_n"]) if b["search_n"] else 0.0
        parts.append(f"{cls} mention_k={mk:.2f} mention_s={ms:.2f} search={sr:.2f}")
    return "class tally: " + " | ".join(parts)


def render_doc(doc: dict[str, Any]) -> str:
    lines = [render_table(rows_from_doc(doc))]
    extras = []
    for key, label in (
        ("mention_rate_knowledge", "mention_rate_knowledge"),
        ("mention_rate_search", "mention_rate_search"),
        ("search_rate", "search_rate"),
        ("vendor_prebelief_rate", "vendor_prebelief_rate"),
    ):
        if key in doc:
            extras.append(f"{label}={doc[key]:.2f}")
    if extras:
        lines.append("")
        lines.append("rates (views, not source of truth): " + "  ".join(extras))
    lines.append(class_tally(doc))
    return "\n".join(lines)
