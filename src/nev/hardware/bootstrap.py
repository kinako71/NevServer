
"""Discover NI devices and own driver tasks used by shell command handlers."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 100000.0
SAMPLES_PER_CHANNEL = 1024
DAQ_GROUPS = ("AO", "AI", "DO", "DI")


@dataclass
class DeviceTask:
    """Pair a protocol task's values/settings with its optional NI task."""

    kind: str
    name: str
    model: str
    channels: list[str]
    values: list[float]
    length: int = SAMPLES_PER_CHANNEL
    frequency: float = SAMPLE_RATE
    native: Any = None
    running: bool = False
    device_id: int = 0


@dataclass
class ScopeDevice:
    """Keep NI-SCOPE identity alongside its live driver session."""

    name: str
    model: str
    channel_count: int
    slot: int
    chassis: int
    serial: str
    native: Any


@dataclass
class HardwareRuntime:
    """Own driver objects for one mock or real-hardware server run."""

    mock: bool
    daq: dict[str, list[DeviceTask]] = field(
        default_factory=lambda: {
            "AO": [],
            "AI": [],
            "DO": [],
            "DI": [],
        }
    )
    scopes: list[ScopeDevice] = field(default_factory=list)
    ai_tasks: dict[int, Any] = field(default_factory=dict)
    ai_data: dict[int, list[float]] = field(default_factory=dict)
    def close(self) -> None:
        """Stop and release all driver objects without double-closing tasks."""
        daq_task_ids = {
            id(item.native)
            for group in self.daq.values()
            for item in group
            if item.native is not None
        }

        for group in self.daq.values():
            for item in group:
                task = item.native
                if task is None:
                    continue

                try:
                    if item.running:
                        task.stop()
                except Exception:
                    log.exception("Could not stop DAQ task %s", item.name)

                try:
                    task.close()
                except Exception:
                    log.exception("Could not close DAQ task %s", item.name)

                item.native = None
                item.running = False

        # AI registry entries normally point to tasks already closed above.
        for task in self.ai_tasks.values():
            if id(task) in daq_task_ids:
                continue
            try:
                task.close()
            except Exception:
                log.exception("Could not close detached AI task")

        self.ai_tasks.clear()
        self.ai_data.clear()

        for item in self.scopes:
            try:
                item.native.close()
            except Exception:
                log.exception(
                    "Could not close niScope session %s", item.name
                )

        self.scopes.clear()


runtime: HardwareRuntime | None = None


def get_runtime() -> HardwareRuntime:
    """Return initialized runtime state."""
    if runtime is None:
        raise RuntimeError("NI hardware runtime is not initialized")
    return runtime


def close_hardware() -> None:
    """Release active runtime."""
    global runtime

    if runtime is not None:
        runtime.close()
        runtime = None


