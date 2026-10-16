"""TCP server entry point and top-level lifecycle for the NEV shell."""

from __future__ import annotations

import argparse
import logging
import sys

from nev.hardware import close_hardware, init_hardware
from nev.metecore import metecore_process
from nev.shell import NevShell, build_command_tree, shell_init
from nev.shell.constants import (
    NEVSHELL_ERR_SOCK,
    SHELL_TIMEOUT_SEC,
    SHELL_TIMEOUT_USEC,
)
from nev.shell.protocol import ShellConnection
from nev.tcps import accept_connection, close_connection, init_tcp_server
from nev.version import REV_DATE, REV_MAJOR, REV_MINOR


def _timeout_seconds() -> float:
    """Convert legacy seconds/microseconds constants to seconds."""
    return SHELL_TIMEOUT_SEC + SHELL_TIMEOUT_USEC / 1_000_000.0


def serve_connection(conn: ShellConnection, shell: NevShell) -> None:
    """Run shell and metecore polling cycles until the client disconnects."""
    timeout = _timeout_seconds()
    while True:
        res = shell.shell(conn, timeout)
        if res <= NEVSHELL_ERR_SOCK:
            break
        res = shell.shell(conn, timeout)
        if res <= NEVSHELL_ERR_SOCK:
            break
        res = metecore_process(conn, timeout)
        if res <= NEVSHELL_ERR_SOCK:
            break


def main(argv: list[str] | None = None) -> int:
    """Initialize the server and hardware, then serve clients until stopped."""
    parser = argparse.ArgumentParser(prog="nev-server")
    parser.add_argument("-m", "--mock", action="store_true", help="simulate hardware calls")
    parser.add_argument(
        "--port",
        default=None,
        help="TCP port (default: 9000)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="debug logging to stderr",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(message)s",
        stream=sys.stderr,
    )
    log = logging.getLogger("nev")

    log.info(
        "nev_server  Rev. %s.%s (%s)  Python port",
        REV_MAJOR,
        REV_MINOR,
        REV_DATE,
    )

    server = init_tcp_server(args.port)
    log.info("# nev tcp subsystem init OK")

    if init_hardware(mock=args.mock) < 0:
        return -1

    if shell_init() < 0:
        log.error("! nevSHELL_init() failed")
        return -1
    log.info("# nev shell tree subsystem init OK")

    shell = NevShell(build_command_tree())
    log.info("# connection ready")

    try:
        while True:
            raw = accept_connection(server)
            conn = ShellConnection(raw)
            log.info("# socket ready")
            try:
                serve_connection(conn, shell)
            finally:
                log.info("# closing socket.")
                close_connection(raw)
    except KeyboardInterrupt:
        log.info("Stopping nev-server")
        return 0
    finally:
        close_connection(server)
        close_hardware()


if __name__ == "__main__":
    raise SystemExit(main())
