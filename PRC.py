import os
import time
import numpy as np
import pandas as pd
import nidaqmx
import matplotlib.pyplot as plt


from nidaqmx.constants import AcquisitionType, Edge, Slope




# ====================================================
# 設定
# ====================================================
MODES = ["cl1_freq"]
# MODES = ["cl1_freq", "cl2_freq"]
# MODES = ["cl2_freq"]


AI_DEVICE = "PXI1Slot4"
AO_DEVICE = "PXI1Slot2"


AI_CHANNEL = f"{AI_DEVICE}/ai0"
AO_CHANNEL = f"{AO_DEVICE}/ao52"


THRESHOLD = 0.1


AI_RATE = 100_000
AI_SAMPLES = 40_000
PRETRIGGER_SAMPLES = 30_000


AO_RATE = 100_000
PULSE_WIDTH = 0.0005


BASE_VOLTAGE = 0.301
PULSE_VOLTAGE = BASE_VOLTAGE + 0.1


PULSE_SAMPLES = round(AO_RATE * PULSE_WIDTH)


N_REPEATS = 8


PHASES_MS = np.concatenate([
   np.arange(0, 7, 1),
   np.arange(7, 17.0001, 1),
   # np.arange(18, 26, 1),
])


CSV_PATH = "test.csv"


AI_REF_TRIGGER_OUTPUT = f"/{AI_DEVICE}/PXI_Trig0"
AO_TRIGGER_SOURCE = f"/{AO_DEVICE}/PXI_Trig0"


os.makedirs("figs", exist_ok=True)
os.makedirs("results", exist_ok=True)




# ====================================================
# AO設定をCSVから読み込む
# ====================================================
def load_ao_settings(mode, csv_path=CSV_PATH):
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
# AI設定
# Reference Triggerによる有限サンプル取得
# ====================================================
def configure_ai_reference_trigger(task):
   task.ai_channels.add_ai_voltage_chan(
       AI_CHANNEL,
       min_val=-10.0,
       max_val=10.0,
   )


   task.timing.cfg_samp_clk_timing(
       rate=AI_RATE,
       sample_mode=AcquisitionType.FINITE,
       samps_per_chan=AI_SAMPLES,
   )


   task.triggers.reference_trigger.cfg_anlg_edge_ref_trig(
       trigger_source=AI_CHANNEL,
       trigger_slope=Slope.RISING,
       trigger_level=THRESHOLD,
       pretrigger_samples=PRETRIGGER_SAMPLES,
   )




# ====================================================
# Reference Data取得
# 各phaseにつき1回、AO刺激なしで測定
# ====================================================
def acquire_reference(phase_ms, mode):
   print(
       f"\n[{mode}] Acquiring reference waveform "
       f"for phase={phase_ms:.3f} ms..."
   )


   start_time = time.monotonic()


   with nidaqmx.Task() as ref_task:
       configure_ai_reference_trigger(ref_task)


       try:
           # Reference測定ではAO刺激を出さない
           ref_task.start()


           # 自然発火によるReference Triggerを待つ
           ref_task.wait_until_done(timeout=30.0)


           ref_data = np.asarray(
               ref_task.read(
                   number_of_samples_per_channel=AI_SAMPLES,
                   timeout=5.0,
               ),
               dtype=float,
           )


       except Exception:
           try:
               ref_task.stop()
           except Exception:
               pass


           print(
               "Reference acquisition failed. "
               "Check spontaneous firing, threshold, "
               "trigger configuration, and timeout."
           )
           raise


   analysis = analyze_crossings(ref_data)


   print(
       f"[{mode}] Reference completed: "
       f"crossings={len(analysis['crossings'])}, "
       f"elapsed={time.monotonic() - start_time:.2f} s"
   )


   # Reference波形を保存
   times = (
       np.arange(len(ref_data)) - PRETRIGGER_SAMPLES
   ) / AI_RATE


   fig, ax = plt.subplots(figsize=(12, 5))


   ax.plot(times, ref_data, linewidth=0.8, label="Reference")
   ax.axhline(
       THRESHOLD,
       color="green",
       linestyle="--",
       label=f"Threshold = {THRESHOLD} V",
   )


   # if len(analysis["crossings"]) > 0:
   #     ax.plot(
   #         analysis["crossings"],
   #         np.full(len(analysis["crossings"]), THRESHOLD),
   #         "rx",
   #         markersize=6,
   #         label="Rising crossings",
   #     )


   # ax.set_xlabel("Time relative to reference trigger (s)")
   # ax.set_ylabel("AI Voltage (V)")
   # ax.set_title(
   #     f"{mode}: Reference, phase={phase_ms:.3f} ms"
   # )
   # ax.grid(True)
   # ax.legend()
   # fig.tight_layout()


   # fig.savefig(
   #     f"figs/{mode}_reference_phase_{phase_ms:.3f}ms.png",
   #     dpi=200,
   # )
   # plt.close(fig)


   return ref_data, analysis




