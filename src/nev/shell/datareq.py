"""State and parser for streamed numeric payloads such as ``SET DAO`` data."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from nev.shell.constants import NEVSHELL_LIT_TRM


@dataclass
class DataRequest:
    """Describe a pending transfer, including its cursor and completion hook."""
    data: list[float] | None = None
    l_data: int = 0
    w_data: int = 0
    l_pos: int = 0
    w_pos: int = 0
    level: int = 0
    proc: Callable[[object | None], int] | None = None
    proc_arg: object | None = None


data_req_recv = DataRequest()
data_req_send = DataRequest()


def clr_data_req(direction: str) -> None:
    """Clear receive (``r``), send (``s``), or both transfer states."""
    global data_req_recv, data_req_send
    if direction == "r":
        data_req_recv = DataRequest()
    elif direction == "s":
        data_req_send = DataRequest()
    else:
        data_req_recv = DataRequest()
        data_req_send = DataRequest()


def set_data_req(
    direction: str,
    data: list[float],
    l_data: int,
    w_data: int,
    l_pos: int,
    w_pos: int,
    proc: Callable[[object | None], int] | None,
    proc_arg: object | None,
) -> int:
    """Register one transfer; reject a second request in the same direction."""
    global data_req_recv, data_req_send
    if direction == "r":
        if data_req_recv.data is not None:
            return -1
        target = data_req_recv
    elif direction == "s":
        if data_req_send.data is not None:
            return -1
        target = data_req_send
    else:
        return -2

    target.data = data
    target.l_data = l_data
    target.w_data = w_data
    target.l_pos = l_pos
    target.w_pos = w_pos
    target.level = 0
    target.proc = proc
    target.proc_arg = proc_arg

    if direction == "r":
        data_req_recv = target
    else:
        data_req_send = target
    return 0


_FLOAT_PREFIX = re.compile(
    r"^[\t\n\v\f\r ]*([+-]?(?:(?:\d+(?:\.\d*)?)|(?:\.\d+))"
    r"(?:[eE][+-]?\d+)?|[+-]?(?:inf(?:inity)?|nan))",
    re.IGNORECASE,
)


def _strtod(token: str) -> tuple[float | None, str]:
    """Return the C ``strtod`` prefix conversion and unconsumed suffix."""
    match = _FLOAT_PREFIX.match(token)
    if match is None:
        return None, token
    consumed = match.end()
    try:
        return float(match.group(1)), token[consumed:]
    except ValueError:
        return None, token


def datareceiver(dreq: DataRequest, args_buf: str) -> int:
    """Consume complete numeric tokens from a possibly partial payload buffer.

    Brackets select scalar fill versus per-sample values, matching the legacy
    receiver's two payload levels. A return value of zero means ``#OK`` ended
    the transfer; a positive value is the number of trailing characters to keep.
    """
    if dreq.data is None:
        return -2
    dbuf = dreq.data
    w = dreq.w_data
    l = dreq.l_data
    if w <= 0 or l <= 0:
        return -2
    widx = dreq.w_pos
    lidx = dreq.l_pos
    if widx >= w or lidx > l:
        return -2
    level = dreq.level
    if level < 0 or level > 1:
        return -2

    res = 1
    cp = sp = 0
    buf = args_buf
    n = len(buf)

    while cp < n:
        curlevel = level
        c = buf[cp]
        if c == "[":
            if curlevel >= 1:
                return -1
            level += 1
            cp += 1
            sp = cp
            continue
        if c == "]":
            if curlevel == 0:
                return -1
            level -= 1

        if c.isspace() or c == "]":
            suffix: str | None = None
            if cp != sp:
                token = buf[sp:cp]
                value, suffix = _strtod(token)
                if value is not None:
                    if curlevel == 0:
                        base = widx * l
                        for j in range(l):
                            if base + j < len(dbuf):
                                dbuf[base + j] = value
                        lidx = l
                    elif curlevel == 1:
                        pos = widx * l + lidx
                        if pos < len(dbuf):
                            dbuf[pos] = value
                        lidx += 1

            closes_train = level == 0 and (cp != sp or (sp < n and buf[sp] == "]"))
            if closes_train:
                widx += 1
                lidx = 0
                if widx >= w:
                    widx = w - 1
                    lidx = l

            # strtod consumes the numeric prefix and leaves #OK in its suffix.
            if suffix == NEVSHELL_LIT_TRM:
                res = 0
                cp += 1
                break
            cp += 1
            sp = cp
            continue
        cp += 1

    if res != 0:
        if cp == n:
            if sp == 0 and cp == 0:
                return -3
            if cp != sp:
                buf = buf[sp:cp]
                res = len(buf)
            else:
                buf = " "
                res = 1

    dreq.l_pos = lidx
    dreq.w_pos = widx
    dreq.level = level

    if res == 0 and dreq.proc is not None:
        return dreq.proc(dreq.proc_arg)
    return res
