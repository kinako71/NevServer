import os
import numpy as np
import pandas as pd
import nidaqmx
import matplotlib.pyplot as plt

from nidaqmx.constants import AcquisitionType, Edge, Signal


# ====================================================
# Configuration
# ====================================================
MODES = ["cl1_freq", "cl2_freq"]

AI_DEVICE = "PXI1Slot4"  # PXIe-4481
AO_DEVICE = "PXI1Slot2"  # PXIe-6739

AI_CHANNEL = f"{AI_DEVICE}/ai0"
AO_CHANNEL = f"{AO_DEVICE}/ao52"

AI_RATE = 100_000
AI_SAMPLES = 10_000

AO_RATE = 100_000
BASE_VOLTAGE = 0.28

PULSE_VOLTAGES = [
    0.29, 0.30, 0.31, 0.32, 0.33
]

PULSE_WIDTH = 0.004
DELAY_SECONDS = 0.020

DELAY_SAMPLES = round(DELAY_SECONDS * AO_RATE)
PULSE_SAMPLES = round(PULSE_WIDTH * AO_RATE)

PXI_TRIGGER = f"/{AO_DEVICE}/PXI_Trig0"

OUTPUT_DIR = "results"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ====================================================
# Plot style
# ====================================================

# import matplotlib.pyplot as plt

#フォント設定
# plt.rcParams['font.family'] = 'Times New Roman' # font familyの設定
#plt.rcParams['mathtext.fontset'] = 'stix' # math fontの設定
plt.rcParams["font.size"] = 15 # 全体のフォントサイズが変更されます。
#plt.rcParams['xtick.labelsize'] = 9 # 軸だけ変更されます。
#plt.rcParams['ytick.labelsize'] = 24 # 軸だけ変更されます


#軸設定
plt.rcParams['xtick.direction'] = 'in' #x軸の目盛りの向き
plt.rcParams['ytick.direction'] = 'in' #y軸の目盛りの向き
#plt.rcParams['axes.grid'] = True # グリッドの作成
#plt.rcParams['grid.linestyle']='--' #グリッドの線種
plt.rcParams["xtick.minor.visible"] = True  #x軸補助目盛りの追加
plt.rcParams["ytick.minor.visible"] = True  #y軸補助目盛りの追加
plt.rcParams['xtick.top'] = True  #x軸の上部目盛り
plt.rcParams['ytick.right'] = True  #y軸の右部目盛り


#軸大きさ
#plt.rcParams["xtick.major.width"] = 1.0             #x軸主目盛り線の線幅
#plt.rcParams["ytick.major.width"] = 1.0             #y軸主目盛り線の線幅
#plt.rcParams["xtick.minor.width"] = 1.0             #x軸補助目盛り線の線幅
#plt.rcParams["ytick.minor.width"] = 1.0             #y軸補助目盛り線の線幅
#plt.rcParams["xtick.major.size"] = 10               #x軸主目盛り線の長さ
#plt.rcParams["ytick.major.size"] = 10               #y軸主目盛り線の長さ
#plt.rcParams["xtick.minor.size"] = 5                #x軸補助目盛り線の長さ
#plt.rcParams["ytick.minor.size"] = 5                #y軸補助目盛り線の長さ
#plt.rcParams["axes.linewidth"] = 1.0                #囲みの太さ


#凡例設定
plt.rcParams["legend.fancybox"] = False  # 丸角OFF
plt.rcParams["legend.framealpha"] = 1  # 透明度の指定、0で塗りつぶしなし
plt.rcParams["legend.edgecolor"] = 'black'  # edgeの色を変更
plt.rcParams["legend.markerscale"] = 5 #markerサイズの倍率


# ====================================================
# Load initial AO settings from CSV
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
        raise ValueError("No AO channels found in test.csv")

    return channels, values


# ====================================================
# Initialize AO voltages
# ====================================================
def initialize_ao(mode):
    ao_channels, initial_values = load_ao_settings(
        mode, "test.csv"
    )

    with nidaqmx.Task() as task:
        for channel in ao_channels:
            task.ao_channels.add_ao_voltage_chan(
                channel,
                min_val=-10.0,
                max_val=10.0,
            )

        task.write(initial_values, auto_start=True)

    print(f"\nInitial AO voltages set for {mode}.")

    input(
        f"[{mode}] Check the initial voltages, then press Enter "
        "to start measurements..."
    )


# ====================================================
# Generate AO command waveform for plotting
# ====================================================
def generate_ao_waveform(voltage, n_samples):
    """
    AO command:
      BASE_VOLTAGE before the pulse
      voltage during the pulse
      BASE_VOLTAGE after the pulse

    The waveform is extended to the AI acquisition length
    for visualization only.
    """
    ao_data = np.full(n_samples, BASE_VOLTAGE, dtype=float)

    pulse_start = DELAY_SAMPLES
    pulse_end = DELAY_SAMPLES + PULSE_SAMPLES

    if pulse_start < n_samples:
        ao_data[pulse_start:min(pulse_end, n_samples)] = voltage

    return ao_data


