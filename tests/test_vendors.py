import unittest

from aeo.vendors import (
    AliasMap,
    annotate_vendor_cell,
    completed_cells,
    expand_competitor_mention_terms,
    is_brand_vendor,
    merge_classified_vendors,
    merge_named_vendors,
    named_vendor_counts_by_origin,
    normalize_vendor_cell,
    normalize_vendor_key,
    preferred_display,
    pretty_strip,
    query_vendors_for_arm,
    seed_alias_map,
    surprise_frequencies,
    vendor_origin,
    who_got_named_counts,
)


class NormalizeTests(unittest.TestCase):
    def test_usercheck_domain_and_inc_share_key(self):
        self.assertEqual(normalize_vendor_key("UserCheck"), "usercheck")
        self.assertEqual(normalize_vendor_key("usercheck.com"), "usercheck")
        self.assertEqual(normalize_vendor_key("https://www.usercheck.com/pricing"), "usercheck")
        self.assertEqual(normalize_vendor_key("UserCheck Inc."), "usercheck")
        self.assertEqual(normalize_vendor_key("UserCheck Labs"), "usercheck")

    def test_ipqualityscore_collapses_spaces(self):
        self.assertEqual(normalize_vendor_key("IPQualityScore"), "ipqualityscore")
        self.assertEqual(normalize_vendor_key("IP Quality Score"), "ipqualityscore")
        self.assertEqual(normalize_vendor_key("ipqualityscore.com"), "ipqualityscore")

    def test_api_suffix_not_middle_token(self):
        self.assertEqual(normalize_vendor_key("SendGrid API"), "sendgrid")
        self.assertEqual(normalize_vendor_key("AWS API Gateway"), "awsapigateway")

    def test_pretty_strip_keeps_camel(self):
        self.assertEqual(pretty_strip("UserCheck Inc"), "UserCheck")
        self.assertEqual(pretty_strip("usercheck.com"), "usercheck")

    def test_preferred_display_stable_names(self):
        self.assertEqual(preferred_display("usercheck.com", "UserCheck"), "UserCheck")
        self.assertEqual(preferred_display("Kickbox Inc", "kickbox"), "Kickbox")
        self.assertEqual(preferred_display("IPQualityScore", "ipqualityscore.com"), "IPQualityScore")