def _open_daq(state: HardwareRuntime) -> None:
    """Discover AO/AI/DO/DI channels and create NI-DAQmx tasks."""
    import nidaqmx
    from nidaqmx.constants import AcquisitionType, LineGrouping
    from nidaqmx.system import System

    for group in DAQ_GROUPS:
        state.daq.setdefault(group, [])

    devices = list(System.local().devices)

    for device_id, device in enumerate(devices):
        name = str(device.name)
        model = str(device.product_type)

        ao = [str(ch.name) for ch in device.ao_physical_chans]
        ai = [str(ch.name) for ch in device.ai_physical_chans]
        do = [str(line.name) for line in device.do_lines]
        di = [str(line.name) for line in device.di_lines]

        log.info(
            "DAQ device %s (%s): AO=%d, AI=%d, DO=%d, DI=%d",
            name, model, len(ao), len(ai), len(do), len(di),
        )

        # --------------------------------------------
        # Analog Output
        # --------------------------------------------
        if ao:
            max_rate = float(
                getattr(device, "ao_max_rate", 0.0) or 0.0
            )
            supports_clock = bool(
                getattr(device, "ao_samp_clk_supported", False)
            )
            dynamic = supports_clock and max_rate > 0
            rate = min(SAMPLE_RATE, max_rate) if dynamic else 0.0
            length = SAMPLES_PER_CHANNEL if dynamic else 1

            task = nidaqmx.Task(f"nev_AO_{name}")
            try:
                for channel in ao:
                    task.ao_channels.add_ao_voltage_chan(channel)

                if dynamic:
                    task.timing.cfg_samp_clk_timing(
                        rate=rate,
                        sample_mode=AcquisitionType.CONTINUOUS,
                        samps_per_chan=length,
                    )

                state.daq["AO"].append(
                    DeviceTask(
                        kind="AO",
                        name=name,
                        model=model,
                        channels=ao,
                        values=[0.0] * (len(ao) * length),
                        length=length,
                        frequency=rate,
                        native=task,
                        device_id=device_id,
                    )
                )
            except Exception:
                task.close()
                raise

        # --------------------------------------------
        # Analog Input
        # --------------------------------------------
        if ai:
            max_rate = float(
                getattr(device, "ai_max_single_chan_rate", 0.0) or 0.0
            )
            supports_clock = bool(
                getattr(device, "ai_samp_clk_supported", False)
            )
            dynamic = supports_clock and max_rate > 0
            rate = min(SAMPLE_RATE, max_rate) if dynamic else 0.0
            length = SAMPLES_PER_CHANNEL if dynamic else 1

            task = nidaqmx.Task(f"nev_AI_{name}")
            try:
                for channel in ai:
                    task.ai_channels.add_ai_voltage_chan(channel)

                if dynamic:
                    task.timing.cfg_samp_clk_timing(
                        rate=rate,
                        sample_mode=AcquisitionType.CONTINUOUS,
                        samps_per_chan=length,
                    )

                state.daq["AI"].append(
                    DeviceTask(
                        kind="AI",
                        name=name,
                        model=model,
                        channels=ai,
                        values=[0.0] * (len(ai) * length),
                        length=length,
                        frequency=rate,
                        native=task,
                        device_id=device_id,
                    )
                )
            except Exception:
                task.close()
                raise

        # --------------------------------------------
        # Digital Output
        # --------------------------------------------
        if do:
            task = nidaqmx.Task(f"nev_DO_{name}")
            try:
                for channel in do:
                    task.do_channels.add_do_chan(
                        channel,
                        line_grouping=LineGrouping.CHAN_PER_LINE,
                    )

                state.daq["DO"].append(
                    DeviceTask(
                        kind="DO",
                        name=name,
                        model=model,
                        channels=do,
                        values=[0.0] * len(do),
                        length=1,
                        frequency=0.0,
                        native=task,
                        device_id=device_id,
                    )
                )
            except Exception:
                task.close()
                raise

        # --------------------------------------------
        # Digital Input
        # --------------------------------------------
        if di:
            task = nidaqmx.Task(f"nev_DI_{name}")
            try:
                for channel in di:
                    task.di_channels.add_di_chan(
                        channel,
                        line_grouping=LineGrouping.CHAN_PER_LINE,
                    )

                state.daq["DI"].append(
                    DeviceTask(
                        kind="DI",
                        name=name,
                        model=model,
                        channels=di,
                        values=[0.0] * len(di),
                        length=1,
                        frequency=0.0,
                        native=task,
                        device_id=device_id,
                    )
                )
            except Exception:
                task.close()
                raise

    if not any(state.daq[group] for group in DAQ_GROUPS):
        raise RuntimeError(
            "No usable NI-DAQmx AO/AI/DO/DI channels found"
        )


def _open_scopes(state: HardwareRuntime) -> None:
    """Discover NI-SCOPE devices and open driver sessions."""
    import nimodinst
    import niscope

    # Copy device properties while the enumeration session is alive.
    with nimodinst.Session("niscope") as enumeration:
        devices = [
            {
                "name": str(d.device_name),
                "model": str(d.device_model),
                "serial": str(d.serial_number),
                "slot": int(d.slot_number),
                "chassis": int(d.chassis_number),
            }
            for d in enumeration
        ][:16]

    if not devices:
        log.warning("No NI-SCOPE devices found")
        return

    for device in devices:
        session = niscope.Session(resource_name=device["name"])
        try:
            state.scopes.append(
                ScopeDevice(
                    name=device["name"],
                    model=device["model"],
                    channel_count=int(session.channel_count),
                    slot=device["slot"],
                    chassis=device["chassis"],
                    serial=device["serial"],
                    native=session,
                )
            )
        except Exception:
            session.close()
            raise


