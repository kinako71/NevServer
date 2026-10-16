"""Discover NI devices and own driver tasks used by shell command handlers."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class DeviceTask:
    """Pair a protocol task's values/settings with its optional NI task."""
    kind: str
    name: str
    model: str
    channels: list[str]
    values: list[float]
    length: int = 1024
    frequency: float = 100000.0
    native: Any = None
    running: bool = False
    device_id: int = 0


@dataclass
class ScopeDevice:
    """Keep enumerated scope identity alongside its live driver session."""
    name: str
    model: str
    channel_count: int
    slot: int
    chassis: int
    serial: str
    native: Any


@dataclass
class HardwareRuntime:
    """Own every driver object for one mock or real-hardware server run."""
    mock: bool
    daq: dict[str, list[DeviceTask]] = field(default_factory=lambda: {"SAO": [], "DAO": [], "SDO": []})
    scopes: list[ScopeDevice] = field(default_factory=list)
    ai_tasks: dict[int, Any] = field(default_factory=dict)
    ai_data: dict[int, list[float]] = field(default_factory=dict)

    def close(self) -> None:
        """Stop and release open tasks, tolerating individual close failures."""
        for group in self.daq.values():
            for item in group:
                if item.native is not None:
                    try:
                        if item.running:
                            item.native.stop()
                        item.native.close()
                    except Exception:
                        log.exception("Could not close DAQmx task %s", item.name)
        for item in self.scopes:
            try:
                item.native.close()
            except Exception:
                log.exception("Could not close niScope session %s", item.name)
        for item in self.ai_tasks.values():
            try:
                item.close()
            except Exception:
                log.exception("Could not close synchronized analog input task")
        self.ai_tasks.clear()


runtime: HardwareRuntime | None = None


def get_runtime() -> HardwareRuntime:
    """Return initialized runtime state or explain why hardware is unavailable."""
    if runtime is None:
        raise RuntimeError("NI hardware runtime is not initialized")
    return runtime


def close_hardware() -> None:
    """Release the active runtime and clear the module-level reference."""
    global runtime
    if runtime is not None:
        runtime.close()
        runtime = None


def _open_daq(state: HardwareRuntime) -> None:
    """Enumerate supported NI DAQ devices and create output tasks for them."""
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, LineGrouping
    from nidaqmx.system import System

    supported = {"PXI-6704": ("S", 16, 8), "PXI-6733": ("D", 8, 4), "PXI-6713": ("D", 8, 4)}
    for device_id, device in enumerate(System.local().devices):
        model = str(device.product_type)
        spec = supported.get(model)
        if spec is None:
            continue
        mode, max_ao, max_do = spec
        name = str(device.name)
        ao = [str(ch.name) for ch in device.ao_physical_chans][:max_ao]
        do = [str(ch.name) for ch in device.do_lines][:max_do]
        if ao:
            group = "SAO" if mode == "S" else "DAO"
            task = nidaqmx.Task(f"nev_{group}_{name}")
            try:
                for channel in ao:
                    task.ao_channels.add_ao_voltage_chan(channel)
                if group == "DAO":
                    task.timing.cfg_samp_clk_timing(100000.0, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=1024)
                is_dynamic = group == "DAO"
                state.daq[group].append(DeviceTask(
                    group, name, model, ao,
                    [0.0] * len(ao) * (1024 if is_dynamic else 1),
                    length=1024 if is_dynamic else 1,
                    frequency=100000.0 if is_dynamic else 0.0,
                    native=task, device_id=device_id,
                ))
            except Exception:
                task.close()
                raise
        if do:
            task = nidaqmx.Task(f"nev_SDO_{name}")
            try:
                for channel in do:
                    task.do_channels.add_do_chan(channel, line_grouping=LineGrouping.CHAN_PER_LINE)
                state.daq["SDO"].append(DeviceTask(
                    "SDO", name, model, do, [0.0] * len(do),
                    length=1, frequency=0.0, native=task, device_id=device_id,
                ))
            except Exception:
                task.close()
                raise
    if not any(state.daq.values()):
        raise RuntimeError("No supported NI-DAQmx outputs found (PXI-6704/6733/6713)")


