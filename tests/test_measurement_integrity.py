"""Tests for the five measurement-integrity fixes (Oct 10, 2026).

1 identity isolation + canary, 2 no-search arm / browsing status,
3 frozen measurement set + exact-text comparisons, 4 retrieval chain,
5 counts built from per-answer records.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aeo import canary, counts, isolation, measurement, retrieval
from aeo.config import starter_config
from aeo.engines import build_invocation, format_command
from aeo.engines import ExecResult

FIX = Path(__file__).parent / "fixtures"


def _env(tmp: Path, **extra) -> dict:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp / "realhome")}
    env.update(extra)
    return env


class IsolationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = self.tmp / "realhome"
        (self.real / ".codex").mkdir(parents=True)
        (self.real / "Library" / "Keychains").mkdir(parents=True)
        (self.real / ".claude.json").write_text(json.dumps({
            "oauthAccount": {"accountUuid": "a", "organizationUuid": "o", "emailAddress": "leo@reqproof.com",
                             "displayName": "Leonid", "fullName": "Leonid Bugaev", "organizationName": "probelabs",
                             "billingType": "x", "accountCreatedAt": "t", "subscriptionCreatedAt": "t",
                             "ccOnboardingFlags": {}},
            "mcpServers": {"secret": {}}, "projects": {"/x": {}},
        }))
        (self.real / ".codex" / "auth.json").write_text(json.dumps({"last_refresh": "2026-10-01T00:00:00Z", "tokens": {"t": 1}}))
        self.patches = [
            mock.patch.dict(os.environ, {"AEO_REAL_HOME": str(self.real), "AEO_ISOLATE_ROOT": str(self.tmp / "iso")}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_claude_keychain_mode_uses_empty_home_without_email(self):
        iso = isolation.prepare("claude", _env(self.tmp, CLAUDE_CONFIG_DIR="/should/go"))
        try:
            self.assertNotIn("CLAUDE_CONFIG_DIR", iso.env)
            self.assertEqual(iso.env["HOME"], str(iso.home))
            self.assertEqual(iso.env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"], "1")
            self.assertNotIn("--bare", iso.extra_argv)
            seeded = json.loads((iso.home / ".claude.json").read_text())
            blob = json.dumps(seeded).lower()
            for term in ("leo@", "leonid", "bugaev", "probelabs", "mcpservers", "projects"):
                self.assertNotIn(term, blob)
            self.assertEqual(seeded["oauthAccount"]["emailAddress"], "")
            self.assertTrue((iso.home / "Library" / "Keychains").is_symlink())
            self.assertEqual(sorted(p.name for p in iso.home.iterdir()), [".claude.json", "Library"])
        finally:
            iso.cleanup()
        self.assertFalse(iso.home.exists())
        self.assertTrue((self.real / "Library" / "Keychains").is_dir(), "cleanup must not touch the real keychain")

    def test_claude_api_key_mode_uses_bare_and_config_dir(self):
        iso = isolation.prepare("claude", _env(self.tmp, ANTHROPIC_API_KEY="sk-test"))
        try:
            self.assertIn("--bare", iso.extra_argv)
            self.assertTrue(iso.env["CLAUDE_CONFIG_DIR"].startswith(str(iso.home)))
            self.assertNotIn("sk-test", json.dumps(iso.settings))
        finally:
            iso.cleanup()

    def test_codex_home_has_only_config_and_symlinked_auth(self):
        iso = isolation.prepare("codex", _env(self.tmp))
        try:
            home = Path(iso.env["CODEX_HOME"])
            self.assertEqual(sorted(p.name for p in home.iterdir()), ["auth.json", "config.toml"])
            self.assertTrue((home / "auth.json").is_symlink())
            cfg = (home / "config.toml").read_text()
            for feat in ("apps", "plugins", "memories", "hooks"):
                self.assertIn(f"{feat} = false", cfg)
        finally:
            iso.cleanup()
        self.assertTrue((self.real / ".codex" / "auth.json").exists())

    def test_codex_refreshed_login_is_synced_back(self):
        iso = isolation.prepare("codex", _env(self.tmp))
        home = Path(iso.env["CODEX_HOME"])
        (home / "auth.json").unlink()
        (home / "auth.json").write_text(json.dumps({"last_refresh": "2026-10-09T00:00:00Z", "tokens": {"t": 2}}))
        iso.cleanup()
        real = json.loads((self.real / ".codex" / "auth.json").read_text())
        self.assertEqual(real["tokens"]["t"], 2)

    def test_codex_older_login_is_not_synced_back(self):
        iso = isolation.prepare("codex", _env(self.tmp))
        home = Path(iso.env["CODEX_HOME"])
        (home / "auth.json").unlink()
        (home / "auth.json").write_text(json.dumps({"last_refresh": "2026-01-01T00:00:00Z", "tokens": {"t": 0}}))
        iso.cleanup()
        self.assertEqual(json.loads((self.real / ".codex" / "auth.json").read_text())["tokens"]["t"], 1)

    def test_describe_leaves_nothing_behind(self):
        before = set((self.tmp / "iso").glob("*")) if (self.tmp / "iso").exists() else set()
        for e in ("claude", "codex", "grok"):
            self.assertEqual(isolation.describe(e, _env(self.tmp))["isolation"], isolation.ISOLATION_VERSION)
        self.assertEqual(set((self.tmp / "iso").glob("*")), before)


class CanaryTests(unittest.TestCase):
    def setUp(self):
        self.cfg = starter_config("ReqProof", "reqproof.com")

    def _runner(self, text, error=None):
        def run(inv, timeout=0):
            self.assertIn(canary.CANARY_PROMPT, inv.argv[-1])
            out = json.dumps({"type": "result", "result": text, "is_error": False})
            return ExecResult(stdout=out if not error else "", stderr="", returncode=0 if not error else 1, error=error)
        return run

    def test_pass(self):
        rec = canary.run_canary("claude", self.cfg, runner=self._runner("I don't know your name or email."))
        self.assertEqual(rec["status"], "pass")

    def test_leak(self):
        rec = canary.run_canary("claude", self.cfg, runner=self._runner("Your email is leo@reqproof.com."))
        self.assertEqual(rec["status"], "leak")
        self.assertIn("reqproof", rec["leaked_terms"])

    def test_error_is_not_a_pass(self):
        rec = canary.run_canary("claude", self.cfg, runner=self._runner("", error="timeout"))
        self.assertEqual(rec["status"], "error")

    def test_word_boundaries(self):
        self.assertEqual(canary.leaked_terms("Bugger off", ["buger"]), [])
        self.assertEqual(canary.leaked_terms("user buger here", ["buger"]), ["buger"])

    def test_run_aborts_with_exit_3_on_leak(self):
        from aeo import cli

        with tempfile.TemporaryDirectory() as d:
            cfg_path = Path(d) / "c.json"
            cfg = starter_config("ReqProof", "reqproof.com")
            from aeo.config import write_config

            write_config(cfg, cfg_path)
            out = Path(d) / "out.json"
            leak = {"status": "leak", "leaked_terms": ["reqproof"], "answer": "leo@reqproof.com"}
            argv = ["run", "--config", str(cfg_path), "--engine", "claude", "--arm", "knowledge",
                    "--out", str(out), "--canary", "require"]
            with mock.patch.object(cli, "canary_with_retries", return_value=leak), \
                 mock.patch.dict(os.environ, {"AEO_CANARY": ""}):
                code = cli.main(argv)
            self.assertEqual(code, 3)
            doc = json.loads(out.read_text())
            env = doc["run"]["environment"]["claude"]
            self.assertEqual(env["canary"]["status"], "leak")
            self.assertIn("isolation", env)
            self.assertIn("argv", env)
            self.assertEqual(doc["prompts"], [])


class NoSearchArmTests(unittest.TestCase):
    def test_codex_knowledge_disables_search_and_uses_json(self):
        cfg = starter_config("ReqProof", "reqproof.com")
        k = format_command(build_invocation("codex", "knowledge", "q", cfg, make_cwd=False).argv)
        self.assertIn("--json", k)
        self.assertIn('web_search="disabled"', k)
        self.assertIn("--disable standalone_web_search", k)
        s = format_command(build_invocation("codex", "search", "q", cfg, make_cwd=False).argv)
        self.assertIn("--json", s)
        self.assertNotIn('web_search="disabled"', s)

    def _codex(self, items):
        docs = [{"type": "turn.started"}] + [{"type": "item.completed", "item": i} for i in items] + [{"type": "turn.completed"}]
        return retrieval.tool_activity("codex", docs)

    def test_browsing_none_only_when_stream_complete_and_no_tools(self):
        act = self._codex([{"type": "agent_message", "text": "x"}])
        self.assertEqual(retrieval.browsing_status(act, parsed_events=True), "none")

    def test_web_search_item_means_searched(self):
        act = self._codex([{"type": "web_search", "action": {"type": "search", "query": "q"}}])
        self.assertEqual(retrieval.browsing_status(act, parsed_events=True), "searched")

    def test_shell_command_means_unknown(self):
        act = self._codex([{"type": "command_execution", "command": "curl https://x"}])
        self.assertEqual(retrieval.browsing_status(act, parsed_events=True), "unknown")

    def test_incomplete_or_unparsed_stream_is_unknown(self):
        act = retrieval.tool_activity("codex", [{"type": "turn.started"}])
        self.assertEqual(retrieval.browsing_status(act, parsed_events=True), "unknown")
        self.assertEqual(retrieval.browsing_status(None, parsed_events=False), "unknown")

    def test_claude_server_search_counts(self):
        docs = [{"type": "system", "subtype": "init", "tools": []},
                {"type": "result", "is_error": False, "usage": {"server_tool_use": {"web_search_requests": 2}}}]
        act = retrieval.tool_activity("claude", docs)
        self.assertEqual(retrieval.browsing_status(act, parsed_events=True), "searched")

    def test_legacy_runs(self):
        self.assertEqual(retrieval.legacy_browsing("codex", "knowledge", {}), "unknown")
        self.assertEqual(retrieval.legacy_browsing("claude", "knowledge", {}), "none")
        self.assertEqual(retrieval.legacy_browsing("claude", "search", {"searched": True}), "searched")
        self.assertEqual(retrieval.arm_browsing("codex", "knowledge", {"browsing": "none"}), "none")


class MeasurementSetTests(unittest.TestCase):
    def _cfg(self, texts, exploratory=()):
        cfg = starter_config("ReqProof", "reqproof.com")
        from aeo.config import Prompt

        cfg.prompts = [Prompt(id=f"q{i}", text=t) for i, t in enumerate(texts)]
        for j, t in enumerate(exploratory):
            p = Prompt(id=f"x{j}", text=t)
            p.group = "exploratory"
            cfg.prompts.append(p)
        return cfg

    def test_hash_ignores_whitespace_and_exploratory(self):
        a = measurement.question_hash(self._cfg(["one  two", "three"]).prompts)
        b = measurement.question_hash(self._cfg(["one two", " three "], exploratory=["new q"]).prompts)
        self.assertEqual(a, b)
        c = measurement.question_hash(self._cfg(["one two", "three!"]).prompts)
        self.assertNotEqual(a, c)

    def test_record_and_mismatch(self):
        cfg = self._cfg(["a", "b"], exploratory=["c"])
        h = measurement.question_hash(cfg.prompts)
        cfg.measurement_set = {"id": "proof-v3", "version": 1, "question_hash": h}
        rec = measurement.measurement_record(cfg, cfg.prompts)
        self.assertTrue(rec["matches"])
        self.assertEqual((rec["measurement_count"], rec["exploratory_count"]), (2, 1))
        cfg.prompts[0].text = "a reworded"
        rec = measurement.measurement_record(cfg, cfg.prompts)
        self.assertFalse(rec["matches"])
        self.assertIn("exploratory", measurement.mismatch_message(rec))

    def test_run_exits_2_when_frozen_set_changed(self):
        from aeo import cli
        from aeo.config import write_config

        cfg = self._cfg(["a", "b"])
        cfg.measurement_set = {"id": "proof-v3", "version": 1, "question_hash": "sha256:old"}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            write_config(cfg, p)
            code = cli.main(["run", "--config", str(p), "--engine", "claude", "--dry-run"])
            self.assertEqual(code, 2)
            code = cli.main(["run", "--config", str(p), "--engine", "claude", "--dry-run", "--allow-measurement-change"])
            self.assertEqual(code, 0)


class RetrievalTests(unittest.TestCase):
    def test_claude_chain_with_results_and_fetch(self):
        docs = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {"query": "requirements traceability tool"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "x"}]},
             "tool_use_result": {"results": [{"content": [{"title": "ReqProof", "url": "https://reqproof.com/"}, {"title": "Jama", "url": "https://jamasoftware.com"}]}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "WebFetch", "input": {"url": "https://jamasoftware.com/x"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "ok"}]}},
        ]
        chain = retrieval.build_chain("claude", docs, "Use Jama (https://jamasoftware.com).")
        self.assertEqual([s["kind"] for s in chain["steps"]], ["search", "fetch"])
        self.assertEqual(len(chain["steps"][0]["results"]), 2)
        f = retrieval.brand_funnel(chain, "reqproof.com", named=False)
        self.assertTrue(f["in_results"])
        self.assertFalse(f["opened"])
        self.assertEqual(f["stage"], "returned but not opened")

    def test_claude_links_text_format(self):
        docs = [
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "WebSearch", "input": {"query": "q"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1",
              "content": 'Web search results for query: "q"\n\nLinks: [{"title":"A","url":"https://a.com"}]\n'}]}},
        ]
        chain = retrieval.claude_chain(docs)
        self.assertEqual(chain["steps"][0]["results"], [{"title": "A", "url": "https://a.com"}])
        f = retrieval.brand_funnel(chain, "reqproof.com", named=False, cited_urls=[])
        self.assertEqual(f["stage"], "never returned by search")

    def test_codex_chain_has_no_result_lists(self):
        docs = [json.loads(line) for line in (FIX / "codex-search.jsonl").read_text().splitlines() if line.strip()]
        chain = retrieval.build_chain("codex", docs, "see https://reqproof.com/docs")
        self.assertFalse(chain["results_exposed"])
        self.assertTrue(all(s["results"] is None for s in chain["steps"] if s["kind"] == "search"))
        self.assertTrue(chain["gaps"])
        f = retrieval.brand_funnel(chain, "reqproof.com", named=True)
        self.assertIsNone(f["in_results"])
        self.assertTrue(f["cited"])
        self.assertEqual(f["stage"], "named")

    def test_codex_shortened_query_copies_are_dropped(self):
        docs = [{"type": "item.completed", "item": {"id": "1", "type": "web_search", "action": {
            "type": "search", "query": "requirements traceability ...",
            "queries": ["requirements traceability ...", "requirements traceability tools"]}}}]
        self.assertEqual([s["query"] for s in retrieval.codex_chain(docs)["steps"]], ["requirements traceability tools"])

    def test_open_page_is_a_fetch(self):
        docs = [{"type": "item.completed", "item": {"id": "1", "type": "web_search", "action": {"type": "open_page", "url": "https://reqproof.com"}}}]
        chain = retrieval.codex_chain(docs)
        self.assertEqual(chain["steps"], [{"kind": "fetch", "url": "https://reqproof.com", "ok": None}])
        self.assertTrue(retrieval.brand_funnel(chain, "reqproof.com", named=False)["opened"])


def _arm(named=False, browsing=None, error=None, text="answer", retrieval_chain=None):
    a = {"raw_response_text": text, "brand_mentioned": named, "brand_mentions": [], "competitor_mentions": [],
         "searched": browsing == "searched", "search_queries": [], "vendors_in_search_queries": [], "recommended": False}
    if browsing:
        a["browsing"] = browsing
    if error:
        a["error"] = error
    if retrieval_chain is not None:
        a["retrieval"] = retrieval_chain
    return a


def _doc(engine, prompts):
    return {"workspace": {"brand": "ReqProof", "domain": "reqproof.com"}, "run": {}, "prompts": [
        {"prompt_id": pid, "prompt_text": pid, **({"group": g} if g else {}), "engines": {engine: {"knowledge": k, "search": s}}}
        for pid, g, k, s in prompts]}


class CountsTests(unittest.TestCase):
    def setUp(self):
        self.docs = {
            "claude": _doc("claude", [
                ("q1", None, _arm(named=True, browsing="none"), _arm(named=True, browsing="searched", text="https://reqproof.com")),
                ("q2", None, _arm(), _arm(browsing="searched")),
                ("x1", "exploratory", _arm(named=True, browsing="none"), _arm()),
            ]),
            "codex": _doc("codex", [
                ("q1", None, _arm(named=True), _arm(error="timeout")),  # legacy knowledge -> browsing unknown
                ("q2", None, _arm(), _arm()),
            ]),
        }
        self.judge = {"q1|claude|knowledge": {"stance": "recommend", "accurate": True},
                      "q1|claude|search": {"stance": "mention", "accurate": False},
                      "q1|codex|knowledge": {"stance": "mention"}}
        self.summary = counts.summarize(counts.answer_records(self.docs, self.judge))

    def test_separate_counts_from_records(self):
        t = self.summary["total"]
        self.assertEqual(t["answers"], 7)  # 8 planned measurement answers, one error
        self.assertEqual(t["errors"], 1)
        self.assertEqual(t["named"], 3)
        self.assertEqual(t["cited"], 1)
        self.assertEqual(t["recommended"], 1)
        self.assertEqual((t["accurate"], t["inaccurate"], t["accuracy_not_judged"]), (1, 1, 1))

    def test_unknown_browsing_is_not_unaided(self):
        t = self.summary["total"]
        self.assertEqual(t["named_unaided"], 1)
        self.assertEqual(t["named_with_search"], 1)
        self.assertEqual(t["named_browsing_unknown"], 1)
        self.assertEqual(self.summary["by_engine"]["codex"]["unaided_answers"], 0)

    def test_not_run_is_not_zero(self):
        self.assertEqual(self.summary["by_engine"]["grok"]["status"], "not run")
        self.assertIn("grok", self.summary["engines_not_run"])
        lines = counts.board_count_lines(self.summary, "ReqProof")
        self.assertIn("grok: not run in this report.", lines)
        self.assertNotIn("grok: 0", "\n".join(lines))

    def test_exploratory_reported_separately(self):
        self.assertEqual(self.summary["groups"]["exploratory"]["named"], 1)
        self.assertTrue(any(l.startswith("Exploratory") for l in counts.board_count_lines(self.summary, "ReqProof")))

    def test_headline_and_count_problems(self):
        h = counts.headline(self.summary, "ReqProof")
        self.assertIn("named in 3 of 7 answers", h)
        self.assertIn("1 more answers failed", h)
        self.assertEqual(counts.count_problems([h], self.summary), [])
        self.assertEqual(counts.count_problems(["named in 3 of 396 answers"], self.summary), ["3 of 396 answers"])

    def test_funnel_from_recorded_chain(self):
        chain = {"steps": [{"kind": "search", "query": "q", "results": [{"url": "https://reqproof.com"}]}],
                 "results_exposed": True, "cited_urls": []}
        docs = {"claude": _doc("claude", [("q1", None, _arm(), _arm(browsing="searched", retrieval_chain=chain))])}
        s = counts.summarize(counts.answer_records(docs))
        self.assertEqual(s["funnel"]["in_results"], 1)
        self.assertEqual(s["funnel"]["stages"], {"returned but not opened": 1})


class ChangeComparisonTests(unittest.TestCase):
    def test_exact_text_overlap_and_drift(self):
        from aeo.change import diff_runs, load_run

        def write(root, name, texts, env=None):
            run = root / name
            run.mkdir()
            doc = _doc("claude", [(f"id-{name}-{i}", None, _arm(named=(i == 0)), _arm()) for i, _ in enumerate(texts)])
            for p, t in zip(doc["prompts"], texts):
                p["prompt_text"] = t
            doc["schema_version"] = "aeo-cli-evidence-v1"
            doc["workspace"].update(aliases=["reqproof"], competitors=["Jama"])
            doc["run"] = {"run_id": name, "timestamp": "2026-10-01T00:00:00Z", "methodology_version": "aeo-cli-v1",
                          "engines": ["claude"], "samples_per_arm": 1}
            if env:
                doc["run"]["environment"] = {"claude": env}
            (run / "claude.json").write_text(json.dumps(doc))
            return run

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            a = write(root, "a", ["same one", "old wording", "gone"], env={"cli_version": "1.0", "argv": {}, "isolation": {}})
            b = write(root, "b", ["same  one", "new wording", "added"], env={"cli_version": "2.0", "argv": {}, "isolation": {}})
            payload = diff_runs(load_run(a), load_run(b), brand="ReqProof")
            self.assertEqual(payload["comparison"]["overlap"], 1)
            self.assertTrue(any("CLI version changed from 1.0 to 2.0" in w for w in payload["warnings"]))


class MappedCompareTests(unittest.TestCase):
    def test_pairs_are_labelled_exact_or_approximate(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("compare_mapped", Path(__file__).parents[1] / "scripts" / "compare_mapped.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        src = Path(mod.__file__).read_text()
        self.assertIn('"approximate"', src)
        self.assertIn("exact_pairs", src)


class SchemaTests(unittest.TestCase):
    def test_new_arm_fields_validate(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        from aeo import runner, score
        from aeo.parsers import parse_engine

        schema = json.loads((Path(__file__).parents[1] / "schemas" / "aeo-cli-evidence-v1.json").read_text())
        cfg = starter_config("ReqProof", "reqproof.com")
        stdout = (FIX / "claude-search-stream.jsonl").read_text()
        parsed = parse_engine("claude", stdout)
        arm = score.score_arm(parsed, cfg, observed=runner.observe("claude", "search", stdout, parsed.raw_response_text))
        arm["isolated"] = True
        doc = {"schema_version": "aeo-cli-evidence-v1",
               "workspace": {"brand": "ReqProof", "domain": "reqproof.com", "aliases": [], "competitors": []},
               "run": {"run_id": "r", "timestamp": "2026-10-10T00:00:00Z", "methodology_version": "aeo-cli-v1",
                       "engines": ["claude"], "samples_per_arm": 1, "measurement_set": {"id": "x"},
                       "environment": {"claude": {"cli_version": "1"}}},
               "prompts": [{"prompt_id": "q", "prompt_text": "q", "group": "measurement",
                            "engines": {"claude": {"search": arm}}}]}
        errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(doc)]
        self.assertEqual(errors, [])
        self.assertEqual(arm["browsing"], "searched")


if __name__ == "__main__":
    unittest.main()