# ====================================================
# しきい値交差時刻を計算
# ====================================================
def analyze_crossings(ai_data):
   ai_data = np.asarray(ai_data, dtype=float)


   indices = np.where(
       (ai_data[:-1] < THRESHOLD)
       & (ai_data[1:] >= THRESHOLD)
   )[0]


   if len(indices) == 0:
       crossings = np.array([], dtype=float)
   else:
       v0 = ai_data[indices]
       v1 = ai_data[indices + 1]


       fractions = (THRESHOLD - v0) / (v1 - v0)


       crossings = (
           indices + fractions - PRETRIGGER_SAMPLES
       ) / AI_RATE


   return {
       "crossings": crossings,
   }




# ====================================================
# Referenceと刺激後の発火時刻からPRCを計算
#
# 各波形の時刻ゼロに最も近い発火を基準とする。
# そこから3つ先の発火時刻を比較する。
# ====================================================
def calculate_prc(ref_crossings, ai_crossings):
   result = {
       "period": np.nan,
       "change": np.nan,
       "normalized_change": np.nan,
       "ref_after": np.nan,
       "ai_after": np.nan,
   }


   if len(ref_crossings) < 5 or len(ai_crossings) < 5:
       return result


   # Referenceの発火時刻から誤検出を除去
   MIN_PERIOD = 0.001  # 0.5 ms


   ref_diffs = np.diff(ref_crossings)


   # 最初の発火は残し、直前との間隔が0.5 ms以上の発火だけ残す
   valid_mask = np.concatenate([
       [True],
       ref_diffs >= MIN_PERIOD,
   ])


   ref_crossings = ref_crossings[valid_mask]


   # 除去後の発火周期を計算
   ref_diffs = np.diff(ref_crossings)


   if len(ref_diffs) == 0:
       return result


   period = np.mean(ref_diffs)


   if not np.isfinite(period) or period <= 0:
       return result


   # 各波形の時刻ゼロに最も近い発火
   ref_idx = int(np.argmin(np.abs(ref_crossings)))
   ai_idx = int(np.argmin(np.abs(ai_crossings)))


   # 同じ発火番号だけ先を比較
   offset = 3
   ref_target_idx = ref_idx + offset
   ai_target_idx = ai_idx + offset


   if (
       ref_target_idx >= len(ref_crossings)
       or ai_target_idx >= len(ai_crossings)
   ):
       return result


   ref_after = ref_crossings[ref_target_idx]
   ai_after = ai_crossings[ai_target_idx]


   change = ref_after - ai_after
   normalized_change = change / period


   result.update({
       "period": period,
       "change": change,
       "normalized_change": normalized_change,
       "ref_after": ref_after,
       "ai_after": ai_after,
   })


   return result




