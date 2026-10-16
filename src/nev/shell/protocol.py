"""Socket adapter that formats protocol acknowledgements and errors."""

from __future__ import annotations

import logging
import socket
from typing import BinaryIO

from nev.shell.constants import (
    NEVSHELL_ERR_SOCK,
    NEVSHELL_ERROR_MESSAGES,
    NEVSHELL_LIT_ERR,
    NEVSHELL_LIT_TRM,
    NEVSHELL_LINE_BUF_SIZE,
)

log = logging.getLogger(__name__)


class ShellConnection:
    """TCP connection with nev shell send/receive helpers."""

    def __init__(self, sock: socket.socket) -> None:
        """Wrap the socket and allocate the legacy-sized line buffer."""
        self._sock = sock
        self.line_buf = bytearray(NEVSHELL_LINE_BUF_SIZE + 1)

    def fileno(self) -> int:
        """Expose the descriptor for ``select`` in the shell loop."""
        return self._sock.fileno()

    def recv(self, buflen: int) -> bytes:
        """Read raw bytes from the client socket."""
        return self._sock.recv(buflen)

    def send_bytes(self, data: bytes) -> int:
        """Send bytes and translate socket failures to the shell error code."""
        try:
            return self._sock.send(data)
        except OSError:
            return NEVSHELL_ERR_SOCK

    def send_text(self, text: str) -> int:
        """Encode protocol text as ASCII, replacing unsupported characters."""
        return self.send_bytes(text.encode("ascii", errors="replace"))

    def sock_read(self, buf: bytearray, buflen: int) -> int:
        """Read into a caller-owned buffer and add a NUL when there is room."""
        try:
            chunk = self._sock.recv(buflen)
        except OSError as exc:
            log.debug("recv failed: %s", exc)
            return NEVSHELL_ERR_SOCK
        if not chunk:
            log.debug("recv returned 0")
            return NEVSHELL_ERR_SOCK
        n = len(chunk)
        buf[:n] = chunk
        if n < len(buf) and buf[n - 1] != 0:
            if n < buflen:
                buf[n] = 0
        return n

    def sh_term(self, message: str | None = None) -> int:
        """Send an optional response body followed by the ``#OK`` marker."""
        log.debug("sh_term()")
        if message:
            if self.send_text(message) < 0:
                return NEVSHELL_ERR_SOCK
        if self.send_bytes(NEVSHELL_LIT_TRM.encode("ascii")) < 0:
            return NEVSHELL_ERR_SOCK
        return 0

    def sh_error(self, message: str) -> int:
        """Send a newline-terminated ``#NG`` response with custom detail."""
        log.debug("sh_error(%s)", message)
        payload = f"{NEVSHELL_LIT_ERR} {message}\n"
        if self.send_text(payload) < 0:
            return NEVSHELL_ERR_SOCK
        return 0

    def sh_error_code(self, code: int, message: str | None) -> int:
        """Format a numeric shell error code using the shared message table."""
        log.debug("sh_error_code(%s, %s)", code, message)
        errmes = NEVSHELL_ERROR_MESSAGES.get(code, "!UNKNOWN_ERROR")
        if message is not None:
            payload = f"{NEVSHELL_LIT_ERR} {errmes}({message})\n"
        else:
            payload = f"{NEVSHELL_LIT_ERR} {errmes}\n"
        if self.send_text(payload) < 0:
            return NEVSHELL_ERR_SOCK
        return 0


def get_error_message(code: int) -> str:
    """Look up the text associated with a shell error code."""
    return NEVSHELL_ERROR_MESSAGES.get(code, "!UNKNOWN_ERROR")
