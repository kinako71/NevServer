"""C-compatible parsing helpers for command indices and value lists."""

from __future__ import annotations

from nev.shell.constants import (
    NEVSHELL_ERR_INDEX_EMPTY,
    NEVSHELL_ERR_INDEX_INVALID,
    NEVSHELL_ERR_VALUE_EMPTY,
    NEVSHELL_ERR_VALUE_INVALID,
)


def tl_get_float(s: str | None) -> tuple[int, float]:
    """Parse a float token and return the legacy error code on failure."""
    if s is None:
        return NEVSHELL_ERR_VALUE_EMPTY, 0.0
    try:
        v = float(s)
    except ValueError:
        return NEVSHELL_ERR_VALUE_INVALID, 0.0
    return 0, v


def tl_get_int(s: str | None) -> tuple[int, int]:
    """Parse a base-10 integer token and return the legacy error code."""
    if s is None:
        return NEVSHELL_ERR_VALUE_EMPTY, 0
    try:
        v = int(s, 10)
    except ValueError:
        return NEVSHELL_ERR_VALUE_INVALID, 0
    return 0, v


def tl_get_index(
    index_str: str | None, index_min: int, index_max: int
) -> tuple[int, int]:
    """Parse and range-check a task/channel index."""
    if index_str is None or index_str == "":
        return NEVSHELL_ERR_INDEX_EMPTY, 0
    code, value = tl_get_int(index_str)
    if code != 0:
        return NEVSHELL_ERR_INDEX_INVALID, 0
    if value < index_min or value > index_max:
        return NEVSHELL_ERR_INDEX_INVALID, 0
    return 0, value


def tl_get_list(
    token_list: list[str], start: int, len_list: int
) -> tuple[int, list[str], int]:
    """Read either a bracket-delimited list or a fixed-length token slice."""
    """Mirror nevSHELL_tlGetList (simplified for plain lists)."""
    if start >= len(token_list):
        return 0, [], 0
    first = token_list[start]
    if first.startswith("["):
        items: list[str] = []
        i = start
        while i < len(token_list):
            tok = token_list[i]
            if tok == "]":
                return 1, items, i + 1
            if tok not in ("[", "]"):
                items.append(tok)
            i += 1
        return -1, [], start
    end = min(start + len_list, len(token_list))
    return 0, token_list[start:end], end
