import unittest
from pathlib import Path

from aeo.parsers import parse_claude, parse_codex, parse_grok

FIX = Path(__file__).resolve().parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_claude_search_stream_json(self):
        raw = (FIX / "claude-search-stream.jsonl").read_text(encoding="utf-8")
        p = parse_claude(raw)
        self.assertTrue(p.searched)
        self.assertIn("ripgrep Recoll DocFetcher folder search", p.search_queries)
        self.assertIn("ripgrep", p.raw_response_text)

    def test_claude_knowledge_json(self):
        raw = (FIX / "claude-knowledge.json").read_text(encoding="utf-8")
        p = parse_claude(raw)
        self.assertFalse(p.searched)
        self.assertEqual(p.search_queries, [])
        self.assertEqual(p.raw_response_text, "ripgrep is the usual tool.")

    def test_codex_search_action_queries(self):
        raw = (FIX / "codex-search.jsonl").read_text(encoding="utf-8")
        p = parse_codex(raw)
        self.assertTrue(p.searched)
        self.assertIn("ripgrep vs Recoll vs DocFetcher", p.search_queries)
        self.assertIn("DocFetcher local folder search", p.search_queries)
        self.assertEqual(p.raw_response_text, "Use Recoll or ripgrep.")

    def test_codex_knowledge_plain(self):
        raw = (FIX / "codex-knowledge.txt").read_text(encoding="utf-8")
        p = parse_codex(raw)
        self.assertFalse(p.searched)
        self.assertIn("ripgrep", p.raw_response_text)

    def test_grok_search_json(self):
        raw = (FIX / "grok-search.json").read_text(encoding="utf-8")
        p = parse_grok(raw)
        self.assertTrue(p.searched)
        self.assertIn("best folder content search tool", p.search_queries)
        self.assertIn("https://example.com/rg", p.search_queries)
        self.assertIn("ripgrep", p.raw_response_text)

    def test_grok_knowledge_plain(self):
        raw = (FIX / "grok-knowledge.txt").read_text(encoding="utf-8")
        p = parse_grok(raw)
        self.assertFalse(p.searched)
        self.assertIn("ripgrep", p.raw_response_text)


if __name__ == "__main__":
    unittest.main()
