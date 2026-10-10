"""What each engine actually did: tool activity, browsing status and the retrieval chain.

Retrieval chain for one answer:

    query -> returned results (title, url) -> pages opened/fetched -> sources cited -> final answer

What each CLI exposes (documented gaps are stored with every chain):

- Claude ``--output-format stream-json --verbose``: every WebSearch query and its
  returned result list (title + url), every WebFetch url and whether it loaded.
  Sources cited = the links written in the final answer.
- Codex ``exec --json``: ``web_search`` items with the query (``action.type
  search``) and pages it opened (``open_page`` / ``find_in_page``). Codex does
  not emit the result list a search returned, so "returned results" is unknown.
- Grok ``--output-format json``: best effort; tool calls when present, no result
  lists.

Browsing status of an answer:

- ``none``: the engine's event stream was complete and showed no search or fetch
- ``searched``: search or fetch activity was observed
- ``unknown``: no machine-readable event stream, or it was cut off, so absence of
  browsing cannot be confirmed
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s)\]>\"'`<]+")
_LINKS_RE = re.compile(r"Links:\s*(\[.*?\])\s*(?:\n|$)", re.S)

BROWSING_STATES = ("none", "searched", "unknown")

GAPS = {
    "claude": [],
    "codex": ["Codex --json does not expose the result list a web search returned; only queries and opened pages."],
    "grok": ["Grok JSON output exposes no search result lists or fetched pages."],
}

_SEARCH_NAMES = {"websearch", "web_search", "websearchtool", "search"}
_FETCH_NAMES = {"webfetch", "web_fetch", "open_page", "browse"}


def _norm(name: Any) -> str:
    return str(name or "").strip().lower().replace("-", "_")


def urls_in(text: str) -> list[str]:
    out: list[str] = []
    for u in URL_RE.findall(text or ""):
        u = u.rstrip(".,;:*_")
        if u not in out:
            out.append(u)
    return out


def host_matches(url: str, domain: str) -> bool:
    domain = (domain or "").lower().strip().lstrip(".")
    if not domain:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host == domain or host.endswith("." + domain)


# --------------------------------------------------------------------------- Claude


def _claude_results_from_tool_result(block: dict[str, Any], extra: Any) -> list[dict[str, str]] | None:
    results: list[dict[str, str]] = []
    if isinstance(extra, dict) and isinstance(extra.get("results"), list):
        for item in extra["results"]:
            if isinstance(item, dict) and isinstance(item.get("content"), list):
                for r in item["content"]:
                    if isinstance(r, dict) and r.get("url"):
                        results.append({"title": str(r.get("title") or ""), "url": str(r["url"])})
        if results:
            return results
    content = block.get("content")
    text = ""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "\n".join(str(c.get("text") or "") for c in content if isinstance(c, dict))
    m = _LINKS_RE.search(text)
    if m:
        try:
            for r in json.loads(m.group(1)):
                if isinstance(r, dict) and r.get("url"):
                    results.append({"title": str(r.get("title") or ""), "url": str(r["url"])})
        except ValueError:
            pass
    if results:
        return results
    return None


def claude_chain(docs: list[Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        msg = doc.get("message") if isinstance(doc.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if doc.get("type") == "assistant":
            for block in content:
                if not isinstance(block, dict) or block.get("type") not in ("tool_use", "server_tool_use"):
                    continue
                name = _norm(block.get("name"))
                inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                if name in _SEARCH_NAMES:
                    step = {"kind": "search", "query": str(inp.get("query") or ""), "results": None}
                elif name in _FETCH_NAMES:
                    step = {"kind": "fetch", "url": str(inp.get("url") or ""), "ok": None}
                else:
                    continue
                steps.append(step)
                if block.get("id"):
                    by_id[str(block["id"])] = step
        elif doc.get("type") == "user":
            extra = doc.get("tool_use_result")
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                step = by_id.get(str(block.get("tool_use_id") or ""))
                if step is None:
                    continue
                if step["kind"] == "search":
                    step["results"] = _claude_results_from_tool_result(block, extra) or []
                else:
                    ok = not block.get("is_error")
                    if isinstance(extra, dict) and isinstance(extra.get("code"), int):
                        ok = ok and 200 <= extra["code"] < 400
                    step["ok"] = bool(ok)
    return {"steps": steps, "results_exposed": True}


# --------------------------------------------------------------------------- Codex


def codex_chain(docs: list[Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        item = doc.get("item") if isinstance(doc.get("item"), dict) else None
        if not item or item.get("type") != "web_search":
            continue
        iid = str(item.get("id") or "")
        if doc.get("type") == "item.started" and iid:
            continue  # use the completed item only
        if iid and iid in seen:
            continue
        if iid:
            seen.add(iid)
        action = item.get("action") if isinstance(item.get("action"), dict) else {}
        atype = _norm(action.get("type") or "search")
        if atype in ("open_page", "find_in_page"):
            steps.append({"kind": "fetch", "url": str(action.get("url") or ""), "ok": None})
            continue
        queries = [q for q in (action.get("queries") or []) if isinstance(q, str) and q.strip()]
        q = action.get("query") or item.get("query")
        if isinstance(q, str) and q.strip() and q not in queries:
            queries.insert(0, q)
        for q in queries or [""]:
            steps.append({"kind": "search", "query": q, "results": None})
    return {"steps": steps, "results_exposed": False}


# --------------------------------------------------------------------------- Grok


def grok_chain(docs: list[Any]) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, list):
            for x in obj:
                walk(x)
            return
        if not isinstance(obj, dict):
            return
        name = _norm(obj.get("name") or obj.get("tool") or obj.get("type"))
        args = obj.get("input") or obj.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {"query": args}
        if name in _SEARCH_NAMES and isinstance(args, dict):
            steps.append({"kind": "search", "query": str(args.get("query") or ""), "results": None})
        elif name in _FETCH_NAMES and isinstance(args, dict):
            steps.append({"kind": "fetch", "url": str(args.get("url") or ""), "ok": None})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                walk(v)

    walk(docs)
    return {"steps": steps, "results_exposed": False}


CHAINS = {"claude": claude_chain, "codex": codex_chain, "grok": grok_chain}


def build_chain(engine: str, docs: list[Any], answer_text: str) -> dict[str, Any]:
    chain = CHAINS.get(engine, claude_chain)(docs)
    chain["cited_urls"] = urls_in(answer_text)
    chain["gaps"] = list(GAPS.get(engine, []))
    return chain


# --------------------------------------------------------------------------- activity


def tool_activity(engine: str, docs: list[Any]) -> dict[str, Any]:
    """Tools the engine offered and used, from its own event stream."""
    used: list[str] = []
    available: list[str] | None = None
    model: str | None = None
    server_search = server_fetch = 0
    complete = False
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        typ = doc.get("type")
        if engine == "claude":
            if typ == "system" and doc.get("subtype") == "init":
                if isinstance(doc.get("tools"), list):
                    available = [str(t) for t in doc["tools"]]
                if doc.get("model"):
                    model = str(doc["model"])
            if typ == "assistant":
                msg = doc.get("message") if isinstance(doc.get("message"), dict) else {}
                if msg.get("model") and not model:
                    model = str(msg["model"])
                for block in msg.get("content") or []:
                    if isinstance(block, dict) and block.get("type") in ("tool_use", "server_tool_use"):
                        used.append(str(block.get("name") or "tool"))
            if typ == "result":
                complete = not doc.get("is_error")
                usage = doc.get("usage") if isinstance(doc.get("usage"), dict) else {}
                stu = usage.get("server_tool_use") if isinstance(usage.get("server_tool_use"), dict) else {}
                server_search = max(server_search, int(stu.get("web_search_requests") or 0), int(doc.get("web_search_requests") or 0))
                server_fetch = max(server_fetch, int(stu.get("web_fetch_requests") or 0))
                mu = doc.get("modelUsage")
                if isinstance(mu, dict) and mu and not model:
                    model = ",".join(sorted(mu))
        elif engine == "codex":
            if typ in ("turn.completed",):
                complete = True
            if typ in ("turn.failed", "error"):
                complete = False
            item = doc.get("item") if isinstance(doc.get("item"), dict) else None
            if item and typ == "item.completed":
                it = str(item.get("type") or "")
                if it in ("agent_message", "reasoning", "todo_list", "error"):
                    continue
                if it == "mcp_tool_call":
                    used.append(f"mcp:{item.get('server') or '?'}/{item.get('tool') or '?'}")
                elif it == "command_execution":
                    used.append("command")
                else:
                    used.append(it)
            if doc.get("model") and not model:
                model = str(doc["model"])
        else:  # grok: single JSON object
            complete = True
            for key in ("tool_calls", "tool_uses", "web_search", "web_fetch", "web_searches"):
                val = doc.get(key)
                if isinstance(val, list):
                    for v in val:
                        used.append(str((v or {}).get("name") if isinstance(v, dict) else key))
                elif val:
                    used.append(key)
            if doc.get("model") and not model:
                model = str(doc["model"])
    searchy = [u for u in used if _norm(u) in _SEARCH_NAMES | _FETCH_NAMES or _norm(u) == "web_search"]
    if server_search:
        searchy.append("server:web_search")
    if server_fetch:
        searchy.append("server:web_fetch")
    return {
        "tools_available": available,
        "tools_used": used,
        "search_activity": bool(searchy),
        "stream_complete": complete,
        "model": model,
    }


def browsing_status(activity: dict[str, Any] | None, *, parsed_events: bool) -> str:
    """none / searched / unknown for one answer.

    Any other tool call (a shell command such as curl, an MCP call) could have
    reached the web without showing up as a search, so it makes the answer
    "unknown" rather than unaided recall.
    """
    if activity and activity.get("search_activity"):
        return "searched"
    if not parsed_events or not activity or not activity.get("stream_complete"):
        return "unknown"
    if activity.get("tools_used"):
        return "unknown"
    return "none"


def legacy_browsing(engine: str, arm_name: str, arm: dict[str, Any]) -> str:
    """Browsing status for answers stored before it was recorded (runs before Oct 10, 2026).

    Claude's no-search arm ran with ``--tools ""`` (no web tools) and Grok's with
    ``--disable-web-search``. Codex's no-search arm did not switch search off and
    recorded no events, so it cannot be confirmed.
    """
    if arm.get("searched"):
        return "searched"
    if arm_name == "search":
        return "none" if engine in ("claude", "codex") else "unknown"
    if engine == "codex":
        return "unknown"
    return "none"


def arm_browsing(engine: str, arm_name: str, arm: dict[str, Any]) -> str:
    val = arm.get("browsing")
    if val in BROWSING_STATES:
        return str(val)
    return legacy_browsing(engine, arm_name, arm)


# --------------------------------------------------------------------------- funnel


def brand_funnel(chain: dict[str, Any] | None, domain: str, *, named: bool, cited_urls: list[str] | None = None) -> dict[str, Any]:
    """Where the brand's site appeared or dropped out along one answer's retrieval chain."""
    chain = chain or {}
    steps = chain.get("steps") or []
    searches = [s for s in steps if s.get("kind") == "search"]
    fetches = [s for s in steps if s.get("kind") == "fetch"]
    exposed = bool(chain.get("results_exposed")) and any(s.get("results") is not None for s in searches)
    in_results: bool | None = None
    if exposed:
        in_results = any(host_matches(r.get("url") or "", domain) for s in searches for r in (s.get("results") or []))
    opened = any(host_matches(s.get("url") or "", domain) for s in fetches)
    cited_list = cited_urls if cited_urls is not None else (chain.get("cited_urls") or [])
    cited = any(host_matches(u, domain) for u in cited_list)
    if not steps:
        stage = "did not search"
    elif named:
        stage = "named"
    elif cited:
        stage = "cited but not named"
    elif opened:
        stage = "opened but not cited"
    elif in_results:
        stage = "returned but not opened"
    elif in_results is False:
        stage = "never returned by search"
    else:
        stage = "not opened or cited (search results not exposed)"
    return {
        "searches": len(searches),
        "fetches": len(fetches),
        "in_results": in_results,
        "opened": opened,
        "cited": cited,
        "named": bool(named),
        "stage": stage,
    }
