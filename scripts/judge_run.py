#!/usr/bin/env python3.11
"""Post-run LLM judge over AEO evidence.

1. Stance/position on brand_mentioned cells → judge.json
2. Vendor extract on every completed arm (hits and misses) → vendors_judged.json
3. Board brief → board.json

Config `competitors` are alias hints, not a ceiling.
"""
from __future__ import annotations

import json, os, re, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from aeo.vendors import (  # noqa: E402
    completed_cells,
    load_vendor_store,
    normalize_vendor_cell,
    workspace_from_docs,
)

STANCE = {"recommend", "mention", "warn", "reject"}
POSITION = {"first", "among", "last", "aside"}
ENGINES = ("claude", "codex", "grok")
BRAND = os.environ.get("AEO_BRAND") or "Tyk"

JUDGE_PROMPT = """You classify how an answer talks about the brand {brand}.
Return ONLY JSON with keys:
stance: recommend | mention | warn | reject
position: first | among | last | aside
ahead: array of vendor names ranked above {brand} (empty if first or aside)
quote: <=40 words copied from the answer (the testimony)
confidence: number 0 to 1

Definitions:
- recommend: pushed as something to use
- mention: named, not pushed
- warn: named with a real caveat
- reject: do not use it
- first: lead pick
- among: shortlist, not first
- last: leftover / if you must
- aside: passing name, not in the ranking

Query:
{query}

Answer:
{answer}
"""

BOARD_PROMPT = """You are the board judge for an AEO run. Brand: {brand}.
You see COUNTS and SAMPLE HITS, not a sales brief. Write 5 to 7 actions a product/content person should take next.
Return ONLY JSON:
{{
  "headline": "one sentence",
  "actions": [
    {{"title": "imperative <=12 words", "why": "what the numbers showed", "do": "concrete next step", "evidence": "ids or engines"}}
  ]
}}
Rules:
- Prefer gaps: named but last/aside/reject; classes with 0 mentions; engines that never search; vendors always ahead of {brand}.
- Do not invent pages or features. Do not mention Tyk marketing slogans.
- No more than 7 actions. Rank by expected AEO lift.

COUNTS:
{counts}

SAMPLE HITS (stance/position/quote):
{samples}
"""

VENDOR_PROMPT = """You extract product, vendor, and tool names from an answer (and optional search-query strings).
Brand to exclude: {brand} (aliases: {aliases})
Return ONLY JSON:
{{
  "vendors": [
    {{"raw": "as written in the answer", "normalized": "stable product name", "role": "recommend|mention|warn|reject|aside"}}
  ],
  "query_vendors": [
    {{"raw": "as written in a search query", "normalized": "stable product name", "role": "mention"}}
  ],
  "confidence": 0.0
}}

Rules:
- Only real product/vendor/SaaS/library names. Not generic categories ("email verification", "API gateway").
- Do not include {brand} or its aliases.
- normalized: well-known spelling (Kickbox, UserCheck, IPQualityScore). Strip Inc/Ltd/Labs/API/.com/.io.
- One object per vendor. Merge domains and legal suffixes into that name.
- role: recommend=pushed as a fit; mention=named; warn=caveat; reject=do not use; aside=passing.
- query_vendors: names that appear in the search-query strings only. Empty array if none or not a search arm.
- Empty vendors is valid when the answer names no products.

Optional bootstrap names (hints, not a ceiling — extract others too):
{hints}

Query:
{query}

Answer:
{answer}

Search queries:
{queries}
"""


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def hits(doc: dict, engine: str) -> list[dict]:
    out = []
    for pr in doc.get("prompts") or []:
        arms = (pr.get("engines") or {}).get(engine) or {}
        for arm_name in ("knowledge", "search"):
            arm = arms.get(arm_name)
            if not isinstance(arm, dict) or arm.get("error"):
                continue
            if not arm.get("brand_mentioned"):
                continue
            key = f"{pr.get('prompt_id')}|{engine}|{arm_name}"
            out.append({
                "key": key,
                "prompt_id": pr.get("prompt_id"),
                "prompt_text": pr.get("prompt_text") or "",
                "engine": engine,
                "arm": arm_name,
                "answer": arm.get("raw_response_text") or "",
            })
    return out