def init_hardware(*, mock: bool) -> int:
    """Initialize mock state or discover/configure real NI hardware."""
    global runtime

    close_hardware()
    state = HardwareRuntime(mock=mock)

    if mock:
        log.info("[mock] Using deterministic simulated NI hardware")
        runtime = state
        _bind_shell_state(state)
        return 0

    try:
        _open_scopes(state)
        _open_daq(state)

        # Initialize outputs. Start only continuous AO tasks.
        for group in ("AO", "DO"):
            for index, item in enumerate(state.daq[group]):
                write_daq(group, index, state=state)

                if group == "AO" and item.frequency > 0:
                    item.native.start()
                    item.running = True

        runtime = state
        _bind_shell_state(state)

        log.info(
            "NI hardware ready: AO=%d, AI=%d, DO=%d, DI=%d, scopes=%d",
            len(state.daq["AO"]),
            len(state.daq["AI"]),
            len(state.daq["DO"]),
            len(state.daq["DI"]),
            len(state.scopes),
        )
        return 0

    except Exception:
        state.close()
        log.exception("NI hardware initialization failed")
        return -1


def _bind_shell_state(state: HardwareRuntime) -> None:
    """Expose discovered devices to shell command handlers."""
    from nev.handlers import device_commands as handlers

    if state.mock:
        return

    # Keep each shell-visible task list in the same order and shape as the
    # physical groups discovered by this bootstrap implementation.
    handlers.TASKS = {
        group: [
            handlers.Task(
                item.device_id, item.name, item.model, item.values,
                item.length, item.frequency, item.running,
            )
            for item in state.daq[group]
        ]
        for group in DAQ_GROUPS
    }

    handlers.SCOPE_DEVICES = [
        (
            d.name,
            d.model,
            d.channel_count,
            d.slot,
            d.chassis,
            d.serial,
        )
        for d in state.scopes
    ]

    handlers.UNIONS = (
        [handlers.ScopeUnion(devices=[0])]
        + [handlers.ScopeUnion() for _ in range(3)]
    )

    handlers.UNIONS[0].channels = {
        index: [(2.0, "DC") for _ in range(device.channel_count)]
        for index, device in enumerate(state.scopes[:1])
    }
def write_daq(
    group: str,
    index: int,
    *,
    state: HardwareRuntime | None = None,
) -> None:
    """Write AO waveforms or digital output values."""
    runtime_ = state or get_runtime()

    if group not in ("AO", "DO"):
        raise ValueError(
            f"Cannot write to {group!r}; only AO and DO are writable"
        )

    items = runtime_.daq.get(group, [])
    if not 0 <= index < len(items):
        raise IndexError(f"Invalid {group} task index: {index}")

    item = items[index]
    if item.native is None:
        raise RuntimeError(
            f"{group} task {item.name} is not initialized"
        )

    if group == "AO":
        expected = len(item.channels) * item.length
        if len(item.values) != expected:
            raise ValueError(
                f"AO values length mismatch: expected {expected}, "
                f"got {len(item.values)}"
            )

        if item.frequency > 0:
            # NI-DAQmx multi-channel analog data is channel-major.
            rows = [
                item.values[i * item.length:(i + 1) * item.length]
                for i in range(len(item.channels))
            ]
            item.native.write(rows, auto_start=False)
        else:
            values = [
                item.values[i * item.length]
                for i in range(len(item.channels))
            ]
            item.native.write(values, auto_start=True)

    else:  # DO
        if len(item.values) != len(item.channels):
            raise ValueError(
                f"DO values length mismatch: expected {len(item.channels)}, "
                f"got {len(item.values)}"
            )

        item.native.write(
            [bool(value) for value in item.values],
            auto_start=True,
        )


