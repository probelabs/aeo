"""Print a table: query, engine, knowledge hit, search hit, searched?, vendors, brand."""

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
    return "\n".join(lines)
