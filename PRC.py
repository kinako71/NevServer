import os
import numpy as np
import pandas as pd
import nidaqmx
import matplotlib.pyplot as plt

from nidaqmx.constants import AcquisitionType, Edge, Slope
from nidaqmx.system import System


# ====================================================
# 設定
# ====================================================
MODES = ["cl1_freq", "cl2_freq"]

AI_DEVICE = "PXI1Slot4"
AO_DEVICE = "PXI1Slot2"

AI_CHANNEL = f"{AI_DEVICE}/ai0"
AO_CHANNEL = f"{AO_DEVICE}/ao52"

THRESHOLD = 0.07

AI_RATE = 100_000
AI_SAMPLES = 40_000
PRETRIGGER_SAMPLES = 2

AO_RATE = 100_000
PULSE_WIDTH = 0.0005
PULSE_VOLTAGE = 0.4
BASE_VOLTAGE = 0.3
PULSE_SAMPLES = round(AO_RATE * PULSE_WIDTH)

N_REPEATS = 8

PHASES_MS = np.concatenate([
    np.arange(0, 7, 1),
    np.arange(7, 17.0001, 1),
    np.arange(18, 26, 1),
])

os.makedirs("figs", exist_ok=True)
os.makedirs("results", exist_ok=True)


# ====================================================
# AO設定をCSVから読み込む
# ====================================================
def load_ao_settings(mode, csv_path="test.csv"):
    df = pd.read_csv(csv_path)

    required = {"#DEV", "#CH", mode}
    missing = required - set(df.columns)

    if missing:
        raise ValueError(
            f"Missing CSV columns for {mode}: {sorted(missing)}"
        )

    channels = []
    values = []

    for _, row in df.iterrows():
        dev = str(row["#DEV"]).strip()
        ch = str(row["#CH"]).strip()

        if ch.startswith("AO-"):
            ch = "ao" + ch[3:]

        ch = ch.lstrip("/")

        if not ch.startswith("ao"):
            raise ValueError(f"Invalid AO channel: {row['#CH']}")

        channel = f"{dev}/{ch}"

        if channel in channels:
            raise ValueError(f"Duplicate AO channel: {channel}")

        channels.append(channel)
        values.append(float(row[mode]))

    if not channels:
        raise ValueError(f"No AO channels found in {csv_path}")

    return channels, values


