import unittest

from backend.collectors.galaxies import normalize_galaxy
from backend.collectors.reports import (
    _chunks,
    _extractor,
    _pdf_items,
    _report_entities,
)


class GalaxyTests(unittest.TestCase):
    def test_cluster_aliases_and_country_are_source_derived(self):
        clusters = normalize_galaxy(
            {
                "values": [{
                    "uuid": "cluster-uuid",
                    "value": "Example Actor",
                    "description": "Source description",
                    "meta": {
                        "aliases": ["Example Group"],
                        "country": "Exampleland",
                    },
                }],
            },
            "threat-actor",
            "https://raw.example/threat-actor.json",
        )

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["name"], "Example Actor")
        self.assertEqual(clusters[0]["aliases"], ["Example Group"])
        self.assertEqual(clusters[0]["country"], "Exampleland")
        self.assertEqual(clusters[0]["source"], "misp-galaxy")


class ReportTests(unittest.TestCase):
    def test_entity_extraction_blocks_common_words_and_requires_case(self):
        threats = [
            {"_id": "one", "name": "Cobalt Strike", "aliases": ["Action", "Explorer", "do not"]},
        ]
        expression, aliases, ids = _extractor(threats)
        names, threat_ids = _report_entities(
            "Cobalt Strike uses action and explorer. cobalt strike is lowercase.",
            expression,
            aliases,
            ids,
        )

        self.assertEqual(names, ["Cobalt Strike"])
        self.assertEqual(threat_ids, ["one"])
        self.assertNotIn("Action", aliases)
        self.assertNotIn("Explorer", aliases)
        self.assertNotIn("do not", aliases)

    def test_passages_are_split_without_padding(self):
        source_text = "First paragraph.\n\n" + ("Real report text " * 150)
        chunks = _chunks(source_text)

        self.assertTrue(chunks)
        self.assertTrue(all(0 < len(chunk) <= 1800 for chunk in chunks))
        self.assertIn("First paragraph.", chunks[0])
        self.assertNotIn("THREAT_INTEL_REPORT_CHUNK", "".join(chunks))

    def test_truncated_github_tree_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, "truncated"):
            _pdf_items({"tree": [], "truncated": True})


if __name__ == "__main__":
    unittest.main()