def _open_scopes(state: HardwareRuntime) -> None:
    """Discover NI-SCOPE devices with NI-ModInst and open driver sessions."""
    import nimodinst
    import niscope

    with nimodinst.Session("niscope") as enumeration:
        # NI-ModInst exposes the device name/model/serial/slot fields used by
        # the standalone device-information example.
        devices = list(enumeration)[:16]
    if not devices:
        raise RuntimeError("No NI-SCOPE device found")
    for device in devices:
        # Keep the session alive; PSA applies the selected union settings before
        # each acquisition rather than assuming every device has two channels.
        session = niscope.Session(resource_name=device.device_name)
        try:
            state.scopes.append(ScopeDevice(
                name=str(device.device_name),
                model=str(device.device_model),
                channel_count=int(session.channel_count),
                slot=int(device.slot_number),
                chassis=int(device.chassis_number),
                serial=str(device.serial_number),
                native=session,
            ))
        except Exception:
            session.close()
            raise


def init_hardware(*, mock: bool) -> int:
    """Initialize mock state or discover/configure real NI DAQ and scope."""
    global runtime
    if runtime is not None:
        runtime.close()
    state = HardwareRuntime(mock=mock)
    if mock:
        # mock時はNI SDKをimportせず、shellハンドラ内のインメモリ値を使う。
        log.info("[mock] Using deterministic simulated NI hardware")
        runtime = state
        _bind_shell_state(state)
        return 0
    try:
        # 実機モードではここでNIタスクを直接列挙・初期化する。
        _open_scopes(state)
        _open_daq(state)
        for group, tasks in state.daq.items():
            for index, item in enumerate(tasks):
                write_daq(group, index, state=state)
                if group == "DAO":
                    item.native.start()
                    item.running = True
        runtime = state
        _bind_shell_state(state)
        log.info(
            "NI hardware ready: %d output task(s), %d niScope device(s)",
            sum(map(len, state.daq.values())), len(state.scopes),
        )
        return 0
    except Exception as exc:
        state.close()
        log.error("! NI hardware initialization failed: %s", exc)
        return -1


def _bind_shell_state(state: HardwareRuntime) -> None:
    """Expose discovered devices in the handler state used by shell commands."""
    from nev.handlers import device_commands as handlers
    if state.mock:
        return
    handlers.TASKS = {
        group: [handlers.Task(d.device_id, d.name, d.model, d.values, d.length, d.frequency, d.running)
                for i, d in enumerate(items)]
        for group, items in state.daq.items()
    }
    handlers.SCOPE_DEVICES = [
        (d.name, d.model, d.channel_count, d.slot, d.chassis, d.serial)
        for d in state.scopes
    ]
    handlers.UNIONS = [handlers.ScopeUnion(devices=[0])] + [handlers.ScopeUnion() for _ in range(3)]
    handlers.UNIONS[0].channels = {
        index: [(2.0, "DC") for _ in range(device.channel_count)]
        for index, device in enumerate(state.scopes[:1])
    }


def write_daq(group: str, index: int, *, state: HardwareRuntime | None = None) -> None:
    """Write stored values using static or per-channel waveform layout."""
    runtime_ = state or get_runtime()
    item = runtime_.daq[group][index]
    if item.native is None:
        return
    if group == "DAO":
        width = len(item.channels)
        rows = [item.values[i * item.length:(i + 1) * item.length] for i in range(width)]
        item.native.write(rows, auto_start=False)
    elif group == "SDO":
        item.native.write([[bool(x)] for x in item.values], auto_start=True)
    else:
        item.native.write([[x] for x in item.values], auto_start=True)


def run_daq(index: int) -> None:
    """Start DAO and AI together using the AI sample clock/start trigger."""
    runtime_ = get_runtime()
    item = runtime_.daq["DAO"][index]
    import nidaqmx
    from nidaqmx.constants import AcquisitionType

    # The regular DAO task created at initialization owns the same AO channels.
    # Release it before creating the synchronized AI/AO pair.
    if item.native is not None:
        try:
            if item.running:
                item.native.stop()
        finally:
            item.native.close()
        item.native = None
    previous_ai = runtime_.ai_tasks.pop(index, None)
    if previous_ai is not None:
        try:
            previous_ai.stop()
        finally:
            previous_ai.close()
    runtime_.ai_data.pop(index, None)

    ai_task = nidaqmx.Task(f"nev_AI_for_DAO_{index}")
    ao_task = None
    try:
        ao_task = nidaqmx.Task(f"nev_DAO_sync_{index}")
        ai_task.ai_channels.add_ai_voltage_chan("PXI1Slot4/ai0")
        ai_task.timing.cfg_samp_clk_timing(
            rate=item.frequency,
            sample_mode=AcquisitionType.CONTINUOUS,
            samps_per_chan=item.length,
        )

        for channel in item.channels:
            ao_task.ao_channels.add_ao_voltage_chan(channel)
        ao_task.timing.cfg_samp_clk_timing(
            rate=item.frequency,
            source="/PXI1Slot4/te0/SampleClock",
            sample_mode=AcquisitionType.CONTINUOUS,
            samps_per_chan=item.length,
        )
        ao_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            "/PXI1Slot4/te0/StartTrigger"
        )

        width = len(item.channels)
        ao_data = [
            item.values[channel * item.length:(channel + 1) * item.length]
            for channel in range(width)
        ]
        ao_task.write(ao_data, auto_start=False)

        # Start output first: it waits for the AI-exported clock/trigger route.
        ao_task.start()
        ai_task.start()
        item.native = ao_task
        item.running = True
        runtime_.ai_tasks[index] = ai_task
    except Exception:
        ai_task.close()
        if ao_task is not None:
            ao_task.close()
        reconfigure_daq("DAO", index)
        raise


