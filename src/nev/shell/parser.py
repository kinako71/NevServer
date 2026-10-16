"""Tokenize shell input and dispatch it through a C-style command tree."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from nev.shell.constants import (
    NEVSHELL_COMMAND_SEPARATORS,
    NEVSHELL_ERR_EMPTY,
    NEVSHELL_ERR_SOCK,
    NEVSHELL_ERR_UNKNOWN,
    NEVSHELL_MAX_COMTOKENS,
)
from nev.shell.protocol import ShellConnection

log = logging.getLogger(__name__)

Handler = Callable[[list[str], ShellConnection], int]


@dataclass
class CommandNode:
    """One keyword or root node in the shell command grammar."""
    literal: str | None = None
    children: list[CommandNode] = field(default_factory=list)
    handler: Handler | None = None
    is_root: bool = False

    def child(self, literal: str) -> CommandNode | None:
        """Find the child node matching one exact command token."""
        for c in self.children:
            if c.literal == literal:
                return c
        return None


def tokenize_command(line: str) -> list[str]:
    """Split input using the separators defined by the original protocol."""
    return [t for t in re.split(f"[{re.escape(NEVSHELL_COMMAND_SEPARATORS)}]+", line) if t]


def comparser(comdef: CommandNode, token_list: list[str], conn: ShellConnection) -> int:
    """Match tokens to a command node and invoke its handler on a full match."""
    log.debug("comparser tokens=%s", token_list)
    if not token_list:
        return conn.sh_error_code(NEVSHELL_ERR_EMPTY, None)

    tlp = 0
    head = token_list[tlp]

    for cdp in comdef.children:
        if cdp.literal != head:
            continue
        res = 0
        rest = token_list[tlp + 1 :]
        if cdp.children:
            res = comparser(_subtree(cdp), rest, conn)
            if res != NEVSHELL_ERR_UNKNOWN:
                return res
        if cdp.handler is not None:
            log.debug("handler call args=%s", rest)
            res = cdp.handler(rest, conn)
            if res != NEVSHELL_ERR_UNKNOWN:
                return res
        if rest:
            return conn.sh_error_code(NEVSHELL_ERR_UNKNOWN, rest[0])
        return conn.sh_error_code(NEVSHELL_ERR_UNKNOWN, head)

    log.debug("unknown literal: %s", head)
    if comdef.is_root:
        return conn.sh_error_code(NEVSHELL_ERR_UNKNOWN, head)
    return NEVSHELL_ERR_UNKNOWN


def _subtree(node: CommandNode) -> CommandNode:
    """Return the child root, or an empty root if this node has no children."""
    """Synthetic node whose children are node.children (for nested comparser)."""
    return CommandNode(children=node.children, is_root=False)


def parse_tokens_from_buffer(shell_buf: str) -> tuple[list[str] | None, int]:
    """Extract complete tokens and report how much trailing input is partial."""
    tokens = tokenize_command(shell_buf.strip("\0"))
    if len(tokens) > NEVSHELL_MAX_COMTOKENS:
        return None, -2
    return tokens, 0
