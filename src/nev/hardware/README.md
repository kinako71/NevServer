# Hardware initialization and NI call sites

- `handlers/device_commands.py` keeps shared command parsing, protocol state,
  and mock data for commands that use the same shell interface in both modes.
- `bootstrap.py` owns native NI resources and contains the NI SDK operations.
  Command handlers check `get_runtime().mock` immediately before those calls.

For example, `GET SAO` follows `shell/commands.py` → `handlers/device.py` →
task buffer → protocol reply. Output commands keep their validation and response
formatting in the handler; `if not runtime.mock` guards only the operation that
must call the NI SDK. This is the same boundary used for NI scope acquisition.

`GET DAQMXINFO` retains the C-compatible SAO/DAO/SDO task summary and appends
one `DEVICE[...]` record per DAQmx device. Real mode obtains product
type/category, serial number, AI/AO physical channel names, and terminals from
`nidaqmx.system.System.local()`. If terminal enumeration fails, the rest of the
device inventory is still returned and the error is included in that record.
Mock mode reports fixture devices and does not load the NI package.

`RUN DAO <index>` starts both the DAO output and AI acquisition. AO uses
`/PXI1Slot4/te0/SampleClock` and `/PXI1Slot4/te0/StartTrigger`; the AI channel is
`PXI1Slot4/ai0`. `GET AI <index>` reads one block, and `STOP DAO <index>` stops
the pair while preserving the last AI block for retrieval. This replaces the
sample's `input()` wait with shell commands, keeping the TCP server responsive.

Keep the SDK calls in `bootstrap.py`; command parsing and response formatting
stay shared, with mock checks directly beside each hardware-specific operation.
