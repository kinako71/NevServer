"""DAQ and scope shell command handlers shared by real and mock operation.

Command validation and protocol responses live here. Mock mode uses in-memory
task values, while real mode reaches NI SDK calls at the explicit mode checks.
"""
from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from nev.shell.constants import (
    NEVSHELL_ERR_COMMAND_INVALID, NEVSHELL_ERR_INDEX_EMPTY,
    NEVSHELL_ERR_INDEX_INVALID, NEVSHELL_ERR_VALUE_EMPTY,
    NEVSHELL_ERR_VALUE_INVALID,
)
from nev.shell.protocol import ShellConnection
from nev.shell.datareq import set_data_req


def _ok(c: ShellConnection, msg: str = "") -> int:
    """Send a successful response, optionally prefixed by a response body."""
    return c.sh_term(msg or None)


def _send(c: ShellConnection, msg: str) -> int:
    """Send protocol text without adding a terminator marker."""
    return c.send_text(msg)


def _error(c: ShellConnection, message: str) -> int:
    """Send a protocol error with its command-specific message."""
    return c.sh_error(message)


def _index(c: ShellConnection, args: list[str], maximum: int) -> int | None:
    """Validate the first argument as a zero-based index in a fixed range."""
    if not args:
        c.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return None
    try:
        n = int(args[0], 10)
    except ValueError:
        c.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return None
    if n < 0 or n >= maximum:
        c.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return None
    return n


def _float(c: ShellConnection, token: str | None) -> float | None:
    """Parse a finite numeric setting and emit the standard error on failure."""
    if token is None:
        c.sh_error_code(NEVSHELL_ERR_VALUE_EMPTY, None)
        return None
    try:
        value = float(token)
    except ValueError:
        c.sh_error_code(NEVSHELL_ERR_VALUE_INVALID, token)
        return None
    if not math.isfinite(value):
        c.sh_error_code(NEVSHELL_ERR_VALUE_INVALID, token)
        return None
    return value


def _int(c: ShellConnection, token: str | None) -> int | None:
    """Parse an integer setting and emit the standard error on failure."""
    if token is None:
        c.sh_error_code(NEVSHELL_ERR_VALUE_EMPTY, None)
        return None
    try:
        return int(token, 10)
    except ValueError:
        c.sh_error_code(NEVSHELL_ERR_VALUE_INVALID, token)
        return None


@dataclass
class Task:
    """In-memory representation of one analog or digital DAQ task."""
    device: int
    device_name: str
    model: str
    values: list[float]
    length: int = 1024
    frequency: float = 100000.0
    running: bool = False


# These task records mirror the physical AO/AI/DO/DI task lists. Mock mode
# stores them in memory; real mode shares their buffers with native NI tasks.
TASKS: dict[str, list[Task]] = {
    "AO": [Task(0, "Dev1", "Mock AO", [0.0] * (4 * 1024))],
    "AI": [Task(0, "Dev1", "Mock AI", [0.0] * 1024)],
    "DO": [Task(0, "Dev1", "Mock DO", [0.0] * 8, length=1, frequency=0.0)],
    "DI": [Task(0, "Dev1", "Mock DI", [0.0] * 8, length=1, frequency=0.0)],
}


def _group(name: str, args: list[str], c: ShellConnection, allow_all=False):
    """Resolve a task index, or expand ``*`` when the command allows it."""
    tasks = TASKS[name]
    if not args:
        c.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return None
    if allow_all and args[0] == "*":
        return list(enumerate(tasks))
    idx = _index(c, args, len(tasks))
    return None if idx is None else [(idx, tasks[idx])]


def _get_values(group, args, c):
    """Format one physical AO/DO task using the shell response layout."""
    found = _group(group, args, c)
    if found is None:
        return 0
    idx, task = found[0]
    label = "GET" + group
    values = " ".join(str(int(v)) if group == "DO" else f"{v:f}" for v in task.values)
    return _send(c, f"#{label} {idx}\n{values} \n#OK")


