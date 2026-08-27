import json
import unittest
from pathlib import Path

from aeo.report import class_tally, render_doc, rows_from_doc

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
        self.assertEqual(by_engine["claude"]["class"], "watch")
        self.assertEqual(by_engine["codex"]["searched"], "yes")
        self.assertIn("ripgrep", by_engine["codex"]["vendors"])
        self.assertEqual(by_engine["grok"]["brand"], "no")
        table = render_doc(doc)
        self.assertIn("knowledge hit", table)
        self.assertIn("vendors in search queries", table)
        self.assertIn("class tally:", table)
        self.assertIn("1 watch", table)
        self.assertIn("0 focus", table)

    def test_class_tally_line(self):
        doc = json.loads(
            (ROOT / "examples" / "xerj" / "aeo-data" / "example-run.json").read_text(encoding="utf-8")
        )
        line = class_tally(doc)
        self.assertIn("1 watch, 0 focus", line)
        self.assertIn("watch mention_k=0.00 mention_s=0.00 search=", line)
        self.assertIn("focus mention_k=—", line)


if __name__ == "__main__":
    unittest.main()
