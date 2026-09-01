#!/usr/bin/env python3.11
"""Post-run LLM judge over AEO evidence. Only brand_mentioned cells. Then board brief."""
from __future__ import annotations

import json, os, re, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path

STANCE = {"recommend", "mention", "warn", "reject"}
POSITION = {"first", "among", "last", "aside"}
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


def claude_judge(query: str, answer: str) -> dict | None:
    text = answer
    if text.strip().startswith("{"):
        try:
            wrap = json.loads(text)
            if isinstance(wrap, dict) and wrap.get("text"):
                text = str(wrap["text"])
        except json.JSONDecodeError:
            pass
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


def summarize_for_board(run: Path, store: dict) -> tuple[str, str]:
    counts = []
    samples = []
    ahead_c = Counter()
    stance_c = Counter()
    pos_c = Counter()
    by_class = defaultdict(Counter)
    for e in ("claude", "codex", "grok"):
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


def board_judge(run: Path, store: dict) -> dict | None:
    counts, samples = summarize_for_board(run, store)
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


def main(argv: list[str]) -> int:
    run = Path(os.environ.get("AEO_TYK_RUN") or str(Path.home() / ".aeo/runs/tyk100-20260901"))
    outp = run / "judge.json"
    store = {}
    if outp.exists():
        store = json.loads(outp.read_text())
    if not isinstance(store, dict):
        store = {}
    engines = argv[1:] or ["claude", "codex", "grok"]
    todo = []
    for e in engines:
        fp = run / f"{e}.json"
        if not fp.exists():
            print(f"skip missing {fp}", flush=True)
            continue
        doc = load_json(fp)
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
    brief_path = run / "board.json"
    print("board judge…", flush=True)
    brief = board_judge(run, store)
    if brief:
        brief_path.write_text(json.dumps(brief, indent=2))
        print(f"wrote {brief_path} actions={len(brief.get('actions') or [])}", flush=True)
    else:
        print("board judge failed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
