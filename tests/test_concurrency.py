import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from aeo.cli import main
from aeo.engines import ExecResult
from aeo.evidence import (
    cell_shard_document,
    parts_dir_for,
    union_documents,
    write_document,
)
from aeo.runner import plan_remaining


def _cfg(prompts, engines=("grok",), samples=1):
    return {
        "brand": "XERJ",
        "domain": "xerj.org",
        "aliases": ["xerj"],
        "competitors": ["ripgrep"],
        "engines": list(engines),
        "samples_per_arm": samples,
        "prompts": [
            {"id": pid, "text": text, "class": "focus"} for pid, text in prompts
        ],
    }


def _doc(*prompt_ids, engine="grok", arms=("knowledge", "search")):
    prompts = []
    for pid in prompt_ids:
        engines = {
            engine: {arm: {"mentioned": False, "searched": False} for arm in arms}
        }
        prompts.append(
            {
                "prompt_id": pid,
                "prompt_text": f"{pid} query",
                "engines": engines,
            }
        )
    return {
        "schema_version": "aeo-cli-evidence-v1",
        "workspace": {
            "brand": "XERJ",
            "domain": "xerj.org",
            "aliases": ["xerj"],
            "competitors": ["ripgrep"],
        },
        "run": {
            "run_id": "aeo-test",
            "timestamp": "2026-08-21T00:00:00Z",
            "methodology_version": "v0",
            "engines": [engine],
            "samples_per_arm": 1,
        },
        "prompts": prompts,
    }


def _shard(prompt_id, engine, arm, *, text="cell"):
    base = _doc()
    base["prompts"] = []
    entry = {
        "prompt_id": prompt_id,
        "prompt_text": f"{prompt_id} query",
        "engines": {engine: {arm: {"raw_response_text": text, "brand_mentioned": False}}},
    }
    return cell_shard_document(base, entry)


class UnionDocumentsTests(unittest.TestCase):
    def test_union_keeps_both_prompts(self):
        base = _doc("a")
        extra = _shard("b", "grok", "knowledge")
        merged = union_documents(base, extra)
        self.assertEqual([p["prompt_id"] for p in merged["prompts"]], ["a", "b"])
        self.assertEqual(merged["run"]["run_id"], "aeo-test")
        self.assertIn("knowledge", merged["prompts"][1]["engines"]["grok"])

    def test_union_merges_arms_on_same_prompt(self):
        base = _doc("q1", arms=("knowledge",))
        extra = _shard("q1", "grok", "search", text="search-arm")
        merged = union_documents(base, extra)
        self.assertEqual(len(merged["prompts"]), 1)
        arms = merged["prompts"][0]["engines"]["grok"]
        self.assertIn("knowledge", arms)
        self.assertIn("search", arms)
        self.assertEqual(arms["search"]["raw_response_text"], "search-arm")

    def test_existing_cell_wins(self):
        base = _doc("q1", arms=("knowledge",))
        base["prompts"][0]["engines"]["grok"]["knowledge"]["raw_response_text"] = "first"
        extra = _shard("q1", "grok", "knowledge", text="second")
        merged = union_documents(base, extra)
        self.assertEqual(
            merged["prompts"][0]["engines"]["grok"]["knowledge"]["raw_response_text"],
            "first",
        )

    def test_second_engine_does_not_drop_first(self):
        base = _doc("q1", engine="claude")
        extra = _shard("q1", "grok", "knowledge")
        merged = union_documents(base, extra)
        engines = merged["prompts"][0]["engines"]
        self.assertIn("claude", engines)
        self.assertIn("grok", engines)


class PlanRemainingTests(unittest.TestCase):
    def test_skips_completed_and_keeps_roster_order(self):
        doc = _doc("already-done")
        prompts = [
            {"id": "already-done", "text": "done query", "intent": None, "class": "focus", "why": None},
            {"id": "todo", "text": "todo query", "intent": None, "class": "focus", "why": None},
        ]
        skipped, jobs = plan_remaining(doc, prompts, ["grok"], ["knowledge", "search"], 1)
        self.assertEqual(skipped, 2)
        self.assertEqual([(j.prompt_id, j.engine, j.arm) for j in jobs], [
            ("todo", "grok", "knowledge"),
            ("todo", "grok", "search"),
        ])
        self.assertEqual([p["prompt_id"] for p in doc["prompts"]], ["already-done", "todo"])

    def test_samples_skip_completed_index_only(self):
        doc = _doc("q")
        doc["run"]["samples_per_arm"] = 2
        doc["prompts"][0]["sample_index"] = 1
        prompts = [
            {"id": "q", "text": "q query", "intent": None, "class": "focus", "why": None},
        ]
        skipped, jobs = plan_remaining(doc, prompts, ["grok"], ["knowledge", "search"], 2)
        self.assertEqual(skipped, 2)
        self.assertEqual(
            [(j.sample_index, j.arm) for j in jobs],
            [(2, "knowledge"), (2, "search")],
        )
        self.assertEqual(
            [p.get("sample_index") for p in doc["prompts"]],
            [1, 2],
        )


