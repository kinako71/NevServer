"""Build the command tree mapping protocol words to handler functions."""

from __future__ import annotations

from nev.handlers import device, device_commands, sys
from nev.shell.parser import CommandNode

S = device_commands.handler_for


def build_command_tree() -> CommandNode:
    """Create the grammar; nested nodes represent successive command words."""
    com_stop_psa = CommandNode(literal="PSA", handler=S("STOP PSA"))

    com_run_dao = CommandNode(literal="DAO", handler=S("RUN DAO"))
    com_run_psa = CommandNode(literal="PSA", handler=S("RUN PSA"))

    com_set_ns_devs = CommandNode(literal="DEVS", handler=S("SET NSU DEVS"))
    com_set_ns_chan = CommandNode(literal="CHAN", handler=S("SET NSU CHAN"))
    com_set_ns_dlen = CommandNode(literal="DLEN", handler=S("SET NSU DLEN"))
    com_set_ns_freq = CommandNode(literal="FREQ", handler=S("SET NSU FREQ"))
    com_set_ns_trig = CommandNode(literal="TRIG", handler=S("SET NSU TRIG"))
    com_set_nsu = CommandNode(
        literal="NSU",
        children=[
            com_set_ns_devs,
            com_set_ns_chan,
            com_set_ns_dlen,
            com_set_ns_freq,
            com_set_ns_trig,
        ],
    )

    com_set_dao_dlen = CommandNode(literal="DLEN", handler=S("SET DAO DLEN"))
    com_set_dao_freq = CommandNode(literal="FREQ", handler=S("SET DAO FREQ"))
    com_set_dao_branch = CommandNode(
        literal="DAO",
        children=[com_set_dao_dlen, com_set_dao_freq],
        handler=S("SET DAO"),
    )  # mirrors SET / DAO + SETDAO subcommands

    com_set = CommandNode(
        children=[
            CommandNode(literal="SAO", handler=S("SET SAO")),
            CommandNode(literal="SDO", handler=S("SET SDO")),
            com_set_dao_branch,
            com_set_nsu,
            CommandNode(literal="PSA", handler=S("SET PSA")),
            CommandNode(literal="PRC", handler=S("SET PRC")),
        ]
    )

    com_get_psa_data = CommandNode(literal="DATA", handler=S("GET PSA DATA"))
    com_get_psa_stat = CommandNode(literal="STAT", handler=S("GET PSA STAT"))
    com_get_psa = CommandNode(
        literal="PSA",
        children=[com_get_psa_data, com_get_psa_stat],
    )
    com_get_prc = CommandNode(literal="PRC", handler=S("GET PRC"))

    com_get_nsu_num = CommandNode(literal="NUM", handler=S("GET NSU NUM"))
    com_get_nsu_devs = CommandNode(literal="DEVS", handler=S("GET NSU DEVS"))
    com_get_nsu_chan = CommandNode(literal="CHAN", handler=S("GET NSU CHAN"))
    com_get_nsu_dlen = CommandNode(literal="DLEN", handler=S("GET NSU DLEN"))
    com_get_nsu_freq = CommandNode(literal="FREQ", handler=S("GET NSU FREQ"))
    com_get_nsu_trig = CommandNode(literal="TRIG", handler=S("GET NSU TRIG"))
    com_get_nsu = CommandNode(
        literal="NSU",
        children=[
            com_get_nsu_num,
            com_get_nsu_devs,
            com_get_nsu_chan,
            com_get_nsu_dlen,
            com_get_nsu_freq,
            com_get_nsu_trig,
        ],
    )

    com_get = CommandNode(
        children=[
            CommandNode(literal="SYSINFO", handler=sys.nev_sys_echoinfo),
            CommandNode(literal="DAQMXINFO", handler=device.get_daqmx_info),
            CommandNode(literal="NISCOPEINFO", handler=S("GET NISCOPEINFO")),
            CommandNode(literal="SAO", handler=device.get_sao),
            CommandNode(literal="SDO", handler=device.get_sdo),
            CommandNode(literal="AI", handler=device.get_ai),
            com_get_nsu,
            com_get_psa,
            com_get_prc,
        ]
    )

    root = CommandNode(
        is_root=True,
        children=[
            CommandNode(literal="GET", children=com_get.children),
            CommandNode(literal="SET", children=com_set.children),
            CommandNode(literal="RUN", children=[com_run_dao, com_run_psa, CommandNode(literal="PRC", handler=S("RUN PRC"))]),
            CommandNode(literal="STOP", children=[com_stop_psa, CommandNode(literal="DAO", handler=S("STOP DAO"))]),
            CommandNode(literal="ZEROCLEAR", handler=S("ZEROCLEAR")),
        ],
    )
    return root
