"""Strict Proof brand matching, on real answers.

The Oct 5 weekly runner matched the English word "proof" case-insensitively
and reported 68 Proof hits on proof93-20261005; the real count was 0. These
tests pin the one shared matcher (aeo.mention) to the strict rule using
verbatim excerpts from those runs (tests/fixtures/brand_proof_samples.json).
"""

import copy
import json
import unittest
from pathlib import Path

from aeo.config import load_config
from aeo.mention import (
    extract_brand_mentions,
    extract_vendors_in_queries,
    product_form_mentioned,
    product_form_only_from_config,
    proof_product_mentioned,
    rescore_brand_cells,
    resolve_product_form_only,
    vendor_name_is_product,
)
from aeo.vendors import is_brand_vendor

FIXTURE = Path(__file__).parent / "fixtures" / "brand_proof_samples.json"
SAMPLES = json.loads(FIXTURE.read_text(encoding="utf-8"))

# Mirrors ~/.aeo/reqproof.config.json
BRAND = "Proof"
ALIASES = [
    "ReqProof",
    "reqproof",
    "reqproof.com",
    "probelabs/proof",
    "github.com/probelabs/proof",
    "reqproof.io",
]
CFG = {"brand": BRAND, "aliases": ALIASES, "brand_match": {"product_form_only": ["Proof"]}}
PFO = ["Proof"]


def hits(text, pfo=PFO):
    return extract_brand_mentions(text, BRAND, ALIASES, product_form_only=pfo)


class RealFalsePositivesOct5(unittest.TestCase):
    def test_fixture_has_every_oct5_cell(self):
        cells = {(s["engine"], s["arm"], s["prompt_id"]) for s in SAMPLES["false_positives"]}
        # 68 = claude K13 + S20, codex K17 + S18 on proof93-20261005
        self.assertEqual(len(cells), 68)

    def test_no_oct5_excerpt_is_a_proof_hit(self):
        for s in SAMPLES["false_positives"]:
            with self.subTest(prompt=s["prompt_id"], engine=s["engine"], arm=s["arm"]):
                self.assertEqual(hits(s["text"]), [], s["text"])

    def test_default_without_config_is_also_strict(self):
        # rescore_brand.py in the live run folder passes only (brand, aliases).
        for s in SAMPLES["false_positives"]:
            with self.subTest(prompt=s["prompt_id"]):
                self.assertEqual(extract_brand_mentions(s["text"], BRAND, ALIASES), [])


class RealTrueHitsOct2(unittest.TestCase):
    def test_all_three_oct2_hits_survive(self):
        self.assertEqual(len(SAMPLES["true_hits"]), 3)
        for s in SAMPLES["true_hits"]:
            with self.subTest(prompt=s["prompt_id"], arm=s["arm"]):
                self.assertEqual(sorted(hits(s["text"])), sorted(s["expect"]))

    def test_intent_accountable_authority_names_proof(self):
        s = next(x for x in SAMPLES["true_hits"] if x["prompt_id"] == "intent-accountable-authority")
        self.assertIn("Proof", hits(s["text"]))
        self.assertIn("reqproof.com", hits(s["text"]))
        self.assertTrue(proof_product_mentioned(s["text"]))

    def test_reqproof_asides(self):
        for pid in ("watch-what-is-software-audit", "watch-check-code-correct"):
            s = next(x for x in SAMPLES["true_hits"] if x["prompt_id"] == pid)
            self.assertEqual(hits(s["text"]), ["ReqProof"])


class ProductFormRules(unittest.TestCase):
    def test_counts(self):
        for text in (
            "- **Proof (reqproof.com):** It's built for agent workflows.",
            "Sources:\n- [Proof (reqproof.com)](https://reqproof.com/)",
            "Proof (ReqProof) for a commercial option.",
            "Use tools like Proof to keep agents within scope.",
            "Requirements tools such as Jama, Polarion, or Proof keep the links current.",
            "Try Proof for MC/DC on Go.",
            "Evaluate Proof if you need the broader assurance workflow.",
            "| **[Proof](https://reqproof.com/engine)** | Documents Go MC/DC |",
            "[Proof’s Go workflow](https://reqproof.com/topics/mcdc-coverage-go)",
            "Proof — reqproof.com — is built for this.",
        ):
            with self.subTest(text=text):
                self.assertIn("Proof", hits(text))

    def test_rejects(self):
        for text in (
            "That test is your proof the problem existed.",
            "A green run is one sample, not proof.",
            "Signed proof of what ran",
            "Use social proof on the landing page.",
            "The burden of proof is on the vendor.",
            "That's a good proof point for sales.",
            "Mathematical proof that the code is correct.",
            "Formal Proof of the theorem follows.",
            "A public Proof-of-concept exists, no attacks seen.",
            "**Proof assistants (heavy-duty, general math and CS):**",
            "\u201cProof\u201d needs a defined scope.",
            "\"Proof\" means different things to different teams.",
            "- **Proof:** a machine-checked argument that the model satisfies the specification.",
            "Proof: the tool emits a checkable certificate.",
            "### Proof\nThe checker re-runs.",
            "| Step | Required work | Proof to retain |\n|---|---|---|",
            "| Proof | SMT/symbolic equivalence | Pure leaf functions |",
            "## 2. Proof that it works\n- CI passes",
            "**Proof of the workflow.** You learn whether your ID scheme works.",
            "Logs you keep. Proof that you follow the provider's instructions.",
            "Tamper-Proof storage: write-once storage such as S3 Object Lock.",
            "Proof obligations, discharged/undischarged status and any axioms.",
            "Use proof assistants like Lean.",
            "proof",
            "PROOF OF DELIVERY",
        ):
            with self.subTest(text=text):
                self.assertEqual(hits(text), [])

    def test_lowercase_proof_never_counts_even_with_leadin(self):
        self.assertEqual(hits("tools like proof checkers help"), [])
        self.assertEqual(hits("try proof by induction"), [])

    def test_unambiguous_aliases(self):
        self.assertEqual(hits("Use ReqProof for this."), ["ReqProof"])
        self.assertEqual(hits("see reqproof for docs"), ["ReqProof"])  # case-insensitive; config form
        self.assertEqual(hits("Docs at https://www.reqproof.com/topics/x"), ["reqproof.com"])
        self.assertEqual(hits("Docs at https://docs.reqproof.com/x"), ["reqproof.com"])
        self.assertEqual(hits("Install from probelabs/proof today."), ["probelabs/proof"])
        self.assertEqual(hits("Code: https://github.com/probelabs/proof/tree/main"), ["probelabs/proof"])

    def test_not_other_hosts_or_paths(self):
        self.assertEqual(hits("See https://proof.com/ and https://example.com/proof"), [])
        self.assertEqual(hits("See https://github.com/probelabs/proofreader"), [])
        self.assertEqual(hits("https://github.com/acme/reqproof-notes"), [])