def stop_daq(index: int) -> None:
    """Stop synchronized AI/AO tasks, cache the last AI block, and restore AO."""
    runtime_ = get_runtime()
    item = runtime_.daq["DAO"][index]
    ai_task = runtime_.ai_tasks.pop(index, None)
    try:
        if ai_task is not None:
            # Preserve the most recent block so GET AI also works after stop.
            runtime_.ai_data[index] = [
                float(value) for value in ai_task.read(
                    number_of_samples_per_channel=item.length,
                    timeout=max(1.0, item.length / item.frequency + 1.0),
                )
            ]
            ai_task.stop()
    finally:
        if item.native is not None:
            try:
                item.native.stop()
            except Exception:
                pass
            item.native.close()
            item.native = None
        if ai_task is not None:
            ai_task.close()
        item.running = False
        # Recreate the ordinary output task so SET DAO can be used again.
        reconfigure_daq("DAO", index)


def read_ai(index: int) -> list[float]:
    """Read one AI block, or return the most recently cached block."""
    runtime_ = get_runtime()
    task = runtime_.ai_tasks.get(index)
    if task is None:
        if index in runtime_.ai_data:
            return list(runtime_.ai_data[index])
        raise RuntimeError("AI acquisition has not been started for this DAO task")
    samples = task.read(
        number_of_samples_per_channel=runtime_.daq["DAO"][index].length,
        timeout=10.0,
    )
    # One configured AI channel: nidaqmx returns the channel's sample list.
    runtime_.ai_data[index] = [float(sample) for sample in samples]
    return list(runtime_.ai_data[index])


def reconfigure_daq(group: str, index: int) -> None:
    """Recreate a NI task after its channels, length, or rate have changed."""
    runtime_ = get_runtime()
    item = runtime_.daq[group][index]
    if item.native is not None:
        item.native.close()
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, LineGrouping
    task = nidaqmx.Task(f"nev_{group}_{item.name}")
    if group == "SDO":
        for channel in item.channels:
            task.do_channels.add_do_chan(channel, line_grouping=LineGrouping.CHAN_PER_LINE)
    else:
        for channel in item.channels:
            task.ao_channels.add_ao_voltage_chan(channel)
        if group == "DAO":
            task.timing.cfg_samp_clk_timing(item.frequency, sample_mode=AcquisitionType.CONTINUOUS, samps_per_chan=item.length)
    item.native = task
    item.running = False


def configure_scope(union: Any) -> None:
    """Apply vertical ranges, sampling settings, and trigger to a scope union."""
    runtime_ = get_runtime()
    import niscope
    for device_index in union.devices:
        session = runtime_.scopes[device_index].native
        defaults = [(2.0, "DC") for _ in range(runtime_.scopes[device_index].channel_count)]
        for channel_index, (voltage_range, coupling) in enumerate(union.channels.get(device_index, defaults)):
            session.channels[str(channel_index)].configure_vertical(range=voltage_range, coupling=getattr(niscope.VerticalCoupling, coupling))
        session.configure_horizontal_timing(min_sample_rate=union.frequency, min_num_pts=union.length, ref_position=float(union.trigger[0]), num_records=1, enforce_realtime=True)

    # Apply the selected trigger once device parameters are configured.
    import niscope
    trigger = union.trigger
    sessions = [runtime_.scopes[i].native for i in union.devices]
    if str(trigger[1]).upper() == "IMMEDIATE":
        for session in sessions:
            session.configure_trigger_immediate()
    elif sessions:
        master_index = int(trigger[3])
        sessions[master_index].configure_trigger_edge(trigger_source=str(trigger[2]), level=float(trigger[4]), slope=getattr(niscope.TriggerSlope, str(trigger[5]).upper()), coupling=getattr(niscope.TriggerCoupling, str(trigger[6]).upper()))


