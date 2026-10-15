from __future__ import annotations

import unittest

import yaml

from src.benchmark.manifest import dump_manifest


class ManifestDumpTests(unittest.TestCase):
    def test_single_letter_booleans_are_quoted(self) -> None:
        text = dump_manifest([{"env": [{"name": "A", "value": "n"}, {"name": "B", "value": "Y"}]}])
        self.assertIn('value: "n"', text)
        self.assertIn('value: "Y"', text)

    def test_round_trips_and_keeps_order_and_other_strings(self) -> None:
        documents = [
            {"kind": "Pod", "metadata": {"name": "x"}, "spec": {"args": ["infinity", "1", "yes", "ny"]}},
            {"kind": "Service"},
        ]
        text = dump_manifest(documents)
        self.assertEqual(list(yaml.safe_load_all(text)), documents)
        self.assertLess(text.index("kind: Pod"), text.index("metadata:"))
        self.assertIn("---", text)


if __name__ == "__main__":
    unittest.main()