def reconfigure_daq(group: str, index: int) -> None:
    """Recreate a DAQ task after channels, length, or rate have changed."""
    runtime_ = get_runtime()

    if group not in DAQ_GROUPS:
        raise ValueError(f"Unsupported DAQ group: {group}")

    items = runtime_.daq.get(group, [])
    if not 0 <= index < len(items):
        raise IndexError(f"Invalid {group} task index: {index}")

    item = items[index]

    if group == "AI":
        runtime_.ai_tasks.pop(index, None)

    if item.native is not None:
        try:
            if item.running:
                item.native.stop()
        finally:
            item.native.close()
            item.native = None
            item.running = False

    import nidaqmx
    from nidaqmx.constants import AcquisitionType, LineGrouping

    task = nidaqmx.Task(f"nev_{group}_{item.name}")

    try:
        if group == "AO":
            for channel in item.channels:
                task.ao_channels.add_ao_voltage_chan(channel)

        elif group == "AI":
            for channel in item.channels:
                task.ai_channels.add_ai_voltage_chan(channel)

        elif group == "DO":
            for channel in item.channels:
                task.do_channels.add_do_chan(
                    channel,
                    line_grouping=LineGrouping.CHAN_PER_LINE,
                )

        elif group == "DI":
            for channel in item.channels:
                task.di_channels.add_di_chan(
                    channel,
                    line_grouping=LineGrouping.CHAN_PER_LINE,
                )

        if group in ("AO", "AI") and item.frequency > 0:
            task.timing.cfg_samp_clk_timing(
                rate=item.frequency,
                sample_mode=AcquisitionType.CONTINUOUS,
                samps_per_chan=item.length,
            )

        item.native = task

    except Exception:
        task.close()
        raise


def run_ai(index: int) -> None:
    """Start the selected hardware-timed AI task."""
    runtime_ = get_runtime()
    items = runtime_.daq["AI"]

    if not 0 <= index < len(items):
        raise IndexError(f"Invalid AI task index: {index}")

    item = items[index]

    if item.frequency <= 0:
        raise RuntimeError(
            f"AI task {item.name} has no sample clock configured"
        )

    if item.native is None:
        reconfigure_daq("AI", index)

    if not item.running:
        item.native.start()
        item.running = True

    runtime_.ai_tasks[index] = item.native


def read_ai(index: int) -> list[float]:
    """Read one AI block and return channel-major flattened samples."""
    runtime_ = get_runtime()
    items = runtime_.daq["AI"]

    if not 0 <= index < len(items):
        raise IndexError(f"Invalid AI task index: {index}")

    item = items[index]

    if item.native is None:
        reconfigure_daq("AI", index)

    if item.frequency > 0:
        if not item.running:
            run_ai(index)

        samples = item.native.read(
            number_of_samples_per_channel=item.length,
            timeout=max(5.0, item.length / item.frequency + 1.0),
        )
    else:
        samples = item.native.read(
            number_of_samples_per_channel=1,
            timeout=5.0,
        )

    if len(item.channels) == 1:
        result = [float(x) for x in samples]
    else:
        result = [
            float(x)
            for channel_samples in samples
            for x in channel_samples
        ]

    item.values[:] = result
    runtime_.ai_data[index] = list(result)
    return result


