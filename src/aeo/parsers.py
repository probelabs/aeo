"""Parse Claude / Codex / Grok CLI stdout for answer text and search tool calls."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Tool names that count as a search (normalized: lowercase, strip _-)
SEARCH_TOOL_NAMES = {
    "websearch",
    "webfetch",
    "web_search",
    "web_fetch",
    "web-search",
    "web-fetch",
    "standalone_web_search",
    "standalonewebsearch",
    "search",
}

QUERY_KEYS = (
    "query",
    "queries",
    "q",
    "search_query",
    "searchQuery",
    "url",
    "uri",
)


@dataclass
class ParsedRun:
    raw_response_text: str = ""
    searched: bool = False
    search_queries: list[str] = field(default_factory=list)
    usage: dict[str, Any] | None = None

    def add_query(self, q: str) -> None:
        q = (q or "").strip()
        if q and q not in self.search_queries:
            self.search_queries.append(q)
            self.searched = True


def parse_json_documents(raw: str) -> list[Any]:
    """Parse a single JSON value, a JSON array, or JSONL. Skip non-JSON lines."""
    text = (raw or "").strip()
    if not text:
        return []
    try:
        val = json.loads(text)
        return val if isinstance(val, list) else [val]
    except json.JSONDecodeError:
        pass
    docs: list[Any] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith(("{", "[")):
            continue
        try:
            docs.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return docs


def _norm_tool(name: str) -> str:
    return (name or "").strip().lower().replace("-", "").replace("_", "")


def _is_search_tool(name: str) -> bool:
    raw = (name or "").strip().lower()
    if raw in SEARCH_TOOL_NAMES:
        return True
    compact = _norm_tool(name)
    return compact in {_norm_tool(n) for n in SEARCH_TOOL_NAMES}


def _as_queries(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item)
            elif isinstance(item, dict):
                out.extend(_queries_from_mapping(item))
        return out
    if isinstance(value, dict):
        return _queries_from_mapping(value)
    return []


def _queries_from_mapping(obj: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in QUERY_KEYS:
        if key in obj:
            out.extend(_as_queries(obj[key]))
    return out


def _collect_tool_use(obj: Any, parsed: ParsedRun) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_tool_use(item, parsed)
        return
    if not isinstance(obj, dict):
        return

    name = obj.get("name") or obj.get("tool") or obj.get("tool_name") or ""
    typ = obj.get("type") or obj.get("item_type") or ""

    # Claude content block: {type: tool_use, name: WebSearch, input: {query}}
    if typ == "tool_use" or _is_search_tool(str(name)) or _is_search_tool(str(typ)):
        if _is_search_tool(str(name)) or _is_search_tool(str(typ)) or typ == "web_search":
            payload = obj.get("input") or obj.get("arguments") or obj.get("action") or obj
            if isinstance(obj.get("arguments"), str):
                try:
                    payload = json.loads(obj["arguments"])
                except json.JSONDecodeError:
                    parsed.add_query(obj["arguments"])
                    payload = {}
            for q in _as_queries(payload if isinstance(payload, (dict, list, str)) else {}):
                parsed.add_query(q)
            # Count a search even if the query string is missing.
            if _is_search_tool(str(name)) or typ in {"web_search", "tool_use"} and _is_search_tool(str(name) or str(typ)):
                parsed.searched = True

    # Codex: item.type == "web_search" with action.queries[] and/or query
    if typ == "web_search" or (isinstance(obj.get("item"), dict) and obj["item"].get("type") == "web_search"):
        item = obj["item"] if isinstance(obj.get("item"), dict) else obj
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        for q in _as_queries(action.get("queries")):
            parsed.add_query(q)
        for q in _as_queries(item.get("query")):
            parsed.add_query(q)
        parsed.searched = True

    # Nested walk (shallow-known keys first, then values)
    for key, val in obj.items():
        if key in {"input", "arguments", "action", "item", "message", "content",
                   "tool_calls", "tool_use", "function", "delta"}:
            _collect_tool_use(val, parsed)
        elif isinstance(val, (dict, list)) and key not in {"usage"}:
            # Don't recurse into huge usage blobs more than once
            if key in {"server_tool_use"}:
                continue
            _collect_tool_use(val, parsed)


def _collect_text(obj: Any, parts: list[str]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _collect_text(item, parts)
        return
    if not isinstance(obj, dict):
        return

    typ = obj.get("type")
    # Prefer final result / agent message over intermediate deltas.
    if typ == "result" and isinstance(obj.get("result"), str):
        parts.append(obj["result"])
        return
    if typ in {"item.completed", "item.updated"} or "item" in obj:
        item = obj.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text") or item.get("content")
            if isinstance(text, str) and text.strip():
                parts.append(text)
    if typ == "assistant":
        msg = obj.get("message") or obj
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and content.strip():
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    t = block.get("text")
                    if isinstance(t, str) and t.strip():
                        parts.append(t)

    # Grok --output-format json --verbatim
    for key in ("message", "response", "text", "output", "verbatim", "content"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip() and key in {"response", "verbatim", "output"}:
            parts.append(val)
        if key == "message" and isinstance(val, str) and val.strip() and typ not in {"assistant", "user"}:
            parts.append(val)
        if key == "content" and isinstance(val, str) and val.strip() and typ not in {"assistant", "tool_use"}:
            parts.append(val)

    # Choices-style
    choices = obj.get("choices")
    if isinstance(choices, list):
        for ch in choices:
            if not isinstance(ch, dict):
                continue
            msg = ch.get("message") or ch.get("delta") or {}
            if isinstance(msg, dict):
                c = msg.get("content")
                if isinstance(c, str) and c.strip():
                    parts.append(c)


def _web_search_requests(obj: Any) -> int:
    if isinstance(obj, list):
        return max((_web_search_requests(x) for x in obj), default=0)
    if not isinstance(obj, dict):
        return 0
    n = 0
    if isinstance(obj.get("web_search_requests"), (int, float)):
        n = max(n, int(obj["web_search_requests"]))
    stu = obj.get("server_tool_use")
    if isinstance(stu, dict) and isinstance(stu.get("web_search_requests"), (int, float)):
        n = max(n, int(stu["web_search_requests"]))
    usage = obj.get("usage")
    if isinstance(usage, dict):
        n = max(n, _web_search_requests(usage))
    return n


def _dedupe_keep_longest(parts: list[str]) -> str:
    """Prefer the longest distinct text blob (final answer over prefixes)."""
    cleaned = [p.strip() for p in parts if p and p.strip()]
    if not cleaned:
        return ""
    # Drop parts that are strict prefixes of a longer part.
    kept: list[str] = []
    for p in sorted(set(cleaned), key=len, reverse=True):
        if any(p != k and p in k for k in kept):
            continue
        kept.append(p)
    # Last-wins among remaining if they look like successive finals; else join last.
    return kept[0]



def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    return 0


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _usage_from_mapping(obj: dict[str, Any]) -> dict[str, Any]:
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    model = obj.get("modelUsage") if isinstance(obj.get("modelUsage"), dict) else {}
    # First modelUsage entry is Claude's per-model rollup; prefer top-level usage.
    mu = {}
    if model:
        first = next(iter(model.values()), None)
        if isinstance(first, dict):
            mu = first
    inp = _as_int(
        usage.get("input_tokens")
        or usage.get("prompt_tokens")
        or usage.get("inputTokens")
        or mu.get("inputTokens")
    )
    out = _as_int(
        usage.get("output_tokens")
        or usage.get("completion_tokens")
        or usage.get("outputTokens")
        or mu.get("outputTokens")
    )
    cache_read = _as_int(
        usage.get("cache_read_input_tokens")
        or usage.get("cacheReadInputTokens")
        or mu.get("cacheReadInputTokens")
    )
    cache_write = _as_int(
        usage.get("cache_creation_input_tokens")
        or usage.get("cacheCreationInputTokens")
        or mu.get("cacheCreationInputTokens")
    )
    cost = _as_float(obj.get("total_cost_usd"))
    if cost is None:
        cost = _as_float(usage.get("total_cost_usd") or usage.get("cost_usd") or mu.get("costUSD"))
    blob: dict[str, Any] = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_tokens": inp + out + cache_read + cache_write,
    }
    if cost is not None:
        blob["cost_usd"] = cost
    return blob


def _add_usage(acc: dict[str, Any] | None, piece: dict[str, Any]) -> dict[str, Any]:
    acc = acc or {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "total_tokens": 0,
    }
    for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "total_tokens"):
        acc[k] = acc.get(k, 0) + piece.get(k, 0)
    if "cost_usd" in piece:
        acc["cost_usd"] = round(float(acc.get("cost_usd") or 0) + float(piece["cost_usd"]), 6)
    return acc


def extract_usage(docs: list[Any]) -> dict[str, Any] | None:
    """Pull token/cost totals from CLI JSON. Prefer a final result row; else sum usage objects."""
    acc: dict[str, Any] | None = None
    result_usage = None
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        if doc.get("type") == "result" and (isinstance(doc.get("usage"), dict) or "total_cost_usd" in doc):
            result_usage = _usage_from_mapping(doc)
        if isinstance(doc.get("usage"), dict) and doc.get("type") != "result":
            acc = _add_usage(acc, _usage_from_mapping(doc))
    picked = result_usage or acc
    if not picked:
        return None
    if not any(picked.get(k) for k in ("input_tokens", "output_tokens", "cost_usd", "cache_read_tokens")):
        return None
    return picked


def parse_claude(raw: str) -> ParsedRun:
    docs = parse_json_documents(raw)
    parsed = ParsedRun()
    if not docs:
        parsed.raw_response_text = (raw or "").strip()
        return parsed
    texts: list[str] = []
    requests = 0
    for doc in docs:
        _collect_tool_use(doc, parsed)
        _collect_text(doc, texts)
        requests = max(requests, _web_search_requests(doc))
    if requests > 0:
        parsed.searched = True
    parsed.raw_response_text = _dedupe_keep_longest(texts) or (raw or "").strip()
    return parsed


def parse_codex(raw: str) -> ParsedRun:
    docs = parse_json_documents(raw)
    parsed = ParsedRun()
    if not docs:
        parsed.raw_response_text = (raw or "").strip()
        return parsed
    texts: list[str] = []
    for doc in docs:
        _collect_tool_use(doc, parsed)
        _collect_text(doc, texts)
    parsed.raw_response_text = _dedupe_keep_longest(texts) or (raw or "").strip()
    return parsed


def parse_grok(raw: str) -> ParsedRun:
    docs = parse_json_documents(raw)
    parsed = ParsedRun()
    if not docs:
        parsed.raw_response_text = (raw or "").strip()
        return parsed
    texts: list[str] = []
    for doc in docs:
        _collect_tool_use(doc, parsed)
        _collect_text(doc, texts)
        # Explicit grok fields
        if isinstance(doc, dict):
            for key in ("web_search", "web_fetch", "web_searches", "tool_calls", "tool_uses"):
                if key in doc:
                    _collect_tool_use(doc[key], parsed)
                    if key in {"web_search", "web_fetch", "web_searches"}:
                        parsed.searched = True
                        for q in _as_queries(doc[key]):
                            parsed.add_query(q)
    parsed.raw_response_text = _dedupe_keep_longest(texts) or (raw or "").strip()
    return parsed


PARSERS = {
    "claude": parse_claude,
    "codex": parse_codex,
    "grok": parse_grok,
}


def parse_engine(engine: str, raw: str) -> ParsedRun:
    fn = PARSERS.get(engine, parse_claude)
    parsed = fn(raw)
    docs = parse_json_documents(raw)
    parsed.usage = extract_usage(docs)
    return parsed
