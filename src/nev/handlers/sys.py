"""System-level shell command handlers."""

from __future__ import annotations

import logging

from nev.shell.constants import NEVSHELL_ERR_SOCK
from nev.shell.protocol import ShellConnection

log = logging.getLogger(__name__)


def nev_sys_echoinfo(token_list: list[str], conn: ShellConnection) -> int:
    """Return the legacy system-information acknowledgement payload."""
    log.debug("nev_sys_echoinfo(%s)", token_list)
    payload = "[nevSYS_echoinfo] respond\n"
    if conn.send_text(payload) < 0:
        return NEVSHELL_ERR_SOCK
    return 0