# ====================================================
# Acquire one waveform
# ====================================================
def acquire_waveform(voltage, mode):
    print(f"\n[{mode}] Pulse voltage: {voltage:.3f} V")

    # AO waveform: baseline -> pulse -> baseline
    ao_command = np.concatenate([
        np.full(DELAY_SAMPLES, BASE_VOLTAGE),
        np.full(PULSE_SAMPLES, voltage),
        np.array([BASE_VOLTAGE]),
    ])

    print(f"AO samples: {len(ao_command)}")
    print(f"AO duration: {len(ao_command) / AO_RATE * 1000:.3f} ms")
    print(f"AI duration: {AI_SAMPLES / AI_RATE * 1000:.3f} ms")

    with nidaqmx.Task() as ai_task, nidaqmx.Task() as ao_task:

        # --------------------------------------------
        # AI configuration
        # --------------------------------------------
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

        ai_task.triggers.start_trigger.cfg_dig_edge_start_trig(
            trigger_source=PXI_TRIGGER,
            trigger_edge=Edge.RISING,
        )

        # --------------------------------------------
        # AO configuration
        # --------------------------------------------
        ao_task.ao_channels.add_ao_voltage_chan(
            AO_CHANNEL,
            min_val=-10.0,
            max_val=10.0,
        )

        ao_task.timing.cfg_samp_clk_timing(
            rate=AO_RATE,
            sample_mode=AcquisitionType.FINITE,
            samps_per_chan=len(ao_command),
        )

        ao_task.export_signals.export_signal(
            signal_id=Signal.START_TRIGGER,
            output_terminal=PXI_TRIGGER,
        )

        ao_task.write(ao_command, auto_start=False)

        # Arm AI first, then start AO
        print("Arming AI...")
        ai_task.start()

        print("Starting AO...")
        ao_task.start()

        ai_data = np.asarray(
            ai_task.read(
                number_of_samples_per_channel=AI_SAMPLES,
                timeout=30.0,
            )
        )

        ao_task.wait_until_done(timeout=10.0)

    # Common time axis, relative to AO start trigger
    times = np.arange(AI_SAMPLES) / AI_RATE

    # AO command on the same time axis as AI
    ao_plot = generate_ao_waveform(voltage, AI_SAMPLES)

    print(f"Acquired {len(ai_data)} AI samples.")

    return times, ao_plot, ai_data


# ====================================================
# Save one plot
# ====================================================
def save_figure(fig, filename):
    path = os.path.join(OUTPUT_DIR, filename)
    fig.savefig(
        path,
        dpi=300,
        bbox_inches="tight",
        facecolor="white",
    )
    plt.close(fig)
    print(f"Saved: {path}")