def _set_values(group, args, c):
    """Update AO/DO values and write them to NI hardware when enabled."""
    from nev.hardware.bootstrap import get_runtime, write_daq
    runtime = get_runtime()
    found = _group(group, args, c)
    if found is None:
        return 0
    task = found[0][1]
    if group == "DO":
        vals = []
        for item in args[1:]:
            try: vals.append(int(float(item)) & 0xff)
            except ValueError: continue
    else:
        vals = []
        for item in args[1:]:
            try: vals.append(float(item))
            except ValueError: continue
    for i, value in enumerate(vals[:len(task.values)]):
        task.values[i] = value
    if not runtime.mock:
        write_daq(group, found[0][0])
    return _ok(c)


def _set_ao(args, c):
    """Enter streamed AO waveform mode and commit the data on ``#OK``."""
    from nev.hardware.bootstrap import get_runtime, reconfigure_daq, write_daq
    runtime = get_runtime()
    found = _group("AO", args, c)
    if found is None: return 0
    idx, task = found[0]
    width=len(task.values)//task.length if task.length else 0
    channel = _index(c, args[1:2], width)
    if channel is None: return 0
    if _send(c, f"#SETAO {idx} #OK\n") < 0: return -17
    width=len(task.values)//task.length
    def update(_task):
        """Commit the received values and restore a ready output task."""
        if not runtime.mock:
            reconfigure_daq("AO", idx)
            write_daq("AO", idx)
        task.running = False
        return 0
    if set_data_req("r", task.values, task.length, width, 0, channel, update, task) < 0:
        return _error(c,"!DATA_REQUEST_ERROR")
    return 0


def _set_ao_param(param, args, c):
    """Set AO length or frequency for one task or every configured task."""
    from nev.hardware.bootstrap import get_runtime, reconfigure_daq
    found = _group("AO", args, c, allow_all=True)
    if found is None: return 0
    value = _int(c, args[1] if len(args) > 1 and param == "DLEN" else None) if param == "DLEN" else _float(c, args[1] if len(args) > 1 else None)
    if value is None: return 0
    if param == "DLEN" and value <= 0:
        return _error(c, "!INVALID_VALUE")
    runtime=get_runtime()
    for idx, task in found:
        if param == "DLEN":
            width=len(task.values)//task.length
            task.length = value
            task.values[:] = [0.0] * (width * value)
            if not runtime.mock:
                runtime.daq["AO"][idx].length = value
        else:
            task.frequency = value
            if not runtime.mock:
                runtime.daq["AO"][idx].frequency = value
        if not runtime.mock:
            reconfigure_daq("AO", idx)
            task.running = False
    return _ok(c)


def _run_ao(args, c):
    """Start one AO task or all AO tasks."""
    from nev.hardware.bootstrap import get_runtime, reconfigure_daq
    runtime = get_runtime()
    found = _group("AO", args, c, allow_all=True)
    if found is None: return 0
    for idx, task in found:
        if not runtime.mock:
            native = runtime.daq["AO"][idx]
            if native.native is None:
                reconfigure_daq("AO", idx)
            if not native.running:
                native.native.start()
                native.running = True
        task.running = True
    return _ok(c)


def _stop_ao(args, c):
    """Stop one AO task or all AO tasks."""
    runtime = get_runtime()
    found = _group("AO", args, c, allow_all=True)
    if found is None: return 0
    for idx, task in found:
        if not runtime.mock:
            native = runtime.daq["AO"][idx]
            if native.native is not None and native.running:
                native.native.stop()
                native.running = False
        task.running = False
    return _ok(c)


def _zero(args, c):
    """Clear every AO and DO task, restarting continuous AO tasks."""
    from nev.hardware.bootstrap import get_runtime, reconfigure_daq, write_daq
    runtime = get_runtime()
    for group in ("AO", "DO"):
        tasks = TASKS[group]
        for idx, task in enumerate(tasks):
            task.values[:] = [0.0] * len(task.values)
            task.running = False
            if not runtime.mock:
                if group == "AO":
                    reconfigure_daq("AO", idx)
                write_daq(group, idx)
                if group == "AO" and task.frequency > 0:
                    runtime.daq["AO"][idx].native.start()
                    runtime.daq["AO"][idx].running = True
                    task.running = True
    return _ok(c)


def _nsu_index(args, c):
    """Validate an NI-SCOPE union index."""
    return _index(c, args, 4)


