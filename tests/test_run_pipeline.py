import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import os
os.environ.setdefault("AEO_CANARY", "skip")  # no live identity canary in tests (unittest discover skips tests/__init__)

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _cell(text, mentioned=False, searched=False, error=None):
    c = {"raw_response_text": text, "brand_mentioned": mentioned, "brand_mentions": ["Proof"] if mentioned else [],
         "searched": searched, "search_queries": [], "vendors_in_search_queries": [], "competitor_mentions": []}
    if error:
        c = {"error": error}
    return c


def _doc(engine, prompts):
    return {"schema_version": "aeo-cli-evidence-v1",
            "workspace": {"brand": "Proof", "aliases": ["ReqProof", "reqproof.com"]},
            "prompts": [{"prompt_id": pid, "prompt_text": pid, "engines": {engine: arms}} for pid, arms in prompts]}


CFG = {"brand": "Proof", "aliases": ["ReqProof", "reqproof.com"], "brand_match": {"product_form_only": ["Proof"]},
       "prompts": [{"id": "new-a", "text": "New question A"}]}


class RenderLabelTests(unittest.TestCase):
    def setUp(self):
        self.r = _load("render_judge_html")

    def test_plain_class_words(self):
        self.assertEqual(self.r.plain_class("knowledge_trap"), "general-knowledge question")
        self.assertEqual(self.r.plain_class("search_likely+product_fit"), "likely to search, product fit")
        self.assertEqual(self.r.plain_class("leonid_v3"), "leonid v3")
        self.assertEqual(self.r.plain_class(""), "")

    def test_brand_terms_words(self):
        self.assertEqual(self.r.brand_terms_words("Proof", ["ReqProof", "reqproof.com"]),
                         "Proof, ReqProof or reqproof.com")
        self.assertEqual(self.r.brand_terms_words("Tyk", []), "Tyk")

    def test_no_headline_patch_up_counts_come_from_records(self):
        # The old fix_headline_total rewrote the judge's denominator after the fact.
        # Now the headline is built from the records and a judge headline with a
        # wrong count is not shown at all.
        self.assertFalse(hasattr(self.r, "fix_headline_total"))
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run1"
            run.mkdir()
            doc = _doc("claude", [("q1", {"knowledge": _cell("Try Jama."), "search": _cell("Use Proof.", True, True)}),
                                  ("q2", {"knowledge": _cell("x"), "search": _cell("y")})])
            (run / "claude.json").write_text(json.dumps(doc))
            (run / "board.json").write_text(json.dumps({"headline": "Proof was named in only 1 of 396 answers.",
                                                        "actions": []}))
            out = self.r.render(run)
        self.assertNotIn("396", out)
        self.assertIn("Proof was named in 1 of 4 answers", out)
        self.assertIn("4 answers</span>", out)  # header pill uses the same count

    def test_report_has_no_internal_labels(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "run1"
            run.mkdir()
            doc = _doc("claude", [("q1", {"knowledge": _cell("Try Jama."), "search": _cell("Use Proof.", True, True)})])
            doc["prompts"][0]["class"] = "search_likely+product_fit"
            (run / "claude.json").write_text(json.dumps(doc))
            (run / "board.json").write_text(json.dumps({"headline": "Proof was named in 1 of 9 answers.", "actions": []}))
            out = self.r.render(run)
        for bad in ("Mention K", "Mention S", "search_likely", "product_fit", "<code>brand_mentioned</code>",
                    "<code>recommended</code>", "<code>vendors_in_search_queries</code>"):
            self.assertNotIn(bad, out)
        self.assertIn("Named without search", out)
        self.assertIn("likely to search, product fit", out)
        self.assertIn("1 of 2 answers", out)


class MergeShardsTests(unittest.TestCase):
    def test_merge_prefers_completed_cells_and_recomputes_rates(self):
        m = _load("merge_shards")
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "claude.w0.json").write_text(json.dumps(_doc("claude", [
                ("q1", {"knowledge": _cell("", error="timeout"), "search": _cell("Use Proof.", True, True)}),
            ])))
            (run / "claude.w1.json").write_text(json.dumps(_doc("claude", [
                ("q1", {"knowledge": _cell("Try Jama."), "search": _cell("x", error="boom")}),
                ("q2", {"knowledge": _cell("Doorstop."), "search": _cell("StrictDoc.", False, True)}),
            ])))
            self.assertEqual(m.engines_with_shards(run), ["claude"])
            m.merge_engine(run, "claude")
            out = json.loads((run / "claude.json").read_text())
        ids = [p["prompt_id"] for p in out["prompts"]]
        self.assertEqual(ids, ["q1", "q2"])
        q1 = out["prompts"][0]["engines"]["claude"]
        self.assertEqual(q1["knowledge"]["raw_response_text"], "Try Jama.")
        self.assertTrue(q1["search"]["brand_mentioned"])
        self.assertAlmostEqual(out["mention_rate_search"], 0.5)
        self.assertAlmostEqual(out["mention_rate_knowledge"], 0.0)


class CompareMappedTests(unittest.TestCase):
    def test_strict_matching_on_both_sides(self):
        c = _load("compare_mapped")
        with tempfile.TemporaryDirectory() as d:
            cur, old = Path(d) / "v3", Path(d) / "old"
            cur.mkdir(); old.mkdir()
            (cur / "claude.json").write_text(json.dumps(_doc("claude", [
                ("new-a", {"knowledge": _cell("ReqProof can trace this."), "search": _cell("Use Jama.")}),
            ])))
            # Old run stored a loose hit on the ordinary word "proof"; strict matching must drop it.
            (old / "claude.json").write_text(json.dumps(_doc("claude", [
                ("old-a", {"knowledge": _cell("You need proof that it works.", True), "search": _cell("Use Jama.")}),
            ])))
            doc = c.compare(cur, CFG, {"new-a": ["old-a"]}, {"Oct 9": old}, "v3", ["claude"])
            html = c.render_html(doc, "v3")
        self.assertEqual(doc["summary"], {"Oct 9": [0, 2], "v3": [1, 2]})
        self.assertIn("claude without search", html)
        self.assertNotIn("claude|knowledge", html)


class RescoreInPlaceTests(unittest.TestCase):
    def test_in_place_updates_summary_rates_and_keeps_backup(self):
        import subprocess, sys
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            doc = _doc("codex", [("q1", {"knowledge": _cell("You need proof.", True), "search": _cell("ReqProof does it.", False, True)})])
            doc["mention_rate_knowledge"] = 1.0
            doc["mention_rate_search"] = 0.0
            (run / "codex.json").write_text(json.dumps(doc))
            (run / "cfg.json").write_text(json.dumps(CFG))
            rc = subprocess.run([sys.executable, str(ROOT / "scripts" / "rescore_run.py"), str(run),
                                 "--config", str(run / "cfg.json"), "--in-place"], capture_output=True, text=True)
            self.assertEqual(rc.returncode, 0, rc.stderr)
            out = json.loads((run / "codex.json").read_text())
            self.assertTrue((run / "codex.json.bak-prerescore").exists())
            self.assertFalse((run / "codex.rescored.json").exists())
        self.assertEqual(out["mention_rate_knowledge"], 0.0)
        self.assertEqual(out["mention_rate_search"], 1.0)


if __name__ == "__main__":
    unittest.main()
