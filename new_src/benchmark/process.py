from __future__ import annotations

import signal
import subprocess


def terminate_process(
    process: subprocess.Popen[bytes] | None,
    timeout_seconds: float = 5.0,
) -> None:
    """Terminate a backend process, escalating to SIGKILL if necessary."""

    if process is None or process.poll() is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=timeout_seconds)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
