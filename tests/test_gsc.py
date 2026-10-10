import io
import json
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path

from aeo import google as g
from aeo import gsc
from aeo.cli import main
from aeo.layers import render_gsc_markdown, render_layers_html, run_layers

import os
os.environ.setdefault("AEO_CANARY", "skip")  # no live identity canary in tests (unittest discover skips tests/__init__)

ROOT = Path(__file__).resolve().parents[1]
FX = json.loads((ROOT / "tests" / "fixtures" / "gsc" / "acme_search_analytics.json").read_text(encoding="utf-8"))
TODAY = date(2026, 10, 9)


class FakeGSC:
    def __init__(self, scale=1.0):
        self.calls = []
        self.scale = scale
        self.period = {}
        # run_layers() uses the real clock, so answer for today's windows as well as TODAY's.
        for day in (TODAY, datetime.now(timezone.utc).date()):
            w = gsc.windows(day)
            self.period.update({w["current"]["start"]: "current", w["previous"]["start"]: "previous"})

    def __call__(self, site, body):
        self.calls.append((site, body))
        period = self.period[body["startDate"]]
        dim = (body.get("dimensions") or ["total"])[0]
        resp = json.loads(json.dumps(FX[f"{period}/{dim}"]))
        for r in resp["rows"]:
            r["impressions"] = int(r["impressions"] * self.scale)
        return resp


def acme_cfg(**extra):
    c = {
        "brand": "Acme",
        "domain": "acme.example",
        "aliases": ["acme.example"],
        "competitors": ["ripgrep"],
        "engines": ["claude"],
        "prompts": [{"id": "q1", "text": "best local search tool?"}],
        "google_targets": ["local folder search", "never seen search"],
        "google_watch": ["markdown search tool"],
    }
    c.update(extra)
    return c


class WindowTests(unittest.TestCase):
    def test_28_vs_prior_28_with_lag(self):
        w = gsc.windows(TODAY)
        self.assertEqual(w["current"], {"start": "2026-09-09", "end": "2026-10-06"})
        self.assertEqual(w["previous"], {"start": "2026-08-12", "end": "2026-09-08"})

    def test_default_site(self):
        self.assertEqual(gsc.load_settings({"domain": "Acme.example"})["site"], "sc-domain:acme.example")
        self.assertEqual(gsc.load_settings({"domain": "a.b", "gsc": {"site": "https://a.b/"}})["site"], "https://a.b/")


class LayerTests(unittest.TestCase):
    def run_layer(self, d, cfg=None, **kw):
        cp = Path(d) / "aeo.config.json"
        cp.write_text(json.dumps(cfg or acme_cfg()), encoding="utf-8")
        return cp, gsc.run_gsc_layer(json.loads(cp.read_text()), Path(d) / "runs" / "r1.google", query=FakeGSC(), today=TODAY,
                                     proposals_file=g.proposals_path(cp), **kw)

    def test_totals_tables_and_tracked_searches(self):
        with tempfile.TemporaryDirectory() as d:
            cp, doc = self.run_layer(d)
            self.assertEqual(doc["site"], "sc-domain:acme.example")
            self.assertEqual(doc["totals"]["current"]["clicks"], 40)
            self.assertEqual(doc["totals"]["change"]["clicks"], 15)
            self.assertEqual(doc["totals"]["change"]["position"], 3.7)  # moved up
            self.assertEqual(doc["top_queries"][0]["key"], "acme")
            self.assertEqual(doc["top_pages"][0]["key"], "https://acme.example/")
            striking = [r["key"] for r in doc["striking_distance"]]
            self.assertEqual(striking, ["local folder search", "search folder by content", "markdown search tool", "ripgrep vs acme"])
            zero = [r["key"] for r in doc["impressions_no_clicks"]]
            self.assertEqual(zero, ["search folder by content", "grep alternative for docs"])
            s = {x["query"]: x for x in doc["searches"]}
            self.assertEqual(s["local folder search"]["current"]["impressions"], 300)
            self.assertEqual(s["local folder search"]["change"]["impressions"], 100)
            self.assertIsNone(s["never seen search"]["current"])
            self.assertEqual(s["markdown search tool"]["tier"], "watch")
            self.assertTrue((Path(d) / "runs" / "r1.google" / "gsc.json").exists())
            self.assertIn("## Search Console", (Path(d) / "runs" / "r1.google" / "gsc.md").read_text())

    def test_untracked_queries_become_unapproved_watch_suggestions(self):
        with tempfile.TemporaryDirectory() as d:
            cp, doc = self.run_layer(d)
            props = json.loads(g.proposals_path(cp).read_text())
            self.assertEqual(props["status"], "unapproved")
            q = [x["query"] for x in props["gsc_watch_suggestions"]]
            self.assertIn("search folder by content", q)
            self.assertNotIn("local folder search", q)  # already a target
            self.assertNotIn("markdown search tool", q)  # already on watch
            self.assertEqual(json.loads(cp.read_text())["google_watch"], ["markdown search tool"])  # config untouched

    def test_run_over_run_delta_and_render(self):
        with tempfile.TemporaryDirectory() as d:
            cp, first = self.run_layer(d)
            first["generated_at"] = "2026-01-01T00:00:00Z"
            (Path(d) / "runs" / "r1.google" / "gsc.json").write_text(json.dumps(first))
            res = run_layers(cp, Path(d) / "runs" / "r2.google", gsc_query=FakeGSC(scale=2), skip_google=True, log=lambda m: None)
            doc = res["gsc"]
            self.assertTrue(doc["baseline"]["path"].endswith("r1.google/gsc.json"))
            self.assertIsNotNone(doc["totals"]["vs_previous_run"])
            md = render_gsc_markdown(doc)
            self.assertIn("Striking distance", md)
            self.assertIn("Since the previous run", md)
            frag = render_layers_html(None, doc)
            self.assertIn('id="search-console"', frag)
            self.assertIn("impressions but no clicks", frag)
            self.assertIn("places higher (better)", frag)

    def test_google_cards_show_gsc_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            cp, sdoc = self.run_layer(d)
            gdoc = {"schema_version": g.SCHEMA_VERSION, "domain": "acme.example", "settings": {"depth": 100},
                    "searches": [{"id": "a", "query": "local folder search", "tier": "target", "priority": 1,
                                  "serp": {"ok": True, "aio_present": False, "top10": [], "own_rank": None, "aio_references": []}}]}
            frag = render_layers_html(gdoc, sdoc)
            self.assertIn("GSC 28d: 12 clicks, 300 impr", frag)


