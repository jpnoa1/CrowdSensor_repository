#!/usr/bin/env python3
"""
test_store_forward.py — Store-and-Forward reliability test.

Tests buffering during connectivity loss and replay after recovery.
Measures: MDR, duplicate count, temporal ordering, replay latency.

Runs ON the Raspberry Pi. Operator manually disconnects/reconnects AP.

Usage:
    python3 test_store_forward.py --outage-minutes 15 --runs 5
    python3 test_store_forward.py --outage-minutes 30 --runs 3
    python3 test_store_forward.py --outage-minutes 60 --runs 3

Prerequisites:
    1. event_logger.py and uart_lock.py deployed
    2. Patches applied to sendCrowdingData.py and communication_manager.py
    3. Sensor running normally on Wi-Fi
    4. Access to backend InfluxDB/MQTT to verify received messages

Output:
    - /home/kali/Desktop/logs/test_sf_<outage>min_<timestamp>.json
    - /home/kali/Desktop/logs/test_sf_<outage>min_<timestamp>.csv
"""

import argparse
import json
import os
import sys
import time
import datetime
import csv
import sqlite3

sys.path.insert(0, "/home/kali/Desktop")

from event_logger import log_event, read_events, rotate_log

DB_CONFIG_PATH = "/home/kali/Desktop/DB/SensorConfiguration.db"
DB_STORED_PATH = "/home/kali/Desktop/DB/StoredMeasurements.db"


def get_current_upload_tech():
    try:
        conn = sqlite3.connect(DB_CONFIG_PATH, timeout=10)
        c = conn.cursor()
        row = c.execute("SELECT Upload_Technology FROM SensorConfiguration").fetchone()
        conn.close()
        return row[0] if row else "unknown"
    except Exception as e:
        return f"error: {e}"


def get_pending_measurements():
    """Get all pending measurements from the store-and-forward buffer."""
    try:
        conn = sqlite3.connect(DB_STORED_PATH, timeout=10)
        c = conn.cursor()
        rows = c.execute(
            "SELECT Timestamp, DevicesDetected FROM PendingMeasurements ORDER BY Timestamp ASC"
        ).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_pending_count():
    rows = get_pending_measurements()
    return len(rows)


def wait_for_tech(target, timeout_sec=600):
    start = time.monotonic()
    while (time.monotonic() - start) < timeout_sec:
        current = get_current_upload_tech()
        if current == target:
            return True, time.monotonic() - start
        time.sleep(2)
    return False, time.monotonic() - start


def snapshot_pending():
    """Take a snapshot of pending measurements with their timestamps."""
    rows = get_pending_measurements()
    return [{"unix_ts": r[0], "devices": r[1]} for r in rows]


def check_temporal_ordering(measurements):
    """Verify that measurements are in strictly ascending temporal order."""
    if len(measurements) < 2:
        return True, []
    
    violations = []
    for i in range(1, len(measurements)):
        if measurements[i]["unix_ts"] <= measurements[i - 1]["unix_ts"]:
            violations.append({
                "index": i,
                "prev_ts": measurements[i - 1]["unix_ts"],
                "curr_ts": measurements[i]["unix_ts"],
            })
    return len(violations) == 0, violations


def check_duplicates(measurements):
    """Check for duplicate timestamps in measurements."""
    seen = {}
    duplicates = []
    for m in measurements:
        ts = m["unix_ts"]
        if ts in seen:
            duplicates.append(ts)
        seen[ts] = seen.get(ts, 0) + 1
    return duplicates



