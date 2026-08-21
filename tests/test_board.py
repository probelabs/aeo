import json
import unittest
from pathlib import Path

from aeo.board import build_board, render_markdown
from aeo.validate import SCHEMA_DIR, validate_with_jsonschema

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "examples" / "xerj" / "aeo-data" / "example-run.json"


class BoardTests(unittest.TestCase):
    def _doc(self):
        return json.loads(FIX.read_text(encoding="utf-8"))

    def test_example_board_cells(self):
        board = build_board(self._doc())
        self.assertEqual(board["schema_version"], "aeo-cli-board-v1")
        sb = board["scoreboard"]
        self.assertEqual(sb["focus_mention_rate_search"], 0.0)
        self.assertEqual(sb["focus_search_rate"], 0.0)
        self.assertEqual(sb["watch_mention_rate"], 0.0)
        self.assertEqual(sb["prebelief_count"], 1)

        watch = next(g for g in board["groups"] if g["id"] == "watch")
        self.assertEqual(len(watch["rows"]), 1)
        row = watch["rows"][0]
        self.assertEqual(row["class"], "watch")
        self.assertEqual(row["why"], "knowledge_trap")
        self.assertEqual(row["call"], "trap")

        claude = row["engines"]["claude"]
        self.assertFalse(claude["knowledge"]["mentioned"])
        self.assertFalse(claude["search"]["mentioned"])
        self.assertFalse(claude["search"]["searched"])

        grok = row["engines"]["grok"]
        self.assertFalse(grok["search"]["searched"])

        codex = row["engines"]["codex"]
        self.assertTrue(codex["search"]["searched"])
        self.assertIn("ripgrep", [v.lower() for v in codex["search"]["vendors_in_search_queries"]])

        md = render_markdown(
            board,
            brand_terms={"xerj", "xerj.org", "xerj.ai", "xerj-org"},
        )
        self.assertIn("Watch mention rate: 0.00", md)
        self.assertIn("Prebelief searches (⚠): 1", md)
        self.assertIn("trap", md)
        self.assertIn("⚠", md)
        # Claude / Grok did not search (✗ in 🔍); Codex confirmation search
        self.assertIn("| trap |", md)
        self.assertIn("## Watch", md)
        # no invented wins
        self.assertNotIn("| win |", md)

    def test_board_json_schema(self):
        board = build_board(self._doc())
        import json as _json
        schema = _json.loads((SCHEMA_DIR / "aeo-cli-board-v1.json").read_text(encoding="utf-8"))
        errors = validate_with_jsonschema(board, schema)
        self.assertEqual(errors, [], errors)


if __name__ == "__main__":
    unittest.main()


class CallRuleTests(unittest.TestCase):
    def _prompt(self, cls, why, *, mentioned=False, searched=False, knowledge_mentioned=False):
        search = {
            "brand_mentioned": mentioned,
            "searched": searched,
            "vendors_in_search_queries": ["ripgrep"] if searched else [],
        }
        return {
            "prompt_id": "q",
            "prompt_text": "q",
            "class": cls,
            "why": why,
            "engines": {
                "claude": {
                    "knowledge": {"brand_mentioned": knowledge_mentioned},
                    "search": search,
                }
            },
        }

    def test_win_gap_trap_search_blind(self):
        from aeo.board import _call_for, _engine_view, build_board

        def call(p):
            return _call_for(p, _engine_view(p))

        self.assertEqual(call(self._prompt("focus", "product_fit", mentioned=True)), "win")
        self.assertEqual(call(self._prompt("focus", "product_fit", knowledge_mentioned=True)), "win")
        self.assertEqual(call(self._prompt("focus", "search_likely", searched=True)), "gap")
        self.assertEqual(call(self._prompt("focus", "product_fit", searched=False)), "search-blind")
        self.assertEqual(call(self._prompt("watch", "knowledge_trap", searched=True)), "trap")
        self.assertEqual(call(self._prompt("watch", "knowledge_trap")), "trap")
