#!/usr/bin/env python3
"""
analyze_results.py — Generate IEEE-quality plots and tables from test results.

Runs on any machine with matplotlib (your laptop, the Pi, etc.).
Copy the CSV/JSON files from /home/kali/Desktop/logs/ and run this.

Usage:
    python3 analyze_results.py --handover test_wifi_to_lora_*.csv
    python3 analyze_results.py --store-forward test_sf_15min_*.csv test_sf_30min_*.csv
    python3 analyze_results.py --endurance test_endurance_*.csv
    python3 analyze_results.py --all results_dir/

Output:
    - PDF figures ready for IEEE submission (vector graphics)
    - LaTeX-formatted tables for copy-paste into paper
"""

import argparse
import csv
import json
import os
import sys
import numpy as np
from pathlib import Path

# ─── Matplotlib config for IEEE ──────────────────────────────────────────────

import matplotlib
matplotlib.use("Agg")  # non-interactive backend
import matplotlib.pyplot as plt

# IEEE-friendly defaults
plt.rcParams.update({
    "figure.figsize": (3.5, 2.5),       # single-column IEEE width
    "figure.dpi": 300,
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "font.size": 8,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "legend.fontsize": 7,
    "xtick.labelsize": 7,
    "ytick.labelsize": 7,
    "lines.linewidth": 1.0,
    "lines.markersize": 4,
    "grid.alpha": 0.3,
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})

OUTPUT_DIR = "ieee_figures"


def ensure_output_dir():
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def read_csv(filepath):
    rows = []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def read_json(filepath):
    with open(filepath, "r") as f:
        return json.load(f)


