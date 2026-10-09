import json
import tempfile
import unittest
from pathlib import Path

from aeo import google as g
from aeo.layers import inject_html, load_layers, render_google_markdown, render_layers_html, run_layers

FX = Path(__file__).resolve().parent / "fixtures" / "google"


def fx(name):
    return json.loads((FX / f"{name}.json").read_text(encoding="utf-8"))


PROOF_ALIASES = ["ReqProof", "reqproof", "reqproof.com", "probelabs/proof", "github.com/probelabs/proof"]


def proof_ctx():
    return {
        "brand": "Proof",
        "aliases": PROOF_ALIASES,
        "product_form_only": None,
        "patterns": g.own_patterns("reqproof.com", PROOF_ALIASES),
    }


def cfg(**extra):
    base = {
        "brand": "Proof",
        "domain": "reqproof.com",
        "aliases": PROOF_ALIASES,
        "competitors": ["Jama"],
        "engines": ["claude"],
        "prompts": [{"id": "p1", "text": "How do I recover requirements from legacy code?"}],
    }
    base.update(extra)
    return base


class FakeDFS:
    """Replays recorded DataForSEO responses by (keyword, endpoint); queues allow 40101 first."""

    def __init__(self, routes):
        self.routes = {k: list(v) for k, v in routes.items()}
        self.calls = []

    def __call__(self, path, payload):
        p = payload[0]
        kind = "ai_mode" if "ai_mode" in path else p.get("device")
        self.calls.append((kind, p["keyword"], p.get("depth")))
        q = self.routes.get((kind, p["keyword"])) or self.routes.get((kind, "*"))
        if not q:
            raise AssertionError(f"unexpected call {kind} {p['keyword']}")
        return q.pop(0) if len(q) > 1 else q[0]


class MatchingTests(unittest.TestCase):
    def test_own_patterns_keep_only_domain_like_aliases(self):
        self.assertEqual(g.own_patterns("reqproof.com", PROOF_ALIASES), ["reqproof.com", "github.com/probelabs/proof"])

    def test_is_own_url(self):
        pats = g.own_patterns("reqproof.com", PROOF_ALIASES)
        self.assertTrue(g.is_own_url("https://www.reqproof.com/topics/x", pats))
        self.assertTrue(g.is_own_url("https://blog.reqproof.com/a", pats))
        self.assertTrue(g.is_own_url("https://github.com/probelabs/proof/blob/main/README.md", pats))
        self.assertFalse(g.is_own_url("https://github.com/probelabs/other", pats))
        self.assertFalse(g.is_own_url("https://notreqproof.com/", pats))
        self.assertFalse(g.is_own_url("https://example.com/proof", pats))

    def test_bare_proof_word_is_not_a_brand_hit(self):
        hits = g.brand_text_hits("You need proof that the change works. Proof: a machine-checked argument.", "Proof", PROOF_ALIASES)
        self.assertEqual(hits, [])
        self.assertIn("ReqProof", g.brand_text_hits("Tools like ReqProof link requirements to code.", "Proof", PROOF_ALIASES))