@dataclass
class ScopeUnion:
    """Configuration shared by the scope devices in one acquisition union."""
    devices: list[int] = field(default_factory=list)
    channels: dict[int, list[tuple[float, str]]] = field(default_factory=dict)
    length: int = 10000
    frequency: float = 50000.0
    trigger: list[str] = field(default_factory=lambda: ["50.000000", "IMMEDIATE", "IMMEDIATE", "0", "0.000000", "POSITIVE", "DC", "0.000000", "0.000000"])


UNIONS = [ScopeUnion(devices=[0])] + [ScopeUnion() for _ in range(3)]
SCOPE_DEVICES = [("Dev1", "PXI-5922", 2, 1, 0, "A1B2")]


def _scope_info(args,c):
    """Return the inventory populated by mock fixtures or NI-ModInst."""
    from nev.hardware.bootstrap import get_runtime, niscope_device_inventory
    devices = SCOPE_DEVICES if get_runtime().mock else niscope_device_inventory()
    if _send(c,"#NISCOPEINFO\n") < 0: return -17
    body="".join(
        f"[{slot},{name},{model},{channels},{chassis},{serial}]"
        for name,model,channels,slot,chassis,serial in devices
    )
    return _ok(c,body+"\n")


def _scope_get(kind,args,c):
    """Format one scope-union property using its GET NSU response format."""
    prefix={"NUM":"#NSUNUM\n","DEVS":"#NSUDEVS","CHAN":"#NSUCHAN","DLEN":"#NSUDLEN","FREQ":"#NSUFREQ","TRIG":"#NSUTRIG"}[kind]
    if kind == "NUM": return _ok(c, f"{prefix}4\n")
    idx=_nsu_index(args,c)
    if idx is None: return 0
    u=UNIONS[idx]
    if _send(c,f"{prefix} {idx}\n") < 0: return -17
    if kind == "DEVS": data=f"{len(u.devices)} ["+" ".join(map(str,u.devices))+"]\n"
    elif kind == "CHAN":
        vals=[
            setting
            for d in u.devices
            for setting in u.channels.get(
                d, [(2.0, "DC") for _ in range(SCOPE_DEVICES[d][2])]
            )
        ]
        data=f"{len(vals)} "+"".join(f"[{r:f} {coupling}]" for r,coupling in vals)+"\n"
    elif kind == "DLEN":
        channels = sum(SCOPE_DEVICES[d][2] for d in u.devices)
        data=f"{u.length} [{ ' '.join(str(u.length) for _ in range(channels)) }]\n"
    elif kind == "FREQ":
        channels = sum(SCOPE_DEVICES[d][2] for d in u.devices)
        data=f"{u.frequency:f} [{ ' '.join(f'{u.frequency:f}' for _ in range(channels)) }]\n"
    else:
        trigger=u.trigger[:]
        if u.devices and 0 <= int(trigger[3]) < len(u.devices):
            trigger[3]=str(u.devices[int(trigger[3])])
        data="["+" ".join(trigger)+"]\n"
    return _ok(c,data)


def _parse_bracketed(args, start):
    """Normalize a bracketed command argument list into plain tokens."""
    if start >= len(args): return None
    vals=[v for v in args[start:] if v not in ("[", "]")]
    if vals and vals[0].startswith("["):
        vals[0]=vals[0][1:]
    if vals and vals[-1].endswith("]"):
        vals[-1]=vals[-1][:-1]
    return [v.strip("()[]") for v in vals if v.strip("()[]")]