# ====================================================
# Run one mode
# ====================================================
def run_mode(mode):
    print("\n" + "=" * 65)
    print(f"START MODE: {mode}")
    print("=" * 65)

    initialize_ao(mode)

    all_results = {}

    # --------------------------------------------
    # Acquire all pulse voltages
    # --------------------------------------------
    for voltage in PULSE_VOLTAGES:
        times, ao_data, ai_data = acquire_waveform(
            voltage=voltage,
            mode=mode,
        )

        all_results[voltage] = {
            "time": times,
            "ao": ao_data,
            "ai": ai_data,
        }

    colors = plt.get_cmap("turbo")(
        np.linspace(0.05, 0.95, len(PULSE_VOLTAGES))
    )

    time_ms = times * 1000

    # --------------------------------------------
    # Save CSV: AO command and AI measurement
    # --------------------------------------------
    csv_data = {"time_ms": time_ms}

    for voltage, result in all_results.items():
        label = f"{voltage:.3f}V"
        csv_data[f"AO_command_{label}"] = result["ao"]
        csv_data[f"AI_measured_{label}"] = result["ai"]

    df = pd.DataFrame(csv_data)

    csv_path = os.path.join(
        OUTPUT_DIR,
        f"waveforms_{mode}.csv",
    )
    df.to_csv(csv_path, index=False)
    print(f"Saved: {csv_path}")

    # =================================================
    # 1. AO pulse overlay
    # =================================================
    fig, ax = plt.subplots(figsize=(10, 5.5))

    for i, (voltage, result) in enumerate(all_results.items()):
        ax.plot(
            time_ms,
            result["ao"],
            color=colors[i],
            linewidth=2,
            label=f"{voltage:.3f} V",
        )

    ax.axvspan(
        DELAY_SECONDS * 1000,
        (DELAY_SECONDS + PULSE_WIDTH) * 1000,
        color="gray",
        alpha=0.12,
        label="Pulse interval",
    )

    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("AO command voltage (V)")
    ax.set_xlim(0, 40)

    # style_axis(ax)
    ax.legend(
        title="Pulse amplitude",
        ncol=1,
        frameon=True,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    save_figure(fig, f"AO_overlay_{mode}.png")

    # =================================================
    # 2. AI response overlay
    # =================================================
    fig, ax = plt.subplots(figsize=(10, 5.5))

    for i, (voltage, result) in enumerate(all_results.items()):
        ax.plot(
            time_ms,
            result["ai"],
            color=colors[i],
            linewidth=1.5,
            label=f"{voltage:.3f} V",
        )

    ax.axvspan(
        DELAY_SECONDS * 1000,
        (DELAY_SECONDS + PULSE_WIDTH) * 1000,
        color="gray",
        alpha=0.12,
        label="AO pulse interval",
    )

    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Amplitude (V)")
    ax.set_xlim(0, AI_SAMPLES / AI_RATE * 1000)

    # style_axis(ax)
    ax.legend(
        title="Pulse amplitude",
        ncol=1,
        frameon=True,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    save_figure(fig, f"AI_overlay_{mode}.png")

    # =================================================
    # 3. Combined AO and AI figure
    # =================================================
    fig, (ax_ao, ax_ai) = plt.subplots(
        2,
        1,
        figsize=(11, 8),
        sharex=True,
        gridspec_kw={"height_ratios": [1, 2]},
    )

    for i, (voltage, result) in enumerate(all_results.items()):
        color = colors[i]

        ax_ao.plot(
            time_ms,
            result["ao"],
            color=color,
            linewidth=2,
            label=f"{voltage:.3f} V",
        )

        ax_ai.plot(
            time_ms,
            result["ai"],
            color=color,
            linewidth=1.5,
            label=f"{voltage:.3f} V",
        )

    for ax in (ax_ao, ax_ai):
        ax.axvspan(
            DELAY_SECONDS * 1000,
            (DELAY_SECONDS + PULSE_WIDTH) * 1000,
            color="gray",
            alpha=0.12,
        )
        # style_axis(ax)

    ax_ao.set_ylabel("AO command (V)")
    ax_ao.set_title(f"Applied Pulse and Measured Response — {mode}")

    ax_ai.set_ylabel("Amplitude (V)")
    ax_ai.set_xlabel("Time (ms)")

    ax_ao.set_xlim(0, 40)

    ax_ao.legend(
        title="Pulse amplitude",
        ncol=1,
        frameon=True,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    save_figure(fig, f"AO_AI_combined_{mode}.png")

    # =================================================
    # 4. Zoomed AO pulse
    # =================================================
    fig, ax = plt.subplots(figsize=(10, 5))

    for i, (voltage, result) in enumerate(all_results.items()):
        ax.plot(
            time_ms,
            result["ao"],
            color=colors[i],
            linewidth=2,
            label=f"{voltage:.3f} V",
        )

    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("AO command voltage (V)")
    ax.set_xlim(18, 26)

    # style_axis(ax)
    ax.legend(
        title="Pulse amplitude",
        ncol=1,
        frameon=True,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    save_figure(fig, f"AO_zoom_{mode}.png")

    # =================================================
    # 5. Zoomed AI response around pulse
    # =================================================
    fig, ax = plt.subplots(figsize=(10, 5))

    for i, (voltage, result) in enumerate(all_results.items()):
        ax.plot(
            time_ms,
            result["ai"],
            color=colors[i],
            linewidth=1.5,
            label=f"{voltage:.3f} V",
        )

    ax.axvspan(
        DELAY_SECONDS * 1000,
        (DELAY_SECONDS + PULSE_WIDTH) * 1000,
        color="gray",
        alpha=0.12,
        label="AO pulse interval",
    )

    ax.set_xlabel("Time (ms)")
    ax.set_ylabel("Amplitude (V)")
    ax.set_xlim(15, 35)

    # style_axis(ax)
    ax.legend(
        title="Pulse amplitude",
        ncol=1,
        frameon=True,
        loc="upper left",
        bbox_to_anchor=(1.02, 1.0),
        borderaxespad=0,
    )

    fig.tight_layout(rect=[0, 0, 0.78, 1])
    save_figure(fig, f"AI_zoom_{mode}.png")

    print(f"\nFINISHED MODE: {mode}")

    return df


# ====================================================
# Entry point
# ====================================================
if __name__ == "__main__":
    all_mode_results = {}

    for mode in MODES:
        all_mode_results[mode] = run_mode(mode)

    print("\n" + "=" * 65)
    print("ALL MODES FINISHED")
    print(f"Output directory: {OUTPUT_DIR}")
    print("=" * 65)
