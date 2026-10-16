"""Shell handlers with shared protocol formatting and localized device calls.

Parsing and response formatting stay here. Only the calls that require NI
hardware branch on the runtime's mock flag.
"""
from __future__ import annotations

import math

from nev.hardware.bootstrap import daqmx_device_inventory, get_runtime, read_ai
from nev.shell.constants import NEVSHELL_ERR_INDEX_EMPTY, NEVSHELL_ERR_INDEX_INVALID
from nev.shell.protocol import ShellConnection


def _get_daq(group: str, args: list[str], connection: ShellConnection) -> int:
    """Send one selected group's current values using the C response layout."""
    from nev.handlers import device_commands
    runtime = get_runtime()
    tasks = device_commands.TASKS[group]
    if not args:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return 0
    try:
        index = int(args[0], 10)
    except ValueError:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0

    if index < 0 or index >= len(tasks):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0

    try:
        # GET reads the same handler-owned protocol buffer in both modes.
        # NI SDK reads are not needed for output buffers that were set here.
        values = list(tasks[index].values)
        if not runtime.mock:
            # The native task's state is kept in sync by SET/RUN handlers.
            native = runtime.daq[group][index]
            values = list(native.values)
    except (IndexError, KeyError):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0

    label = f"GET{group}"
    if group == "SDO":
        payload = " ".join(str(int(value)) for value in values)
    else:
        payload = " ".join(f"{value:f}" for value in values)
    return connection.send_text(f"#{label} {index}\n{payload} \n#OK")


def get_sao(args: list[str], connection: ShellConnection) -> int:
    """Handle ``GET SAO`` using the shared task buffer."""
    return _get_daq("SAO", args, connection)


def get_sdo(args: list[str], connection: ShellConnection) -> int:
    """Handle ``GET SDO`` using the shared task buffer."""
    return _get_daq("SDO", args, connection)


def get_daqmx_info(args: list[str], connection: ShellConnection) -> int:
    """backend情報をC互換タスク形式とデバイス詳細行に整形して返す。"""
    try:
        from nev.handlers import device_commands
        runtime = get_runtime()
        info = {}
        for group, tasks in device_commands.TASKS.items():
            info[group] = [
                (task.device, task.device_name, task.model,
                 len(task.values) // task.length if group == "DAO" else len(task.values),
                 task.length, task.frequency, task.running)
                for task in tasks
            ]
        if runtime.mock:
            # The mock inventory is descriptive fixture data, not NI discovery.
            devices_by_name = {}
            for group, tasks in device_commands.TASKS.items():
                for task in tasks:
                    entry = devices_by_name.setdefault(task.device_name, {
                        "name": task.device_name, "product_type": task.model,
                        "product_category": "Mock", "serial_number": "MOCK",
                        "ai_channels": [], "ao_channels": [], "terminals": [],
                        "terminals_error": None,
                    })
                    if group == "DAO":
                        entry["ai_channels"].append(f"{task.device_name}/ai0")
                    if group in ("SAO", "DAO"):
                        width = len(task.values) // task.length if group == "DAO" else len(task.values)
                        entry["ao_channels"].extend(f"{task.device_name}/ao{i}" for i in range(width))
                    elif group == "SDO":
                        entry["terminals"].extend(f"{task.device_name}/port0/line{i}" for i in range(len(task.values)))
            devices = list(devices_by_name.values())
        else:
            # This is the precise NI-only boundary: mock mode never imports nidaqmx.
            devices = daqmx_device_inventory()
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")
    if connection.send_text("#DAQMXINFO\n") < 0:
        return -17
    # 先頭の3グループはC版 nevDAQmx_shINFO と同じ形式を維持する。
    for group in ("SAO", "DAO", "SDO"):
        records = "".join(
            f"[{device},{name},{model},{channels},{length},{frequency:f},{int(running)}]"
            for device, name, model, channels, length, frequency, running in info.get(group, [])
        )
        if connection.send_text(f"{group}{records}\n") < 0:
            return -17
    # DEVICE行は追加情報。チャンネル・端子一覧は | で連結する。
    # C互換のタスク行を保ったまま、物理デバイスの詳細も確認できる。
    for device in devices:
        ai_channels = "|".join(device["ai_channels"])
        ao_channels = "|".join(device["ao_channels"])
        terminals = "|".join(device["terminals"])
        record = (
            f"DEVICE[{device['name']},{device['product_type']},{device['product_category']},"
            f"{device['serial_number']},AI={ai_channels},AO={ao_channels},"
            f"TERMINALS={terminals}"
        )
        if device["terminals_error"]:
            safe_error = (
                device["terminals_error"].replace("\r", " ")
                .replace("\n", " ")
                .replace(",", ";")
            )
            record += f",TERMINALS_ERROR={safe_error}"
        if connection.send_text(record + "]\n") < 0:
            return -17
    return connection.sh_term()


def get_ai(args: list[str], connection: ShellConnection) -> int:
    """Read and format the AI waveform paired with a running DAO task."""
    from nev.handlers import device_commands
    runtime = get_runtime()
    if not args:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return 0
    try:
        index = int(args[0], 10)
    except ValueError:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0
    if index < 0 or index >= len(device_commands.TASKS["DAO"]):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0
    try:
        if runtime.mock:
            task = device_commands.TASKS["DAO"][index]
            if task.running:
                values = [math.sin(sample * 0.01) for sample in range(task.length)]
                runtime.ai_data[index] = values
            elif index in runtime.ai_data:
                values = list(runtime.ai_data[index])
            else:
                raise RuntimeError("AI acquisition is not running for this DAO task")
        else:
            # Only real mode reaches the NI sample-read function.
            values = read_ai(index)
    except (IndexError, KeyError):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")
    payload = " ".join(f"{value:f}" for value in values)
    return connection.send_text(f"#GETAI {index}\n{payload} \n#OK")