def acquire_pulse(
    *,
    ao_task_index: int,
    ao_channel_index: int,
    sample_rate: float,
    duration: float,
    delay_ms: float,
    width_ms: float,
    amplitude_mv: float,
) -> dict[str, list[float]]:
    """Generate one AO pulse and acquire the synchronized AI waveform.

    Mock mode creates predictable data. Real mode temporarily releases the
    selected AO/AI devices' long-lived tasks, runs finite triggered tasks, and
    restores their prior task configuration and running state afterwards.
    """
    import math

    runtime_ = get_runtime()
    if runtime_.mock:
        from nev.handlers import device_commands

        ao_tasks = device_commands.TASKS["AO"]
        ai_tasks = device_commands.TASKS["AI"]
    else:
        ao_tasks = runtime_.daq["AO"]
        ai_tasks = runtime_.daq["AI"]
    if not ao_tasks or not ai_tasks:
        raise RuntimeError("Pulse acquisition requires both AO and AI devices")
    if not 0 <= ao_task_index < len(ao_tasks):
        raise IndexError(f"Invalid AO task index: {ao_task_index}")
    if ao_channel_index < 0:
        raise IndexError(f"Invalid AO channel number: {ao_channel_index}")

    ao_item = ao_tasks[ao_task_index]
    ai_item = ai_tasks[0]
    ao_channel = f"ao{ao_channel_index}"
    if not runtime_.mock:
        matches = [
            channel for channel in ao_item.channels
            if channel.rsplit("/", 1)[-1].lower() == ao_channel.lower()
        ]
        if not matches:
            raise ValueError(f"AO channel {ao_channel} is not on {ao_item.name}")
        ao_channel = matches[0]
        if not ai_item.channels:
            raise RuntimeError("AI task 0 has no configured channels")
        ai_channel = ai_item.channels[0]
        ao_value_index = ao_item.channels.index(ao_channel)
    else:
        # Mock task values are channel-major too. If the fixture does not
        # include this high physical channel number, use its default 0 V value.
        ao_value_index = ao_channel_index

    count = round(sample_rate * duration)
    if count < 1:
        raise ValueError("Acquisition must contain at least one sample")
    delay_samples = round(delay_ms * sample_rate / 1000.0)
    width_samples = max(1, round(width_ms * sample_rate / 1000.0))
    pulse_voltage = amplitude_mv / 1000.0
    # Preserve the selected channel's configured starting value before and
    # after the pulse instead of forcing the Pulse_Stimulus.py 0.28 V baseline.
    value_offset = ao_value_index * ao_item.length
    baseline = (
        float(ao_item.values[value_offset])
        if value_offset < len(ao_item.values)
        else 0.0
    )
    ao_values = [baseline] * count
    pulse_end = min(count, delay_samples + width_samples)
    for sample in range(min(count, delay_samples), pulse_end):
        ao_values[sample] = pulse_voltage
    times = [sample / sample_rate for sample in range(count)]

    if runtime_.mock:
        # Synthetic response: baseline noise plus a small pulse-shaped signal.
        ai_values = [
            0.01 * math.sin(2.0 * math.pi * 50.0 * time_s)
            + (0.05 if delay_samples <= i < pulse_end else 0.0)
            for i, time_s in enumerate(times)
        ]
        return {"time": times, "ao": ao_values, "ai": ai_values}

    import nidaqmx
    from nidaqmx.constants import AcquisitionType, Edge, Signal

    # The normal runtime keeps full-device tasks open. Release them while the
    # one-channel finite tasks claim the same physical channels.
    was_ao_running = ao_item.running
    was_ai_running = ai_item.running
    runtime_.ai_tasks.pop(0, None)
    for item in (ao_item, ai_item):
        if item.native is not None:
            try:
                if item.running:
                    item.native.stop()
            finally:
                item.native.close()
                item.native = None
                item.running = False

    pulse_ao_task = nidaqmx.Task()
    pulse_ai_task = nidaqmx.Task()
    try:
        trigger = f"/{ao_item.name}/PXI_Trig0"
        pulse_ai_task.ai_channels.add_ai_voltage_chan(
            ai_channel, min_val=-10.0, max_val=10.0
        )
        pulse_ai_task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=count,
        )
        pulse_ai_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source=trigger,
            trigger_edge=Edge.RISING,
        )

        pulse_ao_task.ao_channels.add_ao_voltage_chan(
            ao_channel, min_val=-10.0, max_val=10.0
        )
        pulse_ao_task.timing.cfg_samp_clk_timing(
            rate=sample_rate,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=count,
        )
        pulse_ao_task.export_signals.export_signal(
            signal_id=Signal.START_TRIGGER,
            output_terminal=trigger,
        )
        # Explicitly allocate the finite AO buffer before writing. Some
        # DAQmx/device combinations leave it at zero after timing setup alone.
        pulse_ao_task.out_stream.output_buf_size = count
        pulse_ao_task.write(ao_values, auto_start=False)

        # Arm AI first so it is waiting when AO exports its start trigger.
        pulse_ai_task.start()
        pulse_ao_task.start()
        ai_values = pulse_ai_task.read(
            number_of_samples_per_channel=count,
            timeout=max(30.0, duration + 5.0),
        )
        pulse_ao_task.wait_until_done(timeout=max(10.0, duration + 5.0))
        return {
            "time": times,
            "ao": ao_values,
            "ai": [float(value) for value in ai_values],
        }
    finally:
        # Release temporary tasks before rebuilding the runtime-owned tasks.
        pulse_ai_task.close()
        pulse_ao_task.close()
        reconfigure_daq("AO", ao_task_index)
        reconfigure_daq("AI", 0)
        if was_ao_running:
            write_daq("AO", ao_task_index)
            ao_item.native.start()
            ao_item.running = True
        if was_ai_running:
            ai_item.native.start()
            ai_item.running = True
            runtime_.ai_tasks[0] = ai_item.native