def acquire_scope(union: Any) -> list[list[list[float]]]:
    """Initiate synchronized scope sessions and return samples by device/channel."""
    runtime_ = get_runtime()
    import niscope
    import nitclk
    sessions = [runtime_.scopes[i].native for i in union.devices]
    if not sessions:
        raise RuntimeError("niScope union has no configured devices")
    trigger = union.trigger
    for session in sessions:
        if str(trigger[1]).upper() == "IMMEDIATE":
            session.configure_trigger_immediate()
        else:
            session.configure_trigger_edge(trigger_source=str(trigger[2]), level=float(trigger[4]), slope=getattr(niscope.TriggerSlope, str(trigger[5]).upper()), coupling=getattr(niscope.TriggerCoupling, str(trigger[6]).upper()))
    if len(sessions) > 1:
        nitclk.configure_for_homogeneous_triggers(sessions)
        nitclk.synchronize(sessions)
    results = []
    if len(sessions) > 1:
        # TClk starts the synchronized set; fetch each device's configured
        # channels and requested record length after the common initiation.
        nitclk.initiate(sessions)
    for device_index, session in zip(union.devices, sessions):
        defaults = [(2.0, "DC") for _ in range(runtime_.scopes[device_index].channel_count)]
        channel_settings = union.channels.get(device_index, defaults)
        channel_ids = tuple(str(index) for index in range(len(channel_settings)))
        with session.initiate() if len(sessions) == 1 else nullcontext():
            waveforms = session.channels[channel_ids].fetch(
                num_samples=union.length,
                timeout=max(5.0, union.length / union.frequency + 1.0),
            )
        results.append([
            [float(value) for value in next(
                (waveform.samples for waveform in waveforms if waveform.channel == channel),
                [],
            )]
            for channel in channel_ids
        ])
    return results


def abort_scope(union: Any) -> None:
    """Abort acquisition on each device in a configured scope union."""
    state = get_runtime()
    for index in union.devices:
        state.scopes[index].native.abort()


def daqmx_device_inventory() -> list[dict[str, Any]]:
    """Read detailed physical-device properties directly from NI-DAQmx."""
    from nidaqmx.system import System

    def channel_names(collection: Any) -> tuple[str, ...]:
        """Normalize an NI channel collection to displayable names."""
        return tuple(str(name) for name in collection.channel_names)

    devices: list[dict[str, Any]] = []
    for device in System.local().devices:
        # Devices without AI/AO capabilities still appear in the inventory.
        try:
            ai_channels = channel_names(device.ai_physical_chans)
        except Exception:
            ai_channels = ()
        try:
            ao_channels = channel_names(device.ao_physical_chans)
        except Exception:
            ao_channels = ()
        try:
            terminals = tuple(str(terminal) for terminal in device.terminals)
            terminals_error = None
        except Exception as exc:
            terminals = ()
            terminals_error = str(exc)
        devices.append({
            "name": str(device.name),
            "product_type": str(device.product_type),
            "product_category": str(device.product_category),
            "serial_number": str(getattr(device, "dev_serial_num", "")),
            "ai_channels": ai_channels,
            "ao_channels": ao_channels,
            "terminals": terminals,
            "terminals_error": terminals_error,
        })
    return devices


def niscope_device_inventory() -> list[tuple[str, str, int, int, int, str]]:
    """Enumerate NI-SCOPE identity fields with NI-ModInst at command time."""
    import nimodinst

    runtime_ = get_runtime()
    channel_counts = {device.name: device.channel_count for device in runtime_.scopes}
    with nimodinst.Session("niscope") as enumeration:
        # These are the properties exposed by the NI-ModInst example: device
        # name, model, serial, slot, and chassis. Channel count comes from the
        # already-open NI-SCOPE session for that same resource.
        return [
            (
                str(device.device_name),
                str(device.device_model),
                channel_counts.get(str(device.device_name), 0),
                int(device.slot_number),
                int(device.chassis_number),
                str(device.serial_number),
            )
            for device in enumeration
        ]
