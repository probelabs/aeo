"""Decision board from an evidence document. Markdown for humans, JSON for agents."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

BOARD_SCHEMA_VERSION = "aeo-cli-board-v1"
ENGINES = ("claude", "codex", "grok")
CALLS = ("win", "gap", "trap", "search-blind")

GROUPS = (
    {
        "id": "focus_product",
        "title": "Focus · product-fit",
        "blurb": "can XERJ win from weights or a short search?",
    },
    {
        "id": "focus_search",
        "title": "Focus · search-likely",
        "blurb": "does search even fire?",
    },
    {
        "id": "watch",
        "title": "Watch",
        "blurb": "still the incumbent (usually ripgrep)? keep measuring, do not invest yet",
    },
)

LEGEND = (
    "✓ mention · ✗ miss · — not run · 🔍 searched · ⚠ confirmation search (not discovery) "
    "· call: win | gap | trap | search-blind"
)

QUERY_WIDTH = 52


def _short(text: str, n: int = QUERY_WIDTH) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _brand_terms(doc: dict[str, Any]) -> set[str]:
    ws = doc.get("workspace") or {}
    terms = {str(ws.get("brand") or "").lower()}
    for a in ws.get("aliases") or []:
        terms.add(str(a).lower())
    terms.discard("")
    return terms


def _is_prebelief(search_arm: dict[str, Any] | None, brand_terms: set[str]) -> bool:
    if not isinstance(search_arm, dict) or not search_arm.get("searched"):
        return False
    vendors = [str(v).lower() for v in (search_arm.get("vendors_in_search_queries") or [])]
    if not vendors:
        return False
    return not (set(vendors) & brand_terms)


def _search_mark(search_arm: dict[str, Any] | None, brand_terms: set[str]) -> str:
    if not isinstance(search_arm, dict):
        return "—"
    if not search_arm.get("searched"):
        return "✗"
    if _is_prebelief(search_arm, brand_terms):
        return "⚠"
    return "✓"


def _mention_mark(arm: dict[str, Any] | None) -> str:
    if not isinstance(arm, dict):
        return "—"
    return "✓" if arm.get("brand_mentioned") else "✗"


def _arm_cell(arm: dict[str, Any] | None, *, include_search: bool) -> dict[str, Any] | None:
    if not isinstance(arm, dict):
        return None
    cell: dict[str, Any] = {"mentioned": bool(arm.get("brand_mentioned"))}
    if include_search:
        cell["searched"] = bool(arm.get("searched"))
        cell["vendors_in_search_queries"] = list(arm.get("vendors_in_search_queries") or [])
    return cell


def _group_id(prompt: dict[str, Any]) -> str:
    cls = prompt.get("class")
    why = str(prompt.get("why") or "")
    if cls == "watch":
        return "watch"
    if "product_fit" in why:
        return "focus_product"
    return "focus_search"


def _call_for(prompt: dict[str, Any], engines: dict[str, Any]) -> str:
    mentioned = False
    any_search_run = False
    any_searched = False
    for arms in engines.values():
        if not isinstance(arms, dict):
            continue
        k = arms.get("knowledge")
        s = arms.get("search")
        if isinstance(k, dict) and k.get("mentioned"):
            mentioned = True
        if isinstance(s, dict):
            any_search_run = True
            if s.get("mentioned"):
                mentioned = True
            if s.get("searched"):
                any_searched = True
    if mentioned:
        return "win"
    if prompt.get("class") == "watch":
        return "trap"
    if any_searched:
        return "gap"
    if any_search_run:
        return "search-blind"
    return "search-blind"


def _engine_view(prompt: dict[str, Any]) -> dict[str, Any]:
    raw = prompt.get("engines") or {}
    out: dict[str, Any] = {}
    for engine in ENGINES:
        arms = raw.get(engine)
        if not isinstance(arms, dict):
            continue
        view: dict[str, Any] = {}
        k = _arm_cell(arms.get("knowledge"), include_search=False)
        s = _arm_cell(arms.get("search"), include_search=True)
        if k is not None:
            view["knowledge"] = k
        if s is not None:
            view["search"] = s
        if view:
            out[engine] = view
    return out


def _scoreboard(prompts: list[dict[str, Any]], brand_terms: set[str]) -> dict[str, Any]:
    focus_s_n = focus_s_hits = focus_searched = 0
    watch_n = watch_hits = 0
    prebelief = 0
    for prompt in prompts:
        cls = prompt.get("class")
        raw_engines = prompt.get("engines") or {}
        for arms in raw_engines.values():
            if not isinstance(arms, dict):
                continue
            k = arms.get("knowledge")
            s = arms.get("search")
            if cls == "watch":
                for arm in (k, s):
                    if isinstance(arm, dict) and "error" not in arm:
                        watch_n += 1
                        if arm.get("brand_mentioned"):
                            watch_hits += 1
            else:
                if isinstance(s, dict) and "error" not in s:
                    focus_s_n += 1
                    if s.get("brand_mentioned"):
                        focus_s_hits += 1
                    if s.get("searched"):
                        focus_searched += 1
            if _is_prebelief(s if isinstance(s, dict) else None, brand_terms):
                prebelief += 1
    return {
        "focus_mention_rate_search": (focus_s_hits / focus_s_n) if focus_s_n else 0.0,
        "focus_search_rate": (focus_searched / focus_s_n) if focus_s_n else 0.0,
        "watch_mention_rate": (watch_hits / watch_n) if watch_n else 0.0,
        "prebelief_count": prebelief,
    }


def build_board(doc: dict[str, Any]) -> dict[str, Any]:
    brand_terms = _brand_terms(doc)
    run = doc.get("run") or {}
    grouped: dict[str, list[dict[str, Any]]] = {g["id"]: [] for g in GROUPS}
    for prompt in doc.get("prompts") or []:
        engines = _engine_view(prompt)
        row: dict[str, Any] = {
            "prompt_id": prompt.get("prompt_id") or "",
            "prompt_text": prompt.get("prompt_text") or "",
            "call": _call_for(prompt, engines),
            "engines": engines,
        }
        if prompt.get("class"):
            row["class"] = prompt["class"]
        if prompt.get("why"):
            row["why"] = prompt["why"]
        grouped[_group_id(prompt)].append(row)
    groups = []
    for spec in GROUPS:
        groups.append(
            {
                "id": spec["id"],
                "title": spec["title"],
                "rows": grouped[spec["id"]],
            }
        )
    return {
        "schema_version": BOARD_SCHEMA_VERSION,
        "run_id": run.get("run_id") or "",
        "scoreboard": _scoreboard(doc.get("prompts") or [], brand_terms),
        "groups": groups,
    }


def _prebelief_cell(row: dict[str, Any]) -> str:
    names: list[str] = []
    seen: set[str] = set()
    for arms in (row.get("engines") or {}).values():
        s = (arms or {}).get("search") or {}
        for v in s.get("vendors_in_search_queries") or []:
            key = str(v).lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(str(v))
    return ", ".join(names) if names else "—"


def _engine_marks(row: dict[str, Any], engine: str, brand_terms: set[str]) -> tuple[str, str, str]:
    arms = (row.get("engines") or {}).get(engine) or {}
    k = arms.get("knowledge")
    s = arms.get("search")
    k_mark = "—" if k is None else ("✓" if k.get("mentioned") else "✗")
    s_mark = "—" if s is None else ("✓" if s.get("mentioned") else "✗")
    if s is None:
        search_mark = "—"
    elif not s.get("searched"):
        search_mark = "✗"
    else:
        vendors = [str(v).lower() for v in (s.get("vendors_in_search_queries") or [])]
        if vendors and not (set(vendors) & brand_terms):
            search_mark = "⚠"
        else:
            search_mark = "✓"
    return k_mark, s_mark, search_mark


def _md_scoreboard(sb: dict[str, Any]) -> list[str]:
    return [
        f"Focus mention rate (search arm): {sb['focus_mention_rate_search']:.2f}",
        f"Focus search rate: {sb['focus_search_rate']:.2f}",
        f"Watch mention rate: {sb['watch_mention_rate']:.2f}",
        f"Prebelief searches (⚠): {sb['prebelief_count']}",
    ]


def render_markdown(board: dict[str, Any], *, brand_terms: set[str] | None = None) -> str:
    brand_terms = brand_terms or set()
    lines: list[str] = [f"# Board · {board.get('run_id') or 'run'}", ""]
    lines.extend(_md_scoreboard(board.get("scoreboard") or {}))
    lines.append("")
    headers = [
        "Query",
        "Claude K",
        "Claude S",
        "Claude 🔍",
        "Codex K",
        "Codex S",
        "Codex 🔍",
        "Grok K",
        "Grok S",
        "Grok 🔍",
        "Prebelief",
        "Call",
    ]
    blurb = {g["id"]: g["blurb"] for g in GROUPS}
    for group in board.get("groups") or []:
        rows = group.get("rows") or []
        if not rows:
            continue
        lines.append(f"## {group.get('title')}")
        lines.append(blurb.get(group.get("id"), ""))
        lines.append("")
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            ck, cs, cq = _engine_marks(row, "claude", brand_terms)
            xk, xs, xq = _engine_marks(row, "codex", brand_terms)
            gk, gs, gq = _engine_marks(row, "grok", brand_terms)
            cells = [
                _short(row.get("prompt_text") or row.get("prompt_id") or ""),
                ck, cs, cq, xk, xs, xq, gk, gs, gq,
                _short(_prebelief_cell(row), 28),
                row.get("call") or "",
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
    lines.append(LEGEND)
    return "\n".join(lines).rstrip() + "\n"


def render_html(board: dict[str, Any], *, brand_terms: set[str] | None = None) -> str:
    brand_terms = brand_terms or set()
    sb = board.get("scoreboard") or {}
    run_id = html.escape(str(board.get("run_id") or "run"))
    parts = [
        "<!DOCTYPE html>",
        '<html lang="en"><head><meta charset="utf-8">',
        f"<title>Board · {run_id}</title>",
        "<style>",
        "body{font:14px/1.4 system-ui,sans-serif;margin:24px;color:#111}",
        "table{border-collapse:collapse;margin:12px 0 24px;width:100%}",
        "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left}",
        "th{background:#f6f6f6;font-weight:600}",
        "h1{font-size:20px}h2{font-size:16px;margin-top:28px}",
        ".score{margin:0;padding:0} .score li{margin:2px 0}",
        ".legend{color:#555;font-size:12px}",
        ".blurb{color:#555;margin:0 0 8px}",
        "</style></head><body>",
        f"<h1>Board · {run_id}</h1>",
        "<ul class='score'>",
        f"<li>Focus mention rate (search arm): {sb.get('focus_mention_rate_search', 0):.2f}</li>",
        f"<li>Focus search rate: {sb.get('focus_search_rate', 0):.2f}</li>",
        f"<li>Watch mention rate: {sb.get('watch_mention_rate', 0):.2f}</li>",
        f"<li>Prebelief searches (⚠): {sb.get('prebelief_count', 0)}</li>",
        "</ul>",
    ]
    headers = [
        "Query", "Claude K", "Claude S", "Claude 🔍",
        "Codex K", "Codex S", "Codex 🔍",
        "Grok K", "Grok S", "Grok 🔍", "Prebelief", "Call",
    ]
    blurb = {g["id"]: g["blurb"] for g in GROUPS}
    for group in board.get("groups") or []:
        rows = group.get("rows") or []
        if not rows:
            continue
        parts.append(f"<h2>{html.escape(group.get('title') or '')}</h2>")
        parts.append(f"<p class='blurb'>{html.escape(blurb.get(group.get('id'), ''))}</p>")
        parts.append("<table><thead><tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr></thead><tbody>")
        for row in rows:
            ck, cs, cq = _engine_marks(row, "claude", brand_terms)
            xk, xs, xq = _engine_marks(row, "codex", brand_terms)
            gk, gs, gq = _engine_marks(row, "grok", brand_terms)
            cells = [
                _short(row.get("prompt_text") or row.get("prompt_id") or ""),
                ck, cs, cq, xk, xs, xq, gk, gs, gq,
                _short(_prebelief_cell(row), 28),
                row.get("call") or "",
            ]
            parts.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in cells) + "</tr>")
        parts.append("</tbody></table>")
    parts.append(f"<p class='legend'>{html.escape(LEGEND)}</p>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def render_json(board: dict[str, Any]) -> str:
    return json.dumps(board, indent=2, ensure_ascii=False) + "\n"


def boards_dir_for(evidence_path: Path) -> Path:
    """aeo-data/boards next to the evidence file (or its parent if in runs/)."""
    parent = evidence_path.parent
    if parent.name == "runs":
        return parent.parent / "boards"
    return parent / "boards"


def write_board_files(
    board: dict[str, Any],
    evidence_path: Path,
    *,
    brand_terms: set[str] | None = None,
    formats: tuple[str, ...] = ("md", "json"),
    out_dir: Path | None = None,
) -> dict[str, Path]:
    dest = out_dir or boards_dir_for(evidence_path)
    dest.mkdir(parents=True, exist_ok=True)
    run_id = board.get("run_id") or evidence_path.stem
    written: dict[str, Path] = {}
    if "md" in formats:
        p = dest / f"{run_id}.md"
        p.write_text(render_markdown(board, brand_terms=brand_terms), encoding="utf-8")
        written["md"] = p
    if "json" in formats:
        p = dest / f"{run_id}.json"
        p.write_text(render_json(board), encoding="utf-8")
        written["json"] = p
    if "html" in formats:
        p = dest / f"{run_id}.html"
        p.write_text(render_html(board, brand_terms=brand_terms), encoding="utf-8")
        written["html"] = p
    return written
