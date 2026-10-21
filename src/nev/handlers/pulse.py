"""Shell commands for synchronized AO pulse output and AI waveform capture."""

from __future__ import annotations

import math

from nev.hardware.bootstrap import acquire_pulse
from nev.shell import datareq
from nev.shell.protocol import ShellConnection


PULSE = {
    "status": "UNCONFIGURED",
    "ao_task": 0,
    "ao_channel": 0,
    "sample_rate": 0.0,
    "duration": 0.0,
    "delay_ms": 0.0,
    "width_ms": 0.0,
    "amplitude_mv": 0.0,
    "time": [],
    "ao": [],
    "ai": [],
    "error": "",
}

_MAX_SAMPLES = 2_000_000


def _parse_values(line: str, count: int) -> list[float] | None:
    """Parse one newline-terminated row of finite numeric values."""
    try:
        values = [float(token) for token in line.split()]
    except ValueError:
        return None
    if len(values) != count or not all(math.isfinite(value) for value in values):
        return None
    return values


def set_pulse(args: list[str], connection: ShellConnection) -> int:
    """Select one AO channel, then receive the two pulse-parameter rows."""
    from nev.handlers import device_commands
    from nev.hardware.bootstrap import get_runtime

    if len(args) != 2:
        return connection.sh_error("!PULSE_PARAMETER_NOT_GIVEN")
    try:
        ao_task = int(args[0], 10)
        ao_channel = int(args[1], 10)
    except ValueError:
        return connection.sh_error("!INVALID_PULSE_CONFIGURATION")

    tasks = device_commands.TASKS["AO"]
    runtime = get_runtime()
    if not 0 <= ao_task < len(tasks) or ao_channel < 0:
        return connection.sh_error("!INVALID_PULSE_CONFIGURATION")
    # Mock mode permits the high channel number used by Pulse_Stimulus.py.
    # The protocol's channel number is a physical suffix (52 means ao52),
    # whereas the NI task stores full channel names such as PXI1Slot2/ao52.
    if not runtime.mock:
        channels = runtime.daq["AO"][ao_task].channels
        if not any(channel.rsplit("/", 1)[-1].lower() == f"ao{ao_channel}".lower() for channel in channels):
            return connection.sh_error("!INVALID_PULSE_CONFIGURATION")
    if datareq.data_req_recv.active:
        return connection.sh_error("!DATA_REQUEST_BUSY")

    PULSE.update(
        status="RECEIVING_RATE", ao_task=ao_task, ao_channel=ao_channel,
        sample_rate=0.0, duration=0.0, delay_ms=0.0, width_ms=0.0,
        amplitude_mv=0.0, time=[], ao=[], ai=[], error="",
    )
    if datareq.set_stream_req("r", _receive_pulse_rows) < 0:
        PULSE["status"] = "UNCONFIGURED"
        return connection.sh_error("!DATA_REQUEST_ERROR")
    return connection.sh_term()


def _receive_pulse_rows(buffer: str, connection: object) -> tuple[int, int]:
    """Consume rate/duration, then delay/width/amplitude rows from the stream."""
    consumed = 0
    while True:
        newline = buffer.find("\n", consumed)
        if newline < 0:
            return 1, consumed
        line = buffer[consumed:newline].strip("\r \t")
        consumed = newline + 1

        if PULSE["status"] == "RECEIVING_RATE":
            values = _parse_values(line, 2)
            if values is None:
                PULSE.update(status="FAILED", error="invalid sample-rate row")
                connection.sh_error("!INVALID_PULSE_RATE")
                return -1, consumed
            sample_rate, duration = values
            samples = round(sample_rate * duration)
            if sample_rate <= 0 or duration <= 0 or not 1 <= samples <= _MAX_SAMPLES:
                PULSE.update(status="FAILED", error="invalid acquisition length")
                connection.sh_error("!INVALID_PULSE_RATE")
                return -1, consumed
            PULSE.update(
                sample_rate=sample_rate, duration=duration,
                status="RECEIVING_PULSE",
            )
            if connection.sh_term() < 0:
                return -1, consumed
            continue

        if PULSE["status"] != "RECEIVING_PULSE":
            PULSE.update(status="FAILED", error="unexpected pulse stream state")
            connection.sh_error("!PULSE_STATE_ERROR")
            return -1, consumed

        values = _parse_values(line, 3)
        if values is None:
            PULSE.update(status="FAILED", error="invalid pulse row")
            connection.sh_error("!INVALID_PULSE_CONFIGURATION")
            return -1, consumed
        delay_ms, width_ms, amplitude_mv = values
        if delay_ms < 0 or width_ms <= 0 or abs(amplitude_mv) > 10000:
            PULSE.update(status="FAILED", error="invalid pulse timing")
            connection.sh_error("!INVALID_PULSE_CONFIGURATION")
            return -1, consumed
        if delay_ms + width_ms > PULSE["duration"] * 1000:
            PULSE.update(status="FAILED", error="pulse exceeds acquisition window")
            connection.sh_error("!PULSE_OUTSIDE_AI_WINDOW")
            return -1, consumed

        PULSE.update(
            delay_ms=delay_ms, width_ms=width_ms,
            amplitude_mv=amplitude_mv, status="RUNNING",
        )
        try:
            result = acquire_pulse(
                ao_task_index=PULSE["ao_task"],
                ao_channel_index=PULSE["ao_channel"],
                sample_rate=PULSE["sample_rate"],
                duration=PULSE["duration"],
                delay_ms=delay_ms,
                width_ms=width_ms,
                amplitude_mv=amplitude_mv,
            )
        except Exception as exc:
            PULSE.update(status="FAILED", error=str(exc))
            connection.sh_error(f"!DAQMX_API_ERROR({exc})")
            return -1, consumed

        PULSE.update(
            time=result["time"], ao=result["ao"], ai=result["ai"],
            status="COMPLETE", error="",
        )
        if connection.sh_term() < 0:
            return -1, consumed
        return 0, consumed


def get_pulse(args: list[str], connection: ShellConnection) -> int:
    """Return metadata and one synchronized AO/AI sample row per point."""
    if PULSE["status"] != "COMPLETE":
        return connection.sh_error("!PULSE_DATA_UNAVAILABLE")

    count = len(PULSE["ai"])
    header = (
        f"#GETPULSE {count}\n"
        f"{PULSE['sample_rate']:f} {PULSE['duration']:f} "
        f"{PULSE['delay_ms']:f} {PULSE['width_ms']:f} "
        f"{PULSE['amplitude_mv']:f}\n"
    )

    def send_all(text: str) -> int:
        """Send a complete response chunk even if the socket writes partially."""
        payload = text.encode("ascii", errors="replace")
        while payload:
            sent = connection.send_bytes(payload)
            if sent <= 0:
                return -17
            payload = payload[sent:]
        return 0

    if send_all(header) < 0:
        return -17
    rows = []
    for time_s, ao_value, ai_value in zip(PULSE["time"], PULSE["ao"], PULSE["ai"]):
        rows.append(f"{time_s:f} {ao_value:f} {ai_value:f}\n")
        if len(rows) >= 512:
            if send_all("".join(rows)) < 0:
                return -17
            rows.clear()
    if rows and send_all("".join(rows)) < 0:
        return -17
    return connection.sh_term()

