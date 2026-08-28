"""
uart_lock.py — Mutual exclusion for LoRa UART (/dev/ttyAMA0).

Prevents sendCrowdingData.py (Class C polling) and sensorCommunicationCheck.py
(handover join attempts) from accessing the UART simultaneously.

Usage in sendCrowdingData.py (before Class C polling):
    from uart_lock import acquire_uart_lock, release_uart_lock
    
    if acquire_uart_lock("sendCrowdingData"):
        try:
            # ... Class C polling loop ...
        finally:
            release_uart_lock()

Usage in sensorCommunicationCheck.py (before handover):
    from uart_lock import is_uart_locked
    
    if needs_handover:
        if is_uart_locked():
            log_event("handover_deferred", reason="uart_locked")
        else:
            new_tech, new_network = decide_upload_technology(cursor=cwifi)
"""

import os
import time

LORA_UART_LOCK_FILE = "/tmp/lora_uart.lock"


def acquire_uart_lock(caller: str) -> bool:
    """
    Acquire exclusive access to the LoRa UART.
    
    Args:
        caller: Name of the calling script (for debugging)
    
    Returns:
        True if lock acquired, False if UART is already in use
    """
    if os.path.exists(LORA_UART_LOCK_FILE):
        try:
            with open(LORA_UART_LOCK_FILE, "r") as f:
                content = f.read().strip()
            # Check if the PID that holds the lock is still alive
            parts = content.split("|")
            old_pid = int(parts[0])
            os.kill(old_pid, 0)  # Raises OSError if process is dead
            # Process is still alive — lock is valid
            return False
        except (OSError, ValueError, IndexError):
            # Stale lock — remove it
            try:
                os.remove(LORA_UART_LOCK_FILE)
            except OSError:
                pass
    
    try:
        with open(LORA_UART_LOCK_FILE, "w") as f:
            f.write(f"{os.getpid()}|{caller}|{time.time():.0f}")
        return True
    except Exception:
        return False


def release_uart_lock():
    """Release the UART lock."""
    try:
        if os.path.exists(LORA_UART_LOCK_FILE):
            os.remove(LORA_UART_LOCK_FILE)
    except Exception:
        pass


def is_uart_locked() -> bool:
    """
    Check if the UART is currently locked by another process.
    
    Returns:
        True if locked (should NOT access UART), False if free
    """
    if not os.path.exists(LORA_UART_LOCK_FILE):
        return False
    
    try:
        with open(LORA_UART_LOCK_FILE, "r") as f:
            content = f.read().strip()
        parts = content.split("|")
        old_pid = int(parts[0])
        os.kill(old_pid, 0)
        return True  # Process alive, lock valid
    except (OSError, ValueError, IndexError):
        # Stale lock
        try:
            os.remove(LORA_UART_LOCK_FILE)
        except OSError:
            pass
        return False


def get_uart_lock_info() -> dict:
    """Get information about the current lock holder (for debugging)."""
    if not os.path.exists(LORA_UART_LOCK_FILE):
        return {"locked": False}
    
    try:
        with open(LORA_UART_LOCK_FILE, "r") as f:
            content = f.read().strip()
        parts = content.split("|")
        pid = int(parts[0])
        caller = parts[1] if len(parts) > 1 else "unknown"
        since = float(parts[2]) if len(parts) > 2 else 0
        
        try:
            os.kill(pid, 0)
            alive = True
        except OSError:
            alive = False
        
        return {
            "locked": alive,
            "pid": pid,
            "caller": caller,
            "since_unix": since,
            "stale": not alive,
        }
    except Exception:
        return {"locked": False, "error": True}
