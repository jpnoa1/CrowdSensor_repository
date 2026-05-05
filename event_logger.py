"""
event_logger.py — Structured event logging for CrowdSensor test instrumentation.

Drop this file into /home/kali/Desktop/ alongside the other scripts.
Then import and use in any script:

    from event_logger import log_event

    log_event("connectivity_check", link="wifi", result="fail")
    log_event("handover_start", from_link="wifi", to_link="lora")
    log_event("handover_complete", to_link="lora", duration_ms=1523)
    log_event("message_sent", link="wifi", unix_ts=1234567890, devices=42)
    log_event("message_stored", unix_ts=1234567890, devices=42, reason="no_connectivity")
    log_event("replay_start", pending_count=5)
    log_event("replay_sent", unix_ts=1234567890, devices=42)
    log_event("replay_complete", replayed=5, failed=0)
    log_event("uart_lock_acquired", by="sendCrowdingData")
    log_event("uart_lock_blocked", by="sensorCommunicationCheck")

All events are written to /home/kali/Desktop/logs/comm_events.jsonl
Each line is a self-contained JSON object with UTC ISO-8601 timestamp.

Author: Test instrumentation for IEEE article
"""

import json
import os
import time
import datetime
import logging
import threading

# ─── Configuration ───────────────────────────────────────────────────────────

LOG_DIR = "/home/kali/Desktop/logs"
LOG_FILE = os.path.join(LOG_DIR, "comm_events.jsonl")

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Thread-safe write lock
_write_lock = threading.Lock()

# Monotonic reference for precise interval measurements
_boot_monotonic = time.monotonic()


def log_event(event_type: str, **kwargs):
    """
    Log a structured event to the JSONL file.
    
    Args:
        event_type: String identifying the event (e.g., "handover_start")
        **kwargs: Arbitrary key-value pairs to include in the event
    
    Each event automatically includes:
        - timestamp_utc: ISO-8601 UTC timestamp
        - timestamp_mono: monotonic clock value (for precise interval calculation)
        - event: the event_type string
        - pid: process ID of the caller
        - script: name of the calling script (from sys.argv[0])
    """
    import sys
    
    entry = {
        "timestamp_utc": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "timestamp_mono": round(time.monotonic(), 4),
        "event": event_type,
        "pid": os.getpid(),
        "script": os.path.basename(sys.argv[0]) if sys.argv else "unknown",
    }
    entry.update(kwargs)
    
    line = json.dumps(entry, separators=(",", ":")) + "\n"
    
    with _write_lock:
        try:
            with open(LOG_FILE, "a") as f:
                f.write(line)
        except Exception as e:
            # Fallback to stderr if file write fails
            import sys
            print(f"[EVENT_LOGGER] Failed to write event: {e}", file=sys.stderr)


def get_monotonic_elapsed_ms(start_mono: float) -> float:
    """
    Calculate elapsed time in milliseconds from a monotonic start point.
    Use this for precise interval measurements.
    
    Usage:
        t0 = time.monotonic()
        # ... operation ...
        elapsed = get_monotonic_elapsed_ms(t0)
        log_event("operation_complete", duration_ms=elapsed)
    """
    return round((time.monotonic() - start_mono) * 1000, 2)


def read_events(filepath=LOG_FILE, event_filter=None, since_utc=None):
    """
    Read events from the log file. Useful for analysis scripts.
    
    Args:
        filepath: Path to JSONL file
        event_filter: If set, only return events matching this event type
        since_utc: If set (ISO-8601 string), only return events after this time
    
    Returns:
        List of dicts
    """
    events = []
    if not os.path.exists(filepath):
        return events
    
    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if event_filter and ev.get("event") != event_filter:
                    continue
                if since_utc and ev.get("timestamp_utc", "") < since_utc:
                    continue
                events.append(ev)
            except json.JSONDecodeError:
                continue
    
    return events


def rotate_log(suffix=None):
    """
    Rotate the current log file by renaming it with a timestamp suffix.
    Call this at the start of each test run to get a clean log.
    
    Args:
        suffix: Custom suffix. If None, uses current UTC timestamp.
    
    Returns:
        Path to the rotated file, or None if no file existed.
    """
    if not os.path.exists(LOG_FILE):
        return None
    
    if suffix is None:
        suffix = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    
    rotated_path = LOG_FILE.replace(".jsonl", f"_{suffix}.jsonl")
    os.rename(LOG_FILE, rotated_path)
    return rotated_path
