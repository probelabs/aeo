"""Google layer: live Google SERP (AI Overview + organic, optional AI Mode) via DataForSEO.

This layer is strictly separate from the LLM mention board. It never reads or
writes evidence cells and never changes board scores. Output is a sidecar
`google.json` (+ `google.md`) in a layer directory next to the evidence.

Credentials (never printed):
  DATAFORSEO_LOGIN (or DATAFORSEO_USERNAME) + DATAFORSEO_PASSWORD, or
  DATAFORSEO_CREDENTIALS_FILE pointing at a file containing
  `DATAFORSEO_LOGIN=...` / `DATAFORSEO_PASSWORD=...` (KEY=VALUE or YAML `KEY: value`).
No credentials -> the layer is skipped silently.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = "aeo-google-v1"
PROPOSALS_SCHEMA_VERSION = "aeo-google-proposals-v1"
API = "https://api.dataforseo.com"
SERP_PATH = "/v3/serp/google/organic/live/advanced"
AI_MODE_PATH = "/v3/serp/google/ai_mode/live/advanced"

STATUS_OK = 20000
STATUS_PARTIAL = 40106  # some deep pages missing; AIO + first pages usually fine
# 40101 "Internal SE Server Error" and other task errors are retried.

# DataForSEO list prices (USD) used for the pre-flight estimate. Organic live/advanced
# bills per page of 10 results; load_async_ai_overview adds a flat fee; AI Mode is flat.
PRICE_PER_PAGE = 0.002
PRICE_ASYNC_AIO = 0.002
PRICE_AI_MODE = 0.004

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "location_code": 2840,  # United States
    "language_code": "en",
    "device": "desktop",
    "depth": 100,  # targets: top 100 so we can report our position
    "watch_depth": 100,
    "mobile": False,  # extra mobile snapshot for targets (AIO + top mobile_depth)
    "mobile_depth": 10,
    "ai_mode": True,  # targets only; ~$0.004 per search
    "max_cost_usd": 2.0,
    "own_url_patterns": [],
    "workers": 4,
    "retries": 3,
}

TARGET_TIER = "target"
WATCH_TIER = "watch"


# ---------------------------------------------------------------- config


def load_raw_config(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"config must be a JSON object: {path}")
    return data


def load_settings(raw_cfg: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    s = dict(DEFAULT_SETTINGS)
    block = raw_cfg.get("google") if isinstance(raw_cfg.get("google"), dict) else {}
    for k, v in block.items():
        if k in DEFAULT_SETTINGS and v is not None:
            s[k] = v
    for k, v in overrides.items():
        if v is not None:
            s[k] = v
    s["own_url_patterns"] = [str(p) for p in s.get("own_url_patterns") or []]
    return s


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:80] or "q"


def norm_query(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def normalize_searches(raw: Any, tier: str) -> list[dict[str, Any]]:
    """google_targets / google_watch entries: strings or {query, id?, priority?, lead?, ...}."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, item in enumerate(raw or []):
        if isinstance(item, str):
            item = {"query": item}
        if not isinstance(item, dict) or not str(item.get("query") or "").strip():
            continue
        q = str(item["query"]).strip()
        key = norm_query(q)
        if key in seen:
            continue
        seen.add(key)
        rec: dict[str, Any] = {"id": str(item.get("id") or _slug(q)), "query": q, "tier": tier}
        if tier == TARGET_TIER:
            rec["priority"] = int(item.get("priority") or (i + 1))
        for extra in ("lead", "page_should_demonstrate", "notes"):
            if item.get(extra):
                rec[extra] = str(item[extra])
        out.append(rec)
    return out