# ====================================================
# 1回の測定
# ====================================================
def measure_prc(phase, trial, mode):

    delay_samples = round(phase * AO_RATE)

    pulse_data = np.concatenate([
        np.full(delay_samples, BASE_VOLTAGE),
        np.full(PULSE_SAMPLES, PULSE_VOLTAGE),
        [BASE_VOLTAGE],
    ])

    system = System.local()

    route_source = "/PXI1Slot4/te0/ReferenceTrigger"
    route_destination = "/PXI1Slot4/PXI_Trig0"
    route_connected = False

    try:
        system.connect_terms(route_source, route_destination)
        route_connected = True

        with nidaqmx.Task() as ai_task, nidaqmx.Task() as ao_task:

            # ----------------------------------------
            # AI設定
            # ----------------------------------------
            ai_task.ai_channels.add_ai_voltage_chan(
                AI_CHANNEL,
                min_val=-10.0,
                max_val=10.0,
            )

            ai_task.timing.cfg_samp_clk_timing(
                rate=AI_RATE,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=AI_SAMPLES,
            )

            ai_task.triggers.reference_trigger.cfg_anlg_edge_ref_trig(
                trigger_source=AI_CHANNEL,
                trigger_slope=Slope.RISING,
                trigger_level=THRESHOLD,
                pretrigger_samples=PRETRIGGER_SAMPLES,
            )

            # ----------------------------------------
            # AO設定
            # ----------------------------------------
            ao_task.ao_channels.add_ao_voltage_chan(
                AO_CHANNEL,
                min_val=-10.0,
                max_val=10.0,
            )

            ao_task.timing.cfg_samp_clk_timing(
                rate=AO_RATE,
                sample_mode=AcquisitionType.FINITE,
                samps_per_chan=len(pulse_data),
            )

            ao_task.triggers.start_trigger.cfg_dig_edge_start_trig(
                trigger_source=f"/{AO_DEVICE}/PXI_Trig0",
                trigger_edge=Edge.RISING,
            )

            ao_task.write(pulse_data, auto_start=False)

            # AOを先にトリガー待ちにする
            ao_task.start()

            # AIを開始
            ai_task.start()

            # 測定完了を待つ
            ai_task.wait_until_done(timeout=30.0)
            ao_task.wait_until_done(timeout=5.0)

            # AIデータ取得
            ai_data = np.asarray(
                ai_task.read(
                    number_of_samples_per_channel=AI_SAMPLES,
                    timeout=5.0,
                )
            )

        # --------------------------------------------
        # しきい値の上向き交差を検出
        # --------------------------------------------
        crossings = np.where(
            (ai_data[:-1] < THRESHOLD)
            & (ai_data[1:] >= THRESHOLD)
        )[0]

        if len(crossings) > 0:
            v0 = ai_data[crossings]
            v1 = ai_data[crossings + 1]

            # 線形補間で交差時刻を計算
            t_crossings = (
                crossings
                + (THRESHOLD - v0) / (v1 - v0)
                - PRETRIGGER_SAMPLES
            ) / AI_RATE
        else:
            t_crossings = np.array([])

        # 隣接する交差時刻の差
        diffs = np.diff(t_crossings)

        if len(diffs) >= 1:
            change = diffs[0]
            period = np.mean(diffs)
        else:
            change = np.nan
            period = np.nan

        print(
            f"mode={mode}, phase={phase * 1000:.3f} ms, "
            f"trial={trial}, crossings={len(crossings)}, "
            f"change={change}, period={period}"
        )

        # --------------------------------------------
        # 波形保存
        # --------------------------------------------
        if trial == 0:

            times = (
                np.arange(len(ai_data)) - PRETRIGGER_SAMPLES
            ) / AI_RATE

            fig, ax = plt.subplots(figsize=(12, 5))

            ax.plot(
                times,
                ai_data,
                linewidth=0.8,
                label="AI waveform",
            )

            ax.axhline(
                THRESHOLD,
                color="green",
                linestyle="--",
                label=f"Threshold = {THRESHOLD} V",
            )

            ax.plot(
                t_crossings,
                np.full(len(t_crossings), THRESHOLD),
                "rx",
                markersize=8,
                label="Rising crossings",
            )

            ax.set_xlabel("Time relative to reference trigger (s)")
            ax.set_ylabel("AI Voltage (V)")
            ax.set_title(
                f"{mode}: phase={phase * 1000:.3f} ms, trial={trial}"
            )
            ax.grid(True)
            ax.legend()

            fig.tight_layout()

            fig.savefig(
                f"figs/{mode}_phase_{phase * 1000:.3f}ms"
                f"_trial_{trial:02d}.png",
                dpi=200,
            )

            plt.close(fig)

        return change, period

    finally:
        if route_connected:
            system.disconnect_terms(
                route_source,
                route_destination,
            )