def stop_ai(index: int) -> None:
    """Stop AI acquisition and cache a final block when available."""
    runtime_ = get_runtime()
    items = runtime_.daq["AI"]

    if not 0 <= index < len(items):
        raise IndexError(f"Invalid AI task index: {index}")

    item = items[index]
    task = runtime_.ai_tasks.pop(index, None)

    if task is None:
        return

    try:
        if item.frequency > 0 and item.running:
            samples = task.read(
                number_of_samples_per_channel=item.length,
                timeout=max(1.0, item.length / item.frequency + 1.0),
            )

            if len(item.channels) == 1:
                result = [float(x) for x in samples]
            else:
                result = [
                    float(x)
                    for channel_samples in samples
                    for x in channel_samples
                ]

            item.values[:] = result
            runtime_.ai_data[index] = list(result)

    finally:
        try:
            if item.running:
                task.stop()
        finally:
            item.running = False


def read_di(index: int) -> list[float]:
    """Read the current state of each digital input line."""
    runtime_ = get_runtime()
    items = runtime_.daq["DI"]

    if not 0 <= index < len(items):
        raise IndexError(f"Invalid DI task index: {index}")

    item = items[index]

    if item.native is None:
        reconfigure_daq("DI", index)

    result = item.native.read()

    if not isinstance(result, (list, tuple)):
        result = [result]

    values = [float(bool(value)) for value in result]
    item.values[:] = values
    return values


def configure_scope(union: Any) -> None:
    """Apply vertical ranges, sampling settings, and trigger to a scope union."""
    runtime_ = get_runtime()
    import niscope
    import nitclk

    for device_index in union.devices:
        device = runtime_.scopes[device_index]
        session = device.native
        defaults = [(2.0, "DC") for _ in range(device.channel_count)]

        for channel_index, (voltage_range, coupling) in enumerate(
            union.channels.get(device_index, defaults)
        ):
            session.channels[str(channel_index)].configure_vertical(
                range=voltage_range,
                coupling=getattr(niscope.VerticalCoupling, coupling),
            )

        session.configure_horizontal_timing(
            min_sample_rate=union.frequency,
            min_num_pts=union.length,
            ref_position=float(union.trigger[0]),
            num_records=1,
            enforce_realtime=True,
        )

    trigger = union.trigger
    sessions = [runtime_.scopes[i].native for i in union.devices]
    if sessions:
        master_index = int(trigger[3])
        if not 0 <= master_index < len(sessions):
            raise ValueError("invalid NI-SCOPE trigger device index")
        session = sessions[master_index]
        trigger_type = str(trigger[1]).upper()
        trigger_type = {
            "0": "EDGE", "1": "HYSTERESIS", "2": "DIGITAL",
            "3": "WINDOW", "4": "IMMEDIATE", "5": "SOFTWARE",
        }.get(trigger_type, trigger_type)
        trigger_source = str(trigger[2])
        slope = getattr(niscope.TriggerSlope, str(trigger[5]).upper())
        coupling = getattr(niscope.TriggerCoupling, str(trigger[6]).upper())
        holdoff, delay = float(trigger[7]), float(trigger[8])

        if trigger_type == "IMMEDIATE":
            session.configure_trigger_immediate()
        elif trigger_type == "EDGE":
            session.configure_trigger_edge(
                trigger_source=trigger_source, level=float(trigger[4]),
                trigger_coupling=coupling, slope=slope,
                holdoff=holdoff, delay=delay,
            )
        elif trigger_type == "HYSTERESIS":
            session.configure_trigger_hysteresis(
                trigger_source=trigger_source, level=float(trigger[4]),
                hysteresis=0.0, trigger_coupling=coupling, slope=slope,
                holdoff=holdoff, delay=delay,
            )
        elif trigger_type == "DIGITAL":
            session.configure_trigger_digital(
                trigger_source=trigger_source, slope=slope,
                holdoff=holdoff, delay=delay,
            )
        elif trigger_type == "WINDOW":
            session.configure_trigger_window(
                trigger_source=trigger_source, low_level=0.0,
                high_level=float(trigger[4]),
                window_mode=niscope.TriggerWindowMode.ENTERING,
                trigger_coupling=coupling, holdoff=holdoff, delay=delay,
            )
        elif trigger_type == "SOFTWARE":
            session.configure_trigger_software(holdoff=holdoff, delay=delay)
        else:
            raise ValueError(f"unknown NI-SCOPE trigger type: {trigger[1]}")

    if len(sessions) > 1:
        nitclk.configure_for_homogeneous_triggers(sessions)
        # Match niTClk_Synchronize(..., minTClkPeriod=0.0) in the C backend.
        nitclk.synchronize(sessions, min_tclk_period=0.0)


