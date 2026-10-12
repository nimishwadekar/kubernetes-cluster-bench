from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from src.benchmark.profile import load_profile, profile_section


class ProfileTests(unittest.TestCase):
    def test_loader_rejects_non_mapping_and_reads_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "profile.yaml"
            path.write_text("- list\n")
            with self.assertRaises(ValueError):
                load_profile(path)
            path.write_text("x: 1\n")
            self.assertEqual(profile_section(load_profile(path), "missing"), {})

    def test_loader_reports_missing_and_invalid_yaml(self) -> None:
        with self.assertRaisesRegex(ValueError, "not found"):
            load_profile(Path("missing-profile.yaml"))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.yaml"
            path.write_text("x: [\n")
            with self.assertRaisesRegex(ValueError, "invalid profile YAML"):
                load_profile(path)


if __name__ == "__main__":
    unittest.main()
