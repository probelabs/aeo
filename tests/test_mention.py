import unittest

from aeo.mention import (
    extract_brand_mentions,
    extract_competitor_mentions,
    extract_vendors_in_queries,
    find_terms,
)


BRAND = "XERJ"
ALIASES = ["xerj", "xerj.org", "xerj.ai", "xerj-org"]
COMPS = ["ripgrep", "recoll", "docfetcher", "elasticsearch"]

REQPROOF_ALIASES = ["reqproof.com", "reqproof.io"]


class MentionTests(unittest.TestCase):
    def test_whole_word_hit(self):
        text = "I would try XERJ or Recoll."
        self.assertEqual(extract_brand_mentions(text, BRAND, ALIASES), ["XERJ"])
        self.assertEqual(extract_competitor_mentions(text, COMPS), ["recoll"])

    def test_case_insensitive(self):
        self.assertEqual(extract_brand_mentions("try xerj today", BRAND, ALIASES), ["XERJ"])

    def test_not_substring(self):
        self.assertEqual(extract_brand_mentions("the xerjified build failed", BRAND, ALIASES), [])
        self.assertEqual(find_terms("superelasticsearch", ["elasticsearch"]), [])

    def test_url_host_domain_alias_counts(self):
        text = "See https://xerj.org/docs for details."
        self.assertEqual(extract_brand_mentions(text, BRAND, ALIASES), ["xerj.org"])

    def test_url_www_host_counts(self):
        text = "See https://www.xerj.org/docs for details."
        self.assertEqual(extract_brand_mentions(text, BRAND, ALIASES), ["xerj.org"])

    def test_bare_domain_alias_counts(self):
        text = "The tool at xerj.org is local."
        self.assertEqual(extract_brand_mentions(text, BRAND, ALIASES), ["xerj.org"])

    def test_url_plus_prose_counts(self):
        text = "XERJ (https://xerj.org/docs) can index a folder."
        self.assertEqual(extract_brand_mentions(text, BRAND, ALIASES), ["XERJ", "xerj.org"])

    def test_reqproof_url_only_citation(self):
        text = "see https://reqproof.com/topics/mcdc-coverage-go"
        self.assertEqual(extract_brand_mentions(text, "reqproof", REQPROOF_ALIASES), ["reqproof.com"])

    def test_reqproof_www_and_apex_tld(self):
        self.assertEqual(
            extract_brand_mentions(
                "https://www.reqproof.com/topics/mcdc-coverage-go",
                "reqproof",
                REQPROOF_ALIASES,
            ),
            ["reqproof.com"],
        )
        # Host is {brand}.{other-tld}; prefer a configured domain alias.
        self.assertEqual(
            extract_brand_mentions("https://reqproof.io/docs", "reqproof", ["reqproof.com"]),
            ["reqproof.com"],
        )

    def test_mathematical_proof_is_not_a_mention(self):
        self.assertEqual(
            extract_brand_mentions(
                "A mathematical proof of the claim follows.",
                "reqproof",
                REQPROOF_ALIASES,
            ),
            [],
        )

    def test_bare_proof_without_domain_is_not_a_mention(self):
        self.assertEqual(
            extract_brand_mentions("Try Proof for MC/DC coverage.", "reqproof", REQPROOF_ALIASES),
            [],
        )

    def test_path_only_brand_token_does_not_count(self):
        text = "See https://example.com/topics/reqproof/mcdc or https://example.com/?q=proof"
        self.assertEqual(extract_brand_mentions(text, "reqproof", ["reqproof", *REQPROOF_ALIASES]), [])

    def test_competitor_url_only_still_ignored(self):
        text = "See https://www.elastic.co/elasticsearch for details."
        self.assertEqual(extract_competitor_mentions(text, COMPS), [])

    def test_vendors_in_search_queries(self):
        queries = ["ripgrep vs Recoll vs DocFetcher local folder search"]
        vendors = extract_vendors_in_queries(queries, BRAND, ALIASES, COMPS)
        self.assertEqual(set(v.lower() for v in vendors), {"ripgrep", "recoll", "docfetcher"})

    def test_open_discovery_query_has_no_vendors(self):
        queries = ["best way to search a folder of files by content"]
        vendors = extract_vendors_in_queries(queries, BRAND, ALIASES, COMPS)
        self.assertEqual(vendors, [])


if __name__ == "__main__":
    unittest.main()