class ConcurrentRunTests(unittest.TestCase):
    def test_concurrency_writes_every_cell(self):
        cfg = _cfg([("p1", "one"), ("p2", "two")])
        lock = threading.Lock()
        active = 0
        max_active = 0
        calls = []

        def fake_run(inv, timeout=300):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
                calls.append((inv.engine, inv.arm, inv.prompt))
            time.sleep(0.05)
            with lock:
                active -= 1
            return ExecResult(stdout=f"answer {inv.arm}", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "aeo.config.json"
            out = tmp / "run.json"
            cfg_path.write_text(json.dumps(cfg))
            with patch("aeo.runner.run_invocation", side_effect=fake_run):
                rc = main(
                    [
                        "run",
                        "--config",
                        str(cfg_path),
                        "--engine",
                        "grok",
                        "--arm",
                        "both",
                        "--out",
                        str(out),
                        "--concurrency",
                        "4",
                        "--timeout",
                        "5",
                    ]
                )
            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text())
            ids = [p["prompt_id"] for p in doc["prompts"]]
            self.assertEqual(ids, ["p1", "p2"])
            for entry in doc["prompts"]:
                self.assertEqual(set(entry["engines"]["grok"]), {"knowledge", "search"})
            self.assertEqual(len(calls), 4)
            self.assertGreater(max_active, 1)
            self.assertFalse(parts_dir_for(out).exists())

    def test_resume_skip_under_concurrency(self):
        cfg = _cfg([("already-done", "done query"), ("todo", "todo query")])
        existing = _doc("already-done")
        calls = []

        def fake_run(inv, timeout=300):
            calls.append((inv.engine, inv.arm, inv.prompt))
            return ExecResult(stdout="no brand here", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "aeo.config.json"
            out = tmp / "run.json"
            cfg_path.write_text(json.dumps(cfg))
            out.write_text(json.dumps(existing))
            with patch("aeo.runner.run_invocation", side_effect=fake_run):
                rc = main(
                    [
                        "run",
                        "--config",
                        str(cfg_path),
                        "--engine",
                        "grok",
                        "--arm",
                        "both",
                        "--out",
                        str(out),
                        "--concurrency",
                        "4",
                        "--timeout",
                        "5",
                    ]
                )
            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text())
            self.assertEqual([p["prompt_id"] for p in doc["prompts"]], ["already-done", "todo"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(all("todo query" in c[2] for c in calls))
            self.assertIn("knowledge", doc["prompts"][0]["engines"]["grok"])
            self.assertIn("search", doc["prompts"][1]["engines"]["grok"])

    def test_only_id_with_concurrency(self):
        cfg = _cfg([("keep", "keep query"), ("drop", "drop query")])
        calls = []

        def fake_run(inv, timeout=300):
            calls.append(inv.prompt)
            return ExecResult(stdout="ok", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "aeo.config.json"
            out = tmp / "run.json"
            cfg_path.write_text(json.dumps(cfg))
            with patch("aeo.runner.run_invocation", side_effect=fake_run):
                rc = main(
                    [
                        "run",
                        "--config",
                        str(cfg_path),
                        "--engine",
                        "grok",
                        "--arm",
                        "knowledge",
                        "--only-id",
                        "keep",
                        "--out",
                        str(out),
                        "--concurrency",
                        "3",
                    ]
                )
            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text())
            self.assertEqual([p["prompt_id"] for p in doc["prompts"]], ["keep"])
            self.assertEqual(len(calls), 1)
            self.assertTrue(all("keep query" in c for c in calls))

    def test_leftover_shard_merged_then_skipped(self):
        cfg = _cfg([("a", "a query"), ("b", "b query")])
        existing = _doc("a")
        leftover = _shard("b", "grok", "knowledge", text="from-shard")
        leftover["prompts"][0]["engines"]["grok"]["search"] = {
            "raw_response_text": "from-shard-search",
            "brand_mentioned": False,
        }
        calls = []

        def fake_run(inv, timeout=300):
            calls.append((inv.engine, inv.arm))
            return ExecResult(stdout="should not run", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "aeo.config.json"
            out = tmp / "run.json"
            cfg_path.write_text(json.dumps(cfg))
            out.write_text(json.dumps(existing))
            parts = parts_dir_for(out)
            write_document(leftover, parts / "leftover.json", overwrite=True)
            with patch("aeo.runner.run_invocation", side_effect=fake_run):
                rc = main(
                    [
                        "run",
                        "--config",
                        str(cfg_path),
                        "--engine",
                        "grok",
                        "--arm",
                        "both",
                        "--out",
                        str(out),
                        "--concurrency",
                        "2",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])
            doc = json.loads(out.read_text())
            ids = [p["prompt_id"] for p in doc["prompts"]]
            self.assertEqual(ids, ["a", "b"])
            self.assertEqual(
                doc["prompts"][1]["engines"]["grok"]["knowledge"]["raw_response_text"],
                "from-shard",
            )
            self.assertFalse(parts.exists())

    def test_dry_run_ignores_concurrency(self):
        cfg = _cfg([("p1", "one")])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "aeo.config.json"
            cfg_path.write_text(json.dumps(cfg))
            with patch("aeo.runner.run_invocation") as mocked:
                rc = main(
                    [
                        "run",
                        "--config",
                        str(cfg_path),
                        "--engine",
                        "grok",
                        "--arm",
                        "both",
                        "--dry-run",
                        "--concurrency",
                        "4",
                    ]
                )
            self.assertEqual(rc, 0)
            mocked.assert_not_called()

    def test_concurrency_zero_rejected(self):
        with self.assertRaises(SystemExit) as ctx:
            main(["run", "--concurrency", "0", "--prompt", "x"])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
