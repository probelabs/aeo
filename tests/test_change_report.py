"""Tiny-fixture tests for the AEO change / progress report."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aeo.change import (
    DEFAULT_TOP_LIST,
    diff_runs,
    load_run,
    render_change_html,
    write_change_report,
)


def _arm(
    *,
    mentioned: bool = False,
    comps: list[str] | None = None,
    searched: bool = False,
    qvendors: list[str] | None = None,
    error: str | None = None,
    text: str = "answer",
) -> dict:
    arm = {
        "raw_response_text": text,
        "brand_mentioned": mentioned,
        "brand_mentions": ["Tyk"] if mentioned else [],
        "competitor_mentions": list(comps or []),
        "searched": searched,
        "search_queries": ["api gateway rate limit"] if searched else [],
        "vendors_in_search_queries": list(qvendors or []),
        "recommended": mentioned,
    }
    if error:
        arm["error"] = error
    return arm


def _doc(
    engine: str,
    prompts: list[dict],
    *,
    brand: str = "Tyk",
    run_id: str = "run",
    competitors: list[str] | None = None,
) -> dict:
    return {
        "schema_version": "aeo-cli-evidence-v1",
        "workspace": {
            "brand": brand,
            "domain": "tyk.io",
            "aliases": ["tyk", "tyk.io"],
            "competitors": competitors or ["Kong", "Apigee"],
        },
        "run": {
            "run_id": run_id,
            "timestamp": "2026-09-01T00:00:00Z",
            "methodology_version": "aeo-cli-v1",
            "engines": [engine],
            "samples_per_arm": 1,
        },
        "prompts": prompts,
    }


def _prompt(pid: str, engine: str, knowledge: dict, search: dict, text: str | None = None) -> dict:
    return {
        "prompt_id": pid,
        "prompt_text": text or pid.replace("-", " "),
        "class": "focus",
        "engines": {engine: {"knowledge": knowledge, "search": search}},
    }


def _write_run(
    root: Path,
    *,
    name: str,
    prompts_by_engine: dict[str, list[dict]],
    judge: dict | None = None,
    vendors: dict | None = None,
    run_id: str = "run",
    competitors: list[str] | None = None,
) -> Path:
    run = root / name
    run.mkdir()
    for engine, prompts in prompts_by_engine.items():
        (run / f"{engine}.json").write_text(
            json.dumps(
                _doc(engine, prompts, run_id=f"{run_id}-{engine}", competitors=competitors)
            )
        )
    if judge is not None:
        (run / "judge.json").write_text(json.dumps(judge))
    if vendors is not None:
        (run / "vendors_judged.json").write_text(json.dumps(vendors))
    return run


class ChangeReportTests(unittest.TestCase):
    def _pair(self, tmp: Path) -> tuple[Path, Path]:
        """Baseline: no vendors_judged. Current: vendors_judged + judge + movement."""
        baseline = _write_run(
            tmp,
            name="tyk100-20260901",
            run_id="base",
            prompts_by_engine={
                "claude": [
                    _prompt(
                        "rate-limit",
                        "claude",
                        _arm(mentioned=False, comps=["Kong"]),
                        _arm(mentioned=False, comps=["Kong"], searched=True, qvendors=["Kong"]),
                    ),
                    _prompt(
                        "oauth",
                        "claude",
                        _arm(mentioned=True, comps=["Apigee"]),
                        _arm(mentioned=True, comps=["Apigee"], searched=True, qvendors=["Apigee"]),
                    ),
                    _prompt(
                        "graphql",
                        "claude",
                        _arm(mentioned=False, comps=["Kong"]),
                        _arm(mentioned=False, comps=["Kong"], searched=True, qvendors=["Kong"]),
                    ),
                    _prompt(
                        "broken",
                        "claude",
                        _arm(error="timeout"),
                        _arm(mentioned=False, comps=["Kong"]),
                    ),
                ]
            },
        )
        current = _write_run(
            tmp,
            name="tyk100-20260921",
            run_id="cur",
            prompts_by_engine={
                "claude": [
                    _prompt(
                        "rate-limit",
                        "claude",
                        _arm(mentioned=True, comps=["Kong"]),
                        _arm(mentioned=True, comps=["Kong"], searched=True, qvendors=["Kong"]),
                    ),
                    _prompt(
                        "oauth",
                        "claude",
                        _arm(mentioned=False, comps=["Kong"]),
                        _arm(mentioned=True, comps=["Apigee"], searched=True, qvendors=["Apigee"]),
                    ),
                    _prompt(
                        "graphql",
                        "claude",
                        _arm(mentioned=False, comps=["Kong"]),
                        _arm(mentioned=False, comps=["Kong"], searched=True, qvendors=["Kong"]),
                    ),
                    _prompt(
                        "new-only",
                        "claude",
                        _arm(mentioned=False),
                        _arm(mentioned=False, searched=True),
                    ),
                    _prompt(
                        "broken",
                        "claude",
                        _arm(mentioned=False),
                        _arm(mentioned=False, comps=["Kong"]),
                    ),
                ]
            },
            judge={
                "rate-limit|claude|search": {
                    "stance": "recommend",
                    "position": "first",
                    "quote": "Use Tyk for rate limits.",
                },
                "oauth|claude|search": {
                    "stance": "mention",
                    "position": "among",
                    "quote": "Tyk is also listed.",
                },
            },
            vendors={
                "rate-limit|claude|knowledge": {
                    "vendors": [
                        {"raw": "Kong Gateway", "normalized": "Kong", "role": "mention"},
                        {"raw": "UserCheck", "normalized": "UserCheck", "role": "mention"},
                    ],
                    "query_vendors": [],
                },
                "rate-limit|claude|search": {
                    "vendors": [
                        {"raw": "Kong", "normalized": "Kong", "role": "mention"},
                        {"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"},
                    ],
                    "query_vendors": [{"raw": "Kong", "normalized": "Kong"}],
                },
                "oauth|claude|search": {
                    "vendors": [{"raw": "Apigee", "normalized": "Apigee", "role": "mention"}],
                    "query_vendors": [{"raw": "Apigee", "normalized": "Apigee"}],
                },
                "graphql|claude|search": {
                    "vendors": [{"raw": "Kong", "normalized": "Kong", "role": "mention"}],
                    "query_vendors": [{"raw": "kong", "normalized": "Kong"}],
                },
            },
        )
        return baseline, current

    def test_brand_rates_and_transitions(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline, current = self._pair(tmp)
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")

            search = payload["brand_rates"]["overall"]["search"]
            self.assertEqual(search["baseline"]["hits"], 1)
            self.assertEqual(search["baseline"]["n"], 4)  # 4 search cells ok
            self.assertEqual(search["current"]["hits"], 2)
            self.assertEqual(search["current"]["n"], 5)
            self.assertAlmostEqual(search["baseline"]["rate"], 0.25)
            self.assertAlmostEqual(search["current"]["rate"], 0.4)
            self.assertEqual(search["delta_pp"], 15.0)

            k = payload["brand_rates"]["engines"]["claude"]["knowledge"]
            self.assertEqual(k["baseline"]["hits"], 1)
            self.assertEqual(k["baseline"]["n"], 3)  # broken is error
            self.assertEqual(k["current"]["hits"], 1)
            self.assertEqual(k["current"]["n"], 5)
            self.assertEqual(k["delta_pp"], -13.33)

            sr = payload["brand_rates"]["engines"]["claude"]["search_rate"]
            self.assertEqual(sr["baseline"]["searched"], 3)
            self.assertEqual(sr["baseline"]["n"], 4)
            self.assertEqual(sr["current"]["searched"], 4)
            self.assertEqual(sr["current"]["n"], 5)

            counts = payload["transitions"]["counts"]
            kinds = {r["transition"] for r in payload["transitions"]["rows"]}
            self.assertIn("miss_to_hit", kinds)
            self.assertIn("hit_to_miss", kinds)
            self.assertIn("hit_to_hit", kinds)
            self.assertIn("still_miss", kinds)
            self.assertGreaterEqual(counts["miss_to_hit"], 2)  # rate-limit K+S
            self.assertEqual(counts["hit_to_miss"], 1)  # oauth knowledge
            self.assertEqual(counts["hit_to_hit"], 1)  # oauth search
            self.assertGreaterEqual(counts["still_miss"], 1)

            self.assertEqual(
                payload["transitions"]["unmatched_prompt_ids"]["current_only"],
                ["new-only"],
            )
            self.assertEqual(payload["transitions"]["unmatched_prompt_ids"]["baseline_only"], [])
            incomplete_ids = {
                (c["prompt_id"], c["engine"], c["arm"])
                for c in payload["transitions"]["incomplete_cells"]
            }
            self.assertIn(("broken", "claude", "knowledge"), incomplete_ids)

    def test_hit_to_hit_stance_when_judge_on_both(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline = _write_run(
                tmp,
                name="b",
                prompts_by_engine={
                    "claude": [
                        _prompt("q1", "claude", _arm(mentioned=True), _arm(mentioned=True, searched=True))
                    ]
                },
                judge={
                    "q1|claude|search": {"stance": "mention", "position": "among", "quote": "also Tyk"},
                },
            )
            current = _write_run(
                tmp,
                name="c",
                prompts_by_engine={
                    "claude": [
                        _prompt("q1", "claude", _arm(mentioned=True), _arm(mentioned=True, searched=True))
                    ]
                },
                judge={
                    "q1|claude|search": {
                        "stance": "recommend",
                        "position": "first",
                        "quote": "Use Tyk first.",
                    },
                },
            )
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            row = next(
                r
                for r in payload["transitions"]["rows"]
                if r["prompt_id"] == "q1" and r["arm"] == "search"
            )
            self.assertEqual(row["transition"], "hit_to_hit")
            self.assertTrue(row["stance"]["changed"])
            self.assertEqual(row["stance"]["baseline"], "mention")
            self.assertEqual(row["stance"]["current"], "recommend")
            self.assertTrue(row["position"]["changed"])
            self.assertEqual(payload["transitions"]["stance_changed"], 1)
            self.assertEqual(payload["transitions"]["position_changed"], 1)

    def test_vendors_fallback_and_surprises(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline, current = self._pair(tmp)
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            self.assertEqual(payload["competitors"]["source"]["baseline"], "regex")
            self.assertEqual(payload["competitors"]["source"]["current"], "vendors_judged")

            names = {r["name"]: r for r in payload["competitors"]["all"]}
            self.assertIn("Kong", names)
            self.assertIn("Apigee", names)
            self.assertIn("UserCheck", names)
            self.assertTrue(names["UserCheck"]["surprise"])
            self.assertEqual(names["UserCheck"]["status"], "new")
            self.assertGreater(names["UserCheck"]["current"]["mentions"], 0)
            self.assertEqual(names["UserCheck"]["baseline"]["mentions"], 0)

            surprise = payload["summary"]["new_surprise"]
            self.assertIsNotNone(surprise)
            self.assertEqual(surprise["name"], "UserCheck")

            mover = payload["summary"]["biggest_competitor_mover"]
            self.assertIsNotNone(mover)
            self.assertIn(mover["name"], {"UserCheck", "Kong", "Apigee"})

            # Kong is on both sides after normalize (Kong Gateway ≡ Kong)
            self.assertGreater(names["Kong"]["current"]["mentions"], 0)
            self.assertGreater(names["Kong"]["baseline"]["mentions"], 0)
            self.assertEqual(names["Kong"]["status"], "riser")
            self.assertEqual(names["Apigee"]["status"], "faller")
            self.assertTrue(any(r["name"] == "Kong" for r in payload["competitors"]["risers"]))
            self.assertTrue(any(r["name"] == "Apigee" for r in payload["competitors"]["fallers"]))
            self.assertTrue(any(r["name"] == "UserCheck" for r in payload["competitors"]["new_surprise"]))
            self.assertTrue(any(r["name"] == "UserCheck" for r in payload["competitors"]["new"]))
            rank_by = {r["name"]: r for r in payload["competitors"]["rank"]}
            self.assertIn("Tyk", rank_by)
            self.assertTrue(rank_by["Tyk"]["is_brand"])
            self.assertEqual(rank_by["UserCheck"]["rank_label"], "NEW")
            self.assertIn(rank_by["Kong"]["rank_label"][0], {"↑", "↓", "—"})
            bvf = payload["competitors"]["brand_vs_field"]
            self.assertGreater(bvf["brand"]["current_mentions"], bvf["brand"]["baseline_mentions"])
            self.assertIn("field", bvf)
            self.assertEqual(payload["competitors"]["floor"], 1)

    def test_riser_faller_new_disappeared(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline = _write_run(
                tmp,
                name="b",
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "a",
                            "claude",
                            _arm(comps=["Kong", "Apigee"]),
                            _arm(comps=["Kong"], searched=True, qvendors=["Kong"]),
                        ),
                        _prompt(
                            "b",
                            "claude",
                            _arm(comps=["Apigee"]),
                            _arm(comps=["Apigee"], searched=True, qvendors=["Apigee"]),
                        ),
                    ]
                },
            )
            current = _write_run(
                tmp,
                name="c",
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "a",
                            "claude",
                            _arm(comps=["Kong", "Kong"]),
                            _arm(comps=["Kong"], searched=True, qvendors=["Kong"]),
                        ),
                        _prompt("b", "claude", _arm(comps=["Kong"]), _arm(searched=True)),
                    ]
                },
            )
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            by_status = {}
            for r in payload["competitors"]["all"]:
                by_status.setdefault(r["status"], []).append(r["name"])
            self.assertIn("Apigee", by_status.get("disappeared") or by_status.get("faller") or [])
            self.assertIn("Kong", by_status.get("riser") or by_status.get("flat") or [])

    def test_html_and_write(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline, current = self._pair(tmp)
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            html = render_change_html(payload)
            self.assertIn("change report", html.lower())
            self.assertIn("Tyk", html)
            self.assertIn("miss → hit", html)
            self.assertIn("UserCheck", html)
            self.assertIn("badge-surprise", html)
            self.assertIn("Brand mention rates", html)
            self.assertIn("Brand vs field", html)
            self.assertIn("Rank table", html)
            self.assertIn("New competitors", html)
            self.assertIn("No longer ranking", html)
            self.assertIn("Risers / fallers", html)
            self.assertIn("Unmatched prompt ids", html)
            self.assertIn("same roster", html.lower())
            self.assertIn("floor", html.lower())

            json_path, html_path = write_change_report(payload, current)
            self.assertTrue(json_path.exists())
            self.assertEqual(json_path.name, "change.json")
            self.assertTrue(html_path.name.endswith("-change-report.html"))
            dumped = json.loads(json_path.read_text())
            self.assertEqual(dumped["schema_version"], "aeo-change-v1")
            self.assertEqual(dumped["brand"], "Tyk")
            self.assertIn("search mention", dumped["summary"]["headline"].lower())

    def test_load_single_evidence_file_as_baseline(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            ev = tmp / "legacy.json"
            ev.write_text(
                json.dumps(
                    _doc(
                        "claude",
                        [
                            _prompt(
                                "q1",
                                "claude",
                                _arm(mentioned=False, comps=["Kong"]),
                                _arm(mentioned=False, searched=True, qvendors=["Kong"]),
                            )
                        ],
                    )
                )
            )
            current = _write_run(
                tmp,
                name="now",
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "q1",
                            "claude",
                            _arm(mentioned=True, comps=["Kong"]),
                            _arm(mentioned=True, searched=True, qvendors=["Kong"]),
                        )
                    ]
                },
            )
            payload = diff_runs(load_run(ev), load_run(current), brand="Tyk")
            self.assertEqual(payload["transitions"]["counts"]["miss_to_hit"], 2)
            self.assertEqual(payload["competitors"]["source"]["baseline"], "regex")

    def test_cli_writes_files(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "change_report_cli",
            Path(__file__).resolve().parents[1] / "scripts" / "change_report.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline, current = self._pair(tmp)
            rc = mod.main(
                ["--baseline", str(baseline), "--current", str(current), "--brand", "Tyk"]
            )
            self.assertEqual(rc, 0)
            self.assertTrue((current / "change.json").exists())
            htmls = list(current.glob("*-change-report.html"))
            self.assertEqual(len(htmls), 1)
            text = htmls[0].read_text()
            self.assertIn("tyk100-20260901", text)
            self.assertIn("tyk100-20260921", text)
            self.assertIn("Rank table", text)
            self.assertIn("Brand vs field", text)

    def test_floor_treats_near_zero_as_out(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline = _write_run(
                tmp,
                name="b",
                prompts_by_engine={
                    "claude": [
                        _prompt("a", "claude", _arm(comps=["Kong"]), _arm(comps=["Kong"], searched=True)),
                        _prompt("b", "claude", _arm(comps=["Kong"]), _arm(comps=["Apigee"], searched=True)),
                    ]
                },
            )
            current = _write_run(
                tmp,
                name="c",
                prompts_by_engine={
                    "claude": [
                        _prompt("a", "claude", _arm(comps=["Kong"]), _arm(searched=True)),
                        _prompt("b", "claude", _arm(), _arm(comps=["Apigee"], searched=True)),
                    ]
                },
            )
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk", floor=2)
            names = {r["name"]: r for r in payload["competitors"]["all"]}
            self.assertEqual(payload["competitors"]["floor"], 2)
            # Kong baseline 3, current 1 (< 2) → OUT
            self.assertEqual(names["Kong"]["status"], "disappeared")
            self.assertTrue(any(r["name"] == "Kong" for r in payload["competitors"]["disappeared"]))
            self.assertIn("floor", payload["methodology"]["floor_note"].lower())

    def test_skipped_engine_is_not_a_market_drop(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline = _write_run(
                tmp,
                name="b",
                prompts_by_engine={
                    "claude": [
                        _prompt("a", "claude", _arm(comps=["Kong"]), _arm(comps=["Kong"], searched=True)),
                    ],
                    "grok": [
                        _prompt("a", "grok", _arm(comps=["Apigee"]), _arm(comps=["Apigee"], searched=True)),
                    ],
                },
            )
            current = _write_run(
                tmp,
                name="c",
                prompts_by_engine={
                    "claude": [
                        _prompt("a", "claude", _arm(comps=["Kong"]), _arm(comps=["Kong"], searched=True)),
                    ],
                },
            )
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            cov = payload["engine_coverage"]
            self.assertEqual(cov["missing_in_current"], ["grok"])
            self.assertEqual(cov["comparable"], ["claude"])
            names = {r["name"] for r in payload["competitors"]["all"]}
            self.assertIn("Kong", names)
            self.assertNotIn("Apigee", names)
            html = render_change_html(payload)
            self.assertIn("Engine gap", html)
            self.assertIn("grok", html.lower())
            self.assertIn("not a market change", html.lower())

    def test_rank_labels_new_and_out(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            seeds = ["Kong", "Apigee", "Zuplo"]
            baseline = _write_run(
                tmp,
                name="b",
                competitors=seeds,
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "a",
                            "claude",
                            _arm(mentioned=True, comps=["Kong", "Apigee"]),
                            _arm(comps=["Kong"], searched=True, qvendors=["Kong"]),
                        ),
                    ]
                },
            )
            current = _write_run(
                tmp,
                name="c",
                competitors=seeds,
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "a",
                            "claude",
                            _arm(mentioned=True, comps=["Kong", "Zuplo"]),
                            _arm(comps=["Kong"], searched=True, qvendors=["Kong"]),
                        ),
                    ]
                },
            )
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            by_name = {r["name"]: r for r in payload["competitors"]["rank"]}
            self.assertEqual(by_name["Zuplo"]["rank_label"], "NEW")
            self.assertEqual(by_name["Apigee"]["rank_label"], "OUT")
            self.assertTrue(by_name["Tyk"]["is_brand"])
            self.assertIn("NEW", render_change_html(payload))
            self.assertIn("OUT", render_change_html(payload))

    def test_html_caps_new_list_and_colors_riser_up(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            seeds = [f"Vendor{i:02d}" for i in range(DEFAULT_TOP_LIST + 5)]
            baseline = _write_run(
                tmp,
                name="b",
                competitors=seeds + ["Kong"],
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "a",
                            "claude",
                            _arm(comps=["Kong"]),
                            _arm(comps=["Kong"], searched=True),
                        )
                    ]
                },
            )
            current = _write_run(
                tmp,
                name="c",
                competitors=seeds + ["Kong"],
                prompts_by_engine={
                    "claude": [
                        _prompt(
                            "a",
                            "claude",
                            _arm(comps=["Kong"] + seeds),
                            _arm(comps=["Kong"], searched=True),
                        ),
                        _prompt(
                            "b",
                            "claude",
                            _arm(comps=["Kong"]),
                            _arm(searched=True),
                        ),
                    ]
                },
            )
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            self.assertGreater(len(payload["competitors"]["new"]), DEFAULT_TOP_LIST)
            html = render_change_html(payload)
            self.assertIn(f"Showing {DEFAULT_TOP_LIST} of", html)
            last = seeds[-1]
            # change.json keeps the tail; HTML does not dump every name
            self.assertTrue(any(r["name"] == last for r in payload["competitors"]["new"]))
            self.assertNotIn(f">{last}<", html)
            self.assertIn("delta up", html)
            kong = next(r for r in payload["competitors"]["risers"] if r["name"] == "Kong")
            self.assertGreater(kong["delta"], 0)
            self.assertIn("<td>Kong</td>", html)

    def test_cli_movers_flag(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "change_report_cli",
            Path(__file__).resolve().parents[1] / "scripts" / "change_report.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            baseline, current = self._pair(tmp)
            rc = mod.main(
                [
                    "--baseline",
                    str(baseline),
                    "--current",
                    str(current),
                    "--brand",
                    "Tyk",
                    "--movers",
                    "1",
                ]
            )
            self.assertEqual(rc, 0)
            dumped = json.loads((current / "change.json").read_text())
            self.assertLessEqual(len(dumped["competitors"]["risers"]), 1)
            self.assertLessEqual(len(dumped["competitors"]["fallers"]), 1)

    def _split_pair(self, tmp: Path) -> tuple[Path, Path]:
        """Claude↑ Codex↓, absolute mentions up, field explodes, Grok baseline-only."""
        hypers = ["AWS", "Azure", "Google", "Cloudflare", "IBM"]
        seeds = ["Kong", "Apigee", *hypers]

        def _prompts(engine: str, hits: list[tuple[bool, bool]], comps: list[str]) -> list[dict]:
            out = []
            for i, (k_hit, s_hit) in enumerate(hits, start=1):
                pid = f"q{i}"
                out.append(
                    _prompt(
                        pid,
                        engine,
                        _arm(mentioned=k_hit, comps=comps),
                        _arm(
                            mentioned=s_hit,
                            comps=comps,
                            searched=True,
                            qvendors=comps[:2],
                        ),
                    )
                )
            return out

        # Claude 1/6 → 6/6; Codex 6/6 → 2/6; comparable brand 7→8.
        baseline = _write_run(
            tmp,
            name="b",
            competitors=seeds,
            prompts_by_engine={
                "claude": _prompts(
                    "claude",
                    [(False, False), (False, False), (False, True)],
                    ["Kong", "Apigee"],
                ),
                "codex": _prompts(
                    "codex",
                    [(True, True), (True, True), (True, True)],
                    ["Kong", "Apigee"],
                ),
                "grok": _prompts(
                    "grok",
                    [(True, True), (True, True), (True, True)],
                    ["Apigee"],
                ),
            },
        )
        current = _write_run(
            tmp,
            name="c",
            competitors=seeds,
            prompts_by_engine={
                "claude": _prompts(
                    "claude",
                    [(True, True), (True, True), (True, True)],
                    ["Kong", *hypers],
                ),
                "codex": _prompts(
                    "codex",
                    [(False, False), (False, False), (True, True)],
                    ["Kong", *hypers],
                ),
            },
        )
        return baseline, current

    def test_interpretation_engine_split_claude_up_codex_down(self):
        with tempfile.TemporaryDirectory() as td:
            baseline, current = self._split_pair(Path(td))
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            interp = payload["interpretation"]
            self.assertTrue(interp["engines_disagree"])
            by = {e["engine"]: e for e in interp["engine_split"]}
            self.assertEqual(set(by), {"claude", "codex"})
            self.assertEqual(by["claude"]["direction"], "up")
            self.assertEqual(by["codex"]["direction"], "down")
            self.assertEqual(by["claude"]["role"], "main_win")
            self.assertEqual(by["codex"]["role"], "main_loss")
            self.assertGreater(by["claude"]["mentions"]["delta"], 0)
            self.assertLess(by["codex"]["mentions"]["delta"], 0)
            verdict = interp["verdict"].lower()
            self.assertIn("mixed", verdict)
            self.assertIn("claude", verdict)
            self.assertIn("codex", verdict)
            story = interp["comparable_story"].lower()
            self.assertIn("claude", story)
            self.assertIn("codex", story)
            self.assertTrue(
                any("separate fire" in p.lower() for p in interp["practical"])
            )

    def test_share_down_absolute_up_wording(self):
        with tempfile.TemporaryDirectory() as td:
            baseline, current = self._split_pair(Path(td))
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            share = payload["interpretation"]["share_vs_absolute"]
            self.assertGreater(share["brand_delta"], 0)
            self.assertGreater(share["field_delta"], 0)
            self.assertLess(share["share_delta_pp"], 0)
            self.assertEqual(share["pattern"], "absolute_up_share_down")
            note = share["note"].lower()
            self.assertIn("field", note)
            self.assertIn("grew", note)
            self.assertIn("vanishing", note)
            self.assertIn("rose", note)

    def test_blended_rates_exclude_skipped_grok(self):
        with tempfile.TemporaryDirectory() as td:
            baseline, current = self._split_pair(Path(td))
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            rates = payload["brand_rates"]
            self.assertEqual(rates["compared_engines"], ["claude", "codex"])
            self.assertEqual(rates["skipped_engines"], ["grok"])
            self.assertIn("grok", payload["engine_coverage"]["missing_in_current"])

            overall_s = rates["overall"]["search"]
            claude_s = rates["engines"]["claude"]["search"]
            codex_s = rates["engines"]["codex"]["search"]
            grok_s = rates["engines"]["grok"]["search"]
            self.assertGreater(grok_s["baseline"]["n"], 0)
            self.assertEqual(grok_s["current"]["n"], 0)
            self.assertEqual(
                overall_s["baseline"]["n"],
                claude_s["baseline"]["n"] + codex_s["baseline"]["n"],
            )
            self.assertEqual(
                overall_s["current"]["n"],
                claude_s["current"]["n"] + codex_s["current"]["n"],
            )
            blended_if_grok = (
                claude_s["baseline"]["n"] + codex_s["baseline"]["n"] + grok_s["baseline"]["n"]
            )
            self.assertNotEqual(overall_s["baseline"]["n"], blended_if_grok)
            self.assertEqual(
                payload["summary"]["brand_delta_pp"]["search"],
                overall_s["delta_pp"],
            )
            # Fake blend would pull search down (Grok was 100% in baseline only).
            fake_b_hits = (
                claude_s["baseline"]["hits"]
                + codex_s["baseline"]["hits"]
                + grok_s["baseline"]["hits"]
            )
            fake_b_n = blended_if_grok
            fake_c_hits = claude_s["current"]["hits"] + codex_s["current"]["hits"]
            fake_c_n = claude_s["current"]["n"] + codex_s["current"]["n"]
            fake_delta = round((fake_c_hits / fake_c_n - fake_b_hits / fake_b_n) * 100.0, 2)
            self.assertNotEqual(overall_s["delta_pp"], fake_delta)
            caveats = " ".join(payload["interpretation"]["caveats"]).lower()
            self.assertIn("grok", caveats)
            headline = payload["summary"]["headline"].lower()
            self.assertIn("grok", headline)
            self.assertIn("blended", headline)

    def test_html_contains_executive_section(self):
        with tempfile.TemporaryDirectory() as td:
            baseline, current = self._split_pair(Path(td))
            payload = diff_runs(load_run(baseline), load_run(current), brand="Tyk")
            html = render_change_html(payload)
            self.assertIn("What this means for Tyk", html)
            self.assertIn('id="what-this-means"', html.replace("'", '"'))
            self.assertIn("Engine split", html)
            self.assertIn("Opposite moves", html)
            self.assertIn("What to do", html)
            self.assertIn("Caveats", html)
            self.assertIn("share-vs-absolute", html)
            self.assertIn(payload["interpretation"]["verdict"], html)
            self.assertIn("Claude", html)
            self.assertIn("Codex", html)
            self.assertIn("comparable engines", html.lower())


if __name__ == "__main__":
    unittest.main()