class ConfigDriven(unittest.TestCase):
    def test_reads_brand_match_from_dict_and_config_object(self):
        self.assertEqual(product_form_only_from_config(CFG), ["Proof"])
        self.assertIsNone(product_form_only_from_config({"brand": "XERJ"}))

    def test_live_style_config_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.json"
            p.write_text(json.dumps({**CFG, "domain": "reqproof.com", "competitors": [], "prompts": []}))
            cfg = load_config(p)
            self.assertEqual(cfg.brand_product_form_only, ["Proof"])
            self.assertEqual(cfg.to_dict()["brand_match"], {"product_form_only": ["Proof"]})

    def test_empty_list_turns_strict_off(self):
        # Explicit [] means: match every brand term case-insensitively.
        self.assertEqual(resolve_product_form_only(BRAND, ALIASES, []), [])
        self.assertEqual(hits("a proof of concept", pfo=[]), ["Proof"])

    def test_other_brands_untouched(self):
        self.assertEqual(extract_brand_mentions("try xerj today", "XERJ", ["xerj.org"]), ["XERJ"])


class QueriesAndVendors(unittest.TestCase):
    def test_search_queries(self):
        self.assertEqual(
            extract_vendors_in_queries(["proof of concept exploit CRA"], BRAND, ALIASES, ["Jama"]), []
        )
        self.assertEqual(
            extract_vendors_in_queries(["ReqProof requirements AI agent approval"], BRAND, ALIASES, ["Jama"]),
            ["ReqProof"],
        )

    def test_vendor_names(self):
        self.assertTrue(vendor_name_is_product("Proof"))
        self.assertTrue(vendor_name_is_product("Proof CLI"))
        self.assertTrue(vendor_name_is_product("Proof (reqproof.com)"))
        self.assertFalse(vendor_name_is_product("Proof assistants"))
        self.assertFalse(vendor_name_is_product("Proof-of-concept"))
        self.assertTrue(is_brand_vendor("Proof (reqproof.com)", BRAND, ALIASES))
        self.assertTrue(is_brand_vendor("ReqProof", BRAND, ALIASES))
        self.assertFalse(is_brand_vendor("Proof assistants", BRAND, ALIASES))
        self.assertFalse(is_brand_vendor("Lean proof assistant", BRAND, ALIASES))


class Rescore(unittest.TestCase):
    def _doc(self, text, mentioned=True, queries=None):
        return {
            "workspace": {"brand": BRAND, "aliases": ALIASES, "domain": "reqproof.com", "competitors": []},
            "prompts": [
                {
                    "prompt_id": "p1",
                    "engines": {
                        "codex": {
                            "search": {
                                "raw_response_text": text,
                                "brand_mentioned": mentioned,
                                "brand_mentions": ["Proof"] if mentioned else [],
                                "recommended": mentioned,
                                "searched": True,
                                "search_queries": queries or [],
                                "vendors_in_search_queries": ["Proof", "Jama"],
                            },
                            "knowledge": {"error": "timeout", "brand_mentioned": False},
                        }
                    },
                }
            ],
        }

    def test_rescore_flips_false_positive(self):
        doc = self._doc("| Step | Required work | Proof to retain |", queries=["proof of fix retention"])
        stats = rescore_brand_cells(doc, product_form_only=PFO)
        cell = doc["prompts"][0]["engines"]["codex"]["search"]
        self.assertFalse(cell["brand_mentioned"])
        self.assertFalse(cell["recommended"])
        self.assertEqual(cell["brand_mentions"], [])
        self.assertEqual(cell["vendors_in_search_queries"], ["Jama"])
        self.assertEqual(stats, {"codex": {"search": [1, 1, 0]}})

    def test_rescore_keeps_true_hit_and_skips_error_cells(self):
        doc = self._doc("Tools: **Proof (reqproof.com)** keeps agents in scope.", queries=["reqproof"])
        before = copy.deepcopy(doc["prompts"][0]["engines"]["codex"]["knowledge"])
        rescore_brand_cells(doc)
        cell = doc["prompts"][0]["engines"]["codex"]["search"]
        self.assertTrue(cell["brand_mentioned"])
        self.assertEqual(sorted(cell["brand_mentions"]), ["Proof", "reqproof.com"])
        self.assertEqual(cell["vendors_in_search_queries"], ["ReqProof", "Jama"])
        self.assertEqual(doc["prompts"][0]["engines"]["codex"]["knowledge"], before)


if __name__ == "__main__":
    unittest.main()
