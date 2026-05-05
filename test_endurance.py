#!/usr/bin/env python3
"""
test_endurance.py — 24-hour continuous operation monitoring.

Runs ON the Raspberry Pi as a background daemon. Monitors the sensor's
state every 60 seconds and logs metrics for trend analysis.

Usage:
    nohup python3 test_endurance.py --duration-hours 24 > /dev/null 2>&1 &
    python3 test_endurance.py --duration-hours 12

What it monitors (every 60s):
    - Current upload technology
    - Pending measurements count
    - SQLite DB file sizes
    - System memory usage
    - NTP offset (clock drift)
    - Link state (wifi connected, lora connected)

Output:
    - /home/kali/Desktop/logs/test_endurance_<timestamp>.csv
    - /home/kali/Desktop/logs/test_endurance_<timestamp>.json (summary)

For the IEEE article:
    - Use the CSV to plot MDR in 1-hour windows
    - Plot buffer size over time
    - Plot memory usage (detect leaks)
    - Report NTP drift statistics
"""

import argparse
import csv
import datetime
import json
import os
import subprocess
import sqlite3
import sys
import time

sys.path.insert(0, "/home/kali/Desktop")

from event_logger import log_event, rotate_log

DB_CONFIG_PATH = "/home/kali/Desktop/DB/SensorConfiguration.db"
DB_STORED_PATH = "/home/kali/Desktop/DB/StoredMeasurements.db"
DB_DEVICES_PATH = "/home/kali/Desktop/MemoryDB/DeviceRecords.db"


def get_upload_tech():
    try:
        conn = sqlite3.connect(DB_CONFIG_PATH, timeout=5)
        c = conn.cursor()
        row = c.execute("SELECT Upload_Technology FROM SensorConfiguration").fetchone()
        conn.close()
        return row[0] if row else "unknown"
    except Exception:
        return "db_error"


def get_comm_state():
    try:
        conn = sqlite3.connect(DB_CONFIG_PATH, timeout=5)
        c = conn.cursor()
        row = c.execute(
            "SELECT WifiConnected, LoRaConnected, Upload_Interface FROM SensorCommunication"
        ).fetchone()
        conn.close()
        if row:
            return {
                "wifi_connected": bool(row[0]),
                "lora_connected": bool(row[1]),
                "upload_interface": row[2],
            }
    except Exception:
        pass
    return {"wifi_connected": False, "lora_connected": False, "upload_interface": "unknown"}


def get_pending_count():
    try:
        conn = sqlite3.connect(DB_STORED_PATH, timeout=5)
        c = conn.cursor()
        row = c.execute("SELECT COUNT(*) FROM PendingMeasurements").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return -1


def get_db_sizes():
    sizes = {}
    for name, path in [
        ("config_db_kb", DB_CONFIG_PATH),
        ("stored_db_kb", DB_STORED_PATH),
        ("devices_db_kb", DB_DEVICES_PATH),
    ]:
        try:
            sizes[name] = round(os.path.getsize(path) / 1024, 1)
        except Exception:
            sizes[name] = -1
    return sizes


def get_memory_usage():
    """Get system memory stats from /proc/meminfo."""
    try:
        with open("/proc/meminfo", "r") as f:
            info = {}
            for line in f:
                parts = line.split()
                if parts[0] in ("MemTotal:", "MemAvailable:", "MemFree:"):
                    info[parts[0].rstrip(":")] = int(parts[1])  # kB
        
        total = info.get("MemTotal", 0)
        available = info.get("MemAvailable", 0)
        used = total - available
        usage_pct = round(used / total * 100, 1) if total > 0 else 0
        
        return {
            "mem_total_mb": round(total / 1024, 1),
            "mem_used_mb": round(used / 1024, 1),
            "mem_available_mb": round(available / 1024, 1),
            "mem_usage_pct": usage_pct,
        }
    except Exception:
        return {"mem_total_mb": 0, "mem_used_mb": 0, "mem_available_mb": 0, "mem_usage_pct": 0}