# ====================================================
# 刺激測定
# ====================================================
def measure_prc(phase, phase_ms, trial, mode, ref_data,
               ref_analysis):


   start_time = time.monotonic()


   delay_samples = round(phase * AO_RATE)


   # Reference Trigger後、phaseだけ待って刺激を出す
   pulse_data = np.concatenate([
       np.full(delay_samples, BASE_VOLTAGE, dtype=float),
       np.full(PULSE_SAMPLES, PULSE_VOLTAGE, dtype=float),
       np.full(1, BASE_VOLTAGE, dtype=float),
   ])


   with nidaqmx.Task() as ai_task, nidaqmx.Task() as ao_task:


       # ----------------------------------------
       # AI
       # ----------------------------------------
       configure_ai_reference_trigger(ai_task)


       # AI Reference TriggerをPXI_Trig0へ出力
       ai_task.export_signals.ref_trig_output_term = (
           AI_REF_TRIGGER_OUTPUT
       )


       # ----------------------------------------
       # AO
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


       # AI Reference TriggerをAO開始トリガにする
       ao_task.triggers.start_trigger.cfg_dig_edge_start_trig(
           trigger_source=AO_TRIGGER_SOURCE,
           trigger_edge=Edge.RISING,
       )


       ao_task.write(pulse_data, auto_start=False)


       # ----------------------------------------
       # 測定開始
       # ----------------------------------------
       try:
           print(
               f"[{mode}] phase={phase_ms:.3f} ms, "
               f"trial={trial}: Starting AI..."
           )


           ai_task.start()


           # AIのReference Triggerが発生する前にAOを待機させる
           time.sleep(PRETRIGGER_SAMPLES / AI_RATE)


           ao_task.start()


           ai_task.wait_until_done(timeout=30.0)
           ao_task.wait_until_done(timeout=10.0)


           ai_data = np.asarray(
               ai_task.read(
                   number_of_samples_per_channel=AI_SAMPLES,
                   timeout=5.0,
               ),
               dtype=float,
           )


       except Exception:
           for task in (ai_task, ao_task):
               try:
                   task.stop()
               except Exception:
                   pass
           raise


   # ----------------------------------------
   # Reference / Stimulus解析
   # ----------------------------------------
   ai_analysis = analyze_crossings(ai_data)


   ref_crossings = ref_analysis["crossings"]
   ai_crossings = ai_analysis["crossings"]


   prc_result = calculate_prc(
       ref_crossings,
       ai_crossings,
   )


   period = prc_result["period"]
   change = prc_result["change"]
   normalized_change = prc_result["normalized_change"]


   print(
       f"[{mode}] phase={phase_ms:.3f} ms, "
       f"trial={trial}, "
       f"ref_crossings={len(ref_crossings)}, "
       f"ai_crossings={len(ai_crossings)}, "
       f"period={period:.8g}, "
       f"change={change:.8g}, "
       f"PRC={normalized_change:.8g}"
   )


   # ----------------------------------------
   # 波形保存：各phaseのtrial 0のみ
   # Referenceと刺激後波形を重ねて保存
   # ----------------------------------------
   if trial == 0:
       times = (
           np.arange(len(ai_data)) - PRETRIGGER_SAMPLES
       ) / AI_RATE


       fig, ax = plt.subplots(figsize=(12, 5))


       ax.plot(
           times,
           ref_data,
           linewidth=0.8,
           alpha=0.8,
           label="Reference waveform",
       )


       ax.plot(
           times,
           ai_data,
           linewidth=0.8,
           label="Stimulus waveform",
       )


       ax.axhline(
           THRESHOLD,
           color="green",
           linestyle="--",
           label=f"Threshold = {THRESHOLD} V",
       )


       if len(ref_crossings) > 0:
           ax.plot(
               ref_crossings,
               np.full(len(ref_crossings), THRESHOLD),
               "b|",
               markersize=8,
               label="Reference crossings",
           )


       if len(ai_crossings) > 0:
           ax.plot(
               ai_crossings,
               np.full(len(ai_crossings), THRESHOLD),
               "rx",
               markersize=6,
               label="Stimulus crossings",
           )


       # PRC計算に使用した発火時刻
       if np.isfinite(prc_result["ref_after"]):
           ax.plot(
               prc_result["ref_after"],
               THRESHOLD,
               "bo",
               markersize=8,
               label="Reference target",
           )


       if np.isfinite(prc_result["ai_after"]):
           ax.plot(
               prc_result["ai_after"],
               THRESHOLD,
               "mo",
               markersize=8,
               label="Stimulus target",
           )


       ax.set_xlabel("Time relative to reference trigger (s)")
       ax.set_ylabel("AI Voltage (V)")
       ax.set_title(
           f"{mode}: phase={phase_ms:.3f} ms, trial={trial}"
       )
       ax.grid(True)
       ax.legend()
       fig.tight_layout()


       fig.savefig(
           f"figs/{mode}_phase_{phase_ms:.3f}ms"
           f"_trial_{trial:02d}.png",
           dpi=200,
       )
       plt.close(fig)


   return {
       "change": change,
       "period": period,
       "normalized_change": normalized_change,
       "n_ref_crossings": len(ref_crossings),
       "n_ai_crossings": len(ai_crossings),
       "elapsed_s": time.monotonic() - start_time,
   }