def _scope_set(kind,args,c):
    """Validate and update one scope-union configuration property."""
    idx=_nsu_index(args,c)
    if idx is None: return 0
    u=UNIONS[idx]
    if kind=="DEVS":
        n=_int(c,args[1] if len(args)>1 else None)
        vals=_parse_bracketed(args,2)
        if n is None or vals is None or n>8 or len(vals)<n: return _error(c,"!INVALID_DEVICE_LIST")
        try: devices=[int(v) for v in vals[:n]]
        except ValueError: return _error(c,"!INVALID_DEVICE_INDEX")
        if any(d<0 or d>=len(SCOPE_DEVICES) for d in devices): return _error(c,"!INVALID_DEVICE_INDEX")
        u.devices=devices
        u.channels={d:[(2.0,"DC") for _ in range(SCOPE_DEVICES[d][2])] for d in devices}
    elif kind=="CHAN":
        if len(args)<4: return _error(c,"!INVALID_CHANNEL_CONFIGURATION")
        try: dev=int(args[1])
        except ValueError: return _error(c,"!INVALID_DEVICE_ID")
        pairs=_parse_bracketed(args,2)
        if dev not in u.devices or not pairs or len(pairs)<2 or len(pairs)%2:
            return _error(c,"!INVALID_PARAMETERS")
        try:
            parsed=[(pairs[i] if pairs[i]=="*" else float(pairs[i]),pairs[i+1].upper()) for i in range(0,len(pairs)-1,2)]
        except ValueError: return _error(c,"!INVALID_VALUE")
        if any(coupling not in ("AC","DC","GND","*") for _,coupling in parsed): return _error(c,"!INVALID_VALUE")
        if len(parsed) > SCOPE_DEVICES[dev][2]: return _error(c,"!INVALID_PARAMETERS")
        old=u.channels.get(dev,[(2.0,"DC") for _ in range(SCOPE_DEVICES[dev][2])])
        u.channels[dev]=[(old[i][0] if r == "*" else r, old[i][1] if coupling == "*" else coupling)
                         for i,(r,coupling) in enumerate(parsed)]
    elif kind in ("DLEN","FREQ"):
        value=_int(c,args[1] if len(args)>1 and kind=="DLEN" else None) if kind=="DLEN" else _float(c,args[1] if len(args)>1 else None)
        if value is None: return 0
        if value<=0: return _error(c,"!INVALID_VALUE")
        if kind=="DLEN": u.length=value
        else: u.frequency=value
    elif kind=="TRIG":
        vals=_parse_bracketed(args,1)
        if vals is None or len(vals)<9: return _error(c,"!INVALID_TRIGGER_CONFIGURATION")
        u.trigger=[old if value=="*" else value for old,value in zip(u.trigger,vals[:9])]
        try: trigger_device=int(u.trigger[3])
        except ValueError: return _error(c,"!INVALID_TRIGGERDEVICE")
        if trigger_device not in u.devices: return _error(c,"!INVALID_TRIGGERDEVICE")
        u.trigger[3]=str(u.devices.index(trigger_device))
    return _ok(c)


PSA = {"status":"INVALID","start":0.0,"stop":0.0,"steps":0,"skip":0,"values":[],"source":"AO","device":0,"channel":0,"union":0,"parameter":0.0}
_psa_cancel = threading.Event()


def _psa_set(args,c):
    """Validate scan parameters and store a reusable PSA configuration."""
    # The shell splits on spaces, so grouped values arrive as tokens such as
    # "[AO" and "0]" rather than as a single bracketed token.
    args=[v.strip("[]") for v in args if v.strip("[]")]
    if len(args)<8: return _error(c,"!SCAN_PARAMETER_NOT_GIVEN")
    try:
        union=int(args[0]); source=args[1].strip("[]"); device=int(args[2]); channel=int(args[3]); start=float(args[4]); stop=float(args[5]); steps=int(args[6]); skip=int(args[7])
    except (ValueError,IndexError): return _error(c,"!INVALID_VALUE")
    source = source.upper()
    tasks = TASKS.get(source, [])
    width = (len(tasks[device].values) // tasks[device].length
             if device in range(len(tasks)) and tasks[device].length > 0 else 0)
    if (union not in range(len(UNIONS)) or source not in ("AO", "DO")
            or device not in range(len(tasks)) or channel not in range(width)
            or steps <= 0 or skip < 0 or not math.isfinite(start) or not math.isfinite(stop)):
        return _error(c,"!INVALID_SCAN_CONFIGURATION")
    if not UNIONS[union].devices:
        return _error(c,"!INVALID_SCAN_CONFIGURATION")
    PSA.update(status="VALID",mode="PSA",start=start,stop=stop,steps=steps,skip=skip,values=[],source=source,device=device,channel=channel,union=union,parameter=start)
    PSA["error"]=""
    return _ok(c)


