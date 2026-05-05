#!/usr/bin/env python3
"""
test_handover.py — Handover test orchestration script.

Runs ON the Raspberry Pi sensor. Because the AP cannot be controlled 
programmatically, this script guides the operator through each test run
with prompts, and timestamps all actions precisely.

Usage:
    python3 test_handover.py --test wifi_to_lora --runs 15
    python3 test_handover.py --test lora_to_wifi --runs 15
    python3 test_handover.py --test wifi_to_lora --runs 15 --check-interval 30

Before running:
    1. Copy event_logger.py and uart_lock.py to /home/kali/Desktop/
    2. Apply patches from PATCH_INSTRUCTIONS.py to existing scripts
    3. Ensure cron is running (check and send scripts active)

Output:
    - Raw events: /home/kali/Desktop/logs/comm_events.jsonl
    - Test metadata: /home/kali/Desktop/logs/test_handover_<timestamp>.json
    - Summary CSV: /home/kali/Desktop/logs/test_handover_<timestamp>.csv
"""

import argparse
import json
import os
import sys
import time
import datetime
import csv
import subprocess
import sqlite3

# Add the sensor scripts directory to path
sys.path.insert(0, "/home/kali/Desktop")

from event_logger import log_event, read_events, rotate_log, LOG_FILE


# ─── Configuration ───────────────────────────────────────────────────────────

DB_PATH = "/home/kali/Desktop/DB/SensorConfiguration.db"
DEFAULT_CHECK_INTERVAL_SEC = 300  # current: 5 minutes (from cron */5)
STABILIZATION_WAIT_SEC = 60      # wait for sensor to stabilize between runs


