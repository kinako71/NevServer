"""Compatibility placeholder for the original metecore state machine."""

from __future__ import annotations

import logging
import time

from nev.shell.constants import NEVSHELL_ERR_SOCK
from nev.shell.protocol import ShellConnection

log = logging.getLogger(__name__)

_psa_status = "INVALID"


def metecore_init() -> tuple[int, int, str | None]:
    """Reset the simulated PSA state and return Python-port version fields."""
    global _psa_status
    _psa_status = "INVALID"
    return 1, 3, "python-port"


def metecore_process(conn: ShellConnection, timeout_sec: float) -> int:
    """Reserve one polling interval for the future PSA/metecore state machine."""
    log.debug("metecore_process timeout=%.6f status=%s", timeout_sec, _psa_status)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        time.sleep(min(remaining, 0.005))
    return 0
