import unittest

from aeo.config import starter_config
from aeo.engines import OPERATIONAL_SUFFIX, build_invocation, format_command, user_prompt


class DryRunTests(unittest.TestCase):
    def test_suffix_only(self):
        text = "What's the best way to search through a folder of files by content?"
        p = user_prompt(text)
        self.assertTrue(p.startswith(text))
        self.assertIn(OPERATIONAL_SUFFIX, p)
        self.assertNotIn("XERJ", p)
        self.assertNotIn("rust", p.lower())

    def test_claude_flags(self):
        cfg = starter_config("XERJ", "xerj.org")
        k = build_invocation("claude", "knowledge", "q", cfg)
        self.assertIn("--tools", k.argv)
        self.assertIn("", k.argv)  # empty tools list
        self.assertNotIn("--bare", k.argv)
        s = build_invocation("claude", "search", "q", cfg)
        joined = format_command(s.argv)
        self.assertIn("WebSearch,WebFetch", joined)
        self.assertIn("bypassPermissions", joined)
        self.assertIn("claude-empty-hooks.json", joined)
        self.assertNotIn("--bare", s.argv)

    def test_grok_flags(self):
        cfg = starter_config("XERJ", "xerj.org")
        kn = build_invocation("grok", "knowledge", "q", cfg)
        k = format_command(kn.argv)
        self.assertIn("--disable-web-search", k)
        self.assertIn("--sandbox strict", k)
        self.assertIn("--no-memory", k)
        self.assertIn("--cwd", k)
        self.assertTrue(str(kn.cwd).startswith("/tmp") or "aeo-isolate-" in str(kn.cwd))
        s = format_command(build_invocation("grok", "search", "q", cfg).argv)
        self.assertIn("--output-format json", s)
        self.assertIn("--verbatim", s)
        self.assertIn("--sandbox strict", s)
        self.assertNotIn("streaming-json", s)

    def test_codex_flags(self):
        cfg = starter_config("XERJ", "xerj.org")
        k = format_command(build_invocation("codex", "knowledge", "q", cfg).argv)
        self.assertIn("codex exec", k)
        self.assertIn("--ephemeral", k)
        self.assertIn("--skip-git-repo-check", k)
        self.assertIn("--sandbox read-only", k)
        # No-search arm: search explicitly off, and --json so tool activity is recorded.
        self.assertIn("--json", k)
        self.assertIn("web_search=\"disabled\"", k)
        self.assertIn("--disable standalone_web_search", k)
        self.assertNotIn("--enable", k)
        s = format_command(build_invocation("codex", "search", "q", cfg).argv)
        self.assertIn("--json", s)
        self.assertIn("--enable standalone_web_search", s)
        self.assertNotIn("disabled", s)

    def test_claude_isolation_flags_and_stream_on_both_arms(self):
        cfg = starter_config("XERJ", "xerj.org")
        for arm in ("knowledge", "search"):
            argv = build_invocation("claude", arm, "q", cfg).argv
            for flag in ("--strict-mcp-config", "--no-session-persistence", "--disable-slash-commands"):
                self.assertIn(flag, argv)
            self.assertIn("stream-json", argv)
            self.assertIn("--verbose", argv)

    def test_grok_json_on_both_arms(self):
        cfg = starter_config("XERJ", "xerj.org")
        k = format_command(build_invocation("grok", "knowledge", "q", cfg).argv)
        self.assertIn("--output-format json", k)


if __name__ == "__main__":
    unittest.main()