class AliasMergeTests(unittest.TestCase):
    def test_config_seed_plus_run_growth(self):
        amap = seed_alias_map(
            "Autheona",
            ["autheona", "autheona.com"],
            ["Kickbox", "IPQualityScore"],
            cells=[
                {
                    "vendors": [
                        {"raw": "usercheck.com", "normalized": "UserCheck", "role": "recommend"},
                        {"raw": "Kickbox Inc", "normalized": "Kickbox", "role": "mention"},
                    ]
                }
            ],
        )
        self.assertEqual(amap.display_for("kickbox.com"), "Kickbox")
        self.assertEqual(amap.display_for("USERCHECK"), "UserCheck")
        self.assertEqual(amap.display_for("ip quality score"), "IPQualityScore")

    def test_later_weaker_alias_does_not_overwrite(self):
        amap = AliasMap()
        amap.observe("Kickbox")
        amap.observe("kickbox.com")
        self.assertEqual(amap.display_for("kickbox"), "Kickbox")

    def test_amazon_and_aws_api_gateway_share_key(self):
        amap = seed_alias_map("Tyk", ["tyk"], ["aws api gateway", "amazon api gateway"])
        self.assertEqual(amap.key_for("AWS API Gateway"), amap.key_for("Amazon API Gateway"))
        self.assertEqual(amap.display_for("aws api gateway"), "Amazon API Gateway")
        self.assertEqual(amap.display_for("amazon api gateway"), "Amazon API Gateway")
        self.assertTrue(amap.is_seed("AWS API Gateway"))
        self.assertTrue(amap.is_seed("amazon api gateway"))

    def test_azure_apim_and_api_management_share_key(self):
        amap = seed_alias_map("Tyk", ["tyk"], ["azure apim", "azure api management"])
        self.assertEqual(amap.key_for("Azure APIM"), amap.key_for("Azure API Management"))
        self.assertEqual(amap.display_for("azure apim"), "Azure API Management")
        self.assertTrue(amap.is_seed("Azure APIM"))

    def test_apache_apisix_collapses_to_canonical(self):
        amap = seed_alias_map("Tyk", ["tyk"], ["apache apisix", "apisix"])
        self.assertEqual(amap.key_for("APISIX"), amap.key_for("Apache APISIX"))
        self.assertEqual(amap.display_for("apisix"), "Apache APISIX")

    def test_config_aliases_collapse_custom_pair(self):
        amap = seed_alias_map(
            "Acme",
            ["acme"],
            ["Foo Bar"],
            competitor_aliases={"Foo Bar": ["FB Cloud", "FooBar Cloud"]},
        )
        self.assertEqual(amap.key_for("FB Cloud"), amap.key_for("Foo Bar"))
        self.assertEqual(amap.display_for("FB Cloud"), "Foo Bar")
        self.assertTrue(amap.is_seed("FB Cloud"))

    def test_builtin_synonym_is_not_a_seed_for_other_brands(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox"])
        self.assertFalse(amap.is_seed("Amazon API Gateway"))
        self.assertEqual(
            vendor_origin("Amazon API Gateway", None, amap, "Autheona", ["autheona"]),
            "surprise",
        )

    def test_expand_mention_terms_includes_aws_spelling(self):
        terms = {t.lower() for t in expand_competitor_mention_terms(["Amazon API Gateway"])}
        self.assertIn("aws api gateway", terms)
        self.assertIn("amazon api gateway", terms)


class BrandFilterTests(unittest.TestCase):
    def test_autheona_not_a_competitor(self):
        self.assertTrue(is_brand_vendor("Autheona", "Autheona", ["autheona.com"]))
        self.assertTrue(is_brand_vendor("autheona.com", "Autheona", ["autheona", "autheona.com"]))
        self.assertFalse(is_brand_vendor("UserCheck", "Autheona", ["autheona", "autheona.com"]))

    def test_tyk_aliases_not_a_competitor(self):
        aliases = ["tyk", "tyk.io", "Tyk Gateway", "Tyk API Gateway"]
        self.assertTrue(is_brand_vendor("Tyk", "Tyk", aliases))
        self.assertTrue(is_brand_vendor("Tyk API Gateway", "Tyk", aliases))
        self.assertFalse(is_brand_vendor("Kong", "Tyk", aliases))
        self.assertFalse(is_brand_vendor("Prettyk", "Tyk", aliases))

    def test_proof_not_a_competitor_when_it_is_the_brand(self):
        aliases = ["proof", "reqproof", "reqproof.com"]
        self.assertTrue(is_brand_vendor("Proof", "Proof", aliases))
        self.assertTrue(is_brand_vendor("reqproof.com", "Proof", aliases))
        self.assertFalse(is_brand_vendor("UserCheck", "Proof", aliases))

    def test_proof_ok_when_brand_is_autheona(self):
        self.assertFalse(is_brand_vendor("Proof", "Autheona", ["autheona"]))


class MergeCellTests(unittest.TestCase):
    def test_usercheck_without_config_competitor(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox"])
        names = merge_named_vendors(
            [{"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"}],
            [],
            amap,
            "Autheona",
            ["autheona"],
        )
        self.assertEqual(names, ["UserCheck"])

    def test_union_llm_and_regex(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox"])
        names = merge_named_vendors(
            [{"raw": "usercheck.com", "normalized": "UserCheck"}],
            ["Kickbox"],
            amap,
            "Autheona",
            ["autheona"],
        )
        self.assertEqual(names, ["UserCheck", "Kickbox"])

    def test_empty_llm_falls_back_to_regex(self):
        amap = seed_alias_map("Tyk", ["tyk"], ["Kong"])
        names = merge_named_vendors([], ["kong"], amap, "Tyk", ["tyk"])
        self.assertEqual(names, ["Kong"])

    def test_brand_stripped_from_union(self):
        amap = seed_alias_map("Tyk", ["tyk", "Tyk Gateway"], ["Kong"])
        names = merge_named_vendors(
            [
                {"raw": "Tyk", "normalized": "Tyk"},
                {"raw": "Kong Gateway", "normalized": "Kong"},
            ],
            ["Tyk Gateway", "Kong"],
            amap,
            "Tyk",
            ["tyk", "Tyk Gateway"],
        )
        self.assertEqual(names, ["Kong"])

    def test_known_vs_surprise_after_normalize(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox", "IPQualityScore"])
        recs = merge_classified_vendors(
            [
                {"raw": "usercheck.com", "normalized": "UserCheck"},
                {"raw": "Kickbox Inc", "normalized": "Kickbox"},
                {"raw": "IP Quality Score", "normalized": "IPQualityScore"},
            ],
            [],
            amap,
            "Autheona",
            ["autheona"],
        )
        by_name = {r["name"]: r["origin"] for r in recs}
        self.assertEqual(by_name["UserCheck"], "surprise")
        self.assertEqual(by_name["Kickbox"], "known")
        self.assertEqual(by_name["IPQualityScore"], "known")

    def test_stored_llm_origin_does_not_override_seed(self):
        """vendors_judged `origin` is ignored; is_seed after synonym collapse wins."""
        amap = seed_alias_map(
            "Tyk",
            ["tyk"],
            ["aws api gateway", "amazon api gateway"],
        )
        recs = merge_classified_vendors(
            [
                {
                    "raw": "Amazon API Gateway",
                    "normalized": "Amazon API Gateway",
                    "origin": "surprise",
                },
                {"raw": "UserCheck", "normalized": "UserCheck", "origin": "known"},
            ],
            [],
            amap,
            "Tyk",
            ["tyk"],
        )
        by_name = {r["name"]: r["origin"] for r in recs}
        self.assertEqual(by_name["Amazon API Gateway"], "known")
        self.assertEqual(by_name["UserCheck"], "surprise")
        self.assertTrue(amap.is_seed("Amazon API Gateway"))

        store = {
            "q|claude|knowledge": {
                "vendors": [
                    {
                        "raw": "Amazon API Gateway",
                        "normalized": "Amazon API Gateway",
                        "origin": "surprise",
                        "role": "mention",
                    },
                    {
                        "raw": "UserCheck",
                        "normalized": "UserCheck",
                        "origin": "known",
                        "role": "mention",
                    },
                ],
            }
        }
        rows = [
            {
                "prompt_id": "q",
                "engines": {
                    "claude": {
                        "knowledge": {
                            "brand_mentioned": False,
                            "competitor_mentions": ["amazon api gateway"],
                            "raw_response_text": "Amazon API Gateway and UserCheck.",
                        }
                    }
                },
            }
        ]
        known, surprise = named_vendor_counts_by_origin(
            rows, store, brand="Tyk", aliases=["tyk"], alias_map=amap
        )
        self.assertIn("Amazon API Gateway", known)
        self.assertNotIn("Amazon API Gateway", surprise)
        self.assertIn("UserCheck", surprise)
        self.assertNotIn("UserCheck", known)

    def test_annotate_stamps_origin_and_drops_brand(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox"])
        cell = annotate_vendor_cell(
            {
                "vendors": [
                    {"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"},
                    {"raw": "Autheona", "normalized": "Autheona", "role": "mention"},
                    {"raw": "Kickbox", "normalized": "Kickbox", "role": "mention"},
                ],
                "query_vendors": [],
            },
            amap,
            "Autheona",
            ["autheona"],
        )
        origins = {v["normalized"]: v["origin"] for v in cell["vendors"]}
        self.assertEqual(origins["UserCheck"], "surprise")
        self.assertEqual(origins["Kickbox"], "known")
        self.assertNotIn("Autheona", origins)

    def test_normalize_vendor_cell_shape(self):
        cell = normalize_vendor_cell(
            {
                "vendors": [
                    {"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"},
                    {"raw": "usercheck.com", "normalized": "UserCheck", "role": "mention"},
                    {"raw": "", "role": "mention"},
                    {"role": "nope"},
                ],
                "query_vendors": ["Kickbox Inc"],
                "confidence": 1.4,
            }
        )
        self.assertIsNotNone(cell)
        self.assertEqual(len(cell["vendors"]), 1)
        self.assertEqual(cell["vendors"][0]["normalized"], "UserCheck")
        self.assertEqual(cell["query_vendors"][0]["normalized"], "Kickbox")
        self.assertEqual(cell["confidence"], 1.0)


class WhoGotNamedTests(unittest.TestCase):
    def _row(self, *, mentioned=False, comps=None):
        return {
            "prompt_id": "email-verify",
            "engines": {
                "claude": {
                    "knowledge": {
                        "brand_mentioned": mentioned,
                        "competitor_mentions": list(comps or []),
                        "raw_response_text": "Try UserCheck or Kickbox.",
                    }
                }
            },
        }

    def test_usercheck_is_surprise_when_absent_from_seed(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox"])
        store = {
            "email-verify|claude|knowledge": {
                "vendors": [{"raw": "UserCheck", "normalized": "UserCheck", "role": "recommend"}],
                "query_vendors": [],
                "confidence": 0.9,
            }
        }
        known, surprise = named_vendor_counts_by_origin(
            [self._row(comps=["Kickbox"])],
            store,
            brand="Autheona",
            aliases=["autheona"],
            alias_map=amap,
        )
        self.assertEqual(surprise["UserCheck"], 1)
        self.assertNotIn("UserCheck", known)
        self.assertEqual(known["Kickbox"], 1)
        self.assertNotIn("Autheona", known)
        self.assertEqual(
            who_got_named_counts(
                [self._row(comps=["Kickbox"])],
                store,
                brand="Autheona",
                aliases=["autheona"],
                alias_map=amap,
            )["Kickbox"],
            1,
        )
        freqs = surprise_frequencies(
            {
                "claude": {
                    "prompts": [
                        {
                            "prompt_id": "email-verify",
                            "engines": {
                                "claude": {
                                    "knowledge": {
                                        "raw_response_text": "Try UserCheck.",
                                        "competitor_mentions": ["Kickbox"],
                                    }
                                }
                            },
                        }
                    ]
                }
            },
            store,
            brand="Autheona",
            aliases=["autheona"],
            competitors=["Kickbox"],
        )
        self.assertEqual(freqs[0], ("UserCheck", 1))

    def test_brand_hit_still_uses_brand_mentioned_not_llm(self):
        amap = seed_alias_map("Autheona", ["autheona"], [])
        store = {
            "email-verify|claude|knowledge": {
                "vendors": [{"raw": "Autheona", "normalized": "Autheona", "role": "recommend"}],
            }
        }
        counts = who_got_named_counts(
            [self._row(mentioned=True)],
            store,
            brand="Autheona",
            aliases=["autheona"],
            alias_map=amap,
        )
        self.assertEqual(counts["Autheona"], 1)
        # LLM brand row is filtered; regex brand_mentioned is the only increment.
        self.assertEqual(sum(counts.values()), 1)

    def test_regex_fallback_when_store_empty(self):
        amap = seed_alias_map("Tyk", ["tyk"], ["Kong"])
        counts = who_got_named_counts(
            [self._row(comps=["Kong"])],
            {},
            brand="Tyk",
            aliases=["tyk"],
            alias_map=amap,
        )
        self.assertEqual(counts["Kong"], 1)

    def test_query_vendors_union(self):
        amap = seed_alias_map("Autheona", ["autheona"], ["Kickbox"])
        arm = {
            "vendors_in_search_queries": ["Kickbox"],
            "search_queries": ["usercheck.com vs kickbox email verification"],
        }
        cell = {
            "query_vendors": [{"raw": "usercheck.com", "normalized": "UserCheck", "role": "mention"}],
        }
        names = query_vendors_for_arm(arm, cell, amap, "Autheona", ["autheona"])
        self.assertEqual(set(names), {"UserCheck", "Kickbox"})

    def test_completed_cells_skips_error_and_empty(self):
        doc = {
            "prompts": [
                {
                    "prompt_id": "q",
                    "prompt_text": "verify an email",
                    "engines": {
                        "claude": {
                            "knowledge": {"raw_response_text": "Try UserCheck."},
                            "search": {"raw_response_text": "   "},
                        }
                    },
                },
                {
                    "prompt_id": "e",
                    "engines": {
                        "claude": {
                            "knowledge": {"raw_response_text": "x", "error": "timeout"},
                        }
                    },
                },
            ]
        }
        cells = completed_cells(doc, "claude")
        self.assertEqual([c["key"] for c in cells], ["q|claude|knowledge"])


if __name__ == "__main__":
    unittest.main()