class ParseTests(unittest.TestCase):
    def test_own_rank_from_recorded_serp(self):
        a = g.analyze_serp(fx("serp_own_ranked"), proof_ctx())
        self.assertTrue(a["ok"])
        self.assertEqual(a["own_rank"], 1)
        self.assertTrue(a["top10"][0]["own"])
        self.assertEqual(len(a["top10"]), 10)
        self.assertTrue(a["aio_present"])
        self.assertFalse(a["own_cited"])
        self.assertIn("reqproof.com", a["own_urls"][0]["url"])

    def test_aio_references_and_not_ranked(self):
        a = g.analyze_serp(fx("serp_aio_no_own"), proof_ctx())
        self.assertTrue(a["aio_present"])
        self.assertGreater(len(a["aio_text"]), 100)
        self.assertEqual(len(a["aio_references"]), 7)
        r = a["aio_references"][0]
        self.assertEqual(set(r) >= {"domain", "url", "title", "own"}, True)
        self.assertIsNone(a["own_rank"])
        self.assertFalse(a["own_cited"])

    def test_watch_tier_drops_aio_text(self):
        a = g.analyze_serp(fx("serp_aio_no_own"), proof_ctx(), full=False)
        self.assertNotIn("aio_text", a)
        self.assertTrue(a["aio_references"])

    def test_own_citation_detected(self):
        raw = fx("serp_aio_no_own")
        aio = [it for it in raw["tasks"][0]["result"][0]["items"] if it["type"] == "ai_overview"][0]
        aio.setdefault("references", []).append({"domain": "reqproof.com", "url": "https://reqproof.com/topics/recover", "title": "Recover requirements"})
        a = g.analyze_serp(raw, proof_ctx())
        self.assertTrue(a["own_cited"])
        self.assertTrue(a["aio_references"][-1]["own"])

    def test_partial_is_usable_and_flagged(self):
        a = g.analyze_serp(fx("serp_partial_40106"), proof_ctx())
        self.assertTrue(a["ok"])
        self.assertTrue(a["partial"])

    def test_40101_is_an_error(self):
        a = g.analyze_serp(fx("serp_40101"), proof_ctx())
        self.assertFalse(a["ok"])
        self.assertEqual(a["status_code"], 40101)

    def test_ai_mode_refs(self):
        a = g.analyze_ai_mode(fx("ai_mode"), proof_ctx())
        self.assertTrue(a["ok"])
        self.assertEqual(len(a["references"]), 6)
        self.assertFalse(a["own_cited"])


class CostTests(unittest.TestCase):
    def test_estimate(self):
        self.assertEqual(g.serp_call_cost(10), 0.004)
        self.assertEqual(g.serp_call_cost(100), 0.022)
        t, w = g.config_searches(cfg(google_targets=["a", "b"], google_watch=["c", "a"]))
        self.assertEqual([x["query"] for x in w], ["c"])  # targets win over watch duplicates
        est = g.estimate_cost(t, w, g.load_settings({}))
        self.assertAlmostEqual(est["targets_usd"], 2 * (0.022 + 0.004))
        self.assertAlmostEqual(est["watch_usd"], 0.022)
        s = g.load_settings({"google": {"ai_mode": False, "watch_depth": 10, "max_cost_usd": 5}})
        est = g.estimate_cost(t, w, s)
        self.assertAlmostEqual(est["total_usd"], 2 * 0.022 + 0.004)
        self.assertEqual(est["cap_usd"], 5)

    def test_over_cap_makes_no_calls(self):
        fake = FakeDFS({})
        with tempfile.TemporaryDirectory() as d:
            doc = g.run_google_layer(
                cfg(google_targets=["a"] * 1 + ["b", "c"], google={"max_cost_usd": 0.01}),
                d, client=g.DataForSEOClient(transport=fake), log=lambda m: None, sleep=lambda s: None,
            )
            self.assertIsNone(doc)
            self.assertEqual(fake.calls, [])
            self.assertFalse((Path(d) / "google.json").exists())


class RetryTests(unittest.TestCase):
    def test_retries_40101_then_succeeds(self):
        fake = FakeDFS({("desktop", "*"): [fx("serp_40101"), fx("serp_aio_no_own")]})
        budget = g.Budget(1.0)
        r, codes, capped = g.fetch_with_retries(g.DataForSEOClient(transport=fake), g.SERP_PATH, {"keyword": "k", "device": "desktop"}, budget, 0.022, sleep=lambda s: None)
        self.assertEqual(codes, [40101, 20000])
        self.assertTrue(g.usable(r))
        self.assertFalse(capped)

    def test_partial_kept_after_retries(self):
        fake = FakeDFS({("desktop", "*"): [fx("serp_partial_40106")]})
        r, codes, _ = g.fetch_with_retries(g.DataForSEOClient(transport=fake), g.SERP_PATH, {"keyword": "k", "device": "desktop"}, g.Budget(1.0), 0.022, retries=3, sleep=lambda s: None)
        self.assertEqual(codes, [40106, 40106, 40106])
        self.assertEqual(g.task_status(r), 40106)

    def test_budget_stops_retries(self):
        fake = FakeDFS({("desktop", "*"): [fx("serp_40101")]})
        budget = g.Budget(0.03)
        budget.add(0.02)
        r, codes, capped = g.fetch_with_retries(g.DataForSEOClient(transport=fake), g.SERP_PATH, {"keyword": "k"}, budget, 0.022, sleep=lambda s: None)
        self.assertTrue(capped)
        self.assertEqual(codes, [])


