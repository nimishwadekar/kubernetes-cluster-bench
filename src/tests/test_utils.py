from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
import re
import unittest

from src.benchmark.contracts import BenchmarkConfig
from src.benchmark.resources import PodResources
from src.benchmark.utils import (
    format_eta,
    is_nonnegative_int,
    is_positive_int,
    parse_thread_spec,
    resource_overrides,
    utc_timestamp,
)


class ParseThreadSpecTests(unittest.TestCase):
    def test_ranges_are_inclusive_and_steps_apply(self) -> None:
        self.assertEqual(parse_thread_spec("4"), (4,))
        self.assertEqual(parse_thread_spec("1:4"), (1, 2, 3, 4))
        self.assertEqual(parse_thread_spec("1:8:7"), (1, 8))
        self.assertEqual(parse_thread_spec("1:5:2"), (1, 3, 5))

    def test_invalid_specs_are_rejected(self) -> None:
        for spec in ("", "0", "5:1", "1:4:0", "a", "1:2:3:4", "-1", "1.5"):
            with self.assertRaises(ValueError, msg=spec):
                parse_thread_spec(spec)


class FormatEtaTests(unittest.TestCase):
    def test_formats(self) -> None:
        self.assertEqual(format_eta(0), "0s")
        self.assertEqual(format_eta(45.4), "45s")
        self.assertEqual(format_eta(125), "2m 5s")
        self.assertEqual(format_eta(3600), "1h 0m")
        self.assertEqual(format_eta(3725), "1h 2m")
        self.assertEqual(format_eta(-5), "0s")


class UtcTimestampTests(unittest.TestCase):
    def test_format_and_zone(self) -> None:
        stamp = utc_timestamp()
        self.assertRegex(stamp, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        self.assertLess(abs((datetime.now(timezone.utc) - parsed).total_seconds()), 5)


class IntPredicateTests(unittest.TestCase):
    def test_positive_and_nonnegative(self) -> None:
        self.assertTrue(is_positive_int(1))
        self.assertFalse(is_positive_int(0))
        self.assertTrue(is_nonnegative_int(0))
        self.assertFalse(is_nonnegative_int(-1))
        for value in (True, False, 1.0, "1", None):
            self.assertFalse(is_positive_int(value), msg=repr(value))
            self.assertFalse(is_nonnegative_int(value), msg=repr(value))


class ResourceOverridesTests(unittest.TestCase):
    def test_overrides_are_plain_json(self) -> None:
        config = BenchmarkConfig(
            backend="fake", test_id="t", initiator_node="a", target_node="b",
            namespace="ns", image="x/y:z", log_directory=Path("logs"),
        )
        self.assertEqual(resource_overrides(config), {"requests": {}, "limits": {}})
        config = replace(config, resources=PodResources({"cpu": "2"}, {"memory": "4Gi"}))
        self.assertEqual(
            resource_overrides(config),
            {"requests": {"cpu": "2"}, "limits": {"memory": "4Gi"}},
        )


if __name__ == "__main__":
    unittest.main()
