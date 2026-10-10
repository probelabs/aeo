import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from aeo.vendors import seed_alias_map

ROOT = Path(__file__).resolve().parents[1]


def _load_render():
    path = ROOT / "scripts" / "render_judge_html.py"
    spec = importlib.util.spec_from_file_location("render_judge_html", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _arm(*, mentioned=False, comps=None, text="", searched=False, queries=None, qvendors=None):
    return {
        "brand_mentioned": mentioned,
        "brand_mentions": ["Autheona"] if mentioned else [],
        "competitor_mentions": list(comps or []),
        "searched": searched,
        "search_queries": list(queries or []),
        "vendors_in_search_queries": list(qvendors or []),
        "recommended": mentioned,
        "raw_response_text": text or "Try UserCheck at https://usercheck.com — Kickbox is also fine.",
    }


class JudgeHtmlVendorTests(unittest.TestCase):
    def test_usercheck_appears_without_config_competitor(self):
        render = _load_render()
        doc = {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {
                "brand": "Autheona",
                "domain": "autheona.com",
                "aliases": ["autheona", "autheona.com"],
                "competitors": ["Kickbox"],
            },
            "run": {
                "run_id": "t",
                "timestamp": "2026-09-07T00:00:00Z",
                "engines": ["claude"],
                "samples_per_arm": 1,
            },
            "prompts": [
                {
                    "prompt_id": "email-verify",
                    "prompt_text": "How do I verify an email address is real?",
                    "class": "focus",
                    "engines": {
                        "claude": {
                            "knowledge": _arm(comps=["Kickbox"], text="Use UserCheck or Kickbox."),
                            "search": _arm(
                                searched=True,
                                queries=["usercheck.com email verification"],
                                qvendors=[],
                                text="Use UserCheck.",
                            ),
                        }
                    },
                }
            ],
        }
        store = {
            "email-verify|claude|knowledge": {
                "vendors": [
                    {"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"},
                    {"raw": "Kickbox", "normalized": "Kickbox", "role": "mention"},
                ],
                "query_vendors": [],
                "confidence": 0.9,
                "judge": "claude",
            },
            "email-verify|claude|search": {
                "vendors": [{"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"}],
                "query_vendors": [{"raw": "usercheck.com", "normalized": "UserCheck", "role": "mention"}],
                "confidence": 0.85,
                "judge": "claude",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "claude.json").write_text(json.dumps(doc))
            (run / "vendors_judged.json").write_text(json.dumps(store))
            html = render.render(run)
        who = html[html.index("<h2>Who got named</h2>") : html.index("<h2>Surprise competitors</h2>")]
        surprise = html[html.index("<h2>Surprise competitors</h2>") : html.index("<h2>Vendors typed into search</h2>")]
        self.assertNotIn("UserCheck", who)
        self.assertIn("Kickbox", who)
        self.assertIn("UserCheck", surprise)
        self.assertNotIn("Kickbox", surprise)
        self.assertIn("badge-surprise", surprise)
        self.assertIn("Named instead: UserCheck", html)
        self.assertIn("badge-surprise", html)
        box = html[html.index("<h2>Vendors typed into search</h2>") : html.index("<h2>Queries</h2>")]
        self.assertIn("UserCheck", box)
        self.assertIn("badge-surprise", box)
        self.assertIn("not on the config seed list", box)
        self.assertIn("Surprises", html)
        # header follows the workspace, not the historical Tyk defaults
        head = html[: html.index("<main>")]
        self.assertIn("<title>Autheona · AEO report</title>", head)
        self.assertIn("autheona.com", head)
        self.assertNotIn("Tyk", head)
        self.assertNotIn("of Tyk hits", html)

    def test_search_surprise_set_excludes_seed_even_if_answer_surprise(self):
        render = _load_render()
        amap = seed_alias_map(
            "Tyk",
            ["tyk"],
            ["aws api gateway", "amazon api gateway"],
        )
        counts = Counter(
            {"Amazon API Gateway": 5, "UserCheck": 2, "Tyk": 1}
        )
        # Answer-side Surprise competitors wrongly include the collapsed seed name.
        surprise_names = {"UserCheck", "Amazon API Gateway"}
        got = render.search_chart_surprise_set(counts, "Tyk", amap)
        self.assertNotIn("Amazon API Gateway", got)
        self.assertIn("UserCheck", got)
        self.assertNotIn("Tyk", got)
        self.assertTrue(amap.is_seed("Amazon API Gateway"))
        self.assertNotEqual(got, got | surprise_names)

    def test_search_chart_does_not_badge_seed_from_answer_surprises(self):
        """Amazon API Gateway is a seed; UserCheck is an answer-side surprise.

        Both appear in the search box. Only UserCheck should get a search-chart badge.
        """
        render = _load_render()
        doc = {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {
                "brand": "Tyk",
                "domain": "tyk.io",
                "aliases": ["tyk", "tyk.io"],
                "competitors": ["aws api gateway", "amazon api gateway"],
            },
            "run": {
                "run_id": "t",
                "timestamp": "2026-09-07T00:00:00Z",
                "engines": ["claude"],
                "samples_per_arm": 1,
            },
            "prompts": [
                {
                    "prompt_id": "leave-aws",
                    "prompt_text": "What is closest to AWS API Gateway but self-hosted?",
                    "class": "focus",
                    "engines": {
                        "claude": {
                            "knowledge": _arm(
                                comps=["amazon api gateway"],
                                text="Amazon API Gateway and UserCheck are listed.",
                            ),
                            "search": _arm(
                                searched=True,
                                queries=["amazon api gateway vs usercheck"],
                                qvendors=["amazon api gateway", "UserCheck"],
                                comps=["UserCheck"],
                                text="UserCheck is also named.",
                            ),
                        }
                    },
                }
            ],
        }
        store = {
            "leave-aws|claude|knowledge": {
                "vendors": [
                    {"raw": "Amazon API Gateway", "normalized": "Amazon API Gateway", "role": "mention"},
                    {"raw": "UserCheck", "normalized": "UserCheck", "role": "mention"},
                ],
                "query_vendors": [],
                "confidence": 0.9,
                "judge": "claude",
            },
            "leave-aws|claude|search": {
                "vendors": [{"raw": "UserCheck", "normalized": "UserCheck", "role": "mention"}],
                "query_vendors": [
                    {"raw": "amazon api gateway", "normalized": "Amazon API Gateway", "role": "mention"},
                    {"raw": "UserCheck", "normalized": "UserCheck", "role": "mention"},
                ],
                "confidence": 0.85,
                "judge": "claude",
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "claude.json").write_text(json.dumps(doc))
            (run / "vendors_judged.json").write_text(json.dumps(store))
            html = render.render(run)

        surprise = html[
            html.index("<h2>Surprise competitors</h2>") : html.index(
                "<h2>Vendors typed into search</h2>"
            )
        ]
        self.assertIn("UserCheck", surprise)
        self.assertIn("badge-surprise", surprise)
        self.assertNotIn("Amazon API Gateway", surprise)

        box = html[html.index("<h2>Vendors typed into search</h2>") : html.index("<h2>Queries</h2>")]
        self.assertIn("Amazon API Gateway", box)
        self.assertIn("UserCheck", box)
        self.assertIn("not on the config seed list", box)
        # Badge only on the non-seed search name, not the collapsed seed.
        self.assertIn("UserCheck <span class='badge-surprise'>surprise</span>", box)
        self.assertNotIn("Amazon API Gateway <span class='badge-surprise'>surprise</span>", box)

    def test_regex_fallback_without_vendor_store(self):
        render = _load_render()
        doc = {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {
                "brand": "Tyk",
                "domain": "tyk.io",
                "aliases": ["tyk"],
                "competitors": ["Kong"],
            },
            "run": {"run_id": "t", "timestamp": "2026-09-07T00:00:00Z", "engines": ["claude"]},
            "prompts": [
                {
                    "prompt_id": "q1",
                    "prompt_text": "rate limit partner APIs",
                    "engines": {
                        "claude": {
                            "knowledge": _arm(comps=["Kong"], text="Use Kong."),
                            "search": _arm(mentioned=True, comps=["Kong"], text="Tyk or Kong."),
                        }
                    },
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "claude.json").write_text(json.dumps(doc))
            html = render.render(run)
        self.assertIn("Kong", html)
        self.assertIn("Tyk", html)
        self.assertIn("strict brand match", html)
        self.assertNotIn("brand_mentioned", html)


def _load_judge():
    path = ROOT / "scripts" / "judge_run.py"
    spec = importlib.util.spec_from_file_location("judge_run", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class JudgeRunCliTests(unittest.TestCase):
    def test_parse_args_vendors_only_and_path(self):
        judge = _load_judge()
        run, engines, vendors_only, stance_only = judge.parse_args(
            ["judge_run.py", "--vendors-only", "/tmp/evidence.json", "claude"]
        )
        self.assertEqual(run, Path("/tmp/evidence.json"))
        self.assertEqual(engines, ["claude"])
        self.assertTrue(vendors_only)
        self.assertFalse(stance_only)

    def test_vendor_cell_done(self):
        judge = _load_judge()
        self.assertTrue(judge.vendor_cell_done({"vendors": []}))
        self.assertFalse(judge.vendor_cell_done({"confidence": 0.5}))
        self.assertFalse(judge.vendor_cell_done(None))

    def test_board_counts_include_surprises(self):
        judge = _load_judge()
        docs = {
            "claude": {
                "workspace": {"brand": "Autheona", "aliases": ["autheona"], "competitors": ["Kickbox"]},
                "prompts": [
                    {
                        "prompt_id": "email-verify",
                        "why": "focus",
                        "engines": {
                            "claude": {
                                "knowledge": {
                                    "brand_mentioned": False,
                                    "raw_response_text": "Use UserCheck.",
                                    "competitor_mentions": ["Kickbox"],
                                }
                            }
                        },
                    }
                ],
            }
        }
        vstore = {
            "email-verify|claude|knowledge": {
                "vendors": [{"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"}],
            }
        }
        counts, _ = judge.summarize_for_board(Path("/tmp"), {}, docs, vstore)
        self.assertIn("surprises", counts)
        self.assertIn("UserCheck", counts)

    def test_board_rates_come_from_cells_not_stale_doc_fields(self):
        judge = _load_judge()
        cell = lambda hit, **kw: {"brand_mentioned": hit, "raw_response_text": "x", **kw}
        docs = {
            "claude": {
                "workspace": {"brand": "Proof", "aliases": ["ReqProof"]},
                # Stale loose-matcher aggregates left over from before a rescore.
                "mention_rate_knowledge": 0.1828,
                "mention_rate_search": 0.1935,
                "search_rate": 0.9,
                "prompts": [
                    {"prompt_id": "a", "engines": {"claude": {
                        "knowledge": cell(False), "search": cell(True, searched=True)}}},
                    {"prompt_id": "b", "engines": {"claude": {
                        "knowledge": cell(False), "search": cell(False, searched=False)}}},
                    {"prompt_id": "c", "engines": {"claude": {
                        "knowledge": {"error": "timeout"}, "search": cell(False)}}},
                ],
            }
        }
        counts, _ = judge.summarize_for_board(Path("/tmp"), {}, docs, {})
        lines = counts.splitlines()
        self.assertIn("TOTAL: 5 answers.", lines[0])
        self.assertIn("named in 1 of 5 answers", lines[0])
        claude = next(l for l in lines if l.startswith("claude:"))
        self.assertIn("claude: 5 answers.", claude)
        self.assertNotIn("/100", claude)
        self.assertIn("0 without search of 2 answers confirmed not browsing", claude)
        self.assertIn("1 with search of 3", claude)
        self.assertIn("Searched the web on 1 of 3", claude)
        self.assertIn("codex: not run", counts)
        self.assertNotIn("mention_k", counts)
        self.assertNotIn("0.1828", counts)
        self.assertNotIn("0.1935", counts)


    def test_class_words_are_plain(self):
        judge = _load_judge()
        self.assertEqual(
            judge.class_words("search_likely+product_fit"),
            "questions assistants usually search the web for that are also questions the brand is a direct fit for",
        )
        self.assertEqual(judge.class_words("my_group"), "my group")

    def test_search_evidence_lists_real_queries_and_urls(self):
        judge = _load_judge()
        docs = {"codex": {"prompts": [
            {"prompt_id": "p1", "why": "product_fit", "prompt_text": "How do I trace requirements to tests?",
             "engines": {"codex": {"search": {
                 "searched": True, "brand_mentioned": False,
                 "search_queries": ["requirements traceability tool open source"],
                 "raw_response_text": "Try StrictDoc (https://strictdoc.readthedocs.io/en/stable/).",
             }}}},
            {"prompt_id": "p2", "why": "product_fit", "prompt_text": "skip me",
             "engines": {"codex": {"search": {
                 "searched": True, "brand_mentioned": True,
                 "search_queries": ["x"], "raw_response_text": "Proof"}}}},
        ]}}
        ev = judge.search_evidence(docs)
        self.assertIn("requirements traceability tool open source", ev)
        self.assertIn("https://strictdoc.readthedocs.io/en/stable/", ev)
        self.assertNotIn("skip me", ev)

    def test_jargon_problems_flags_labels_and_ratios_not_urls(self):
        judge = _load_judge()
        bad = judge.jargon_problems({"headline": "mention_k 0.172 vs mention_s; search_likely 0/52", "actions": []})
        self.assertIn("mention_k", bad)
        self.assertIn("0/52", bad)
        self.assertIn("0.172", bad)
        ok = judge.jargon_problems({
            "headline": "Codex named Proof in none of its 93 answers.",
            "actions": [{"title": "t", "why": "w", "do": "d",
                         "evidence": "Codex typed \"site_search tool\"; cited https://docs.pact.io/pact_broker/can_i_deploy"}],
        })
        self.assertEqual(ok, [])


if __name__ == "__main__":
    unittest.main()