class CredentialTests(unittest.TestCase):
    def test_none_without_files(self):
        self.assertIsNone(gsc.gsc_credentials({"GSC_OAUTH_TOKEN_FILE": "/nonexistent/token.json"}))

    def test_authorized_user_file_refreshes(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "au.json"
            f.write_text(json.dumps({"type": "authorized_user", "client_id": "cid", "client_secret": "sec", "refresh_token": "rt"}))
            ts = gsc.gsc_credentials({"GSC_CREDENTIALS_FILE": str(f)})
            seen = {}

            def post(url, body):
                seen["url"], seen["body"] = url, body.decode()
                return {"access_token": "AT", "expires_in": 3600}

            self.assertEqual(ts.token(post), "AT")
            self.assertIn("refresh_token=rt", seen["body"])
            self.assertEqual(seen["url"], gsc.TOKEN_URL)

    def test_mcp_token_file_with_valid_access_token(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.json"
            f.write_text(json.dumps({"access_token": "AT2", "refresh_token": "rt", "expiry_date": (time.time() + 3600) * 1000}))
            ts = gsc.gsc_credentials({"GSC_OAUTH_TOKEN_FILE": str(f)})
            self.assertEqual(ts.token(lambda *a: self.fail("should not refresh")), "AT2")

    def test_expired_token_without_client_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "t.json"
            f.write_text(json.dumps({"access_token": "old", "refresh_token": "rt", "expiry_date": 1000}))
            self.assertIsNone(gsc.gsc_credentials({"GSC_OAUTH_TOKEN_FILE": str(f)}))


def _arm(mentioned):
    return {"raw_response_text": "Acme is good" if mentioned else "use ripgrep", "brand_mentioned": mentioned,
            "brand_mentions": ["Acme"] if mentioned else [], "competitor_mentions": [], "searched": False,
            "search_queries": [], "vendors_in_search_queries": [], "recommended": mentioned}


class BoardSeparationTests(unittest.TestCase):
    def test_board_scores_identical_with_and_without_layers(self):
        doc = {"schema_version": "aeo-cli-evidence-v1",
               "workspace": {"brand": "Acme", "domain": "acme.example", "aliases": ["acme.example"], "competitors": []},
               "run": {"run_id": "r1", "timestamp": "2026-10-09T00:00:00Z", "methodology_version": "aeo-cli-v1", "engines": ["claude"], "samples_per_arm": 1},
               "prompts": [{"prompt_id": "q1", "prompt_text": "best?", "engines": {"claude": {"knowledge": _arm(False), "search": _arm(True)}}}]}
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d) / "runs"
            runs.mkdir()
            ev = runs / "r1.json"
            ev.write_text(json.dumps(doc))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["board", str(ev), "--format", "json"])
            before = json.loads((Path(d) / "boards" / "r1.json").read_text())
            cp = Path(d) / "aeo.config.json"
            cp.write_text(json.dumps(acme_cfg()))
            gsc.run_gsc_layer(json.loads(cp.read_text()), runs / "r1.google", query=FakeGSC(), today=TODAY)
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                main(["board", str(ev), "--format", "html"])
            after = json.loads((Path(d) / "boards" / "r1.json").read_text())
            self.assertEqual(before["scoreboard"], after["scoreboard"])
            self.assertEqual(before["groups"], after["groups"])
            self.assertIn("search_console", after)
            self.assertNotIn("search_console", before)
            self.assertIn('id="search-console"', out.getvalue())
            md = (Path(d) / "boards" / "r1.md").read_text()
            self.assertIn("## Search Console", md)
            out = io.StringIO()
            with redirect_stdout(out):
                main(["report", str(ev)])
            self.assertIn("## Search Console", out.getvalue())


class SchemaTests(unittest.TestCase):
    def test_config_schema_has_layer_fields(self):
        schema = json.loads((ROOT / "schemas" / "aeo-cli-config-v1.json").read_text())
        for k in ("google_targets", "google_watch", "google", "gsc"):
            self.assertIn(k, schema["properties"])
        self.assertIn("max_cost_usd", schema["properties"]["google"]["properties"])
        self.assertIn("site", schema["properties"]["gsc"]["properties"])
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            return
        from aeo.validate import validate_config

        cfg = acme_cfg(google_targets=[{"query": "x", "priority": 1, "lead": "l"}, "y"], google={"max_cost_usd": 1}, gsc={"site": "sc-domain:acme.example"})
        self.assertEqual(validate_config(cfg), [])


if __name__ == "__main__":
    unittest.main()
