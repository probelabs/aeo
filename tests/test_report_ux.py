"""Single-report UX: plain labels, TRT times, honest percentages, surprise filtering, comparability."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("AEO_CANARY", "skip")  # no live identity canary in tests

from aeo import report_ux as ux
from aeo.change import assess_comparability

ROOT = Path(__file__).resolve().parents[1]


def _render():
    spec = importlib.util.spec_from_file_location("render_judge_html", ROOT / "scripts" / "render_judge_html.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FormatTests(unittest.TestCase):
    def test_user_time_is_trt(self):
        self.assertEqual(ux.user_time("2026-10-09T19:18:00Z"), "Fri Oct 9, 2026, 22:18 TRT")

    def test_pct_never_rounds_nonzero_to_zero(self):
        self.assertEqual(ux.pct(0), "0%")
        self.assertEqual(ux.pct(0.0046), "0.46%")
        self.assertNotEqual(ux.pct(0.0001), "0.0%")
        self.assertEqual(ux.pct(0.25), "25.0%")
        self.assertEqual(ux.n_of(3, 108), "3 of 108")


class SurpriseTests(unittest.TestCase):
    def test_exclusions(self):
        counts = Counter({"Python": 40, "Claude": 9, "AWS": 5, "GCC": 2, "Jira": 12, "StrictDoc": 7, "Hypothesis": 4})
        kept, excluded = ux.filter_surprises(counts, ["How do I trace Jira tickets to tests?"], ["claude", "codex"])
        self.assertEqual(set(kept), {"StrictDoc", "Hypothesis"})
        self.assertEqual(excluded["programming language"], 40)
        self.assertEqual(excluded["assistant under test"], 9)
        self.assertEqual(excluded["cloud provider"], 5)
        self.assertEqual(excluded["compiler or build tool"], 2)
        self.assertEqual(excluded["named in the question"], 12)

    def test_grouped_by_category(self):
        groups = ux.group_by_category(Counter({"StrictDoc": 5, "Hypothesis": 3, "Stryker": 2, "Zorblax": 9}))
        cats = [g[0] for g in groups]
        self.assertEqual(cats[-1], "Other")  # Other always last
        testing = dict((g[0], g) for g in groups)["Testing (property, mutation, contract, fuzz)"]
        self.assertEqual(testing[1], 5)


def _snap(n_env: bool, mset=None):
    run = {"run_id": "r"}
    if n_env:
        run["environment"] = {"claude": {"isolated": True}}
    if mset:
        run["measurement_set"] = mset
    return SimpleNamespace(docs={"claude": {"run": run, "prompts": []}})


class ComparabilityTests(unittest.TestCase):
    def test_question_set_change_is_not_comparable(self):
        out = assess_comparability(_snap(True), _snap(True),
                                   {"overlap": 9, "baseline_questions": 93, "current_questions": 108}, ["claude"])
        self.assertFalse(out["comparable"])
        self.assertIn("question set changed (9 of 108 questions identical)", out["reasons"])

    def test_same_set_is_comparable(self):
        m = {"id": "proof-v3", "question_hash": "sha256:x"}
        out = assess_comparability(_snap(True, m), _snap(True, m),
                                   {"overlap": 108, "baseline_questions": 108, "current_questions": 108}, ["claude"])
        self.assertTrue(out["comparable"], out)

    def test_pre_isolation_baseline_not_comparable(self):
        out = assess_comparability(_snap(False), _snap(True),
                                   {"overlap": 5, "baseline_questions": 5, "current_questions": 5}, ["claude"])
        self.assertFalse(out["comparable"])
        self.assertTrue(any("identity isolation" in r for r in out["reasons"]))


def _arm(mentioned=False, text="answer"):
    return {"raw_response_text": text, "brand_mentioned": mentioned, "brand_mentions": ["Proof"] if mentioned else [],
            "competitor_mentions": [], "searched": False, "search_queries": [], "vendors_in_search_queries": [],
            "recommended": False, "browsing": "none"}


class SingleReportTests(unittest.TestCase):
    def _run(self, tmp: Path, change=None, mapped=None) -> str:
        doc = {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {"brand": "Proof", "domain": "reqproof.com", "aliases": ["proof"], "competitors": ["Jama"]},
            "run": {"run_id": "r", "timestamp": "2026-10-09T19:18:00Z", "engines": ["claude"]},
            "prompts": [{"prompt_id": "q1", "prompt_text": "trace requirements to tests",
                         "engines": {"claude": {"knowledge": _arm(), "search": _arm(True, "Use Proof.")}}}],
        }
        (tmp / "claude.json").write_text(json.dumps(doc))
        if change:
            (tmp / "change.json").write_text(json.dumps(change))
        if mapped:
            (tmp / "mapped-compare.json").write_text(json.dumps(mapped))
        return _render().render(tmp)

    def test_structure(self):
        with tempfile.TemporaryDirectory() as d:
            html = self._run(Path(d))
        self.assertIn("name='viewport'", html)
        for anchor in ("id='verdict'", "id='actions'", "id='changes'", "id='competitors'", "id='questions'", "id='method'"):
            self.assertIn(anchor, html)
        self.assertLess(html.index("id='verdict'"), html.index("id='method'"))
        self.assertIn("22:18 TRT", html)
        self.assertIn("Codex not run", html)
        for stale in ("Tyk", "Kong", "Apigee", "Mention K", "Mention S"):
            self.assertNotIn(stale, html)
        self.assertIn("data-tags='", html)

    def test_not_comparable_drops_trend_styling(self):
        change = {"verdict": "Not comparable: question set changed (9 of 108 questions identical)",
                  "comparability": {"comparable": False, "reasons": ["question set changed (9 of 108 questions identical)"]},
                  "interpretation": {"not_comparable": True}}
        with tempfile.TemporaryDirectory() as d:
            html = self._run(Path(d), change=change)
        sec = html[html.index("id='changes'"):html.index("id='engines'")]
        self.assertIn("Not comparable", sec)
        self.assertNotIn("class='win'", sec)
        self.assertNotIn("class='loss'", sec)

    def test_mapped_appendix_shows_only_changed_rows(self):
        mapped = {"labels": ["Oct 9", "now"], "rows": [
            {"new_text": "changed one", "match": "approximate",
             "cells": {"claude|search": {"Oct 9": {"brand": False}, "now": {"brand": True}}}},
            {"new_text": "same one", "match": "exact",
             "cells": {"claude|search": {"Oct 9": {"brand": False}, "now": {"brand": False}}}},
        ]}
        with tempfile.TemporaryDirectory() as d:
            html = self._run(Path(d), mapped=mapped)
        app = html[html.index("id='mapped'"):]
        self.assertIn("<details class='appendix'", html)
        self.assertIn("changed one", app)
        self.assertNotIn("same one", app)


if __name__ == "__main__":
    unittest.main()