# ====================================================
# 1つのmodeを実行
# ====================================================
def run_mode(mode):


   print("\n" + "=" * 60)
   print(f"START MODE: {mode}")
   print("=" * 60)


   mode_start = time.monotonic()


   # ----------------------------------------
   # AO初期電圧をCSVから設定
   # ----------------------------------------
   ao_channels, initial_values = load_ao_settings(
       mode,
       CSV_PATH,
   )


   if AO_CHANNEL not in ao_channels:
       raise ValueError(
           f"{AO_CHANNEL} is not listed in {CSV_PATH}"
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


   results = []


   # ----------------------------------------
   # 各phaseのReferenceを1回ずつ取得
   # ----------------------------------------
   references = {}


   for phase_ms in PHASES_MS:
       ref_data, ref_analysis = acquire_reference(
           phase_ms=phase_ms,
           mode=mode,
       )
       references[phase_ms] = (ref_data, ref_analysis)


   # phaseごとのtrial結果を保存
   phase_results = {
       phase_ms: [] for phase_ms in PHASES_MS
   }


   # ----------------------------------------
   # Trial outer loop / Phase(delay) inner loop
   # ----------------------------------------
   for trial in range(N_REPEATS):
       for phase_ms in PHASES_MS:
           phase = phase_ms / 1000.0


           print(
               f"\\n[{mode}] "
               f"========== trial {trial}, "
               f"phase {phase_ms:.3f} ms =========="
           )


           ref_data, ref_analysis = references[phase_ms]


           result = measure_prc(
               phase=phase,
               phase_ms=phase_ms,
               trial=trial,
               mode=mode,
               ref_data=ref_data,
               ref_analysis=ref_analysis,
           )


           phase_results[phase_ms].append(result)


   # ----------------------------------------
   # PhaseごとのTrial間統計
   # ----------------------------------------
   for phase_ms in PHASES_MS:
       trial_results = phase_results[phase_ms]


       changes = np.array(
           [r["change"] for r in trial_results],
           dtype=float,
       )


       periods = np.array(
           [r["period"] for r in trial_results],
           dtype=float,
       )


       normalized = np.array(
           [r["normalized_change"] for r in trial_results],
           dtype=float,
       )


       valid_changes = changes[np.isfinite(changes)]
       valid_periods = periods[np.isfinite(periods)]
       valid_normalized = normalized[np.isfinite(normalized)]


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


       mean_normalized = (
           np.mean(valid_normalized)
           if len(valid_normalized) else np.nan
       )


       var_normalized = (
           np.var(valid_normalized, ddof=1)
           if len(valid_normalized) >= 2 else np.nan
       )


       print(f"Phase                 = {phase_ms:.3f} ms")
       print(f"Mean change            = {mean_change}")
       print(f"Variance change        = {var_change}")
       print(f"Mean period            = {mean_period}")
       print(f"Mean normalized change = {mean_normalized}")
       print(f"Valid trials           = {len(valid_normalized)}")


       results.append({
           "mode": mode,
           "phase_ms": phase_ms,
           "mean_change": mean_change,
           "variance_change": var_change,
           "mean_period": mean_period,
           "variance_period": var_period,
           "mean_normalized_change": mean_normalized,
           "variance_normalized_change": var_normalized,
           "valid_changes": len(valid_changes),
           "valid_periods": len(valid_periods),
           "valid_normalized": len(valid_normalized),
       })


   # ----------------------------------------
   # 結果をDataFrame化
   # ----------------------------------------
   results_df = pd.DataFrame(results)


   results_df["prc"] = results_df["mean_normalized_change"]


   results_df["std_change"] = np.sqrt(
       results_df["variance_change"]
   )


   results_df["std_period"] = np.sqrt(
       results_df["variance_period"]
   )


   results_df["std_normalized_change"] = np.sqrt(
       results_df["variance_normalized_change"]
   )


   # ----------------------------------------
   # CSV保存
   # ----------------------------------------
   csv_path = f"results/prc_results_{mode}.csv"
   results_df.to_csv(csv_path, index=False)


   print(f"\nSaved: {csv_path}")


   # ----------------------------------------
   # PRC plot
   # ----------------------------------------
   fig, ax = plt.subplots(figsize=(9, 5))


   ax.errorbar(
       results_df["phase_ms"],
       results_df["prc"],
       yerr=results_df["std_normalized_change"],
       fmt="o-",
       markersize=4,
       capsize=3,
       elinewidth=1,
       linewidth=1,
   )


   ax.set_xlabel("Phase (ms)")
   ax.set_ylabel("(Reference time - Stimulus time) / Period")
   ax.set_title(f"Phase Response Curve: {mode}")
   ax.set_xlim(0, 25)
   ax.grid(True)


   fig.tight_layout()


   plot_path = f"results/prc_errorbar_{mode}.png"
   fig.savefig(plot_path, dpi=200)
   plt.close(fig)


   print(f"Saved: {plot_path}")
   print(
       f"FINISHED MODE: {mode}, "
       f"elapsed={time.monotonic() - mode_start:.2f} s"
   )


   return results_df




# ====================================================
# 全modeを実行
# ====================================================
def main():


   start_time = time.monotonic()
   all_results = []


   try:
       for mode in MODES:
           result_df = run_mode(mode)
           all_results.append(result_df)


       combined_df = pd.concat(
           all_results,
           ignore_index=True,
       )


       combined_path = "results/prc_results_all_modes.csv"
       combined_df.to_csv(combined_path, index=False)


       print("\n" + "=" * 60)
       print("ALL MODES FINISHED")
       print(f"Saved: {combined_path}")
       print(
           f"Total elapsed time: "
           f"{time.monotonic() - start_time:.2f} s"
       )
       print("=" * 60)


   except Exception as exc:
       print(f"\nERROR: {exc}")
       raise




if __name__ == "__main__":
   main()