def get_ntp_offset_ms():
    """Get NTP clock offset. Returns offset in ms or None if unavailable."""
    try:
        output = subprocess.check_output(
            ["ntpdate", "-q", "pool.ntp.org"],
            stderr=subprocess.STDOUT,
            timeout=10
        ).decode("utf-8")
        
        # Parse line like: "server ... offset 0.001234 sec"
        for line in output.splitlines():
            if "offset" in line:
                parts = line.split("offset")
                if len(parts) > 1:
                    offset_str = parts[1].strip().split()[0]
                    return round(float(offset_str) * 1000, 2)  # convert to ms
    except Exception:
        pass
    
    # Fallback: try timedatectl
    try:
        output = subprocess.check_output(
            ["timedatectl", "show", "--property=NTPSynchronized"],
            timeout=5
        ).decode("utf-8").strip()
        # Can't get exact offset from timedatectl, just sync status
    except Exception:
        pass
    
    return None


def get_cpu_temp():
    """Get CPU temperature in Celsius."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000, 1)
    except Exception:
        return None


def get_uptime_seconds():
    try:
        with open("/proc/uptime", "r") as f:
            return round(float(f.read().split()[0]), 0)
    except Exception:
        return None


def collect_sample(sample_num, start_utc):
    """Collect one monitoring sample."""
    now_utc = datetime.datetime.utcnow()
    elapsed_min = (now_utc - start_utc).total_seconds() / 60
    
    tech = get_upload_tech()
    comm = get_comm_state()
    pending = get_pending_count()
    db_sizes = get_db_sizes()
    mem = get_memory_usage()
    cpu_temp = get_cpu_temp()
    uptime = get_uptime_seconds()
    
    sample = {
        "sample": sample_num,
        "timestamp_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%S") + "Z",
        "elapsed_min": round(elapsed_min, 1),
        "upload_tech": tech,
        "wifi_connected": comm["wifi_connected"],
        "lora_connected": comm["lora_connected"],
        "pending_count": pending,
        "mem_usage_pct": mem["mem_usage_pct"],
        "mem_used_mb": mem["mem_used_mb"],
        "cpu_temp_c": cpu_temp,
        "uptime_sec": uptime,
    }
    sample.update(db_sizes)
    
    return sample


def main():
    parser = argparse.ArgumentParser(description="24h Endurance Monitoring")
    parser.add_argument("--duration-hours", type=float, default=24,
                        help="Test duration in hours (default: 24)")
    parser.add_argument("--sample-interval", type=int, default=60,
                        help="Sampling interval in seconds (default: 60)")
    parser.add_argument("--ntp-check-interval", type=int, default=3600,
                        help="NTP offset check interval in seconds (default: 3600)")
    
    args = parser.parse_args()
    
    ts_str = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_dir = "/home/kali/Desktop/logs"
    os.makedirs(log_dir, exist_ok=True)
    
    csv_path = os.path.join(log_dir, f"test_endurance_{ts_str}.csv")
    json_path = os.path.join(log_dir, f"test_endurance_{ts_str}.json")
    
    print(f"\n{'#'*60}")
    print(f"  Endurance Test — {args.duration_hours}h")
    print(f"  Sample interval: {args.sample_interval}s")
    print(f"  Output: {csv_path}")
    print(f"{'#'*60}\n")
    
    rotate_log(suffix=f"pre_endurance")
    
    log_event("test_endurance_start",
              duration_hours=args.duration_hours,
              sample_interval_sec=args.sample_interval)
    
    start_utc = datetime.datetime.utcnow()
    duration_sec = args.duration_hours * 3600
    
    # NTP baseline
    ntp_offset = get_ntp_offset_ms()
    ntp_samples = []
    if ntp_offset is not None:
        ntp_samples.append({"elapsed_min": 0, "offset_ms": ntp_offset})
        print(f"  NTP baseline offset: {ntp_offset}ms")
    
    # CSV setup
    csv_fields = [
        "sample", "timestamp_utc", "elapsed_min", "upload_tech",
        "wifi_connected", "lora_connected", "pending_count",
        "mem_usage_pct", "mem_used_mb", "cpu_temp_c", "uptime_sec",
        "config_db_kb", "stored_db_kb", "devices_db_kb",
    ]
    
    csv_file = open(csv_path, "w", newline="")
    csv_writer = csv.DictWriter(csv_file, fieldnames=csv_fields, extrasaction="ignore")
    csv_writer.writeheader()
    csv_file.flush()
    
    sample_num = 0
    last_ntp_check = time.monotonic()
    tech_changes = []
    last_tech = get_upload_tech()
    
    try:
        while True:
            elapsed = (datetime.datetime.utcnow() - start_utc).total_seconds()
            if elapsed >= duration_sec:
                break
            
            sample_num += 1
            sample = collect_sample(sample_num, start_utc)
            
            # Detect technology changes
            if sample["upload_tech"] != last_tech:
                change = {
                    "elapsed_min": sample["elapsed_min"],
                    "from": last_tech,
                    "to": sample["upload_tech"],
                    "timestamp_utc": sample["timestamp_utc"],
                }
                tech_changes.append(change)
                log_event("endurance_tech_change", **change)
                print(f"  [{sample['elapsed_min']:.0f}min] Tech change: "
                      f"{last_tech} → {sample['upload_tech']}")
                last_tech = sample["upload_tech"]
            
            # Write CSV
            csv_writer.writerow(sample)
            csv_file.flush()
            
            # Periodic NTP check
            if (time.monotonic() - last_ntp_check) >= args.ntp_check_interval:
                ntp_offset = get_ntp_offset_ms()
                if ntp_offset is not None:
                    ntp_samples.append({
                        "elapsed_min": round(elapsed / 60, 1),
                        "offset_ms": ntp_offset,
                    })
                    log_event("ntp_check", offset_ms=ntp_offset)
                last_ntp_check = time.monotonic()
            
            # Progress indicator (every 10 samples)
            if sample_num % 10 == 0:
                hours_left = (duration_sec - elapsed) / 3600
                print(f"  [{sample['elapsed_min']:.0f}min] "
                      f"tech={sample['upload_tech']} "
                      f"pending={sample['pending_count']} "
                      f"mem={sample['mem_usage_pct']}% "
                      f"temp={sample['cpu_temp_c']}°C "
                      f"({hours_left:.1f}h left)")
            
            time.sleep(args.sample_interval)
    
    except KeyboardInterrupt:
        print("\n  Interrupted by user.")
    
    finally:
        csv_file.close()
    
    # Final NTP check
    final_ntp = get_ntp_offset_ms()
    if final_ntp is not None:
        elapsed_min = (datetime.datetime.utcnow() - start_utc).total_seconds() / 60
        ntp_samples.append({"elapsed_min": round(elapsed_min, 1), "offset_ms": final_ntp})
    
    # Save JSON summary
    summary = {
        "test_type": "endurance",
        "duration_hours": args.duration_hours,
        "actual_duration_min": round(
            (datetime.datetime.utcnow() - start_utc).total_seconds() / 60, 1
        ),
        "total_samples": sample_num,
        "sample_interval_sec": args.sample_interval,
        "tech_changes": tech_changes,
        "ntp_samples": ntp_samples,
        "csv_path": csv_path,
    }
    
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)
    
    log_event("test_endurance_complete",
              samples=sample_num,
              tech_changes=len(tech_changes))
    
    print(f"\n{'='*60}")
    print(f"  ENDURANCE TEST COMPLETE")
    print(f"  Duration: {summary['actual_duration_min']:.0f} min")
    print(f"  Samples: {sample_num}")
    print(f"  Tech changes: {len(tech_changes)}")
    print(f"  NTP drift range: "
          f"{min(s['offset_ms'] for s in ntp_samples):.1f}ms to "
          f"{max(s['offset_ms'] for s in ntp_samples):.1f}ms"
          if ntp_samples else "  NTP: no data")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {json_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
