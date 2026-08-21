import json
import unittest
from pathlib import Path

from aeo.config import filter_prompts, load_config
from aeo.validate import validate_config

ROOT = Path(__file__).resolve().parents[1]


class ClassFilterTests(unittest.TestCase):
    def test_xerj_pack_counts(self):
        cfg = load_config(ROOT / "examples" / "xerj" / "aeo.config.json")
        self.assertEqual(len(cfg.prompts), 84)
        watch = filter_prompts(cfg.prompts, "watch")
        focus = filter_prompts(cfg.prompts, "focus")
        self.assertEqual(len(watch), 12)
        self.assertEqual(len(focus), 72)
        self.assertTrue(all(p.class_ == "watch" for p in watch))
        self.assertTrue(all(p.class_ == "focus" for p in focus))
        self.assertTrue(all(p.enabled for p in cfg.prompts))
        self.assertTrue(all(p.why == "knowledge_trap" for p in watch))

    def test_class_focus_selects_only_focus(self):
        cfg = load_config(ROOT / "examples" / "xerj" / "aeo.config.json")
        focus = filter_prompts(cfg.prompts, "focus")
        self.assertTrue(focus)
        self.assertTrue(all(p.class_ == "focus" for p in focus))
        self.assertFalse(any(p.class_ == "watch" for p in focus))
        all_ = filter_prompts(cfg.prompts, "all")
        self.assertEqual(len(all_), 84)

    def test_disabled_skipped_but_watch_stay_enabled(self):
        cfg = load_config(ROOT / "examples" / "xerj" / "aeo.config.json")
        watch = [p for p in cfg.prompts if p.class_ == "watch"]
        watch[0].enabled = False
        selected = filter_prompts(cfg.prompts, "watch")
        self.assertEqual(len(selected), 11)
        still = filter_prompts(cfg.prompts, "watch", include_disabled=True)
        self.assertEqual(len(still), 12)

    def test_config_with_class_fields_validates(self):
        doc = json.loads(
            (ROOT / "examples" / "xerj" / "aeo.config.json").read_text(encoding="utf-8")
        )
        errors = validate_config(doc)
        self.assertEqual(errors, [], errors)
        self.assertEqual(doc["prompts"][0]["class"], "watch")
        mini = {
            "schema_version": "aeo-cli-config-v1",
            "brand": "XERJ",
            "domain": "xerj.org",
            "aliases": ["xerj"],
            "competitors": ["ripgrep"],
            "engines": ["claude"],
            "prompts": [
                {
                    "id": "search-file-contents",
                    "text": "What's the best way to search through a folder of files by content?",
                    "class": "watch",
                    "why": "knowledge_trap",
                    "enabled": True,
                }
            ],
        }
        self.assertEqual(validate_config(mini), [])

    def test_ids_filter(self):
        cfg = load_config(ROOT / "examples" / "xerj" / "aeo.config.json")
        one = filter_prompts(cfg.prompts, "all", ids=["search-pdfs-folder"])
        self.assertEqual([p.id for p in one], ["search-pdfs-folder"])
        two = filter_prompts(
            cfg.prompts, "focus", ids=["search-pdfs-folder", "search-file-contents"]
        )
        self.assertEqual([p.id for p in two], ["search-pdfs-folder"])
        missing = filter_prompts(cfg.prompts, "all", ids=["does-not-exist"])
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