def parse_json_blob(raw: str) -> dict | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        try:
            doc = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if isinstance(doc, dict) and "result" in doc and isinstance(doc["result"], str):
        return parse_json_blob(doc["result"])
    if isinstance(doc, dict) and "content" in doc and isinstance(doc["content"], list):
        texts = []
        for block in doc["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
        if texts:
            return parse_json_blob("\n".join(texts))
    if isinstance(doc, dict) and "text" in doc and not {"stance", "position", "actions"} & set(doc):
        return parse_json_blob(str(doc.get("text") or ""))
    return doc if isinstance(doc, dict) else None


def normalize_hit(doc: dict) -> dict | None:
    stance = str(doc.get("stance") or "").lower().strip()
    position = str(doc.get("position") or "").lower().strip()
    if stance not in STANCE or position not in POSITION:
        return None
    ahead = doc.get("ahead") or []
    if not isinstance(ahead, list):
        ahead = []
    quote = " ".join(str(doc.get("quote") or "").split())
    words = quote.split()
    if len(words) > 40:
        quote = " ".join(words[:40])
    try:
        conf = float(doc.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "stance": stance,
        "position": position,
        "ahead": [str(x) for x in ahead][:8],
        "quote": quote,
        "judge": "claude",
        "confidence": max(0.0, min(1.0, conf)),
    }


def claude_json(prompt: str, timeout: int = 120) -> dict | None:
    cmd = ["claude", "-p", "--tools", "", "--output-format", "json", "--", prompt]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return parse_json_blob(proc.stdout or proc.stderr or "")


def answer_text(raw: str) -> str:
    text = raw or ""
    if text.strip().startswith("{"):
        try:
            wrap = json.loads(text)
            if isinstance(wrap, dict) and wrap.get("text"):
                text = str(wrap["text"])
        except json.JSONDecodeError:
            pass
    return text


def claude_extract_vendors(
    query: str,
    answer: str,
    search_queries: list[str],
    *,
    brand: str,
    aliases: list[str],
    hints: list[str],
) -> dict | None:
    """LLM vendor extract. `claude_json` is the only network/CLI call."""
    qs = "\n".join(q for q in search_queries if q) or "(none)"
    hint = ", ".join(hints[:40]) if hints else "(none)"
    alias_txt = ", ".join(aliases[:24]) if aliases else "(none)"
    prompt = VENDOR_PROMPT.format(
        brand=brand,
        aliases=alias_txt,
        hints=hint,
        query=(query or "").strip(),
        answer=answer_text(answer).strip()[:8000],
        queries=qs[:4000],
    )
    for _ in range(2):
        try:
            doc = claude_json(prompt)
        except subprocess.TimeoutExpired:
            doc = None
        if not doc:
            continue
        cell = normalize_vendor_cell(doc)
        if cell:
            return cell
    return None


def claude_judge(query: str, answer: str) -> dict | None:
    text = answer_text(answer)
    prompt = JUDGE_PROMPT.format(brand=BRAND, query=query.strip(), answer=text.strip()[:8000])
    for _ in range(2):
        try:
            doc = claude_json(prompt)
        except subprocess.TimeoutExpired:
            doc = None
        if not doc:
            continue
        hit = normalize_hit(doc)
        if hit:
            return hit
    return None


def summarize_for_board(run: Path, store: dict, docs: dict | None = None) -> tuple[str, str]:
    counts = []
    samples = []
    ahead_c = Counter()
    stance_c = Counter()
    pos_c = Counter()
    by_class = defaultdict(Counter)
    for e in ENGINES:
        if docs and e in docs:
            doc = docs[e]
        else:
            fp = run / f"{e}.json"
            if not fp.exists():
                continue
            doc = load_json(fp)
        n = len(doc.get("prompts") or [])
        counts.append(
            f"{e} prompts={n}/100 mention_k={doc.get('mention_rate_knowledge')} "
            f"mention_s={doc.get('mention_rate_search')} search_rate={doc.get('search_rate')}"
        )
        for pr in doc.get("prompts") or []:
            why = pr.get("why") or pr.get("class") or "?"
            arms = (pr.get("engines") or {}).get(e) or {}
            for arm_name in ("knowledge", "search"):
                arm = arms.get(arm_name) or {}
                key = f"{pr.get('prompt_id')}|{e}|{arm_name}"
                j = store.get(key)
                if not arm.get("brand_mentioned"):
                    by_class[why]["miss"] += 1
                    continue
                by_class[why]["hit"] += 1
                if not j:
                    continue
                stance_c[j.get("stance")] += 1
                pos_c[j.get("position")] += 1
                for v in j.get("ahead") or []:
                    ahead_c[str(v)] += 1
                interesting = j.get("position") in ("first", "last", "aside") or j.get("stance") in ("warn", "reject")
                if interesting and len(samples) < 40:
                    samples.append(
                        f"{key} {j.get('stance')}/{j.get('position')} ahead={j.get('ahead')} quote={j.get('quote')}"
                    )
    counts.append("stance " + json.dumps(dict(stance_c)))
    counts.append("position " + json.dumps(dict(pos_c)))
    counts.append("ahead " + json.dumps(ahead_c.most_common(12)))
    counts.append("class " + json.dumps({k: dict(v) for k, v in list(by_class.items())[:40]}))
    return "\n".join(counts), "\n".join(samples[:40])


def board_judge(run: Path, store: dict, docs: dict | None = None) -> dict | None:
    counts, samples = summarize_for_board(run, store, docs)
    prompt = BOARD_PROMPT.format(brand=BRAND, counts=counts, samples=samples)
    for _ in range(2):
        try:
            doc = claude_json(prompt, timeout=180)
        except subprocess.TimeoutExpired:
            doc = None
        if not isinstance(doc, dict) or not doc.get("actions"):
            continue
        clean = []
        for a in (doc.get("actions") or [])[:7]:
            if not isinstance(a, dict):
                continue
            clean.append({
                "title": str(a.get("title") or "")[:120],
                "why": str(a.get("why") or "")[:400],
                "do": str(a.get("do") or "")[:400],
                "evidence": str(a.get("evidence") or "")[:300],
            })
        if clean:
            return {"headline": str(doc.get("headline") or "")[:280], "actions": clean, "judge": "claude"}
    return None


def parse_args(argv: list[str]) -> tuple[Path, list[str], bool, bool]:
    args = argv[1:]
    vendors_only = False
    stance_only = False
    engines: list[str] = []
    run: Path | None = None
    for a in args:
        if a in ("-h", "--help"):
            print(
                "Usage: judge_run.py [--vendors-only|--stance-only] [run_dir_or_evidence.json] [claude|codex|grok...]\n"
                "Env: AEO_BRAND, AEO_RUN or AEO_TYK_RUN (directory of claude.json/codex.json/grok.json,\n"
                "     or a single evidence JSON). Writes judge.json, vendors_judged.json, board.json."
            )
            raise SystemExit(0)
        if a == "--vendors-only":
            vendors_only = True
        elif a == "--stance-only":
            stance_only = True
        elif a in ENGINES:
            engines.append(a)
        else:
            run = Path(a)
    if run is None:
        run = Path(
            os.environ.get("AEO_RUN")
            or os.environ.get("AEO_TYK_RUN")
            or str(Path.home() / ".aeo/runs/tyk100-20260901")
        )
    return run, engines or list(ENGINES), vendors_only, stance_only


def load_engine_docs(run: Path, engines: list[str]) -> tuple[Path, dict[str, dict]]:
    """Directory of `{engine}.json`, or one combined evidence file."""
    if run.is_file():
        doc = load_json(run)
        found: set[str] = set()
        for pr in doc.get("prompts") or []:
            found.update((pr.get("engines") or {}).keys())
        stem = run.stem.lower()
        docs = {}
        for e in engines:
            if e in found or stem == e:
                docs[e] = doc
        return run.parent, docs
    docs = {}
    for e in engines:
        fp = run / f"{e}.json"
        if not fp.exists():
            print(f"skip missing {fp}", flush=True)
            continue
        docs[e] = load_json(fp)
    return run, docs


def load_store(path: Path) -> dict:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return raw if isinstance(raw, dict) else {}


def resolve_brand(docs: dict[str, dict]) -> str:
    env = os.environ.get("AEO_BRAND")
    if env:
        return env
    brand, _, _ = workspace_from_docs(docs)
    return brand or "Tyk"


def vendor_cell_done(cell: object) -> bool:
    return isinstance(cell, dict) and "vendors" in cell


def run_vendor_pass(
    out_dir: Path,
    docs: dict[str, dict],
    engines: list[str],
    *,
    brand: str,
) -> int:
    outp = out_dir / "vendors_judged.json"
    store = load_vendor_store(load_store(outp))
    _, aliases, competitors = workspace_from_docs(docs)
    todo = []
    for e in engines:
        doc = docs.get(e)
        if not doc:
            continue
        for cell in completed_cells(doc, e):
            if vendor_cell_done(store.get(cell["key"])):
                continue
            todo.append(cell)
    print(f"to_extract {len(todo)} already {len(store)}", flush=True)
    fails = 0
    for i, cell in enumerate(todo, 1):
        print(f"{i}/{len(todo)} vendors {cell['key']}", flush=True)
        judged = claude_extract_vendors(
            cell["prompt_text"],
            cell["answer"],
            cell.get("search_queries") or [],
            brand=brand,
            aliases=aliases,
            hints=competitors,
        )
        if not judged:
            print("  FAIL", flush=True)
            fails += 1
            continue
        store[cell["key"]] = judged
        outp.write_text(json.dumps(store, indent=2))
        names = [v.get("normalized") or v.get("raw") for v in judged.get("vendors") or []]
        print(f"  n={len(names)} {', '.join(str(x) for x in names[:8])}", flush=True)
    print(f"wrote {outp} n={len(store)} fails={fails}", flush=True)
    return fails


def main(argv: list[str]) -> int:
    global BRAND
    run, engines, vendors_only, stance_only = parse_args(argv)
    out_dir, docs = load_engine_docs(run, engines)
    BRAND = resolve_brand(docs)
    outp = out_dir / "judge.json"
    store = load_store(outp)
    if not vendors_only:
        todo = []
        for e in engines:
            doc = docs.get(e)
            if not doc:
                continue
            for h in hits(doc, e):
                if h["key"] in store and isinstance(store[h["key"]], dict) and store[h["key"]].get("stance"):
                    continue
                todo.append(h)
        print(f"to_judge {len(todo)} already {len(store)}", flush=True)
        fails = 0
        for i, h in enumerate(todo, 1):
            print(f"{i}/{len(todo)} {h['key']}", flush=True)
            judged = claude_judge(h["prompt_text"], h["answer"])
            if not judged:
                print("  FAIL", flush=True)
                fails += 1
                continue
            store[h["key"]] = judged
            outp.write_text(json.dumps(store, indent=2))
            print(f"  {judged['stance']}/{judged['position']}", flush=True)
        print(f"wrote {outp} n={len(store)} fails={fails}", flush=True)
    if not stance_only:
        run_vendor_pass(out_dir, docs, engines, brand=BRAND)
    if vendors_only:
        return 0
    brief_path = out_dir / "board.json"
    print("board judge…", flush=True)
    brief = board_judge(out_dir, store, docs)
    if brief:
        brief_path.write_text(json.dumps(brief, indent=2))
        print(f"wrote {brief_path} actions={len(brief.get('actions') or [])}", flush=True)
    else:
        print("board judge failed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