def config_searches(raw_cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    targets = normalize_searches(raw_cfg.get("google_targets"), TARGET_TIER)
    tkeys = {norm_query(t["query"]) for t in targets}
    watch = [w for w in normalize_searches(raw_cfg.get("google_watch"), WATCH_TIER) if norm_query(w["query"]) not in tkeys]
    ids: set[str] = set()
    for rec in targets + watch:  # ids must be unique for raw file names
        base, n = rec["id"], 2
        while rec["id"] in ids:
            rec["id"] = f"{base}-{n}"
            n += 1
        ids.add(rec["id"])
    return targets, watch


# ---------------------------------------------------------------- credentials


_CRED_RE = {
    "login": re.compile(r"DATAFORSEO_(?:LOGIN|USERNAME)\s*[:=]\s*[\"']?([^\"'\s#]+)"),
    "password": re.compile(r"DATAFORSEO_PASSWORD\s*[:=]\s*[\"']?([^\"'\s#]+)"),
}


def dataforseo_credentials(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    env = os.environ if env is None else env
    login = env.get("DATAFORSEO_LOGIN") or env.get("DATAFORSEO_USERNAME")
    pw = env.get("DATAFORSEO_PASSWORD")
    if login and pw:
        return login, pw
    f = env.get("DATAFORSEO_CREDENTIALS_FILE")
    if f:
        p = Path(f).expanduser()
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            m1, m2 = _CRED_RE["login"].search(text), _CRED_RE["password"].search(text)
            if m1 and m2:
                return m1.group(1), m2.group(1)
    return None


Transport = Callable[[str, list[dict[str, Any]]], dict[str, Any]]


class DataForSEOClient:
    def __init__(self, credentials: tuple[str, str] | None = None, transport: Transport | None = None):
        self._transport = transport
        self._auth = None
        if transport is None:
            if credentials is None:
                raise RuntimeError("DataForSEO credentials missing")
            self._auth = "Basic " + base64.b64encode(f"{credentials[0]}:{credentials[1]}".encode()).decode()

    def post(self, path: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
        if self._transport is not None:
            return self._transport(path, payload)
        req = urllib.request.Request(
            API + path,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={"Authorization": self._auth or "", "Content-Type": "application/json"},
        )
        last: Exception | None = None
        for i in range(3):
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
                time.sleep(3 * (i + 1))
        raise RuntimeError(f"DataForSEO call failed: {path}: {type(last).__name__}")


# ---------------------------------------------------------------- matching


def strip_www(host: str) -> str:
    h = (host or "").lower().strip().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def host_of(url: str) -> str:
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#:]+)", (url or "").strip(), re.I)
    return strip_www(m.group(1)) if m else ""


_HOSTLIKE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+(/\S*)?$", re.I)


def own_patterns(domain: str, aliases: Iterable[str] = (), extra: Iterable[str] = ()) -> list[str]:
    """Domain-style aliases (reqproof.com, github.com/probelabs/proof). Plain words never count."""
    out: list[str] = []
    for t in [domain, *aliases, *extra]:
        t = re.sub(r"^https?://", "", str(t or "").strip().lower()).rstrip("/")
        t = strip_www(t)
        if t and _HOSTLIKE.match(t) and t not in out:
            out.append(t)
    return out


def is_own_url(url: str, patterns: Iterable[str], domain_field: str | None = None) -> bool:
    host = host_of(url) or strip_www(domain_field or "")
    path = re.sub(r"^[a-z][a-z0-9+.-]*://[^/]+", "", (url or "").strip(), flags=re.I).lower()
    for p in patterns:
        phost, _, ppath = p.partition("/")
        if not (host == phost or host.endswith("." + phost)):
            continue
        if not ppath or path.lstrip("/").startswith(ppath):
            return True
    return False


def brand_text_hits(text: str, brand: str, aliases: Iterable[str], product_form_only: Iterable[str] | None = None) -> list[str]:
    """Brand names in AIO / AI Mode prose. Secondary signal; citations are the main one.

    Uses aeo.mention.extract_brand_mentions with product_form_only when that strict
    matcher is available (PR #10). Otherwise falls back to a conservative match that
    never counts dictionary-word brands (default: "proof") as bare words.
    """
    text = text or ""
    try:  # strict matcher (product-form brands) if present
        from aeo import mention as _m

        if hasattr(_m, "resolve_product_form_only"):
            return list(_m.extract_brand_mentions(text, brand, list(aliases), product_form_only=product_form_only))
    except Exception:  # pragma: no cover - defensive
        pass
    risky = {t.lower() for t in (product_form_only or ["proof"])}
    hits: list[str] = []
    for term in [brand, *aliases]:
        term = str(term or "").strip()
        if not term or term.lower() in risky or term.lower() in {h.lower() for h in hits}:
            continue
        flags = re.I if ("." in term or "/" in term) else 0
        if re.search(r"(?<![\w.\-/])" + re.escape(term) + r"(?![\w\-])", text, flags):
            hits.append(term)
    return hits


def product_form_only_of(raw_cfg: dict[str, Any]) -> list[str] | None:
    bm = raw_cfg.get("brand_match")
    if isinstance(bm, dict) and bm.get("product_form_only") is not None:
        return [str(t) for t in bm.get("product_form_only") or [] if str(t).strip()]
    return None


# ---------------------------------------------------------------- parsing


def _task(raw: dict[str, Any] | None) -> dict[str, Any]:
    tasks = (raw or {}).get("tasks") or [{}]
    t = tasks[0] if isinstance(tasks[0], dict) else {}
    if t.get("status_code") is None and isinstance(raw, dict) and raw.get("status_code") not in (None, STATUS_OK):
        # Request-level failure (e.g. 50000 "Internal Server Error.") comes back with tasks: null;
        # surface the envelope code/message instead of reporting "None".
        t = dict(t, status_code=raw.get("status_code"), status_message=raw.get("status_message"))
    return t


def task_status(raw: dict[str, Any] | None) -> int | None:
    t = _task(raw)
    code = t.get("status_code")
    return int(code) if code is not None else None


def usable(raw: dict[str, Any] | None) -> bool:
    t = _task(raw)
    return t.get("status_code") in (STATUS_OK, STATUS_PARTIAL) and bool(t.get("result"))


def _collect_refs(node: Any, out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "references" and isinstance(v, list):
                out.extend(r for r in v if isinstance(r, dict) and r.get("url"))
            else:
                _collect_refs(v, out)
    elif isinstance(node, list):
        for x in node:
            _collect_refs(x, out)
    return out


def _norm_refs(raw_refs: list[dict[str, Any]], patterns: list[str], inline_text: str | None = None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []

    def add(url: str, domain: str | None, title: Any, source: Any) -> None:
        key = url.split("#")[0]
        if key in seen:
            return
        seen.add(key)
        dom = strip_www(domain or "") or host_of(url)
        out.append(
            {
                "position": len(out) + 1,
                "domain": dom,
                "url": url,
                "title": title,
                "source": source,
                "own": is_own_url(url, patterns, dom),
            }
        )

    for r in raw_refs:
        add(str(r["url"]), r.get("domain"), r.get("title"), r.get("source"))
    if inline_text:
        for u in re.findall(r"\]\((https?://[^)\s]+)\)", inline_text):
            d = host_of(u)
            if "google." in d or d.endswith("dataforseo.com"):
                continue
            add(u, d, None, "inline_link")
    return out


def _error(raw: dict[str, Any] | None, fallback: str = "missing") -> dict[str, Any]:
    t = _task(raw)
    if not raw:
        return {"ok": False, "error": fallback}
    return {"ok": False, "status_code": t.get("status_code"), "error": f"{t.get('status_code')} {t.get('status_message') or ''}".strip()}


def analyze_serp(raw: dict[str, Any] | None, ctx: dict[str, Any], *, full: bool = True) -> dict[str, Any]:
    """One organic live/advanced response -> record. full=False drops AIO text (watch tier)."""
    if not usable(raw):
        return _error(raw)
    t = _task(raw)
    res = t["result"][0]
    items = res.get("items") or []
    aio = [it for it in items if it.get("type") == "ai_overview"]
    aio_text = "\n\n".join((it.get("markdown") or it.get("text") or "") for it in aio).strip()
    refs = _norm_refs(_collect_refs(aio, []), ctx["patterns"]) if aio else []
    organic = [it for it in items if it.get("type") == "organic"]
    own = [it for it in organic if is_own_url(it.get("url") or "", ctx["patterns"], it.get("domain"))]
    top10 = [
        {
            "rank": it.get("rank_group"),
            "title": it.get("title"),
            "url": it.get("url"),
            "domain": strip_www(it.get("domain") or "") or host_of(it.get("url") or ""),
            "own": is_own_url(it.get("url") or "", ctx["patterns"], it.get("domain")),
        }
        for it in organic[:10]
    ]
    own_rank = min(int(it.get("rank_group") or 10**6) for it in own) if own else None
    rec: dict[str, Any] = {
        "ok": True,
        "status_code": t.get("status_code"),
        "partial": t.get("status_code") == STATUS_PARTIAL,
        "se_datetime": res.get("datetime"),
        "check_url": res.get("check_url"),
        "organic_count": len(organic),
        "aio_present": bool(aio),
        "aio_references": refs,
        "aio_ref_domains": list(dict.fromkeys(r["domain"] for r in refs)),
        "own_cited": any(r["own"] for r in refs),
        "own_text_hits": brand_text_hits(aio_text, ctx["brand"], ctx["aliases"], ctx.get("product_form_only")),
        "top10": top10,
        "own_rank": own_rank,
        "own_urls": [{"rank": it.get("rank_group"), "url": it.get("url")} for it in own],
    }
    if full:
        rec["aio_text"] = aio_text
    return rec


def analyze_ai_mode(raw: dict[str, Any] | None, ctx: dict[str, Any]) -> dict[str, Any]:
    if not usable(raw):
        return _error(raw)
    items = _task(raw)["result"][0].get("items") or []
    text = "\n\n".join((it.get("markdown") or it.get("text") or "") for it in items).strip()
    refs = _norm_refs(_collect_refs(items, []), ctx["patterns"], text)
    return {
        "ok": True,
        "present": bool(items),
        "text": text,
        "references": refs,
        "ref_domains": list(dict.fromkeys(r["domain"] for r in refs)),
        "own_cited": any(r["own"] for r in refs),
        "own_text_hits": brand_text_hits(text, ctx["brand"], ctx["aliases"], ctx.get("product_form_only")),
    }


# ---------------------------------------------------------------- cost + fetching


def serp_call_cost(depth: int) -> float:
    return round(PRICE_PER_PAGE * math.ceil(max(1, int(depth)) / 10) + PRICE_ASYNC_AIO, 4)


def plan_calls(targets: list[dict[str, Any]], watch: list[dict[str, Any]], s: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for q in targets:
        calls.append({"search": q, "kind": s["device"], "depth": int(s["depth"]), "est": serp_call_cost(s["depth"])})
        if s.get("mobile") and s["device"] != "mobile":
            calls.append({"search": q, "kind": "mobile", "depth": int(s["mobile_depth"]), "est": serp_call_cost(s["mobile_depth"])})
        if s.get("ai_mode"):
            calls.append({"search": q, "kind": "ai_mode", "depth": None, "est": PRICE_AI_MODE})
    for q in watch:
        calls.append({"search": q, "kind": s["device"], "depth": int(s["watch_depth"]), "est": serp_call_cost(s["watch_depth"])})
    return calls


def estimate_cost(targets: list[dict[str, Any]], watch: list[dict[str, Any]], s: dict[str, Any]) -> dict[str, Any]:
    calls = plan_calls(targets, watch, s)
    t = sum(c["est"] for c in calls if c["search"]["tier"] == TARGET_TIER)
    w = sum(c["est"] for c in calls if c["search"]["tier"] == WATCH_TIER)
    return {
        "calls": len(calls),
        "targets": len(targets),
        "watch": len(watch),
        "targets_usd": round(t, 4),
        "watch_usd": round(w, 4),
        "total_usd": round(t + w, 4),
        "cap_usd": float(s["max_cost_usd"]),
        "note": "upper bound before retries; partial (40106) pages are not billed",
    }


class Budget:
    def __init__(self, cap: float):
        self.cap = float(cap)
        self.spent = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def reserve(self, est: float) -> bool:
        with self._lock:
            return self.spent + est <= self.cap + 1e-9

    def add(self, cost: float) -> None:
        with self._lock:
            self.spent += float(cost or 0)
            self.calls += 1


def _call_cost(raw: dict[str, Any] | None) -> float:
    if not raw:
        return 0.0
    c = raw.get("cost")
    if c is None:
        c = _task(raw).get("cost")
    return float(c or 0)


def _rank(raw: dict[str, Any] | None) -> int:
    code = task_status(raw)
    if code == STATUS_OK and _task(raw).get("result"):
        return 2
    return 1 if usable(raw) else 0


def fetch_with_retries(
    client: DataForSEOClient,
    path: str,
    payload: dict[str, Any],
    budget: Budget,
    est: float,
    *,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[dict[str, Any] | None, list[int | None], bool]:
    """Retry 40101 and other task errors; accept 40106 partial after the last try.

    Returns (best response, status codes seen, capped).
    """
    best: dict[str, Any] | None = None
    codes: list[int | None] = []
    for attempt in range(max(1, retries)):
        if not budget.reserve(est):
            return best, codes, True
        try:
            r = client.post(path, [payload])
        except RuntimeError as e:
            r = {"tasks": [{"status_code": None, "status_message": str(e)}]}
        budget.add(_call_cost(r))
        codes.append(task_status(r))
        if best is None or _rank(r) > _rank(best):
            best = r
        if _rank(best) == 2:
            break
        if attempt < retries - 1:
            sleep(5 + 10 * attempt)
    return best, codes, False


def _payload(q: dict[str, Any], kind: str, depth: int | None, s: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    base = {"keyword": q["query"][:700], "location_code": int(s["location_code"]), "language_code": str(s["language_code"]), "tag": q["id"]}
    if kind == "ai_mode":
        return AI_MODE_PATH, dict(base, device="desktop")
    return SERP_PATH, dict(base, device=kind, depth=int(depth or 10), load_async_ai_overview=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _log_default(msg: str) -> None:
    import sys

    print(msg, file=sys.stderr, flush=True)


def run_google_layer(
    raw_cfg: dict[str, Any],
    out_dir: str | Path,
    *,
    client: DataForSEOClient,
    settings: dict[str, Any] | None = None,
    baseline: dict[str, Any] | None = None,
    baseline_path: str | None = None,
    log: Callable[[str], None] = _log_default,
    sleep: Callable[[float], None] = time.sleep,
    reuse_raw: bool = False,
    retry_failed: bool = False,
) -> dict[str, Any] | None:
    """Fetch every google_targets / google_watch search and write google.json + google.md.

    Returns None (and writes nothing) when there is nothing approved to check or the
    pre-flight estimate exceeds the cap.
    """
    s = settings or load_settings(raw_cfg)
    targets, watch = config_searches(raw_cfg)
    if not targets and not watch:
        log("google: no google_targets/google_watch in config; nothing to check")
        return None
    est = estimate_cost(targets, watch, s)
    log(
        f"google: {est['targets']} target(s) + {est['watch']} watch search(es), {est['calls']} call(s); "
        f"estimated max ${est['total_usd']:.3f} (targets ${est['targets_usd']:.3f}, watch ${est['watch_usd']:.3f}); cap ${est['cap_usd']:.2f}"
    )
    if est["total_usd"] > est["cap_usd"] and not reuse_raw:
        log(f"google: estimate ${est['total_usd']:.3f} exceeds cap ${est['cap_usd']:.2f}; skipped before any API call")
        return None
    out = Path(out_dir)
    ctx = {
        "brand": str(raw_cfg.get("brand") or ""),
        "aliases": [str(a) for a in raw_cfg.get("aliases") or []],
        "product_form_only": product_form_only_of(raw_cfg),
        "patterns": own_patterns(str(raw_cfg.get("domain") or ""), raw_cfg.get("aliases") or [], s["own_url_patterns"]),
    }
    budget = Budget(s["max_cost_usd"])
    calls = plan_calls(targets, watch, s)
    raw_by: dict[tuple[str, str], dict[str, Any] | None] = {}
    meta_by: dict[tuple[str, str], dict[str, Any]] = {}
    lock = threading.Lock()

    def raw_path(qid: str, kind: str) -> Path:
        return out / "raw" / kind / f"{qid}.json"

    def job(c: dict[str, Any]) -> None:
        q, kind = c["search"], c["kind"]
        rp = raw_path(q["id"], kind)
        prev = None
        if (reuse_raw or retry_failed) and rp.exists():
            try:
                prev = json.loads(rp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                prev = None
        if reuse_raw or (retry_failed and usable(prev)):
            r = prev
            codes, capped = [task_status(r)] if r else [], False
        else:
            path, payload = _payload(q, kind, c["depth"], s)
            r, codes, capped = fetch_with_retries(client, path, payload, budget, c["est"], retries=int(s["retries"]), sleep=sleep)
            if r is not None and not usable(r) and kind != "ai_mode" and (c["depth"] or 10) > 10 and not capped:
                # deep pagination keeps failing: settle for AIO + top 10 rather than nothing
                path, payload = _payload(q, kind, 10, s)
                r2, codes2, capped = fetch_with_retries(client, path, payload, budget, serp_call_cost(10), retries=2, sleep=sleep)
                codes += codes2
                if _rank(r2) > _rank(r):
                    r = r2
            if r is not None:
                rp.parent.mkdir(parents=True, exist_ok=True)
                rp.write_text(json.dumps(r, ensure_ascii=False), encoding="utf-8")
        with lock:
            raw_by[(q["id"], kind)] = r
            meta_by[(q["id"], kind)] = {"status_codes": codes, "capped": capped}

    workers = max(1, int(s["workers"]))
    if workers == 1:
        for c in calls:
            job(c)
    else:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(job, calls))

    searches: list[dict[str, Any]] = []
    for q in targets + watch:
        rec = dict(q)
        full = q["tier"] == TARGET_TIER
        kinds = [s["device"]] + (["mobile"] if full and s.get("mobile") and s["device"] != "mobile" else [])
        for kind in kinds:
            r = raw_by.get((q["id"], kind))
            a = analyze_serp(r, ctx, full=full) if r else {"ok": False, "error": "skipped: spending cap" if meta_by.get((q["id"], kind), {}).get("capped") else "missing"}
            a.update({k: v for k, v in meta_by.get((q["id"], kind), {}).items() if k == "status_codes"})
            rec["serp" if kind == s["device"] else kind] = a
        if full and s.get("ai_mode"):
            r = raw_by.get((q["id"], "ai_mode"))
            rec["ai_mode"] = analyze_ai_mode(r, ctx) if r else {"ok": False, "error": "skipped: spending cap" if meta_by.get((q["id"], "ai_mode"), {}).get("capped") else "missing"}
        searches.append(rec)

    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "brand": ctx["brand"],
        "domain": str(raw_cfg.get("domain") or ""),
        "own_url_patterns": ctx["patterns"],
        "settings": {k: s[k] for k in ("location_code", "language_code", "device", "depth", "watch_depth", "mobile", "mobile_depth", "ai_mode", "max_cost_usd")},
        "estimate": est,
        "cost_usd": round(budget.spent, 4),
        "api_calls": budget.calls,
        **_prior_spend(out, budget, retry_failed),
        "searches": searches,
    }
    doc["summary"] = summarize(doc)
    if baseline:
        doc["baseline"] = {"path": baseline_path, "generated_at": baseline.get("generated_at")}
        apply_deltas(doc, baseline)
    write_google_doc(doc, out)
    log(f"google: wrote {out / 'google.json'} (spent ${doc['cost_usd']:.3f} in {doc['api_calls']} call(s))")
    return doc


def _prior_spend(out: Path, budget: Budget, retry_failed: bool) -> dict[str, Any]:
    """--retry-failed keeps earlier good responses; fold the earlier run's spend into the totals."""
    if not retry_failed:
        return {}
    try:
        prev = json.loads((out / "google.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    pc, pn = float(prev.get("cost_usd") or 0), int(prev.get("api_calls") or 0)
    return {
        "cost_usd": round(pc + budget.spent, 4),
        "api_calls": pn + budget.calls,
        "retry_failed": {"cost_usd": round(budget.spent, 4), "api_calls": budget.calls, "previous_cost_usd": pc, "previous_api_calls": pn},
    }


def summarize(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for tier in (TARGET_TIER, WATCH_TIER):
        rows = [x for x in doc.get("searches") or [] if x.get("tier") == tier]
        ok = [x for x in rows if (x.get("serp") or {}).get("ok")]
        aio = [x for x in ok if x["serp"].get("aio_present")]
        out[tier] = {
            "searches": len(rows),
            "ok": len(ok),
            "aio_present": len(aio),
            "own_cited": sum(1 for x in aio if x["serp"].get("own_cited")),
            "own_top10": sum(1 for x in ok if (x["serp"].get("own_rank") or 999) <= 10),
            "own_top100": sum(1 for x in ok if x["serp"].get("own_rank")),
            "ai_mode_own_cited": sum(1 for x in rows if (x.get("ai_mode") or {}).get("own_cited")),
        }
    ok = [x for x in doc.get("searches") or [] if (x.get("serp") or {}).get("ok")]
    out["top_aio_domains"] = Counter(d for x in ok for d in x["serp"].get("aio_ref_domains") or []).most_common(15)
    out["top_top10_domains"] = Counter(d for x in ok for d in dict.fromkeys(r["domain"] for r in x["serp"].get("top10") or [])).most_common(15)
    return out


def write_google_doc(doc: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "google.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    from aeo.layers import render_google_markdown

    (out / "google.md").write_text(render_google_markdown(doc), encoding="utf-8")
    return p


# ---------------------------------------------------------------- deltas + baseline


def _domains(rows: Iterable[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(r.get("domain") for r in rows if r.get("domain")))


def search_delta(cur: dict[str, Any], prev: dict[str, Any] | None) -> dict[str, Any]:
    if prev is None:
        return {"new_search": True}
    c, p = cur.get("serp") or {}, prev.get("serp") or {}
    if not c.get("ok") or not p.get("ok"):
        return {"comparable": False}
    d: dict[str, Any] = {"rank_prev": p.get("own_rank"), "rank_now": c.get("own_rank")}
    if d["rank_prev"] and d["rank_now"]:
        d["rank_move"] = int(d["rank_prev"]) - int(d["rank_now"])  # positive = moved up
    elif d["rank_now"] and not d["rank_prev"]:
        d["rank_change"] = "entered top 100"
    elif d["rank_prev"] and not d["rank_now"]:
        d["rank_change"] = "dropped out of top 100"
    if bool(c.get("own_cited")) != bool(p.get("own_cited")):
        d["citation"] = "gained" if c.get("own_cited") else "lost"
    if bool(c.get("aio_present")) != bool(p.get("aio_present")):
        d["aio"] = "appeared" if c.get("aio_present") else "disappeared"
    cr, pr = c.get("aio_ref_domains") or [], p.get("aio_ref_domains") or []
    d["new_aio_domains"] = [x for x in cr if x not in pr]
    d["dropped_aio_domains"] = [x for x in pr if x not in cr]
    ct, pt = _domains(c.get("top10") or []), _domains(p.get("top10") or [])
    d["new_top10_domains"] = [x for x in ct if x not in pt]
    d["dropped_top10_domains"] = [x for x in pt if x not in ct]
    return d


def apply_deltas(doc: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    prev = {norm_query(x.get("query", "")): x for x in baseline.get("searches") or []}
    for x in doc.get("searches") or []:
        x["delta"] = search_delta(x, prev.get(norm_query(x.get("query", ""))))
    return doc


def discover_baseline(out_dir: str | Path, domain: str, before: str | None = None, name: str = "google.json") -> Path | None:
    """Latest earlier layer file for the same domain among sibling runs."""
    out = Path(out_dir).resolve()
    # <runs>/<run>/google -> scan <runs>/*/google; <runs>/<stem>.google or <data>/google/<ts> -> scan siblings
    if out.name == "google":
        roots, pats = [out.parent.parent], (f"*/google/{name}",)
    else:
        roots, pats = [out.parent], (f"*/{name}",)
    best: tuple[str, Path] | None = None
    for root in roots:
        if not root.is_dir():
            continue
        for pat in pats:
            for p in root.glob(pat):
                if p.resolve().parent == out:
                    continue
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                ts = str(d.get("generated_at") or "")
                if str(d.get("domain") or "").lower() != (domain or "").lower() or not ts:
                    continue
                if before and ts >= before:
                    continue
                if best is None or ts > best[0]:
                    best = (ts, p)
    return best[1] if best else None


# ---------------------------------------------------------------- proposals (never auto-approved)


def proposals_path(config_path: str | Path) -> Path:
    p = Path(config_path)
    return p.with_name(f"{p.stem}.google-proposals.json")


def proposal_prompt(raw_cfg: dict[str, Any], n_targets: int = 10, n_watch: int = 20) -> str:
    prompts = [str(p.get("text")) for p in raw_cfg.get("prompts") or [] if isinstance(p, dict) and p.get("text")][:40]
    lines = [
        "You pick Google searches for an SEO tracker. Return JSON only.",
        f"Product: {raw_cfg.get('brand')} ({raw_cfg.get('domain')}).",
    ]
    if raw_cfg.get("description"):
        lines.append(f"Description: {raw_cfg['description']}")
    if raw_cfg.get("competitors"):
        lines.append("Known competitors: " + ", ".join(map(str, raw_cfg["competitors"][:40])))
    if prompts:
        lines.append("Questions buyers ask AI assistants about this space:")
        lines += [f"- {t}" for t in prompts]
    lines += [
        "",
        f"Propose {n_targets} 'targets' and up to {n_watch} 'watch' Google searches.",
        "Rules: short real searches people type into Google (2-6 words, lowercase, no question marks),",
        "buying or problem intent where this product could be the answer, no brand names of this product,",
        "competitor names allowed only as '<competitor> alternatives'. Targets = highest-value; watch = broader.",
        'Shape: {"targets":[{"query":"...","why":"..."}],"watch":[{"query":"...","why":"..."}]}',
    ]
    return "\n".join(lines)


def _parse_json_blob(text: str) -> dict[str, Any] | None:
    text = text or ""
    try:
        outer = json.loads(text)
        if isinstance(outer, dict) and isinstance(outer.get("result"), str):
            text = outer["result"]
        elif isinstance(outer, dict) and ("targets" in outer or "watch" in outer):
            return outer
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def claude_llm(prompt: str, timeout: int = 180) -> str:
    """Same headless Claude call scripts/judge_run.py uses (no tools)."""
    cmd = ["claude", "-p", "--tools", "", "--output-format", "json", "--", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return proc.stdout or proc.stderr or ""


def _clean_suggestions(items: Any, limit: int, skip: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in items or []:
        q = it.get("query") if isinstance(it, dict) else it
        q = re.sub(r"\s+", " ", str(q or "")).strip().strip("?")
        if not q or len(q.split()) > 10 or norm_query(q) in skip:
            continue
        skip.add(norm_query(q))
        rec = {"query": q}
        if isinstance(it, dict) and it.get("why"):
            rec["why"] = str(it["why"])
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def propose_searches(raw_cfg: dict[str, Any], llm: Callable[[str], str] = claude_llm) -> dict[str, list[dict[str, Any]]]:
    """LLM-drafted candidates: 10 targets + up to 20 watch. Callers must store them as unapproved."""
    blob = _parse_json_blob(llm(proposal_prompt(raw_cfg))) or {}
    t, w = config_searches(raw_cfg)
    skip = {norm_query(x["query"]) for x in t + w}
    targets = _clean_suggestions(blob.get("targets"), 10, skip)
    watch = _clean_suggestions(blob.get("watch"), 20, skip)
    return {"google_targets": targets, "google_watch": watch}


def write_proposals(path: str | Path, **sections: list[dict[str, Any]]) -> Path:
    """Merge suggestion sections into the proposals file. Always status=unapproved."""
    p = Path(path)
    doc: dict[str, Any] = {}
    if p.exists():
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            doc = {}
    doc.update(
        {
            "schema_version": PROPOSALS_SCHEMA_VERSION,
            "status": "unapproved",
            "approved": False,
            "updated_at": _now_iso(),
            "note": "Suggestions only. Nothing here is checked until you copy the searches you want into google_targets / google_watch in the config.",
        }
    )
    for k, v in sections.items():
        if v is not None:
            doc[k] = v
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return p