# ====================================================
# 1つのモードを実行
# ====================================================
def run_mode(mode):

    print("\n" + "=" * 60)
    print(f"START MODE: {mode}")
    print("=" * 60)

    # ----------------------------------------
    # AO初期電圧を設定
    # ----------------------------------------
    ao_channels, initial_values = load_ao_settings(
        mode,
        "test.csv",
    )

    with nidaqmx.Task() as init_task:
        for channel in ao_channels:
            init_task.ao_channels.add_ao_voltage_chan(
                channel,
                min_val=-10.0,
                max_val=10.0,
            )

        init_task.write(initial_values, auto_start=True)

        input(
            f"\n[{mode}] Initial AO voltage is set. "
            "Press Enter when ready to start..."
        )

    # ----------------------------------------
    # 全phaseを測定
    # ----------------------------------------
    results = []

    for phase_ms in PHASES_MS:
        phase = phase_ms / 1000.0

        changes = []
        periods = []

        print(
            f"\n[{mode}] "
            f"========== phase {phase_ms:.3f} ms =========="
        )

        for trial in range(N_REPEATS):
            change, period = measure_prc(
                phase=phase,
                trial=trial,
                mode=mode,
            )

            changes.append(change)
            periods.append(period)

        changes = np.asarray(changes, dtype=float)
        periods = np.asarray(periods, dtype=float)

        valid_changes = changes[np.isfinite(changes)]
        valid_periods = periods[np.isfinite(periods)]

        mean_change = (
            np.mean(valid_changes)
            if len(valid_changes) else np.nan
        )

        var_change = (
            np.var(valid_changes, ddof=1)
            if len(valid_changes) >= 2 else np.nan
        )

        mean_period = (
            np.mean(valid_periods)
            if len(valid_periods) else np.nan
        )

        var_period = (
            np.var(valid_periods, ddof=1)
            if len(valid_periods) >= 2 else np.nan
        )

        print(f"Mean change     = {mean_change}")
        print(f"Variance change = {var_change}")
        print(f"Mean period     = {mean_period}")
        print(f"Variance period = {var_period}")

        results.append({
            "mode": mode,
            "phase_ms": phase_ms,
            "mean_change": mean_change,
            "variance_change": var_change,
            "mean_period": mean_period,
            "variance_period": var_period,
            "valid_changes": len(valid_changes),
            "valid_periods": len(valid_periods),
        })

    # ----------------------------------------
    # 結果をDataFrame化
    # ----------------------------------------
    results_df = pd.DataFrame(results)

    # PRC = (change - period) / period
    results_df["prc"] = (
        results_df["mean_period"] - results_df["mean_change"]
    ) / results_df["mean_period"]

    results_df["std_change"] = np.sqrt(
        results_df["variance_change"]
    )

    results_df["std_period"] = np.sqrt(
        results_df["variance_period"]
    )

    # CSV保存
    csv_path = f"results/prc_results_{mode}.csv"
    results_df.to_csv(csv_path, index=False)

    print(f"\nSaved: {csv_path}")

    # ----------------------------------------
    # PRC plot with error bars
    # ----------------------------------------
    fig, ax = plt.subplots(figsize=(9, 5))

    ax.errorbar(
        results_df["phase_ms"],
        results_df["prc"],
        yerr=(
            results_df["std_change"]
            / results_df["mean_period"]
        ),
        fmt="o-",
        markersize=4,
        capsize=3,
        elinewidth=1,
        linewidth=1,
    )

    ax.set_xlabel("Phase (ms)")
    ax.set_ylabel("PRC")
    ax.set_title(f"Phase Response Curve: {mode}")
    ax.set_xlim(0, 25)
    ax.grid(True)

    fig.tight_layout()

    plot_path = f"results/prc_errorbar_{mode}.png"
    fig.savefig(plot_path, dpi=200)
    plt.close(fig)

    print(f"Saved: {plot_path}")
    print(f"FINISHED MODE: {mode}")

    return results_df


# ====================================================
# 全モードを順番に実行
# ====================================================
all_results = []

for mode in MODES:
    result_df = run_mode(mode)
    all_results.append(result_df)

# 両モードの結果を統合
combined_df = pd.concat(all_results, ignore_index=True)

combined_path = "results/prc_results_all_modes.csv"
combined_df.to_csv(combined_path, index=False)

print("\n" + "=" * 60)
print("ALL MODES FINISHED")
print(f"Saved: {combined_path}")
print("=" * 60)
