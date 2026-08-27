import unittest

from aeo.html_report import build_report_payload, merge_docs, render_html_report


def _arm(*, mentioned=False, searched=False, vendors=None, text="", cost=None):
    arm = {
        "brand_mentioned": mentioned,
        "brand_mentions": ["Acme"] if mentioned else [],
        "competitor_mentions": ["ripgrep"] if vendors else [],
        "searched": searched,
        "search_queries": ["ripgrep alternative"] if searched else [],
        "vendors_in_search_queries": list(vendors or []),
        "recommended": mentioned,
        "raw_response_text": text or "short answer",
    }
    if cost is not None:
        arm["usage"] = {"cost_usd": cost}
    return arm


def _doc(engine, *, brand="Acme", run_id="run-a", prompt_id="q1", prompt_text="best local search tool?", **arms):
    return {
        "schema_version": "aeo-cli-evidence-v1",
        "workspace": {
            "brand": brand,
            "domain": "acme.example",
            "aliases": ["acme", "acme.example"],
        },
        "run": {
            "run_id": run_id,
            "timestamp": "2026-08-21T16:00:00Z",
            "engines": [engine],
            "samples_per_arm": 1,
        },
        "prompts": [
            {
                "prompt_id": prompt_id,
                "prompt_text": prompt_text,
                "class": "focus",
                "engines": {engine: arms},
            }
        ],
    }


class HtmlReportTests(unittest.TestCase):
    def test_merge_two_engines(self):
        a = _doc("alpha", run_id="r1", knowledge=_arm(), search=_arm(searched=True, vendors=["ripgrep"]))
        b = _doc("beta", run_id="r2", knowledge=_arm(), search=_arm(searched=True))
        merged = merge_docs([a, b])
        self.assertEqual(merged["run"]["run_id"], "r1+r2")
        self.assertEqual(merged["run"]["engines"], ["alpha", "beta"])
        self.assertEqual(len(merged["prompts"]), 1)
        engines = merged["prompts"][0]["engines"]
        self.assertIn("alpha", engines)
        self.assertIn("beta", engines)
        self.assertEqual(merged["workspace"]["brand"], "Acme")

    def test_prebelief_vs_discovery(self):
        doc = {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {"brand": "Acme", "aliases": ["acme"]},
            "run": {"run_id": "r", "timestamp": "2026-08-21T16:00:00Z", "engines": ["alpha"]},
            "prompts": [
                {
                    "prompt_id": "confirm",
                    "prompt_text": "how do I search files?",
                    "class": "focus",
                    "engines": {
                        "alpha": {
                            "knowledge": _arm(),
                            "search": _arm(searched=True, vendors=["ripgrep"]),
                        }
                    },
                },
                {
                    "prompt_id": "discover",
                    "prompt_text": "local document search software",
                    "class": "focus",
                    "engines": {
                        "alpha": {
                            "knowledge": _arm(),
                            "search": _arm(searched=True, vendors=[]),
                        }
                    },
                },
                {
                    "prompt_id": "brand-only",
                    "prompt_text": "acme category query",
                    "class": "focus",
                    "engines": {
                        "alpha": {
                            "knowledge": _arm(),
                            "search": _arm(searched=True, vendors=["acme"]),
                        }
                    },
                },
            ],
        }
        payload = build_report_payload(doc)
        by_id = {r["prompt_id"]: r for r in payload["rows"]}
        self.assertTrue(by_id["confirm"]["engines"]["alpha"]["search"]["prebelief"])
        self.assertFalse(by_id["confirm"]["engines"]["alpha"]["search"]["discovery"])
        self.assertTrue(by_id["discover"]["engines"]["alpha"]["search"]["discovery"])
        self.assertFalse(by_id["discover"]["engines"]["alpha"]["search"]["prebelief"])
        self.assertTrue(by_id["brand-only"]["engines"]["alpha"]["search"]["discovery"])
        self.assertFalse(by_id["brand-only"]["engines"]["alpha"]["search"]["prebelief"])
        self.assertEqual(payload["engines"]["alpha"]["prebelief"], 1)
        self.assertEqual(payload["engines"]["alpha"]["discovery"], 2)

    def test_mention_without_search(self):
        doc = _doc(
            "alpha",
            knowledge=_arm(),
            search=_arm(mentioned=True, searched=False, text="I recommend Acme"),
        )
        payload = build_report_payload(doc)
        s = payload["rows"][0]["engines"]["alpha"]["search"]
        self.assertTrue(s["mention_without_search"])
        self.assertTrue(s["mentioned"])
        self.assertFalse(s["searched"])
        self.assertEqual(payload["overall"]["mention_without_search"], 1)

    def test_render_self_contained(self):
        a = _doc("alpha", knowledge=_arm(), search=_arm(searched=True, vendors=["ripgrep"]))
        b = _doc("beta", run_id="r2", knowledge=_arm(mentioned=True), search=_arm())
        html = render_html_report([a, b])
        self.assertIn("Acme", html)
        self.assertIn("best local search tool?", html)
        self.assertIn("<!DOCTYPE html>", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)
        self.assertIn("#0b0d10", html)
        self.assertNotIn("How to read", html)
        self.assertIn("Query", html)
        self.assertTrue("Vendors" in html or "Call" in html)
        self.assertIn("knowledge", html.lower())
        self.assertIn("confirmation", html.lower())
        self.assertTrue("Watch" in html or "Searched, no mention" in html)

    def test_prompts_compact_table(self):
        a = _doc("claude", knowledge=_arm(), search=_arm(searched=True, vendors=["ripgrep"]))
        html = render_html_report([a])
        self.assertNotIn("How to read", html)
        self.assertNotIn("prompt-card", html)
        self.assertNotIn("engine-cards", html)
        self.assertNotIn("hero-rate", html)
        self.assertIn("<th", html)
        self.assertIn("Query", html)
        self.assertIn("Vendors", html)
        self.assertIn("Call", html)
        self.assertIn(">Claude<", html)
        self.assertIn("ripgrep", html)
        self.assertIn("table-legend", html)
        self.assertIn("knowledge", html.lower())
        self.assertIn("confirmation", html.lower())
        self.assertIn("Searched, no mention", html)
        self.assertIn("Mention (S)", html)
        self.assertIn("Confirm", html)
        self.assertNotIn(">Prebelief<", html)
        legend = html[html.index('class="table-legend"'):html.index('class="prompt-table"')]
        self.assertIn("knowledge", legend.lower())
        self.assertIn("confirmation", legend.lower())
        self.assertIn("prompt-row", html)
        payload = build_report_payload(merge_docs([a]))
        ov = payload["overall"]
        self.assertIn(f"{int(ov['search_mentions'])} / {int(ov['search_cells'])}", html)
        self.assertIn(str(int(ov["prebelief"])), html)
        self.assertIn(str(int(ov["discovery"])), html)
        self.assertIn(str(int(ov["mention_without_search"])), html)

if __name__ == "__main__":
    unittest.main()
