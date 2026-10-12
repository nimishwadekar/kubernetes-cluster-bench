from __future__ import annotations

import unittest
from src.benchmark.process import terminate_process


class FakeProcess:
    def __init__(self) -> None:
        self.status = None
        self.signals: list[object] = []

    def poll(self):
        return self.status

    def send_signal(self, signal):
        self.signals.append(signal)
        self.status = 0

    def wait(self, timeout=None):
        return self.status or 0

    def kill(self):
        self.status = -9


class ProcessTests(unittest.TestCase):
    def test_termination_is_safe(self) -> None:
        process = FakeProcess()
        terminate_process(process)  # type: ignore[arg-type]
        self.assertTrue(process.signals)
        terminate_process(None)


if __name__ == "__main__":
    unittest.main()
