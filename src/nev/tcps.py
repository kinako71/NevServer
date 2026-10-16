"""TCP helpers for the server's listen, accept, and close lifecycle."""

from __future__ import annotations

import logging
import socket
from typing import Optional

from nev.shell.constants import NEVTCPS_DEFAULT_PORT

log = logging.getLogger(__name__)

MAX_BACKLOG = 5


def init_tcp_server(port: Optional[str] = None) -> socket.socket:
    """Bind an IPv4 listening socket on the requested or default port."""
    port = port or NEVTCPS_DEFAULT_PORT
    addrinfos = socket.getaddrinfo(
        None, port, family=socket.AF_INET, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE
    )
    last_err: OSError | None = None
    for family, socktype, proto, _, sockaddr in addrinfos:
        sock = socket.socket(family, socktype, proto)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(sockaddr)
            sock.listen(MAX_BACKLOG)
            log.info("[nev_tcps] listening on %s:%s", sockaddr[0], sockaddr[1])
            return sock
        except OSError as exc:
            last_err = exc
            sock.close()
    raise OSError(last_err or "failed to listen")


def accept_connection(server: socket.socket) -> socket.socket:
    """Wait for a client connection and log its peer address."""
    conn, addr = server.accept()
    log.info("[nev_tcps] connection from %s:%s", addr[0], addr[1])
    return conn


def close_connection(conn: socket.socket) -> None:
    """Close a client socket or the listening socket."""
    conn.close()
