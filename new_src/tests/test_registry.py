from __future__ import annotations

import unittest

from new_src.backends.plugin import BackendPlugin
from new_src.backends.registry import (
    BackendAlreadyRegisteredError,
    BackendNotFoundError,
    BackendRegistry,
)


class RegistryTests(unittest.TestCase):
    def test_registers_and_resolves_canonical_name_and_alias(self) -> None:
        plugin = BackendPlugin(name="iperf3", description="TCP throughput")
        registry = BackendRegistry()
        registry.register(plugin, aliases=("iperf",))

        self.assertIs(registry.get("iperf3"), plugin)
        self.assertIs(registry.get(" IPERF "), plugin)
        self.assertTrue(registry.contains("iperf"))
        self.assertEqual(registry.names(), ("iperf3",))

    def test_constructor_registers_initial_plugins(self) -> None:
        plugin = BackendPlugin(name="nixl")
        registry = BackendRegistry((plugin,))
        self.assertEqual(registry.names(), ("nixl",))

    def test_duplicate_name_or_alias_is_rejected(self) -> None:
        registry = BackendRegistry()
        registry.register(BackendPlugin(name="iperf3"), aliases=("iperf",))
        with self.assertRaises(BackendAlreadyRegisteredError):
            registry.register(BackendPlugin(name="iperf3"))
        with self.assertRaises(BackendAlreadyRegisteredError):
            registry.register(BackendPlugin(name="other"), aliases=("iperf",))

    def test_unknown_backend_reports_available_names(self) -> None:
        registry = BackendRegistry((BackendPlugin(name="iperf3"),))
        with self.assertRaisesRegex(BackendNotFoundError, "available backends: iperf3"):
            registry.get("nixl")

    def test_empty_names_are_rejected(self) -> None:
        registry = BackendRegistry()
        with self.assertRaises(ValueError):
            registry.get(" ")
        with self.assertRaises(ValueError):
            registry.register(BackendPlugin(name="iperf3"), aliases=("",))


if __name__ == "__main__":
    unittest.main()