def run_store_forward_test(run_number, total_runs, outage_minutes, upload_periodicity_min=5, precision=6):
    """
    Single store-and-forward test run.
    
    Flow:
    1. Verify sensor is on Wi-Fi, buffer is empty
    2. Operator disconnects AP → sensor falls to LoRa or 'none'
    3. Wait for outage_minutes (sensor generates and buffers measurements)
    4. Operator reconnects AP → sensor returns to Wi-Fi
    5. Measure: buffer drain time, MDR, duplicates, ordering
    """
    print(f"\n{'='*60}")
    print(f"  RUN {run_number}/{total_runs} — Store-and-Forward ({outage_minutes}min outage)")
    print(f"{'='*60}")
    
    # Step 1: Verify preconditions
    current_tech = get_current_upload_tech()
    initial_pending = get_pending_count()
    
    print(f"  Current tech: {current_tech}")
    print(f"  Initial pending: {initial_pending}")
    
    if current_tech != "wifi":
        print(f"  [!] Sensor not on Wi-Fi ('{current_tech}'). Wait for Wi-Fi first.")
        return None
    
    expected_messages = outage_minutes // upload_periodicity_min
    print(f"  Outage duration: {outage_minutes} min")
    print(f"  Upload periodicity: {upload_periodicity_min} min")
    print(f"  Expected buffered messages: ~{expected_messages}")
    print()
    
    # Step 2: Operator disconnects AP
    input("  >>> DISCONNECT THE WI-FI AP, THEN PRESS ENTER <<<")
    
    t_outage_start = time.monotonic()
    t_outage_start_utc = datetime.datetime.utcnow()
    t_outage_start_str = t_outage_start_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    log_event("test_sf_outage_start",
              run=run_number,
              outage_minutes=outage_minutes,
              initial_pending=initial_pending,
              t0_utc=t_outage_start_str)
    
    print(f"  Outage started at: {t_outage_start_str}")
    print(f"  Waiting {outage_minutes} minutes for measurements to accumulate...")
    print(f"  (Sensor will buffer locally during this time)")
    print()
    
    # Step 3: Wait for the outage period, monitoring buffer growth
    buffer_snapshots = []
    outage_seconds = outage_minutes * 60
    check_interval = 30  # check buffer every 30s
    
    elapsed = 0
    while elapsed < outage_seconds:
        remaining = outage_seconds - elapsed
        wait_chunk = min(check_interval, remaining)
        time.sleep(wait_chunk)
        elapsed = time.monotonic() - t_outage_start
        
        count = get_pending_count()
        buffer_snapshots.append({
            "elapsed_sec": round(elapsed, precision),
            "pending_count": count,
        })
        
        minutes_left = max(0, (outage_seconds - elapsed) / 60)
        print(f"    [{(elapsed/60):.{precision}f}min] Buffer: {count} messages | "
              f"{minutes_left:.{precision}f}min remaining", end="\r")
    
    print()
    
    # Snapshot buffer BEFORE reconnection
    pre_reconnect_pending = snapshot_pending()
    pre_reconnect_count = len(pre_reconnect_pending)
    
    print(f"\n  Outage complete. Buffer has {pre_reconnect_count} messages.")
    
    # Check temporal ordering in buffer
    ordering_ok, ordering_violations = check_temporal_ordering(pre_reconnect_pending)
    if ordering_ok:
        print(f"  Buffer temporal ordering: OK ✓")
    else:
        print(f"  Buffer temporal ordering: VIOLATED ✗ ({len(ordering_violations)} violations)")
    
    # Check duplicates in buffer
    buffer_duplicates = check_duplicates(pre_reconnect_pending)
    if not buffer_duplicates:
        print(f"  Buffer duplicates: None ✓")
    else:
        print(f"  Buffer duplicates: {len(buffer_duplicates)} found ✗")
    
    log_event("test_sf_pre_reconnect",
              run=run_number,
              buffered_messages=pre_reconnect_count,
              ordering_ok=ordering_ok,
              duplicates=len(buffer_duplicates))
    
    # Step 4: Operator reconnects AP
    print()
    input("  >>> RECONNECT THE WI-FI AP, THEN PRESS ENTER <<<")
    
    t_reconnect = time.monotonic()
    t_reconnect_utc = datetime.datetime.utcnow()
    t_reconnect_str = t_reconnect_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    log_event("test_sf_ap_reconnected",
              run=run_number,
              t_reconnect_utc=t_reconnect_str,
              buffered_messages=pre_reconnect_count)
    
    print(f"  AP reconnected at: {t_reconnect_str}")
    print(f"  Waiting for Wi-Fi handover...")
    
    # Wait for Wi-Fi to come back
    wifi_ok, wifi_wait = wait_for_tech("wifi", timeout_sec=420)
    
    if not wifi_ok:
        print(f"  [!] Wi-Fi did not recover after {wifi_wait:.0f}s. Test incomplete.")
        log_event("test_sf_wifi_recovery_failed", run=run_number)
        return {
            "run": run_number,
            "outage_minutes": outage_minutes,
            "success": False,
            "reason": "wifi_recovery_failed",
            "buffered_messages": pre_reconnect_count,
        }
    
    t_wifi_back = time.monotonic()
    print(f"  Wi-Fi recovered in {wifi_wait:.{precision}f}s")
    
    # Step 5: Monitor buffer drain (replay)
    print(f"  Monitoring replay (buffer drain)...")
    
    replay_timeout = 300  # 5 minutes max for replay
    replay_start = time.monotonic()
    drain_snapshots = []
    
    while (time.monotonic() - replay_start) < replay_timeout:
        count = get_pending_count()
        drain_snapshots.append({
            "elapsed_sec": round(time.monotonic() - replay_start, precision),
            "pending_count": count,
        })
        
        if count == 0:
            break
        
        print(f"    Draining... {count} remaining", end="\r")
        time.sleep(5)
    
    print()
    
    t_drain_complete = time.monotonic()
    final_pending = get_pending_count()
    replay_duration_sec = round(t_drain_complete - replay_start, precision)
    
    # Calculate results
    messages_delivered = pre_reconnect_count - final_pending
    mdr = messages_delivered / pre_reconnect_count if pre_reconnect_count > 0 else 1.0
    
    log_event("test_sf_replay_complete",
              run=run_number,
              buffered_messages=pre_reconnect_count,
              delivered=messages_delivered,
              remaining=final_pending,
              mdr=round(mdr, precision),
              replay_duration_sec=replay_duration_sec)
    
    result = {
        "run": run_number,
        "outage_minutes": outage_minutes,
        "success": True,
        "t_outage_start_utc": t_outage_start_str,
        "t_reconnect_utc": t_reconnect_str,
        "t_wifi_recovery_sec": round(wifi_wait, 2),
        "buffered_messages": pre_reconnect_count,
        "expected_messages": expected_messages,
        "messages_delivered": messages_delivered,
        "messages_remaining": final_pending,
        "mdr": round(mdr, precision),
        "replay_duration_sec": replay_duration_sec,
        "replay_throughput_msg_per_sec": round(
            messages_delivered / replay_duration_sec, precision
        ) if replay_duration_sec > 0 else 0,
        "buffer_ordering_ok": ordering_ok,
        "buffer_ordering_violations": len(ordering_violations) if not ordering_ok else 0,
        "buffer_duplicates": len(buffer_duplicates),
        "buffer_growth_snapshots": buffer_snapshots,
        "drain_snapshots": drain_snapshots,
    }
    
    # Summary
    print(f"\n  ── Results ──")
    print(f"  Buffered: {pre_reconnect_count} messages")
    print(f"  Delivered: {messages_delivered}")
    print(f"  MDR: {mdr * 100:.{precision}f}%")
    print(f"  Replay time: {replay_duration_sec:.{precision}f}s")
    print(f"  Remaining: {final_pending}")
    print(f"  Temporal ordering: {'OK ✓' if ordering_ok else 'VIOLATED ✗'}")
    print(f"  Duplicates in buffer: {len(buffer_duplicates)}")
    
    return result


