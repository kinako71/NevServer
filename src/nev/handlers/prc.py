"""Command handlers for the phase-response curve experiment in PRC.py."""

from __future__ import annotations

import math
import re
import threading

from nev.shell import datareq
from nev.shell.protocol import ShellConnection


PRC = {
    "status": "UNCONFIGURED",
    "ao_task": 0,
    "ao_channel": 0,
    "duration_ms": 0.0,
    "phase_step_ms": 0.0,
    "repeats": 0,
    "threshold_mv": 100.0,
    "pulse_width_ms": 0.5,
    "pulse_amplitude_mv": 100.0,
    "phases_ms": [],
    "current_parameter": 0.0,
    "results": [],
    "error": "",
}
_MAX_PHASES = 1000
_finished = threading.Event()


def set_prc(args: list[str], connection: ShellConnection) -> int:
    """Select an AO channel; receive scan duration and phase step next."""
    from nev.handlers import device_commands
    from nev.hardware.bootstrap import get_runtime

    if PRC["status"] == "RUNNING":
        return connection.sh_error("!PRC_BUSY")
    if len(args) != 2:
        return connection.sh_error("!PRC_PARAMETER_NOT_GIVEN")
    try:
        ao_task, ao_channel = (int(value, 10) for value in args)
    except ValueError:
        return connection.sh_error("!INVALID_PRC_CONFIGURATION")

    runtime = get_runtime()
    if not 0 <= ao_task < len(device_commands.TASKS["AO"]) or ao_channel < 0:
        return connection.sh_error("!INVALID_PRC_CONFIGURATION")
    if not runtime.mock:
        channels = runtime.daq["AO"][ao_task].channels
        if not any(
            channel.rsplit("/", 1)[-1].lower() == f"ao{ao_channel}".lower()
            for channel in channels
        ):
            return connection.sh_error("!INVALID_PRC_CONFIGURATION")
    if datareq.data_req_recv.active:
        return connection.sh_error("!DATA_REQUEST_BUSY")

    PRC.update(
        status="RECEIVING_TIMING", ao_task=ao_task, ao_channel=ao_channel,
        duration_ms=0.0, phase_step_ms=0.0, repeats=0,
        threshold_mv=0.0, pulse_width_ms=0.0, pulse_amplitude_mv=0.0,
        phases_ms=[], results=[], error="",
        current_parameter=0.0,
    )
    _finished.clear()
    if datareq.set_stream_req("r", _receive_scan_settings) < 0:
        PRC["status"] = "UNCONFIGURED"
        return connection.sh_error("!DATA_REQUEST_ERROR")
    return connection.sh_term()


def _receive_scan_settings(buffer: str, connection: object) -> tuple[int, int]:
    """Read timing/repeat settings, then threshold/width/amplitude settings."""
    newline = buffer.find("\n")
    if newline < 0:
        return 1, 0
    line = buffer[:newline].strip("\r \t")
    try:
        values = [float(value) for value in line.split()]
    except ValueError:
        values = []
    consumed = newline + 1
    if len(values) != 3 or not all(math.isfinite(value) for value in values):
        PRC.update(status="FAILED", error="invalid scan settings")
        connection.sh_error("!INVALID_PRC_CONFIGURATION")
        return -1, consumed

    if PRC["status"] == "RECEIVING_TIMING":
        duration_ms, phase_step_ms, repeats_value = values
        repeats = int(repeats_value)
        if (duration_ms <= 0 or duration_ms > 400 or phase_step_ms <= 0
                or repeats_value != repeats
                or repeats <= 0 or repeats > 1000):
            PRC.update(status="FAILED", error="invalid timing or repeat count")
            connection.sh_error("!INVALID_PRC_CONFIGURATION")
            return -1, consumed
        phase_ratio = duration_ms / phase_step_ms
        if not math.isfinite(phase_ratio) or phase_ratio >= _MAX_PHASES:
            PRC.update(status="FAILED", error="too many phase points")
            connection.sh_error("!INVALID_PRC_CONFIGURATION")
            return -1, consumed
        phase_count = math.floor(phase_ratio) + 1
        if phase_count * repeats > 10_000:
            PRC.update(status="FAILED", error="too many phase/repeat acquisitions")
            connection.sh_error("!INVALID_PRC_CONFIGURATION")
            return -1, consumed
        phases = [index * phase_step_ms for index in range(phase_count)]
        PRC.update(
            duration_ms=duration_ms,
            phase_step_ms=phase_step_ms,
            repeats=repeats,
            phases_ms=phases,
            current_parameter=phases[0],
            status="RECEIVING_PULSE",
        )
    elif PRC["status"] == "RECEIVING_PULSE":
        threshold_mv, pulse_width_ms, pulse_amplitude_mv = values
        if (threshold_mv <= 0 or pulse_width_ms <= 0 or pulse_width_ms > 400
                or PRC["duration_ms"] + pulse_width_ms > 400
                or abs(threshold_mv) > 10000 or abs(pulse_amplitude_mv) > 10000):
            PRC.update(status="FAILED", error="invalid threshold or pulse settings")
            connection.sh_error("!INVALID_PRC_CONFIGURATION")
            return -1, consumed
        PRC.update(
            threshold_mv=threshold_mv,
            pulse_width_ms=pulse_width_ms,
            pulse_amplitude_mv=pulse_amplitude_mv,
            status="READY",
        )
    else:
        PRC.update(status="FAILED", error="unexpected PRC stream state")
        connection.sh_error("!PRC_STATE_ERROR")
        return -1, consumed

    if connection.sh_term() < 0:
        return -1, consumed
    return (0 if PRC["status"] == "READY" else 1), consumed