def _psa_run(args,c):
    """Prepare scope hardware synchronously, then execute the scan asynchronously."""
    if PSA["status"] not in ("VALID","FAILED","HARVESTED","ABORTED"): return _error(c,"!PSA_NOT_READY")
    from nev.hardware.bootstrap import get_runtime
    _psa_cancel.clear()
    PSA["values"]=[]
    PSA.update(values=[], parameter=PSA["start"], error="", status="TRIGGERED")
    if not get_runtime().mock:
        try:
            from nev.hardware.bootstrap import configure_scope
            configure_scope(UNIONS[PSA["union"]])
        except Exception as exc:
            PSA.update(status="FAILED", error=str(exc))
            return _error(c,f"!NISCOPE_API_ERROR({exc})")
    threading.Thread(target=_execute_psa_scan, name="nev-psa", daemon=True).start()
    return _ok(c)


def _execute_psa_scan():
    """Step the selected output through scan values and collect scope samples."""
    from nev.hardware.bootstrap import (
        acquire_scope, get_runtime,
        reconfigure_daq, write_daq,
    )
    runtime = get_runtime()
    task=TASKS[PSA["source"]][PSA["device"]]
    count=PSA["steps"]+1
    union=UNIONS[PSA["union"]]
    try:
        for i in range(count):
            if _psa_cancel.is_set():
                PSA["status"]="ABORTED"
                return
            parameter=PSA["parameter"]
            offset=PSA["channel"]*task.length
            if PSA["source"] == "DO":
                value = float(int(parameter) & 0xff)
                task.values[offset:offset + task.length] = [value] * task.length
            else:
                task.values[offset:offset + task.length] = [parameter] * task.length
            if not runtime.mock:
                PSA["status"]="PLANTED"
                if PSA["source"] == "AO" and task.frequency > 0:
                    reconfigure_daq("AO", PSA["device"])
                    write_daq("AO", PSA["device"])
                    runtime.daq["AO"][PSA["device"]].native.start()
                    runtime.daq["AO"][PSA["device"]].running = True
                    task.running = True
                else:
                    write_daq(PSA["source"], PSA["device"])
                samples=acquire_scope(union, sample_offset=PSA["skip"])
            else:
                PSA["status"]="PLANTED"
                samples = [
                    [
                        [math.sin(i * .1 + channel * .01 + j * .001)
                         for j in range(PSA["skip"], PSA["skip"] + union.length)]
                        for channel in range(SCOPE_DEVICES[device_index][2])
                    ]
                    for device_index in union.devices
                ]
            PSA["status"]="READY"
            PSA["values"].append((parameter,samples))
            if i + 1 < count:
                PSA["parameter"] = PSA["start"] + (PSA["stop"] - PSA["start"]) * (i + 1) / PSA["steps"]
                PSA["status"]="TRIGGERED"
    except Exception as exc:
        PSA["status"]="ABORTED" if _psa_cancel.is_set() else "FAILED"
        PSA["error"]=str(exc)
        return
    PSA["status"]="HARVESTED"


def _psa_data(args,c):
    """Return one or more acquired parameter/sample sets."""
    if PSA["status"]=="INVALID": return _error(c,"!PSA_NOT_READY")
    if not args:
        c.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return -1
    spec=args[0]
    match=re.fullmatch(r"(\d+)(?:-(\d*)?)?",spec)
    if not match: return _error(c,"!INVALID_INDEX")
    if match.group(2) == "":
        c.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return -1
    begin=int(match.group(1)); end=int(match.group(2)) if match.group(2) is not None else begin
    # In the C command, an explicit end index of zero means “latest”.
    if match.group(2) == "0": end=len(PSA["values"])-1
    if begin<0 or begin>=len(PSA["values"]) or end<0 or end>=len(PSA["values"]): return _error(c,"!INDEX_OUT_OF_RANGE")
    if _send(c,f"#PSADATA {begin} {end}\n") < 0: return -17
    for parameter,data in PSA["values"][begin:end+1]:
        if _send(c,f"{parameter:f}\n")<0: return -17
        for device in data:
            for channel in device:
                if _send(c,"["+" ".join(f"{v:f}" for v in channel)+"]\n")<0: return -17
    return _ok(c)


def _psa_stat(args,c):
    """Report scan progress, current parameter, state, and any scan error."""
    status={"INVALID":"UNCONFIGURED","FAILED":"FAILED","VALID":"IDLE","TRIGGERED":"RUNNING","PLANTED":"RUNNING","READY":"RUNNING","HARVESTED":"COMPLETE","ABORTED":"ABORTED"}[PSA["status"]]
    current=PSA["parameter"]
    error=f" {PSA.get('error','')}" if PSA["status"]=="FAILED" else ""
    return _ok(c,f"#PSASTAT\n{len(PSA['values'])} {current:f} {status}{error}\n")


