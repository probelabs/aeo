import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

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
                            "knowledge": _arm(text="Use UserCheck or usercheck.com."),
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
                "vendors": [{"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"}],
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
        who = html[html.index("<h2>Who got named</h2>") : html.index("<h2>Vendors typed into search</h2>")]
        self.assertIn("UserCheck", who)
        self.assertNotIn(">Kickbox<", who)
        self.assertIn("Named instead: UserCheck", html)
        box = html[html.index("<h2>Vendors typed into search</h2>") : html.index("<h2>Queries</h2>")]
        self.assertIn("UserCheck", box)

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
        self.assertIn("brand_mentioned", html)


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


if __name__ == "__main__":
    unittest.main()