def acquire_scope(union: Any, sample_offset: int = 0) -> list[list[list[float]]]:
    """Acquire waveforms using the C implementation's pretrigger offset."""
    runtime_ = get_runtime()
    import niscope
    import nitclk

    sessions = [runtime_.scopes[i].native for i in union.devices]
    if not sessions:
        raise RuntimeError("niScope union has no configured devices")

    if len(sessions) > 1:
        nitclk.initiate(sessions)

    results = []

    for device_index, session in zip(union.devices, sessions):
        device = runtime_.scopes[device_index]
        defaults = [(2.0, "DC") for _ in range(device.channel_count)]
        settings = union.channels.get(device_index, defaults)
        channel_ids = tuple(str(i) for i in range(len(settings)))

        context = (
            session.initiate()
            if len(sessions) == 1
            else nullcontext()
        )

        with context:
            waveforms = session.channels[channel_ids].fetch(
                num_samples=union.length,
                relative_to=niscope.FetchRelativeTo.PRETRIGGER,
                offset=sample_offset,
                timeout=max(5.0, union.length / union.frequency + 1.0),
            )

        results.append([
            [
                float(value)
                for value in next(
                    (
                        waveform.samples
                        for waveform in waveforms
                        if waveform.channel == channel
                    ),
                    [],
                )
            ]
            for channel in channel_ids
        ])

    return results


def abort_scope(union: Any) -> None:
    """Abort acquisition on each scope in a configured union."""
    state = get_runtime()

    for index in union.devices:
        state.scopes[index].native.abort()


def daqmx_device_inventory() -> list[dict[str, Any]]:
    """Read physical-device properties directly from NI-DAQmx."""
    from nidaqmx.system import System

    def channel_names(collection: Any) -> tuple[str, ...]:
        return tuple(str(name) for name in collection.channel_names)

    devices: list[dict[str, Any]] = []

    for device in System.local().devices:
        try:
            ai_channels = channel_names(device.ai_physical_chans)
        except Exception:
            ai_channels = ()

        try:
            ao_channels = channel_names(device.ao_physical_chans)
        except Exception:
            ao_channels = ()

        try:
            do_lines = tuple(str(x.name) for x in device.do_lines)
        except Exception:
            do_lines = ()

        try:
            di_lines = tuple(str(x.name) for x in device.di_lines)
        except Exception:
            di_lines = ()

        try:
            terminals = tuple(str(x) for x in device.terminals)
            terminals_error = None
        except Exception as exc:
            terminals = ()
            terminals_error = str(exc)

        devices.append({
            "name": str(device.name),
            "product_type": str(device.product_type),
            "product_category": str(device.product_category),
            "serial_number": str(
                getattr(device, "dev_serial_num", "")
            ),
            "ai_channels": ai_channels,
            "ao_channels": ao_channels,
            "do_lines": do_lines,
            "di_lines": di_lines,
            "terminals": terminals,
            "terminals_error": terminals_error,
        })

    return devices


def niscope_device_inventory() -> list[tuple[str, str, int, int, int, str]]:
    """Enumerate NI-SCOPE identity and channel information."""
    import nimodinst

    runtime_ = get_runtime()
    channel_counts = {
        device.name: device.channel_count
        for device in runtime_.scopes
    }

    with nimodinst.Session("niscope") as enumeration:
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
