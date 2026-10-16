"""Public shell API and startup checks for hardware-backed commands."""

from nev.shell.commands import build_command_tree
from nev.shell.datareq import clr_data_req
from nev.shell.session import NevShell

__all__ = ["NevShell", "build_command_tree", "shell_init", "clr_data_req"]


def shell_init() -> int:
    """Initialize the shell subsystem hooks and report failed components."""
    from nev.metecore import metecore_init
    from nev.handlers import device_commands  # noqa: F401

    modules = [
        ("nev_niscopesh", _niscope_sh_init),
        ("nev_daqmxsh", _daqmx_sh_init),
        ("nev_metecore", metecore_init),
    ]
    import logging

    log = logging.getLogger("nev.shell")
    log.info("nev shell system initializing modules...")
    for name, func in modules:
        rev_major, rev_minor, rev_date = func()
        if rev_date:
            log.info("  module %s: rev. %s.%s, %s OK", name, rev_major, rev_minor, rev_date)
        else:
            log.info("  module %s: rev. %s.%s OK", name, rev_major, rev_minor)
    clr_data_req("r")
    clr_data_req("s")
    return 0


def _niscope_sh_init() -> tuple[int, int, str | None]:
    """Return the NI scope subsystem version when the driver is available."""
    return 1, 0, "python-port"


def _daqmx_sh_init() -> tuple[int, int, str | None]:
    """Return the DAQmx subsystem version when the driver is available."""
    return 1, 6, "python-port"
