import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from aeo.cli import main
from aeo.engines import ExecResult


class ResumeTests(unittest.TestCase):
    def test_resume_skips_completed_cell(self):
        cfg = {
            "brand": "XERJ",
            "domain": "xerj.org",
            "aliases": ["xerj"],
            "competitors": ["ripgrep"],
            "engines": ["grok"],
            "samples_per_arm": 1,
            "prompts": [
                {"id": "already-done", "text": "done query", "class": "focus"},
                {"id": "todo", "text": "todo query", "class": "focus"},
            ],
        }
        existing = {
            "schema_version": "aeo-cli-evidence-v1",
            "workspace": {"brand": "XERJ", "domain": "xerj.org", "aliases": ["xerj"], "competitors": ["ripgrep"]},
            "run": {"run_id": "aeo-test", "timestamp": "2026-08-21T00:00:00Z", "methodology_version": "v0", "engines": ["grok"], "samples_per_arm": 1},
            "prompts": [
                {
                    "prompt_id": "already-done",
                    "prompt_text": "done query",
                    "engines": {
                        "grok": {
                            "knowledge": {"mentioned": False, "searched": False},
                            "search": {"mentioned": False, "searched": False},
                        }
                    },
                }
            ],
        }
        calls = []

        def fake_run(inv, timeout=300):
            calls.append((inv.engine, inv.arm, inv.prompt))
            return ExecResult(stdout="no brand here", stderr="", returncode=0)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg_path = tmp / "aeo.config.json"
            out = tmp / "run.json"
            import json
            cfg_path.write_text(json.dumps(cfg))
            out.write_text(json.dumps(existing))
            with patch("aeo.cli.run_invocation", side_effect=fake_run):
                rc = main(["run", "--config", str(cfg_path), "--engine", "grok", "--arm", "both", "--out", str(out), "--timeout", "5"])
            self.assertEqual(rc, 0)
            doc = json.loads(out.read_text())
            ids = [p["prompt_id"] for p in doc["prompts"]]
            self.assertEqual(ids, ["already-done", "todo"])
            self.assertEqual(len(calls), 2)
            self.assertTrue(all("todo query" in c[2] for c in calls))


if __name__ == "__main__":
    unittest.main()
