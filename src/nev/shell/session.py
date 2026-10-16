"""Incremental command and data-mode processing for one client session."""

from __future__ import annotations

import logging
import select
import time

from nev.shell.constants import (
    NEVSHELL_ERR_MALLOC,
    NEVSHELL_ERR_SOCK,
    NEVSHELL_LINE_BUF_SIZE,
    NEVSHELL_MAX_COMTOKENS,
)
from nev.shell import datareq
from nev.shell.parser import CommandNode, comparser, tokenize_command
from nev.shell.protocol import ShellConnection

log = logging.getLogger(__name__)


class NevShell:
    """Own parser state and receive buffers for a TCP client session."""

    def __init__(self, command_tree: CommandNode) -> None:
        """Bind the command grammar and initialize the input buffer."""
        self._tree = command_tree
        self._shell_buf = ""

    def shell(self, conn: ShellConnection, timeout_sec: float) -> int:
        """Process available input until timeout, completion, or socket error.

        Pending data requests switch input from commands to numeric payloads;
        the buffer retains incomplete transfers across socket reads.
        """
        res = 0
        bofst = 0
        fd = conn.fileno()
        deadline = time.monotonic() + timeout_sec

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return res

            log.debug("shell remaining=%.6f", remaining)
            rlist, _, _ = select.select([fd], [], [], remaining)
            if not rlist:
                return res

            if datareq.data_req_recv.data is None:
                chunk = conn.recv(NEVSHELL_LINE_BUF_SIZE)
                if not chunk:
                    return NEVSHELL_ERR_SOCK
                text = chunk.decode("ascii", errors="replace")
                log.debug("command mode: %r", text)
                tokens = tokenize_command(text)
                if len(tokens) > NEVSHELL_MAX_COMTOKENS:
                    if conn.send_text("!TOO_MANY_TOKENS") < 0:
                        return NEVSHELL_ERR_SOCK
                    return -2
                res = comparser(self._tree, tokens, conn)
                if res <= NEVSHELL_ERR_MALLOC:
                    log.debug("comparser returned %s", res)
                    return res
            else:
                chunk = conn.recv(NEVSHELL_LINE_BUF_SIZE - bofst)
                if not chunk:
                    return NEVSHELL_ERR_SOCK
                self._shell_buf += chunk.decode("ascii", errors="replace")
                log.debug("data mode: %r", self._shell_buf)
                res = datareq.datareceiver(datareq.data_req_recv, self._shell_buf)
                if res < 0:
                    log.debug("datareceiver returned %s", res)
                    return res
                if res == 0:
                    log.debug("data mode complete")
                    datareq.clr_data_req("r")
                    self._shell_buf = ""
                    bofst = 0
                    if conn.sh_term() < 0:
                        return NEVSHELL_ERR_SOCK
                elif res > 0:
                    self._shell_buf = self._shell_buf[-res:]
                    bofst = len(self._shell_buf)

        return res
