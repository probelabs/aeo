import json
import unittest
from pathlib import Path

from aeo.report import render_doc, rows_from_doc

ROOT = Path(__file__).resolve().parents[1]


class ReportTests(unittest.TestCase):
    def test_example_rows(self):
        doc = json.loads(
            (ROOT / "examples" / "xerj" / "aeo-data" / "example-run.json").read_text(encoding="utf-8")
        )
        rows = rows_from_doc(doc)
        self.assertEqual(len(rows), 3)
        by_engine = {r["engine"]: r for r in rows}
        self.assertEqual(by_engine["claude"]["searched"], "no")
        self.assertEqual(by_engine["claude"]["knowledge"], "no")
        self.assertEqual(by_engine["codex"]["searched"], "yes")
        self.assertIn("ripgrep", by_engine["codex"]["vendors"])
        self.assertEqual(by_engine["grok"]["brand"], "no")
        table = render_doc(doc)
        self.assertIn("knowledge hit", table)
        self.assertIn("vendors in search queries", table)


if __name__ == "__main__":
    unittest.main()
