# Hardware initialization and NI call sites

- `handlers/device_commands.py` keeps shared command parsing, protocol state,
  and mock data for commands that use the same shell interface in both modes.
- `bootstrap.py` owns native NI resources and contains the NI SDK operations.
  Command handlers check `get_runtime().mock` immediately before those calls.

For example, `GET AO` follows `shell/commands.py` → `handlers/device.py` →
task buffer → protocol reply. Output commands keep their validation and response
formatting in the handler; `if not runtime.mock` guards only the operation that
must call the NI SDK. This is the same boundary used for NI scope acquisition.

`GET DAQMXINFO` reports the AO/AI/DO/DI task groups and appends one
`DEVICE[...]` record per DAQmx device. Real mode obtains product
type/category, serial number, AI/AO physical channel names, and terminals from
`nidaqmx.system.System.local()`. If terminal enumeration fails, the rest of the
device inventory is still returned and the error is included in that record.
Mock mode reports fixture devices and does not load the NI package.

`bootstrap.py` discovers physical AO/AI/DO/DI tasks independently, and the shell
uses those same group names and indices. `SET AO` updates one channel's streamed
waveform; `SET DO` writes digital output values. `RUN AO`/`STOP AO` control AO
tasks. `RUN AI`/`STOP AI` control AI tasks, and `GET AI` reads one block (starting
the task on demand). No device or trigger terminal is hard-coded; physical
channel names and supported clock rates come from NI-DAQmx device discovery.

Keep the SDK calls in `bootstrap.py`; command parsing and response formatting
stay shared, with mock checks directly beside each hardware-specific operation.