def routes():
    return {
        ("desktop", "recover requirements from legacy code"): [fx("serp_40101"), fx("serp_aio_no_own")],
        ("ai_mode", "recover requirements from legacy code"): [fx("ai_mode")],
        ("desktop", "soc 2 business logic gap"): [fx("serp_own_ranked")],
        ("desktop", "legacy modernization testing"): [fx("serp_partial_40106")],
    }


class LayerRunTests(unittest.TestCase):
    def config(self, d, **extra):
        c = cfg(
            google_targets=[
                {"query": "recover requirements from legacy code", "priority": 1, "page_should_demonstrate": "One recovered requirement."},
            ],
            google_watch=["soc 2 business logic gap", "legacy modernization testing"],
            google={"workers": 1},
            **extra,
        )
        p = Path(d) / "aeo.config.json"
        p.write_text(json.dumps(c), encoding="utf-8")
        return p

    def test_full_run_writes_sidecar_and_renders(self):
        with tempfile.TemporaryDirectory() as d:
            cp = self.config(d)
            fake = FakeDFS(routes())
            out = Path(d) / "runs" / "r1.google"
            res = run_layers(cp, out, google_client=g.DataForSEOClient(transport=fake), skip_gsc=True, log=lambda m: None, sleep=lambda s: None)
            doc = res["google"]
            self.assertIsNotNone(doc)
            self.assertTrue((out / "google.json").exists())
            self.assertTrue((out / "google.md").exists())
            # depth 100 for desktop calls, AI Mode only for targets
            kinds = sorted((k, q) for k, q, _ in fake.calls)
            self.assertEqual(kinds.count(("ai_mode", "recover requirements from legacy code")), 1)
            self.assertNotIn(("ai_mode", "soc 2 business logic gap"), kinds)
            t = doc["searches"][0]
            self.assertEqual(t["tier"], "target")
            self.assertEqual(t["serp"]["status_codes"], [40101, 20000])
            self.assertTrue(t["ai_mode"]["ok"])
            w = {x["query"]: x for x in doc["searches"] if x["tier"] == "watch"}
            self.assertEqual(w["soc 2 business logic gap"]["serp"]["own_rank"], 1)
            self.assertNotIn("aio_text", w["soc 2 business logic gap"]["serp"])
            self.assertEqual(doc["summary"]["watch"]["own_top10"], 1)
            md = render_google_markdown(doc)
            self.assertIn("## Google", md)
            self.assertIn("not in top 100", md)
            frag = render_layers_html(doc, None)
            self.assertIn('id="google"', frag)
            self.assertIn('class="own"', frag)
            page = inject_html("<html><body><main><p>board</p></main></body></html>", frag)
            self.assertLess(page.index("board"), page.index('id="google"'))
            self.assertLess(page.index('id="google"'), page.index("</main>"))
            gd, sd = load_layers(Path(d) / "runs" / "r1.json")
            self.assertEqual(gd["schema_version"], g.SCHEMA_VERSION)
            self.assertIsNone(sd)

    def test_second_run_has_deltas(self):
        with tempfile.TemporaryDirectory() as d:
            cp = self.config(d)
            runs = Path(d) / "runs"
            run_layers(cp, runs / "r1.google", google_client=g.DataForSEOClient(transport=FakeDFS(routes())), skip_gsc=True, log=lambda m: None, sleep=lambda s: None)
            first = json.loads((runs / "r1.google" / "google.json").read_text())
            first["generated_at"] = "2026-01-01T00:00:00Z"
            (runs / "r1.google" / "google.json").write_text(json.dumps(first))
            r2 = routes()
            raw = fx("serp_aio_no_own")
            items = raw["tasks"][0]["result"][0]["items"]
            aio = [it for it in items if it["type"] == "ai_overview"][0]
            aio["references"] = [{"domain": "reqproof.com", "url": "https://reqproof.com/r", "title": "R"}]
            org = [it for it in items if it["type"] == "organic"]
            org[3].update({"domain": "reqproof.com", "url": "https://reqproof.com/r"})
            r2[("desktop", "recover requirements from legacy code")] = [raw]
            res = run_layers(cp, runs / "r2.google", google_client=g.DataForSEOClient(transport=FakeDFS(r2)), skip_gsc=True, log=lambda m: None, sleep=lambda s: None)
            doc = res["google"]
            self.assertTrue(doc["baseline"]["path"].endswith("r1.google/google.json"))
            dl = doc["searches"][0]["delta"]
            self.assertEqual(dl["citation"], "gained")
            self.assertEqual(dl["rank_change"], "entered top 100")
            self.assertIn("reqproof.com", dl["new_top10_domains"])
            self.assertIn("AIO citation gained", render_google_markdown(doc))

    def test_no_credentials_skips_silently(self):
        with tempfile.TemporaryDirectory() as d:
            cp = self.config(d)
            res = run_layers(cp, Path(d) / "x.google", env={"GSC_OAUTH_TOKEN_FILE": str(Path(d) / "none.json")}, log=lambda m: self.fail(m))
            self.assertEqual(res, {"google": None, "gsc": None, "proposals": None})
            self.assertFalse((Path(d) / "x.google").exists())

    def test_disabled_in_config(self):
        with tempfile.TemporaryDirectory() as d:
            cp = self.config(d)
            c = json.loads(cp.read_text())
            c["google"] = {"enabled": False}
            c["gsc"] = {"enabled": False}
            cp.write_text(json.dumps(c))
            fake = FakeDFS({})
            res = run_layers(cp, Path(d) / "x.google", google_client=g.DataForSEOClient(transport=fake), gsc_query=lambda *a: self.fail("gsc called"))
            self.assertIsNone(res["google"])
            self.assertEqual(fake.calls, [])

    def test_missing_targets_writes_unapproved_proposals_only(self):
        with tempfile.TemporaryDirectory() as d:
            c = cfg()
            cp = Path(d) / "aeo.config.json"
            cp.write_text(json.dumps(c))
            reply = json.dumps({"result": json.dumps({
                "targets": [{"query": f"target search {i}", "why": "fit"} for i in range(12)],
                "watch": [{"query": f"watch search {i}"} for i in range(25)] + [{"query": "target search 1"}],
            })})
            fake = FakeDFS({})
            res = run_layers(cp, Path(d) / "x.google", google_client=g.DataForSEOClient(transport=fake), skip_gsc=True, llm=lambda p: reply, log=lambda m: None)
            self.assertIsNone(res["google"])
            self.assertEqual(fake.calls, [])  # suggestions are never fetched as if approved
            props = json.loads(g.proposals_path(cp).read_text())
            self.assertEqual(props["status"], "unapproved")
            self.assertFalse(props["approved"])
            self.assertEqual(len(props["google_targets"]), 10)
            self.assertEqual(len(props["google_watch"]), 20)
            self.assertNotIn("google_targets", json.loads(cp.read_text()))

    def test_proposal_prompt_uses_product_context(self):
        p = g.proposal_prompt(cfg(description="Requirements traceability for code"))
        self.assertIn("reqproof.com", p)
        self.assertIn("recover requirements from legacy code", p.lower())
        self.assertIn("Requirements traceability for code", p)


