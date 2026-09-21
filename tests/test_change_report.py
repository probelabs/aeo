"""Tiny-fixture tests for the AEO change / progress report."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from aeo.change import (
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
                        _arm(mentioned=False, comps=["Apigee"]),
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
            self.assertEqual(sr["current"]["searched"], 3)
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
            self.assertIn("Unmatched prompt ids", html)
            self.assertIn("same roster", html.lower())

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


if __name__ == "__main__":
    unittest.main()