# ═════════════════════════════════════════════════════════════════════════════
# HANDOVER ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def plot_handover_cdf(csv_files, output_name="handover_cdf"):
    """
    Generate CDF plot of handover times.
    If multiple CSVs are provided (e.g., different check intervals), overlay them.
    """
    ensure_output_dir()
    fig, ax = plt.subplots()
    
    markers = ["o", "s", "^", "D"]
    
    for i, fpath in enumerate(csv_files):
        rows = read_csv(fpath)
        successful = [r for r in rows if r.get("success", "").lower() == "true"]
        
        if not successful:
            print(f"  [!] No successful runs in {fpath}")
            continue
        
        t_totals = sorted([float(r["t_total_sec"]) for r in successful])
        cdf = np.arange(1, len(t_totals) + 1) / len(t_totals)
        
        # Extract label from filename or check_interval
        check_int = successful[0].get("check_interval_sec", "?")
        test_type = successful[0].get("test", "handover")
        label = f"{test_type} (check={check_int}s)"
        
        ax.plot(t_totals, cdf, marker=markers[i % len(markers)],
                markevery=max(1, len(t_totals) // 8), label=label)
    
    ax.set_xlabel("Total Handover Time (s)")
    ax.set_ylabel("CDF")
    ax.set_ylim(0, 1.05)
    ax.grid(True, linestyle="--", alpha=0.3)
    ax.legend(loc="lower right")
    
    outpath = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    fig.savefig(outpath)
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


def plot_handover_decomposition(csv_files, output_name="handover_decomp"):
    """
    Stacked bar chart showing T_detect vs T_handover decomposition.
    """
    ensure_output_dir()
    fig, ax = plt.subplots()
    
    all_detect = []
    all_handover = []
    labels = []
    
    for fpath in csv_files:
        rows = read_csv(fpath)
        successful = [r for r in rows
                      if r.get("success", "").lower() == "true"
                      and r.get("t_detect_sec")
                      and r.get("t_handover_sec")]
        
        if not successful:
            continue
        
        detects = [float(r["t_detect_sec"]) for r in successful]
        handovers = [float(r["t_handover_sec"]) for r in successful]
        
        check_int = successful[0].get("check_interval_sec", "?")
        labels.append(f"check={check_int}s")
        all_detect.append(np.mean(detects))
        all_handover.append(np.mean(handovers))
    
    if not labels:
        print("  [!] No data for decomposition plot")
        return None
    
    x = np.arange(len(labels))
    width = 0.5
    
    bars1 = ax.bar(x, all_detect, width, label="$T_{detect}$", color="#4878CF")
    bars2 = ax.bar(x, all_handover, width, bottom=all_detect,
                   label="$T_{handover}$", color="#D65F5F")
    
    ax.set_xlabel("Configuration")
    ax.set_ylabel("Time (s)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.legend()
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    
    # Add value labels on bars
    for bar, val in zip(bars1, all_detect):
        if val > 2:
            ax.text(bar.get_x() + bar.get_width() / 2, val / 2,
                    f"{val:.1f}s", ha="center", va="center", fontsize=6, color="white")
    
    outpath = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    fig.savefig(outpath)
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


def generate_handover_latex_table(csv_files):
    """Generate a LaTeX table summarizing handover results."""
    
    print("\n  ── LaTeX Table: Handover Results ──")
    print(r"  \begin{table}[t]")
    print(r"  \centering")
    print(r"  \caption{Handover Performance Results}")
    print(r"  \label{tab:handover}")
    print(r"  \begin{tabular}{lccccc}")
    print(r"  \hline")
    print(r"  Test & $N$ & $\bar{T}_{detect}$ (s) & $\bar{T}_{handover}$ (s) & $\bar{T}_{total}$ (s) & $\sigma$ (s) \\")
    print(r"  \hline")
    
    for fpath in csv_files:
        rows = read_csv(fpath)
        successful = [r for r in rows if r.get("success", "").lower() == "true"]
        
        if not successful:
            continue
        
        test = successful[0].get("test", "?")
        n = len(successful)
        
        t_totals = [float(r["t_total_sec"]) for r in successful]
        t_detects = [float(r["t_detect_sec"]) for r in successful if r.get("t_detect_sec")]
        t_handovers = [float(r["t_handover_sec"]) for r in successful if r.get("t_handover_sec")]
        
        mean_total = np.mean(t_totals)
        std_total = np.std(t_totals)
        mean_detect = np.mean(t_detects) if t_detects else 0
        mean_handover = np.mean(t_handovers) if t_handovers else 0
        
        test_label = test.replace("_", r"\_")
        print(f"  {test_label} & {n} & {mean_detect:.1f} & {mean_handover:.1f} & "
              f"{mean_total:.1f} & {std_total:.1f} \\\\")
    
    print(r"  \hline")
    print(r"  \end{tabular}")
    print(r"  \end{table}")


# ═════════════════════════════════════════════════════════════════════════════
# STORE-AND-FORWARD ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def plot_sf_mdr_bar(csv_files, output_name="sf_mdr"):
    """Bar chart of MDR for different outage durations."""
    ensure_output_dir()
    fig, ax = plt.subplots()
    
    outage_labels = []
    mdr_means = []
    mdr_stds = []
    
    for fpath in csv_files:
        rows = read_csv(fpath)
        successful = [r for r in rows
                      if r.get("success", "").lower() == "true"
                      and r.get("mdr")]
        
        if not successful:
            continue
        
        outage = successful[0].get("outage_minutes", "?")
        mdrs = [float(r["mdr"]) * 100 for r in successful]
        
        outage_labels.append(f"{outage} min")
        mdr_means.append(np.mean(mdrs))
        mdr_stds.append(np.std(mdrs))
    
    if not outage_labels:
        print("  [!] No data for MDR plot")
        return None
    
    x = np.arange(len(outage_labels))
    bars = ax.bar(x, mdr_means, 0.5, yerr=mdr_stds,
                  capsize=3, color="#6ACC65", edgecolor="black", linewidth=0.5)
    
    ax.set_xlabel("Outage Duration")
    ax.set_ylabel("Message Delivery Ratio (%)")
    ax.set_xticks(x)
    ax.set_xticklabels(outage_labels)
    ax.set_ylim(0, 105)
    ax.axhline(y=100, color="gray", linestyle="--", linewidth=0.5, alpha=0.5)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    
    # Value labels
    for bar, mean_val in zip(bars, mdr_means):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 1,
                f"{mean_val:.1f}%", ha="center", va="bottom", fontsize=7)
    
    outpath = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    fig.savefig(outpath)
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


def plot_sf_buffer_growth(json_file, output_name="sf_buffer_growth"):
    """Plot buffer growth over time during outage (from a single run's JSON)."""
    ensure_output_dir()
    
    data = read_json(json_file)
    results = data.get("results", [])
    
    fig, ax = plt.subplots()
    
    for r in results:
        if not r or not r.get("success"):
            continue
        
        snapshots = r.get("buffer_growth_snapshots", [])
        if not snapshots:
            continue
        
        elapsed = [s["elapsed_sec"] / 60 for s in snapshots]
        counts = [s["pending_count"] for s in snapshots]
        
        ax.plot(elapsed, counts, marker="o", markersize=3,
                label=f"Run {r['run']}")
    
    ax.set_xlabel("Time Since Outage Start (min)")
    ax.set_ylabel("Buffered Messages")
    ax.grid(True, linestyle="--", alpha=0.3)
    if len(results) <= 5:
        ax.legend(fontsize=6)
    
    outpath = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    fig.savefig(outpath)
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


def generate_sf_latex_table(csv_files):
    """LaTeX table for store-and-forward results."""
    
    print("\n  ── LaTeX Table: Store-and-Forward Results ──")
    print(r"  \begin{table}[t]")
    print(r"  \centering")
    print(r"  \caption{Store-and-Forward Reliability}")
    print(r"  \label{tab:store-forward}")
    print(r"  \begin{tabular}{lcccccc}")
    print(r"  \hline")
    print(r"  Outage & $N$ & Buffered & MDR (\%) & Replay (s) & Order & Dup. \\")
    print(r"  \hline")
    
    for fpath in csv_files:
        rows = read_csv(fpath)
        successful = [r for r in rows if r.get("success", "").lower() == "true"]
        
        if not successful:
            continue
        
        outage = successful[0].get("outage_minutes", "?")
        n = len(successful)
        
        buffered = [int(r["buffered_messages"]) for r in successful]
        mdrs = [float(r["mdr"]) * 100 for r in successful]
        replays = [float(r["replay_duration_sec"]) for r in successful]
        all_ordered = all(r.get("buffer_ordering_ok", "").lower() == "true"
                         for r in successful)
        total_dupes = sum(int(r.get("buffer_duplicates", 0)) for r in successful)
        
        # Move LaTeX commands outside f-string expressions
        checkmark = r"\checkmark" if all_ordered else r"\ding{55}"
        
        print(f"  {outage} min & {n} & {np.mean(buffered):.0f} & "
              f"{np.mean(mdrs):.1f}$\\pm${np.std(mdrs):.1f} & "
              f"{np.mean(replays):.1f} & "
              f"{checkmark} & "
              f"{total_dupes} \\\\")
    
    print(r"  \hline")
    print(r"  \end{tabular}")
    print(r"  \end{table}")


# ═════════════════════════════════════════════════════════════════════════════
# ENDURANCE ANALYSIS
# ═════════════════════════════════════════════════════════════════════════════

def plot_endurance_timeline(csv_file, output_name="endurance_timeline"):
    """Multi-panel time series: tech state, pending, memory, temperature."""
    ensure_output_dir()
    
    rows = read_csv(csv_file)
    if not rows:
        print("  [!] No data in endurance CSV")
        return None
    
    elapsed_h = [float(r["elapsed_min"]) / 60 for r in rows]
    pending = [int(r.get("pending_count", 0)) for r in rows]
    mem_pct = [float(r.get("mem_usage_pct", 0)) for r in rows]
    temps = [float(r["cpu_temp_c"]) for r in rows if r.get("cpu_temp_c")]
    
    # Map tech to numeric for plotting
    tech_map = {"wifi": 2, "lora": 1, "none": 0}
    tech_vals = [tech_map.get(r.get("upload_tech", "none"), 0) for r in rows]
    
    fig, axes = plt.subplots(4, 1, figsize=(7, 6), sharex=True)
    
    # Panel 1: Upload technology state
    ax1 = axes[0]
    ax1.fill_between(elapsed_h, tech_vals, step="post", alpha=0.4, color="#4878CF")
    ax1.step(elapsed_h, tech_vals, where="post", color="#4878CF", linewidth=0.8)
    ax1.set_ylabel("Link")
    ax1.set_yticks([0, 1, 2])
    ax1.set_yticklabels(["none", "LoRa", "Wi-Fi"])
    ax1.set_ylim(-0.2, 2.5)
    ax1.grid(True, linestyle="--", alpha=0.3)
    
    # Panel 2: Pending messages
    ax2 = axes[1]
    ax2.plot(elapsed_h, pending, color="#D65F5F", linewidth=0.8)
    ax2.fill_between(elapsed_h, pending, alpha=0.2, color="#D65F5F")
    ax2.set_ylabel("Pending\nMessages")
    ax2.grid(True, linestyle="--", alpha=0.3)
    
    # Panel 3: Memory usage
    ax3 = axes[2]
    ax3.plot(elapsed_h, mem_pct, color="#6ACC65", linewidth=0.8)
    ax3.set_ylabel("Memory\nUsage (%)")
    ax3.grid(True, linestyle="--", alpha=0.3)
    
    # Panel 4: CPU temperature
    ax4 = axes[3]
    if temps:
        temp_h = elapsed_h[:len(temps)]
        ax4.plot(temp_h, temps, color="#E5AE38", linewidth=0.8)
        ax4.set_ylabel("CPU\nTemp (°C)")
    ax4.set_xlabel("Time (hours)")
    ax4.grid(True, linestyle="--", alpha=0.3)
    
    fig.align_ylabels(axes)
    fig.tight_layout(h_pad=0.3)
    
    outpath = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    fig.savefig(outpath)
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


def plot_endurance_mdr_hourly(csv_file, json_file=None, upload_periodicity_min=5,
                               output_name="endurance_mdr_hourly"):
    """
    Plot MDR in 1-hour windows over the endurance test.
    
    MDR per window = messages NOT pending / expected messages.
    This is approximate — for exact MDR, cross-reference with server data.
    """
    ensure_output_dir()
    
    rows = read_csv(csv_file)
    if not rows:
        return None
    
    # Group by hour
    hourly_data = {}
    for r in rows:
        hour = int(float(r["elapsed_min"]) // 60)
        if hour not in hourly_data:
            hourly_data[hour] = []
        hourly_data[hour].append({
            "pending": int(r.get("pending_count", 0)),
            "tech": r.get("upload_tech", "none"),
        })
    
    hours = sorted(hourly_data.keys())
    availability = []
    
    for h in hours:
        samples = hourly_data[h]
        # Availability = fraction of samples where tech != "none"
        connected = sum(1 for s in samples if s["tech"] != "none")
        avail = connected / len(samples) if samples else 0
        availability.append(avail * 100)
    
    fig, ax = plt.subplots()
    ax.bar(hours, availability, width=0.8, color="#4878CF", edgecolor="black",
           linewidth=0.3)
    ax.set_xlabel("Hour")
    ax.set_ylabel("Connectivity Availability (%)")
    ax.set_ylim(0, 105)
    ax.axhline(y=100, color="gray", linestyle="--", linewidth=0.5)
    ax.grid(True, axis="y", linestyle="--", alpha=0.3)
    
    outpath = os.path.join(OUTPUT_DIR, f"{output_name}.pdf")
    fig.savefig(outpath)
    plt.close(fig)
    print(f"  Saved: {outpath}")
    return outpath


# ═════════════════════════════════════════════════════════════════════════════
# COMBINED SUMMARY TABLE
# ═════════════════════════════════════════════════════════════════════════════

def generate_summary_latex():
    """
    Generate the main summary table for Section V of the paper.
    This is a template — fill in values from your actual results.
    """
    print("\n  ── LaTeX Template: Summary Table ──")
    print(r"""
  \begin{table}[t]
  \centering
  \caption{System Performance Summary}
  \label{tab:summary}
  \begin{tabular}{lcc}
  \hline
  \textbf{Metric} & \textbf{Wi-Fi} & \textbf{LoRaWAN} \\
  \hline
  $T_{detect}$ (s)           & N/A          & $\mu \pm \sigma$ \\
  $T_{handover}$ (s)         & N/A          & $\mu \pm \sigma$ \\
  $T_{total}$ (s)            & N/A          & $\mu \pm \sigma$ \\
  MDR (\%)                   & XX.X         & XX.X \\
  $L_{e2e}$ (s)              & $\mu \pm \sigma$ & $\mu \pm \sigma$ \\
  Replay latency (s)         & $\mu \pm \sigma$ & N/A \\
  Buffer integrity           & \multicolumn{2}{c}{\checkmark} \\
  Duplicates                 & \multicolumn{2}{c}{0} \\
  24h stability              & \multicolumn{2}{c}{\checkmark} \\
  \hline
  \end{tabular}
  \end{table}
  """)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    global OUTPUT_DIR
    
    parser = argparse.ArgumentParser(
        description="Generate IEEE-quality figures from CrowdSensor test results"
    )
    parser.add_argument("--handover", nargs="+", metavar="CSV",
                        help="Handover test CSV file(s)")
    parser.add_argument("--store-forward", nargs="+", metavar="CSV",
                        help="Store-and-forward test CSV file(s)")
    parser.add_argument("--sf-json", metavar="JSON",
                        help="Store-and-forward JSON (for buffer growth plot)")
    parser.add_argument("--endurance", metavar="CSV",
                        help="Endurance test CSV file")
    parser.add_argument("--endurance-json", metavar="JSON",
                        help="Endurance test JSON file")
    parser.add_argument("--latex", action="store_true",
                        help="Print LaTeX tables to stdout")
    parser.add_argument("--output-dir", default=OUTPUT_DIR,
                        help=f"Output directory (default: {OUTPUT_DIR})")
    
    args = parser.parse_args()
    OUTPUT_DIR = args.output_dir
    
    print(f"\n{'#'*60}")
    print(f"  CrowdSensor Results Analysis")
    print(f"  Output: {OUTPUT_DIR}/")
    print(f"{'#'*60}\n")
    
    generated = []
    
    if args.handover:
        print("  ── Handover Analysis ──")
        generated.append(plot_handover_cdf(args.handover))
        generated.append(plot_handover_decomposition(args.handover))
        if args.latex:
            generate_handover_latex_table(args.handover)
    
    if args.store_forward:
        print("\n  ── Store-and-Forward Analysis ──")
        generated.append(plot_sf_mdr_bar(args.store_forward))
        if args.latex:
            generate_sf_latex_table(args.store_forward)
    
    if args.sf_json:
        generated.append(plot_sf_buffer_growth(args.sf_json))
    
    if args.endurance:
        print("\n  ── Endurance Analysis ──")
        generated.append(plot_endurance_timeline(args.endurance))
        generated.append(plot_endurance_mdr_hourly(args.endurance))
    
    if args.latex:
        generate_summary_latex()
    
    generated = [g for g in generated if g is not None]
    
    print(f"\n{'='*60}")
    print(f"  Generated {len(generated)} figures in {OUTPUT_DIR}/")
    for g in generated:
        print(f"    {g}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
