"""Search Console layer: last 28 days vs the prior 28 days for the product's GSC property.

Separate from the LLM board: never touches evidence or board scores. Writes gsc.json
(+ gsc.md) into the same layer directory as google.json.

Credentials (first match wins; nothing is printed):
  1. GSC_CREDENTIALS_FILE: Google `authorized_user` JSON (client_id, client_secret,
     refresh_token), or a `service_account` key (needs the optional google-auth package).
  2. GSC_OAUTH_TOKEN_FILE (default ~/.gsc-mcp/oauth-token.json, the token written by
     `npx suganthan-gsc-mcp setup`) with GSC_OAUTH_SECRETS_FILE (the Google OAuth
     client JSON) to refresh it. A still-valid access_token in the token file is used as is.
No credentials -> the layer is skipped silently. Read-only scope is enough
(https://www.googleapis.com/auth/webmasters.readonly).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from aeo.google import config_searches, norm_query, write_proposals

SCHEMA_VERSION = "aeo-gsc-v1"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/webmasters/v3/sites/{site}/searchAnalytics/query"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
DEFAULT_TOKEN_FILE = "~/.gsc-mcp/oauth-token.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "enabled": True,
    "site": None,  # default sc-domain:<domain>
    "window_days": 28,
    "lag_days": 3,  # GSC data settles ~2-3 days late
    "row_limit": 5000,
    "top_n": 25,
    "min_impressions": 5,
    "striking_min_position": 5.0,
    "striking_max_position": 20.0,
    "suggest_limit": 20,
}


def load_settings(raw_cfg: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    s = dict(DEFAULT_SETTINGS)
    block = raw_cfg.get("gsc") if isinstance(raw_cfg.get("gsc"), dict) else {}
    for k, v in block.items():
        if k in DEFAULT_SETTINGS and v is not None:
            s[k] = v
    for k, v in overrides.items():
        if v is not None:
            s[k] = v
    if not s.get("site"):
        s["site"] = f"sc-domain:{str(raw_cfg.get('domain') or '').lower()}"
    return s


# ---------------------------------------------------------------- credentials


def _read_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return d if isinstance(d, dict) else None


def _client_pair(d: dict[str, Any] | None) -> tuple[str, str] | None:
    if not d:
        return None
    c = d.get("installed") or d.get("web") or d
    if c.get("client_id") and c.get("client_secret"):
        return str(c["client_id"]), str(c["client_secret"])
    return None


class TokenSource:
    """Holds a refresh_token (or a fixed access token) and returns a bearer token."""

    def __init__(self, *, refresh: tuple[str, str, str] | None = None, access_token: str | None = None,
                 expires_at: float = 0.0, google_credentials: Any = None, origin: str = ""):
        self._refresh = refresh
        self._access = access_token
        self._exp = expires_at
        self._gcreds = google_credentials
        self.origin = origin

    def token(self, post: Callable[[str, bytes], dict[str, Any]] | None = None) -> str:
        if self._gcreds is not None:  # service account via google-auth
            from google.auth.transport.requests import Request  # type: ignore

            if not self._gcreds.valid:
                self._gcreds.refresh(Request())
            return self._gcreds.token
        if self._access and self._exp - 60 > time.time():
            return self._access
        if not self._refresh:
            raise RuntimeError("GSC access token expired and no refresh credentials")
        cid, secret, rt = self._refresh
        body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "refresh_token": rt, "grant_type": "refresh_token"}).encode()
        r = (post or _http_post_form)(TOKEN_URL, body)
        self._access = r["access_token"]
        self._exp = time.time() + float(r.get("expires_in") or 3000)
        return self._access


def _http_post_form(url: str, body: bytes) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def gsc_credentials(env: dict[str, str] | None = None) -> TokenSource | None:
    env = os.environ if env is None else env
    f = env.get("GSC_CREDENTIALS_FILE")
    if f:
        d = _read_json(f)
        if d and d.get("type") == "service_account":
            try:
                from google.oauth2 import service_account  # type: ignore

                creds = service_account.Credentials.from_service_account_file(str(Path(f).expanduser()), scopes=[SCOPE])
                return TokenSource(google_credentials=creds, origin="GSC_CREDENTIALS_FILE (service_account)")
            except Exception:
                return None
        pair = _client_pair(d)
        if d and pair and d.get("refresh_token"):
            return TokenSource(refresh=(pair[0], pair[1], str(d["refresh_token"])), origin="GSC_CREDENTIALS_FILE (authorized_user)")
    tok = _read_json(env.get("GSC_OAUTH_TOKEN_FILE") or DEFAULT_TOKEN_FILE)
    if not tok:
        return None
    pair = _client_pair(_read_json(env["GSC_OAUTH_SECRETS_FILE"])) if env.get("GSC_OAUTH_SECRETS_FILE") else _client_pair(tok)
    exp = float(tok.get("expiry_date") or 0) / 1000.0  # ms epoch (googleapis node format)
    refresh = (pair[0], pair[1], str(tok["refresh_token"])) if pair and tok.get("refresh_token") else None
    if refresh is None and not (tok.get("access_token") and exp - 60 > time.time()):
        return None
    return TokenSource(refresh=refresh, access_token=tok.get("access_token"), expires_at=exp, origin="GSC_OAUTH_TOKEN_FILE")


# ---------------------------------------------------------------- API


Query = Callable[[str, dict[str, Any]], dict[str, Any]]


def http_query(tokens: TokenSource) -> Query:
    def q(site: str, body: dict[str, Any]) -> dict[str, Any]:
        url = API.format(site=urllib.parse.quote(site, safe=""))
        last: Exception | None = None
        for i in range(3):
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": "Bearer " + tokens.token(), "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return json.loads(r.read())
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    raise RuntimeError(f"Search Console {e.code} for {site}") from None
                last = e
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
            time.sleep(3 * (i + 1))
        raise RuntimeError(f"Search Console query failed: {type(last).__name__}")

    return q


def windows(today: date, window_days: int = 28, lag_days: int = 3) -> dict[str, dict[str, str]]:
    end = today - timedelta(days=lag_days)
    start = end - timedelta(days=window_days - 1)
    pend = start - timedelta(days=1)
    pstart = pend - timedelta(days=window_days - 1)
    return {"current": {"start": start.isoformat(), "end": end.isoformat()},
            "previous": {"start": pstart.isoformat(), "end": pend.isoformat()}}


def _rows(resp: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in resp.get("rows") or []:
        out.append({
            "key": (r.get("keys") or [""])[0] if r.get("keys") else "",
            "clicks": int(r.get("clicks") or 0),
            "impressions": int(r.get("impressions") or 0),
            "ctr": round(float(r.get("ctr") or 0), 4),
            "position": round(float(r.get("position") or 0), 1),
        })
    return out


def _metrics(r: dict[str, Any] | None) -> dict[str, Any] | None:
    if not r:
        return None
    return {k: r[k] for k in ("clicks", "impressions", "ctr", "position")}


def _change(cur: dict[str, Any] | None, prev: dict[str, Any] | None) -> dict[str, Any]:
    c, p = cur or {}, prev or {}
    out: dict[str, Any] = {}
    for k in ("clicks", "impressions"):
        out[k] = int(c.get(k) or 0) - int(p.get(k) or 0)
    out["ctr"] = round(float(c.get("ctr") or 0) - float(p.get("ctr") or 0), 4)
    if c.get("position") and p.get("position"):
        out["position"] = round(float(p["position"]) - float(c["position"]), 1)  # positive = moved up
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_gsc_layer(
    raw_cfg: dict[str, Any],
    out_dir: str | Path,
    *,
    query: Query,
    settings: dict[str, Any] | None = None,
    today: date | None = None,
    baseline: dict[str, Any] | None = None,
    baseline_path: str | None = None,
    proposals_file: str | Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    s = settings or load_settings(raw_cfg)
    site = str(s["site"])
    w = windows(today or datetime.now(timezone.utc).date(), int(s["window_days"]), int(s["lag_days"]))
    limit = int(s["row_limit"])

    def fetch(period: str, dims: list[str], n: int) -> list[dict[str, Any]]:
        body: dict[str, Any] = {"startDate": w[period]["start"], "endDate": w[period]["end"], "rowLimit": n}
        if dims:
            body["dimensions"] = dims
        return _rows(query(site, body))

    def total(period: str) -> dict[str, Any]:
        rows = fetch(period, [], 1)
        return _metrics(rows[0]) if rows else {"clicks": 0, "impressions": 0, "ctr": 0.0, "position": 0.0}

    tot_c, tot_p = total("current"), total("previous")
    q_c, q_p = fetch("current", ["query"], limit), fetch("previous", ["query"], limit)
    pg_c, pg_p = fetch("current", ["page"], min(limit, 1000)), fetch("previous", ["page"], min(limit, 1000))
    top_n, min_imp = int(s["top_n"]), int(s["min_impressions"])
    qp = {norm_query(r["key"]): r for r in q_p}
    pp = {r["key"]: r for r in pg_p}

    def with_prev(rows: list[dict[str, Any]], prev: dict[str, dict[str, Any]], keyf: Callable[[str], str]) -> list[dict[str, Any]]:
        out = []
        for r in rows:
            pr = prev.get(keyf(r["key"]))
            out.append(dict(r, previous=_metrics(pr), change=_change(r, pr)))
        return out

    top_queries = with_prev(sorted(q_c, key=lambda r: (-r["clicks"], -r["impressions"]))[:top_n], qp, norm_query)
    top_pages = with_prev(sorted(pg_c, key=lambda r: (-r["clicks"], -r["impressions"]))[:top_n], pp, lambda k: k)
    striking = [r for r in q_c if s["striking_min_position"] <= r["position"] <= s["striking_max_position"] and r["impressions"] >= min_imp]
    striking = with_prev(sorted(striking, key=lambda r: -r["impressions"])[:top_n], qp, norm_query)
    zero = with_prev(sorted([r for r in q_c if r["clicks"] == 0 and r["impressions"] >= min_imp], key=lambda r: -r["impressions"])[:top_n], qp, norm_query)

    targets, watch = config_searches(raw_cfg)
    qc = {norm_query(r["key"]): r for r in q_c}
    searches = []
    for x in targets + watch:
        k = norm_query(x["query"])
        cur, prev = qc.get(k), qp.get(k)
        searches.append({"id": x["id"], "query": x["query"], "tier": x["tier"], "current": _metrics(cur),
                         "previous": _metrics(prev), "change": _change(cur, prev) if (cur or prev) else None})

    tracked = {norm_query(x["query"]) for x in targets + watch}
    suggestions = [
        {"query": r["key"], "impressions": r["impressions"], "clicks": r["clicks"], "position": r["position"], "source": "gsc"}
        for r in sorted(q_c, key=lambda r: -r["impressions"])
        if r["impressions"] >= min_imp and norm_query(r["key"]) not in tracked and len(r["key"].split()) <= 10
    ][: int(s["suggest_limit"])]

    doc: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "brand": str(raw_cfg.get("brand") or ""),
        "domain": str(raw_cfg.get("domain") or ""),
        "site": site,
        "windows": w,
        "totals": {"current": tot_c, "previous": tot_p, "change": _change(tot_c, tot_p)},
        "query_rows": len(q_c),
        "top_queries": top_queries,
        "top_pages": top_pages,
        "striking_distance": striking,
        "impressions_no_clicks": zero,
        "searches": searches,
        "watch_suggestions": suggestions,
        "settings": {k: s[k] for k in ("window_days", "lag_days", "min_impressions", "striking_min_position", "striking_max_position")},
    }
    if baseline:
        doc["baseline"] = {"path": baseline_path, "generated_at": baseline.get("generated_at"),
                           "windows": baseline.get("windows")}
        bt = (baseline.get("totals") or {}).get("current")
        doc["totals"]["vs_previous_run"] = _change(tot_c, bt) if bt else None
        bs = {norm_query(x.get("query", "")): x for x in baseline.get("searches") or []}
        for x in searches:
            b = bs.get(norm_query(x["query"]))
            x["vs_previous_run"] = _change(x["current"], b.get("current")) if b and (x["current"] or b.get("current")) else None
    if proposals_file is not None and suggestions:
        write_proposals(proposals_file, gsc_watch_suggestions=suggestions)
    write_gsc_doc(doc, out_dir)
    if log:
        log(f"gsc: {site} {w['current']['start']}..{w['current']['end']} clicks {tot_c['clicks']} impressions {tot_c['impressions']}; wrote {Path(out_dir) / 'gsc.json'}")
    return doc


def write_gsc_doc(doc: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "gsc.json"
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(p)
    from aeo.layers import render_gsc_markdown

    (out / "gsc.md").write_text(render_gsc_markdown(doc), encoding="utf-8")
    return p