def run_prc(args: list[str], connection: ShellConnection) -> int:
    """Start all phase/repeat acquisitions on a background worker."""
    if args:
        return connection.sh_error("!INVALID_PRC_CONFIGURATION")
    if PRC["status"] not in ("READY", "COMPLETE"):
        return connection.sh_error("!PRC_NOT_CONFIGURED")

    from nev.hardware.bootstrap import acquire_prc_series

    PRC.update(
        status="RUNNING", results=[], error="",
        current_parameter=PRC["phases_ms"][0],
    )
    _finished.clear()

    def acquire() -> None:
        """Acquire phase points and retain the metrics until the next setup."""
        def update_progress(
            phase_ms: float,
            metrics: dict[str, list[float]] | None,
        ) -> None:
            """Expose the current phase and append each completed phase."""
            PRC["current_parameter"] = phase_ms
            if metrics is None:
                return
            changes = metrics["changes"]
            periods = metrics["periods"]
            normalized = metrics["normalized"]
            valid_changes = [value for value in changes if math.isfinite(value)]
            valid_periods = [value for value in periods if math.isfinite(value)]
            valid_normalized = [value for value in normalized if math.isfinite(value)]
            mean_change = sum(valid_changes) / len(valid_changes) if valid_changes else math.nan
            mean_period = sum(valid_periods) / len(valid_periods) if valid_periods else math.nan
            mean_normalized = (
                sum(valid_normalized) / len(valid_normalized)
                if valid_normalized else math.nan
            )
            variance_change = (
                sum((value - mean_change) ** 2 for value in valid_changes) / (len(valid_changes) - 1)
                if len(valid_changes) >= 2 else math.nan
            )
            variance_period = (
                sum((value - mean_period) ** 2 for value in valid_periods) / (len(valid_periods) - 1)
                if len(valid_periods) >= 2 else math.nan
            )
            variance_normalized = (
                sum((value - mean_normalized) ** 2 for value in valid_normalized)
                / (len(valid_normalized) - 1)
                if len(valid_normalized) >= 2 else math.nan
            )
            PRC["results"].append({
                "phase_ms": phase_ms,
                "mean_change": mean_change,
                "variance_change": variance_change,
                "mean_period": mean_period,
                "variance_period": variance_period,
                "mean_normalized_change": mean_normalized,
                "variance_normalized_change": variance_normalized,
                "valid_trials": len(valid_normalized),
                "prc": mean_normalized,
            })

        try:
            acquire_prc_series(
                ao_task_index=PRC["ao_task"],
                ao_channel_index=PRC["ao_channel"],
                phases_ms=list(PRC["phases_ms"]),
                repeats=PRC["repeats"],
                threshold_mv=PRC["threshold_mv"],
                pulse_width_ms=PRC["pulse_width_ms"],
                pulse_amplitude_mv=PRC["pulse_amplitude_mv"],
                progress_callback=update_progress,
            )
        except Exception as exc:
            PRC.update(status="FAILED", error=str(exc))
        else:
            PRC.update(status="COMPLETE", error="")
        finally:
            _finished.set()

    threading.Thread(target=acquire, name="nev-prc", daemon=True).start()
    return connection.sh_term()


def get_prc_data(args: list[str], connection: ShellConnection) -> int:
    """Return scalar PRC values for a requested phase-point range."""
    if len(args) != 1:
        return connection.sh_error("!PRC_POINT_NOT_GIVEN")
    if not PRC["results"]:
        if PRC["status"] == "FAILED":
            return connection.sh_error(f"!PRC_API_ERROR({PRC['error']})")
        return connection.sh_error("!PRC_DATA_UNAVAILABLE")

    match = re.fullmatch(r"(\d+)(?:-(\d*))?", args[0])
    if match is None:
        return connection.sh_error("!INVALID_INDEX")
    begin = int(match.group(1))
    if match.group(2) is None:
        end = begin
    elif match.group(2) == "":
        end = len(PRC["results"]) - 1
    else:
        end = int(match.group(2))
    if begin < 0 or begin >= len(PRC["results"]) or end < begin or end >= len(PRC["results"]):
        return connection.sh_error("!INDEX_OUT_OF_RANGE")

    if connection.send_text(f"#PRCDATA {begin} {end}\n") < 0:
        return -17
    for result in PRC["results"][begin:end + 1]:
        if connection.send_text(
            f"{result['phase_ms']:f}\n[{result['prc']:f}]\n"
        ) < 0:
            return -17
    return connection.sh_term()


def get_prc_stat(args: list[str], connection: ShellConnection) -> int:
    """Report available phase points, active phase, and workflow status."""
    if args:
        return connection.sh_error("!INVALID_PRC_CONFIGURATION")
    status = {
        "UNCONFIGURED": "UNKNOWN",
        "RECEIVING_TIMING": "CONFIGURED",
        "RECEIVING_PULSE": "CONFIGURED",
        "READY": "IDLE",
        "RUNNING": "RUNNING",
        "COMPLETE": "COMPLETE",
        "FAILED": "FAILED",
        "ABORTED": "ABORTED",
    }.get(PRC["status"], "UNKNOWN")
    error = f" {PRC['error']}" if status == "FAILED" and PRC["error"] else ""
    response = (
        f"#PRCSTAT\n{len(PRC['results'])} "
        f"{PRC['current_parameter']:f} {status}{error}\n"
    )
    return connection.sh_term(response)