def _psa_stop(args,c):
    """Request scan cancellation and abort the active real scope acquisition."""
    if PSA["status"]=="INVALID": return _error(c,"!NO_PSA_TO_STOP")
    from nev.hardware.bootstrap import abort_scope, get_runtime
    _psa_cancel.set()
    if not get_runtime().mock:
        abort_scope(UNIONS[PSA["union"]])
    PSA["status"]="ABORTED"
    return _ok(c)


def _prc_set(args,c):
    """Reuse scan configuration parsing and label the configuration as PRC."""
    res=_psa_set(args,c)
    if PSA["status"]=="VALID": PSA["mode"]="PRC"
    return res


def _prc_run(args,c):
    """Run the configured PRC workflow through the shared scan machinery."""
    if PSA.get("mode")!="PRC": return _error(c,"!PRC_NOT_CONFIGURED")
    return _psa_run(args,c)


def _prc_get(args,c):
    """Return a peak-to-peak summary for each acquired PRC parameter."""
    if PSA.get("mode")!="PRC" or not PSA["values"]: return _error(c,"!PRC_DATA_UNAVAILABLE")
    if _send(c,"#PRC\n")<0: return -17
    for parameter, devices in PSA["values"]:
        samples=[v for device in devices for channel in device for v in channel]
        response=(max(samples)-min(samples)) if samples else 0.0
        if _send(c,f"{parameter:f} {response:f}\n")<0: return -17
    return _ok(c)


def handler_for(name: str) -> Callable[[list[str], ShellConnection], int]:
    """Bind a command name to its handler and convert device exceptions.

    The table preserves the shell grammar while the returned wrapper keeps
    unexpected device errors inside the protocol as ``#NG`` responses.
    """
    table={
        "GET NISCOPEINFO":_scope_info,
        "GET AO":lambda a,c:_get_values("AO",a,c),"GET DO":lambda a,c:_get_values("DO",a,c),
        "SET AO":_set_ao,"SET AO DLEN":lambda a,c:_set_ao_param("DLEN",a,c),
        "SET AO FREQ":lambda a,c:_set_ao_param("FREQ",a,c),"SET DO":lambda a,c:_set_values("DO",a,c),
        "RUN AO":_run_ao,"STOP AO":_stop_ao,"ZEROCLEAR":_zero,
        "GET NSU NUM":lambda a,c:_scope_get("NUM",a,c),"GET NSU DEVS":lambda a,c:_scope_get("DEVS",a,c),
        "GET NSU CHAN":lambda a,c:_scope_get("CHAN",a,c),"GET NSU DLEN":lambda a,c:_scope_get("DLEN",a,c),
        "GET NSU FREQ":lambda a,c:_scope_get("FREQ",a,c),"GET NSU TRIG":lambda a,c:_scope_get("TRIG",a,c),
        "SET NSU DEVS":lambda a,c:_scope_set("DEVS",a,c),"SET NSU CHAN":lambda a,c:_scope_set("CHAN",a,c),
        "SET NSU DLEN":lambda a,c:_scope_set("DLEN",a,c),"SET NSU FREQ":lambda a,c:_scope_set("FREQ",a,c),
        "SET NSU TRIG":lambda a,c:_scope_set("TRIG",a,c),"SET PSA":_psa_set,"RUN PSA":_psa_run,
        "GET PSA DATA":_psa_data,"GET PSA STAT":_psa_stat,"STOP PSA":_psa_stop,
        "SET PRC":_prc_set,"RUN PRC":_prc_run,"GET PRC":_prc_get,
    }
    try: handler=table[name]
    except KeyError as exc: raise ValueError(f"unknown device command handler: {name}") from exc
    def safe_handler(args: list[str], conn: ShellConnection) -> int:
        """Keep device exceptions inside the shell response protocol."""
        try:
            return handler(args, conn)
        except Exception as exc:
            import logging
            logging.getLogger("nev.handlers").exception("Hardware command failed: %s", name)
            return conn.sh_error(f"!HARDWARE_API_ERROR({exc})")
    safe_handler.__name__=name.replace(" ","_")
    return safe_handler
