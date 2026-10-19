"""Shell handlers with shared protocol formatting and localized device calls.

Parsing and response formatting stay here. Only the calls that require NI
hardware branch on the runtime's mock flag.
"""
from __future__ import annotations

import math

from nev.hardware.bootstrap import daqmx_device_inventory, get_runtime, read_ai, read_di
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
            # Output buffers are shared with their physical task records.
            values = list(runtime.daq[group][index].values)
    except (IndexError, KeyError):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0

    label = f"GET{group}"
    if group in ("DO", "DI"):
        payload = " ".join(str(int(value)) for value in values)
    else:
        payload = " ".join(f"{value:f}" for value in values)
    return connection.send_text(f"#{label} {index}\n{payload} \n#OK")


def get_ao(args: list[str], connection: ShellConnection) -> int:
    """Handle ``GET AO`` using the shared output task buffer."""
    return _get_daq("AO", args, connection)


def get_do(args: list[str], connection: ShellConnection) -> int:
    """Handle ``GET DO`` using the shared output task buffer."""
    return _get_daq("DO", args, connection)


def get_di(args: list[str], connection: ShellConnection) -> int:
    """Read or return one digital-input task."""
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
    tasks = device_commands.TASKS["DI"]
    if index < 0 or index >= len(tasks):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0
    try:
        values = list(tasks[index].values) if runtime.mock else read_di(index)
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")
    payload = " ".join(str(int(value)) for value in values)
    return connection.send_text(f"#GETDI {index}\n{payload} \n#OK")


def get_daqmx_info(args: list[str], connection: ShellConnection) -> int:
    """backend情報をC互換タスク形式とデバイス詳細行に整形して返す。"""
    try:
        from nev.handlers import device_commands
        runtime = get_runtime()
        info = {}
        for group, tasks in device_commands.TASKS.items():
            info[group] = [
                (task_index, task.device_name, task.model,
                 len(task.values) // task.length if group in ("AO", "AI") and task.length else len(task.values),
                 task.length, task.frequency, task.running)
                for task_index, task in enumerate(tasks)
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
                    if group == "AI":
                        entry["ai_channels"].extend(f"{task.device_name}/ai{i}" for i in range(len(task.values) // task.length))
                    if group == "AO":
                        width = len(task.values) // task.length if task.length else len(task.values)
                        entry["ao_channels"].extend(f"{task.device_name}/ao{i}" for i in range(width))
                    elif group == "DO":
                        entry["terminals"].extend(f"{task.device_name}/port0/line{i}" for i in range(len(task.values)))
            devices = list(devices_by_name.values())
        else:
            # This is the precise NI-only boundary: mock mode never imports nidaqmx.
            devices = daqmx_device_inventory()
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")
    if connection.send_text("#DAQMXINFO\n") < 0:
        return -17
    # Report the physical groups discovered by bootstrap.py.
    for group in ("AO", "AI", "DO", "DI"):
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
    """Read and format the indexed analog-input task."""
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
    # Both modes index AI independently from analog output tasks.
    task_count = len(device_commands.TASKS["AI"]) if runtime.mock else len(runtime.daq["AI"])
    if index < 0 or index >= task_count:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0
    try:
        if runtime.mock:
            task = device_commands.TASKS["AI"][index]
            values = list(runtime.ai_data.get(
                index, [math.sin(sample * 0.01) for sample in range(task.length)]
            ))
            runtime.ai_data[index] = values
        else:
            # The bootstrap owns AI tasks separately from output tasks.
            values = read_ai(index)
            device_commands.TASKS["AI"][index].running = runtime.daq["AI"][index].running
    except (IndexError, KeyError):
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return 0
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")
    payload = " ".join(f"{value:f}" for value in values)
    return connection.send_text(f"#GETAI {index}\n{payload} \n#OK")


def run_ai(args: list[str], connection: ShellConnection) -> int:
    """Start one independently discovered AI task."""
    runtime = get_runtime()
    try:
        index = _ai_index(args, connection, runtime)
        if index is None:
            return 0
        if runtime.mock:
            import nev.handlers.device_commands as commands
            task = commands.TASKS["AI"][index]
            task.running = True
            runtime.ai_data[index] = [math.sin(sample * 0.01) for sample in range(task.length)]
        else:
            from nev.hardware.bootstrap import run_ai as start_ai
            start_ai(index)
            from nev.handlers import device_commands
            device_commands.TASKS["AI"][index].running = True
        return connection.sh_term()
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")


def stop_ai(args: list[str], connection: ShellConnection) -> int:
    """Stop one AI task and retain its last acquired block."""
    runtime = get_runtime()
    try:
        index = _ai_index(args, connection, runtime)
        if index is None:
            return 0
        if not runtime.mock:
            from nev.hardware.bootstrap import stop_ai as halt_ai
            halt_ai(index)
            from nev.handlers import device_commands
            device_commands.TASKS["AI"][index].running = False
        else:
            import nev.handlers.device_commands as commands
            commands.TASKS["AI"][index].running = False
        return connection.sh_term()
    except Exception as exc:
        return connection.sh_error(f"!DAQMX_API_ERROR({exc})")


def _ai_index(args: list[str], connection: ShellConnection, runtime) -> int | None:
    """Validate an AI task index for mock or real hardware."""
    from nev.handlers import device_commands
    if not args:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_EMPTY, None)
        return None
    try:
        index = int(args[0], 10)
    except ValueError:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return None
    count = len(device_commands.TASKS["AI"]) if runtime.mock else len(runtime.daq["AI"])
    if not 0 <= index < count:
        connection.sh_error_code(NEVSHELL_ERR_INDEX_INVALID, args[0])
        return None
    return index
