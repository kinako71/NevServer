"""Public hardware initialization and shutdown hooks."""

from nev.hardware.bootstrap import close_hardware, init_hardware

__all__ = ["close_hardware", "init_hardware"]