class CredentialTests(unittest.TestCase):
    def test_env_and_file(self):
        self.assertEqual(g.dataforseo_credentials({"DATAFORSEO_LOGIN": "a", "DATAFORSEO_PASSWORD": "b"}), ("a", "b"))
        self.assertEqual(g.dataforseo_credentials({"DATAFORSEO_USERNAME": "a", "DATAFORSEO_PASSWORD": "b"}), ("a", "b"))
        self.assertIsNone(g.dataforseo_credentials({}))
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "c.yaml"
            f.write_text("mcp:\n  env:\n    DATAFORSEO_LOGIN: user@x.com\n    DATAFORSEO_PASSWORD: 'pw123'\n")
            self.assertEqual(g.dataforseo_credentials({"DATAFORSEO_CREDENTIALS_FILE": str(f)}), ("user@x.com", "pw123"))


class RunHookTests(unittest.TestCase):
    def _run(self, extra):
        from unittest.mock import patch

        from aeo.cli import main
        from aeo.engines import ExecResult

        calls = []
        with tempfile.TemporaryDirectory() as d:
            cp = Path(d) / "aeo.config.json"
            cp.write_text(json.dumps(cfg(engines=["grok"], google_targets=["x"])))
            out = Path(d) / "runs" / "r.json"
            with patch("aeo.runner.run_invocation", return_value=ExecResult(stdout="answer", stderr="", returncode=0)), \
                    patch("aeo.layers.run_layers", side_effect=lambda *a, **k: calls.append((a, k)) or {}):
                rc = main(["run", "--config", str(cp), "--engine", "grok", "--arm", "knowledge", "--out", str(out), "--timeout", "5", *extra])
            self.assertEqual(rc, 0)
            return calls, out

    def test_full_run_triggers_layers_into_sidecar_dir(self):
        calls, out = self._run(["--google-max-cost", "0.5"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][1], out.parent / "r.google")
        self.assertEqual(calls[0][1]["google_overrides"], {"max_cost_usd": 0.5})

    def test_no_google_flag_and_only_id_skip_layers(self):
        self.assertEqual(self._run(["--no-google"])[0], [])
        self.assertEqual(self._run(["--only-id", "p1"])[0], [])


