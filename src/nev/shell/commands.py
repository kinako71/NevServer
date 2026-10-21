"""Build the command tree mapping protocol words to handler functions."""

from __future__ import annotations

from nev.handlers import device, device_commands, pulse, sys
from nev.shell.parser import CommandNode

S = device_commands.handler_for


def build_command_tree() -> CommandNode:
    """Create the grammar; nested nodes represent successive command words."""
    com_stop_psa = CommandNode(literal="PSA", handler=S("STOP PSA"))

    com_run_ao = CommandNode(literal="AO", handler=S("RUN AO"))
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

    com_set_ao_dlen = CommandNode(literal="DLEN", handler=S("SET AO DLEN"))
    com_set_ao_freq = CommandNode(literal="FREQ", handler=S("SET AO FREQ"))
    com_set_ao_branch = CommandNode(
        literal="AO",
        children=[com_set_ao_dlen, com_set_ao_freq],
        handler=S("SET AO"),
    )

    com_set = CommandNode(
        children=[
            CommandNode(literal="DO", handler=S("SET DO")),
            com_set_ao_branch,
            com_set_nsu,
            CommandNode(literal="PSA", handler=S("SET PSA")),
            CommandNode(literal="PRC", handler=S("SET PRC")),
            CommandNode(literal="PULSE", handler=pulse.set_pulse),
        ]
    )

    com_get_psa_ns_data = CommandNode(literal="DATA", handler=S("GET PSA NS DATA"))
    com_get_psa_ns_stat = CommandNode(literal="STAT", handler=S("GET PSA NS STAT"))
    com_get_psa_ns = CommandNode(
        literal="NS",
        children=[com_get_psa_ns_data, com_get_psa_ns_stat],
    )
    com_get_psa_daq_data = CommandNode(literal="DATA", handler=S("GET PSA DAQ DATA"))
    com_get_psa_daq_stat = CommandNode(literal="STAT", handler=S("GET PSA DAQ STAT"))
    com_get_psa_daq = CommandNode(
        literal="DAQ",
        children=[com_get_psa_daq_data, com_get_psa_daq_stat],
    )
    com_get_psa = CommandNode(
        literal="PSA",
        children=[com_get_psa_ns, com_get_psa_daq],
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
            CommandNode(literal="PULSE", handler=pulse.get_pulse),
            CommandNode(literal="AO", handler=device.get_ao),
            CommandNode(literal="DO", handler=device.get_do),
            CommandNode(literal="AI", handler=device.get_ai),
            CommandNode(literal="DI", handler=device.get_di),
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
            CommandNode(literal="RUN", children=[
                com_run_ao,
                CommandNode(literal="AI", handler=device.run_ai),
                com_run_psa,
                CommandNode(literal="PRC", handler=S("RUN PRC")),
            ]),
            CommandNode(literal="STOP", children=[
                com_stop_psa,
                CommandNode(literal="AO", handler=S("STOP AO")),
                CommandNode(literal="AI", handler=device.stop_ai),
            ]),
            CommandNode(literal="ZEROCLEAR", handler=S("ZEROCLEAR")),
        ],
    )
    return root