def get_current_upload_tech():
    """Read current upload technology from database."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        c = conn.cursor()
        row = c.execute("SELECT Upload_Technology FROM SensorConfiguration").fetchone()
        conn.close()
        return row[0] if row else "unknown"
    except Exception as e:
        return f"error: {e}"


def get_pending_count():
    """Count pending measurements in store-and-forward buffer."""
    try:
        conn = sqlite3.connect("/home/kali/Desktop/DB/StoredMeasurements.db", timeout=10)
        c = conn.cursor()
        row = c.execute("SELECT COUNT(*) FROM PendingMeasurements").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return -1


def wait_for_tech(target_tech, timeout_sec=600):
    """
    Wait until the sensor's upload technology changes to the target.
    Returns (success, elapsed_seconds, tech_found).
    """
    start = time.monotonic()
    start_utc = datetime.datetime.utcnow()
    
    while (time.monotonic() - start) < timeout_sec:
        current = get_current_upload_tech()
        if current == target_tech:
            elapsed = time.monotonic() - start
            return True, elapsed, current
        time.sleep(2)  # poll every 2 seconds
    
    elapsed = time.monotonic() - start
    return False, elapsed, get_current_upload_tech()


def wait_for_any_change(original_tech, timeout_sec=600):
    """
    Wait until the upload technology changes FROM the original.
    Returns (success, elapsed_seconds, new_tech).
    """
    start = time.monotonic()
    
    while (time.monotonic() - start) < timeout_sec:
        current = get_current_upload_tech()
        if current != original_tech:
            elapsed = time.monotonic() - start
            return True, elapsed, current
        time.sleep(2)
    
    elapsed = time.monotonic() - start
    return False, elapsed, get_current_upload_tech()


def extract_handover_events(since_utc_str):
    """Extract handover-related events from the log since a given timestamp."""
    events = read_events(event_filter=None, since_utc=since_utc_str)
    relevant_types = {
        "connectivity_check", "handover_triggered", "handover_complete",
        "handover_deferred", "connectivity_ok", "boot_handover_complete"
    }
    return [e for e in events if e.get("event") in relevant_types]


def run_wifi_to_lora_test(run_number, total_runs, check_interval_sec):
    """
    Test: Wi-Fi → LoRa handover.
    
    Precondition: sensor is currently on Wi-Fi.
    Action: operator disconnects AP.
    Measurement: time from AP disconnect to LoRa becoming active.
    """
    print(f"\n{'='*60}")
    print(f"  RUN {run_number}/{total_runs} — Wi-Fi → LoRa Handover")
    print(f"{'='*60}")
    
    # Verify precondition
    current = get_current_upload_tech()
    if current != "wifi":
        print(f"  [!] Sensor is on '{current}', not 'wifi'. Skipping this run.")
        return None
    
    pending_before = get_pending_count()
    print(f"  Current tech: {current}")
    print(f"  Pending measurements: {pending_before}")
    print(f"  Check interval: {check_interval_sec}s")
    print(f"  Max expected handover: ~{check_interval_sec + 30}s")
    print()
    
    # Prompt operator
    input("  >>> PRESS ENTER IMMEDIATELY AFTER DISCONNECTING THE WI-FI AP <<<")
    
    # T0: operator disconnected AP
    t0_mono = time.monotonic()
    t0_utc = datetime.datetime.utcnow()
    t0_utc_str = t0_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    log_event("test_ap_disconnected",
              test="wifi_to_lora",
              run=run_number,
              t0_utc=t0_utc_str)
    
    print(f"  T0 = {t0_utc_str}")
    print(f"  Waiting for handover to LoRa (timeout: {check_interval_sec + 120}s)...")
    
    # Wait for technology change
    success, elapsed, new_tech = wait_for_tech("lora", timeout_sec=check_interval_sec + 120)
    
    t1_utc = datetime.datetime.utcnow()
    t1_utc_str = t1_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    pending_after = get_pending_count()
    
    # Extract detailed events
    handover_events = extract_handover_events(t0_utc_str)
    
    # Find T_detect and T_handover from events
    t_detect_event = None
    t_handover_event = None
    for ev in handover_events:
        if ev["event"] == "handover_triggered" and ev.get("reason") == "wifi_lost":
            t_detect_event = ev
        if ev["event"] == "handover_complete" and ev.get("to_link") == "lora":
            t_handover_event = ev
    
    result = {
        "run": run_number,
        "test": "wifi_to_lora",
        "t0_utc": t0_utc_str,
        "t1_utc": t1_utc_str,
        "success": success,
        "t_total_sec": round(elapsed, 2),
        "new_tech": new_tech,
        "check_interval_sec": check_interval_sec,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "handover_events": handover_events,
    }
    
    # Decompose timing if events are available
    if t_detect_event and t_handover_event:
        result["t_detect_utc"] = t_detect_event.get("timestamp_utc")
        result["t_handover_duration_ms"] = t_handover_event.get("duration_ms")
        
        # Calculate T_detect from T0 to handover_triggered event
        detect_utc = datetime.datetime.strptime(
            t_detect_event["timestamp_utc"].replace("Z", ""),
            "%Y-%m-%dT%H:%M:%S.%f"
        )
        t_detect_sec = (detect_utc - t0_utc).total_seconds()
        result["t_detect_sec"] = round(t_detect_sec, 2)
        
        if t_handover_event.get("duration_ms"):
            result["t_handover_sec"] = round(t_handover_event["duration_ms"] / 1000, 2)
    
    log_event("test_run_complete",
              test="wifi_to_lora",
              run=run_number,
              success=success,
              t_total_sec=result["t_total_sec"],
              new_tech=new_tech)
    
    # Print summary
    if success:
        print(f"  ✓ Handover complete in {result['t_total_sec']:.1f}s")
        if "t_detect_sec" in result:
            print(f"    T_detect  = {result['t_detect_sec']:.1f}s (time to detect Wi-Fi loss)")
            print(f"    T_handover = {result.get('t_handover_sec', 'N/A')}s (LoRa join + switch)")
        print(f"    Pending measurements: {pending_before} → {pending_after}")
    else:
        print(f"  ✗ Handover FAILED after {result['t_total_sec']:.1f}s")
        print(f"    Current tech: {new_tech}")
    
    return result


def run_lora_to_wifi_test(run_number, total_runs, check_interval_sec):
    """
    Test: LoRa → Wi-Fi fallback reversal (probe-back).
    
    Precondition: sensor is currently on LoRa.
    Action: operator reconnects AP.
    Measurement: time from AP reconnect to Wi-Fi becoming active.
    """
    print(f"\n{'='*60}")
    print(f"  RUN {run_number}/{total_runs} — LoRa → Wi-Fi Probe-back")
    print(f"{'='*60}")
    
    current = get_current_upload_tech()
    if current != "lora":
        print(f"  [!] Sensor is on '{current}', not 'lora'. Skipping this run.")
        return None
    
    pending_before = get_pending_count()
    print(f"  Current tech: {current}")
    print(f"  Pending measurements: {pending_before}")
    print()
    
    input("  >>> PRESS ENTER IMMEDIATELY AFTER RECONNECTING THE WI-FI AP <<<")
    
    t0_mono = time.monotonic()
    t0_utc = datetime.datetime.utcnow()
    t0_utc_str = t0_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    log_event("test_ap_reconnected",
              test="lora_to_wifi",
              run=run_number,
              t0_utc=t0_utc_str)
    
    print(f"  T0 = {t0_utc_str}")
    print(f"  Waiting for probe-back to Wi-Fi (timeout: {check_interval_sec + 120}s)...")
    
    success, elapsed, new_tech = wait_for_tech("wifi", timeout_sec=check_interval_sec + 120)
    
    t1_utc = datetime.datetime.utcnow()
    t1_utc_str = t1_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    pending_after = get_pending_count()
    handover_events = extract_handover_events(t0_utc_str)
    
    result = {
        "run": run_number,
        "test": "lora_to_wifi",
        "t0_utc": t0_utc_str,
        "t1_utc": t1_utc_str,
        "success": success,
        "t_total_sec": round(elapsed, 2),
        "new_tech": new_tech,
        "check_interval_sec": check_interval_sec,
        "pending_before": pending_before,
        "pending_after": pending_after,
        "handover_events": handover_events,
    }
    
    # Decompose
    t_detect_event = None
    t_handover_event = None
    for ev in handover_events:
        if ev["event"] == "handover_triggered" and ev.get("reason") == "wifi_available":
            t_detect_event = ev
        if ev["event"] == "handover_complete" and ev.get("to_link") == "wifi":
            t_handover_event = ev
    
    if t_detect_event:
        detect_utc = datetime.datetime.strptime(
            t_detect_event["timestamp_utc"].replace("Z", ""),
            "%Y-%m-%dT%H:%M:%S.%f"
        )
        result["t_detect_sec"] = round((detect_utc - t0_utc).total_seconds(), 2)
    
    if t_handover_event and t_handover_event.get("duration_ms"):
        result["t_handover_sec"] = round(t_handover_event["duration_ms"] / 1000, 2)
    
    log_event("test_run_complete",
              test="lora_to_wifi",
              run=run_number,
              success=success,
              t_total_sec=result["t_total_sec"])
    
    if success:
        print(f"  ✓ Probe-back complete in {result['t_total_sec']:.1f}s")
        if "t_detect_sec" in result:
            print(f"    T_detect  = {result['t_detect_sec']:.1f}s")
        print(f"    Pending: {pending_before} → {pending_after}")
    else:
        print(f"  ✗ Probe-back FAILED after {result['t_total_sec']:.1f}s")
    
    return result


def run_full_cycle_test(run_number, total_runs, check_interval_sec):
    """
    Full cycle: Wi-Fi → LoRa → Wi-Fi.
    Operator disconnects AP, waits for LoRa, reconnects AP, waits for Wi-Fi.
    Measures both directions in one run.
    """
    print(f"\n{'='*60}")
    print(f"  RUN {run_number}/{total_runs} — Full Cycle (Wi-Fi → LoRa → Wi-Fi)")
    print(f"{'='*60}")
    
    current = get_current_upload_tech()
    if current != "wifi":
        print(f"  [!] Sensor is on '{current}', not 'wifi'. Skipping.")
        return None
    
    # Phase 1: Wi-Fi → LoRa
    result_1 = run_wifi_to_lora_test(run_number, total_runs, check_interval_sec)
    
    if result_1 is None or not result_1.get("success"):
        print("  [!] Phase 1 failed, cannot continue full cycle")
        return {"phase1": result_1, "phase2": None}
    
    print(f"\n  Stabilizing on LoRa for {STABILIZATION_WAIT_SEC}s...")
    time.sleep(STABILIZATION_WAIT_SEC)
    
    # Phase 2: LoRa → Wi-Fi
    result_2 = run_lora_to_wifi_test(run_number, total_runs, check_interval_sec)
    
    return {"phase1": result_1, "phase2": result_2}


def save_results(results, test_type, check_interval_sec):
    """Save test results as JSON and CSV."""
    timestamp_str = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_dir = "/home/kali/Desktop/logs"
    
    # JSON with full detail
    json_path = os.path.join(log_dir, f"test_{test_type}_{timestamp_str}.json")
    metadata = {
        "test_type": test_type,
        "check_interval_sec": check_interval_sec,
        "total_runs": len(results),
        "successful_runs": sum(1 for r in results if r and r.get("success", False)),
        "timestamp_utc": timestamp_str,
        "results": results,
    }
    with open(json_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    print(f"\n  Results (JSON): {json_path}")
    
    # CSV summary (for quick analysis and import into Excel/R)
    csv_path = os.path.join(log_dir, f"test_{test_type}_{timestamp_str}.csv")
    valid_results = [r for r in results if r is not None]
    
    if valid_results:
        fieldnames = ["run", "test", "success", "t_total_sec", "t_detect_sec",
                       "t_handover_sec", "check_interval_sec", "pending_before",
                       "pending_after", "new_tech"]
        
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for r in valid_results:
                writer.writerow(r)
        print(f"  Results (CSV):  {csv_path}")
    
    return json_path, csv_path


def main():
    parser = argparse.ArgumentParser(description="CrowdSensor Handover Test Orchestration")
    parser.add_argument("--test", choices=["wifi_to_lora", "lora_to_wifi", "full_cycle"],
                        required=True, help="Type of handover test")
    parser.add_argument("--runs", type=int, default=15,
                        help="Number of test runs (default: 15)")
    parser.add_argument("--check-interval", type=int, default=DEFAULT_CHECK_INTERVAL_SEC,
                        help="Connectivity check interval in seconds (default: 300)")
    
    args = parser.parse_args()
    
    print(f"\n{'#'*60}")
    print(f"  CrowdSensor Handover Test — {args.test}")
    print(f"  Runs: {args.runs}  |  Check interval: {args.check_interval}s")
    print(f"{'#'*60}")
    
    # Rotate log for clean test
    rotated = rotate_log(suffix=f"pre_{args.test}")
    if rotated:
        print(f"  Previous log rotated to: {rotated}")
    
    log_event("test_session_start",
              test_type=args.test,
              planned_runs=args.runs,
              check_interval_sec=args.check_interval)
    
    results = []
    
    for run_num in range(1, args.runs + 1):
        if args.test == "wifi_to_lora":
            result = run_wifi_to_lora_test(run_num, args.runs, args.check_interval)
        elif args.test == "lora_to_wifi":
            result = run_lora_to_wifi_test(run_num, args.runs, args.check_interval)
        elif args.test == "full_cycle":
            result = run_full_cycle_test(run_num, args.runs, args.check_interval)
        
        results.append(result)
        
        if run_num < args.runs:
            print(f"\n  --- Stabilization pause ({STABILIZATION_WAIT_SEC}s) before next run ---")
            if args.test == "wifi_to_lora":
                print("  >>> Reconnect the AP now and wait for probe-back before next run <<<")
                input("  >>> Press ENTER when sensor is back on Wi-Fi <<<")
                # Verify
                time.sleep(5)
                current = get_current_upload_tech()
                if current != "wifi":
                    print(f"  [!] Sensor is on '{current}', waiting for Wi-Fi...")
                    wait_for_tech("wifi", timeout_sec=args.check_interval + 120)
                time.sleep(STABILIZATION_WAIT_SEC)
            
            elif args.test == "lora_to_wifi":
                print("  >>> Disconnect the AP now and wait for LoRa handover <<<")
                input("  >>> Press ENTER when sensor is on LoRa <<<")
                time.sleep(5)
                current = get_current_upload_tech()
                if current != "lora":
                    print(f"  [!] Sensor is on '{current}', waiting for LoRa...")
                    wait_for_tech("lora", timeout_sec=args.check_interval + 120)
                time.sleep(STABILIZATION_WAIT_SEC)
    
    # Save results
    log_event("test_session_complete",
              test_type=args.test,
              completed_runs=len([r for r in results if r is not None]))
    
    json_path, csv_path = save_results(results, args.test, args.check_interval)
    
    # Print summary
    valid = [r for r in results if r is not None]
    successful = [r for r in valid if r.get("success")]
    
    print(f"\n{'='*60}")
    print(f"  TEST SESSION COMPLETE")
    print(f"  Total runs: {len(results)}")
    print(f"  Successful: {len(successful)}/{len(valid)}")
    
    if successful:
        totals = [r["t_total_sec"] for r in successful]
        print(f"  T_total:  mean={sum(totals)/len(totals):.1f}s  "
              f"min={min(totals):.1f}s  max={max(totals):.1f}s")
        
        detects = [r["t_detect_sec"] for r in successful if "t_detect_sec" in r]
        if detects:
            print(f"  T_detect: mean={sum(detects)/len(detects):.1f}s  "
                  f"min={min(detects):.1f}s  max={max(detects):.1f}s")
        
        handovers = [r["t_handover_sec"] for r in successful if "t_handover_sec" in r]
        if handovers:
            print(f"  T_handover: mean={sum(handovers)/len(handovers):.1f}s  "
                  f"min={min(handovers):.1f}s  max={max(handovers):.1f}s")
    
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
