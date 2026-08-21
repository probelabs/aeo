import json
import unittest
from pathlib import Path

from aeo.validate import validate_config, validate_evidence

ROOT = Path(__file__).resolve().parents[1]


class SchemaTests(unittest.TestCase):
    def test_evidence_schema_file_present(self):
        p = ROOT / "schemas" / "aeo-cli-evidence-v1.json"
        schema = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema["title"], "aeo-cli-evidence-v1")

    def test_config_schema_file_present(self):
        p = ROOT / "schemas" / "aeo-cli-config-v1.json"
        schema = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(schema["title"], "aeo-cli-config-v1")
        prompt_props = schema["properties"]["prompts"]["items"]["properties"]
        self.assertEqual(prompt_props["class"]["enum"], ["watch", "focus"])
        self.assertIn("why", prompt_props)
        self.assertIn("enabled", prompt_props)

    def test_example_fixture_validates(self):
        doc = json.loads(
            (ROOT / "examples" / "xerj" / "aeo-data" / "example-run.json").read_text(encoding="utf-8")
        )
        errors = validate_evidence(doc)
        self.assertEqual(errors, [], errors)
        self.assertEqual(doc["prompts"][0]["class"], "watch")

    def test_xerj_config_validates(self):
        doc = json.loads(
            (ROOT / "examples" / "xerj" / "aeo.config.json").read_text(encoding="utf-8")
        )
        errors = validate_config(doc)
        self.assertEqual(errors, [], errors)
        self.assertEqual(len(doc["prompts"]), 84)

    def test_bad_evidence_fails(self):
        errors = validate_evidence({"schema_version": "nope"})
        self.assertTrue(errors)

    def test_prompts_have_no_brand_or_rust(self):
        doc = json.loads(
            (ROOT / "examples" / "xerj" / "aeo.config.json").read_text(encoding="utf-8")
        )
        for p in doc["prompts"]:
            t = p["text"].lower()
            self.assertNotIn("xerj", t)
            self.assertNotIn("rust", t)


if __name__ == "__main__":
    unittest.main()