def save_results(results, outage_minutes):
    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_dir = "/home/kali/Desktop/logs"
    os.makedirs(log_dir, exist_ok=True)
    
    # JSON
    json_path = os.path.join(log_dir, f"test_sf_{outage_minutes}min_{ts}.json")
    valid_results = [r for r in results if r is not None]
    
    metadata = {
        "test_type": "store_and_forward",
        "outage_minutes": outage_minutes,
        "total_runs": len(results),
        "successful_runs": sum(1 for r in valid_results if r.get("success")),
        "results": valid_results,
    }
    
    with open(json_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
    print(f"\n  JSON: {json_path}")
    
    # CSV
    csv_path = os.path.join(log_dir, f"test_sf_{outage_minutes}min_{ts}.csv")
    if valid_results:
        fields = [
            "run", "outage_minutes", "success", "buffered_messages",
            "expected_messages", "messages_delivered", "messages_remaining",
            "mdr", "replay_duration_sec", "replay_throughput_msg_per_sec",
            "t_wifi_recovery_sec", "buffer_ordering_ok", "buffer_duplicates",
        ]
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for r in valid_results:
                writer.writerow(r)
        print(f"  CSV:  {csv_path}")
    
    return json_path, csv_path


def main():
    parser = argparse.ArgumentParser(description="Store-and-Forward Reliability Test")
    parser.add_argument("--outage-minutes", type=int, required=True,
                        help="Duration of simulated outage in minutes")
    parser.add_argument("--runs", type=int, default=5,
                        help="Number of test runs (default: 5)")
    parser.add_argument("--upload-periodicity", type=int, default=5,
                        help="Upload periodicity in minutes (default: 5)")
    parser.add_argument("--precision", type=int, default=6,
                        help="Decimal places for numeric results (default: 6)")
    
    args = parser.parse_args()
    
    print(f"\n{'#'*60}")
    print(f"  Store-and-Forward Test — {args.outage_minutes}min outage")
    print(f"  Runs: {args.runs}  |  Upload periodicity: {args.upload_periodicity}min")
    print(f"{'#'*60}")
    
    rotated = rotate_log(suffix=f"pre_sf_{args.outage_minutes}min")
    if rotated:
        print(f"  Previous log rotated to: {rotated}")
    
    log_event("test_sf_session_start",
              outage_minutes=args.outage_minutes,
              planned_runs=args.runs)
    
    results = []
    
    for run_num in range(1, args.runs + 1):
        result = run_store_forward_test(
            run_num, args.runs, args.outage_minutes, args.upload_periodicity
            , precision=args.precision
        )
        results.append(result)
        
        if run_num < args.runs and result and result.get("success"):
            print(f"\n  Stabilization pause (120s) before next run...")
            time.sleep(120)
    
    save_results(results, args.outage_minutes)
    
    # Summary
    valid = [r for r in results if r and r.get("success")]
    print(f"\n{'='*60}")
    print(f"  SESSION COMPLETE: {len(valid)}/{len(results)} successful")
    
    if valid:
        mdrs = [r["mdr"] for r in valid]
        replays = [r["replay_duration_sec"] for r in valid]
        print(f"  MDR:     mean={sum(mdrs)/len(mdrs)*100:.1f}%  "
              f"min={min(mdrs)*100:.1f}%  max={max(mdrs)*100:.1f}%")
        print(f"  Replay:  mean={sum(replays)/len(replays):.1f}s  "
              f"min={min(replays):.1f}s  max={max(replays):.1f}s")
        
        all_ordered = all(r["buffer_ordering_ok"] for r in valid)
        all_no_dupes = all(r["buffer_duplicates"] == 0 for r in valid)
        print(f"  Ordering: {'ALL OK ✓' if all_ordered else 'VIOLATIONS FOUND ✗'}")
        print(f"  Duplicates: {'NONE ✓' if all_no_dupes else 'FOUND ✗'}")
    
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