if __name__ == "__main__":
    unittest.main()


ENVELOPE_50000 = {"verion": "0.1", "status_code": 50000, "status_message": "Internal Server Error.", "cost": 0, "tasks_count": 0, "tasks": None}


class LiveRunRegressionTests(unittest.TestCase):
    """Bugs seen on the first live Proof run (2026-10-09)."""

    def test_envelope_50000_is_reported_not_none(self):
        self.assertEqual(g.task_status(ENVELOPE_50000), 50000)
        rec = g.analyze_serp(ENVELOPE_50000, proof_ctx())
        self.assertFalse(rec["ok"])
        self.assertEqual(rec["error"], "50000 Internal Server Error.")

    def test_exception_records_one_code_per_attempt(self):
        def boom(path, payload):
            raise RuntimeError("DataForSEO call failed: x: URLError")
        _, codes, _ = g.fetch_with_retries(g.DataForSEOClient(transport=boom), g.SERP_PATH, {"keyword": "k"}, g.Budget(1.0), 0.022, retries=2, sleep=lambda s: None)
        self.assertEqual(codes, [None, None])

    def test_load_layers_from_out_dir_itself(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "google-layers"
            out.mkdir()
            (out / "google.json").write_text(json.dumps({"schema_version": g.SCHEMA_VERSION, "searches": []}))
            gd, _ = load_layers(out)
            self.assertIsNotNone(gd)

    def test_retry_failed_refetches_only_failures(self):
        with tempfile.TemporaryDirectory() as d:
            c = cfg(google_watch=["soc 2 business logic gap", "legacy modernization testing"], google={"workers": 1, "ai_mode": False})
            out = Path(d) / "layers"
            first = FakeDFS({("desktop", "soc 2 business logic gap"): [fx("serp_own_ranked")],
                             ("desktop", "legacy modernization testing"): [ENVELOPE_50000]})
            doc1 = g.run_google_layer(c, out, client=g.DataForSEOClient(transport=first), log=lambda m: None, sleep=lambda s: None)
            self.assertEqual(doc1["summary"]["watch"]["ok"], 1)
            second = FakeDFS({("desktop", "legacy modernization testing"): [fx("serp_aio_no_own")]})
            doc2 = g.run_google_layer(c, out, client=g.DataForSEOClient(transport=second), log=lambda m: None, sleep=lambda s: None, retry_failed=True)
            self.assertEqual({k for _, k, _ in second.calls}, {"legacy modernization testing"})
            self.assertEqual(doc2["summary"]["watch"]["ok"], 2)
            self.assertEqual(doc2["retry_failed"]["previous_api_calls"], doc1["api_calls"])
            self.assertEqual(doc2["api_calls"], doc1["api_calls"] + len(second.calls))
