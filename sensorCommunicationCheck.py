import sqlite3
import subprocess
import netifaces as ni
import sys
import os
import time as _time

from sensorFunctions import *
from event_logger import log_event
from uart_lock import is_uart_locked

#                   sensorCommunicationCheck.py
#
#   This script is responsible for checking the available upload
#   technologies and if they have connection.
#
#   It is aimed to run periodically to check if the network connection
#   was lost or not.
#
#
#   Author: Tomas Mestre Santos
#   Date: 09-03-2024
#
# ── Cycle start — reference point for all intervals in this execution ─────────
_t0_cycle = _time.monotonic()
log_event("cycle_start")

COMM_CHECK_LOCK_FILE = "/tmp/sensor_communication_check.lock"
_comm_check_lock_acquired = False

def acquire_comm_check_lock():
    global _comm_check_lock_acquired
    try:
        fd = os.open(COMM_CHECK_LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        _comm_check_lock_acquired = True
        return True
    except FileExistsError:
        return False

def release_comm_check_lock():
    global _comm_check_lock_acquired
    if not _comm_check_lock_acquired:
        return
    try:
        if os.path.exists(COMM_CHECK_LOCK_FILE):
            os.remove(COMM_CHECK_LOCK_FILE)
    except Exception:
        pass
    _comm_check_lock_acquired = False

def finish_cycle_and_exit(code=0, reason=None, error=None):
    log_event(
        "cycle_complete",
        cycle_ms=round((_time.monotonic() - _t0_cycle) * 1000, 2),
        exit_reason=reason,
        error=error,
    )
    release_comm_check_lock()
    sys.exit(code)

def update_wifi_state_after_profile_change(cursor):
    try:
        upload_interface, detection_interface = check_upload_detection_interfaces(False)
        ip_addr = "nd"
        try:
            ip_addr = ni.ifaddresses(upload_interface)[ni.AF_INET][0]['addr']
        except Exception:
            pass
        cursor.execute(
            """UPDATE SensorCommunication SET WifiConnected=?, IP_Address=?, Upload_Interface=?, Detection_Interface=?, Last_Update=CURRENT_TIMESTAMP""",
            (True, ip_addr, upload_interface, detection_interface)
        )
        cursor.execute(
            """UPDATE SensorConfiguration SET Upload_Technology=?, Active_LoRa_Network=NULL, Last_Update=CURRENT_TIMESTAMP""",
            ("wifi",)
        )
    except Exception as e:
        log_event("wifi_state_update_error", error=str(e))

if not acquire_comm_check_lock():
    print("[CHECK] Another sensorCommunicationCheck.py is already running. Exiting.")
    log_event(
        "cycle_complete",
        cycle_ms=round((_time.monotonic() - _t0_cycle) * 1000, 2),
        exit_reason="comm_check_lock_busy",
    )
    sys.exit(0)

if not os.path.exists(BOOT_COMPLETE_FILE):
    print("[CHECK] Boot initialization not complete yet. Exiting.")
    finish_cycle_and_exit(0, reason="boot_not_complete")

_, available_released = wait_for_script_lock(
    COMM_AVAILABLE_LOCK_FILE,
    max_wait_sec=90,
    poll_sec=2,
    log_prefix="[CHECK]"
)

if not available_released:
    print("[CHECK] sensorCommunicationAvailable.py still running. Skipping this cycle to avoid serial contention.")
    finish_cycle_and_exit(0, reason="comm_available_lock_timeout")

# ── Read current state from DB ────────────────────────────────────────────────
try:
    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchone()

    current_upload_technology = "none"
    current_lora_network = None

    if sensor_configuration is not None:
        sensor_uuid = sensor_configuration[0]
        current_upload_technology = sensor_configuration[12]

        if len(sensor_configuration) > 16:
            current_lora_network = sensor_configuration[16]

        print(f"[CHECK] Current technology: '{current_upload_technology}', LoRa network: {current_lora_network}")

    sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

    if sensor_communication is None:
        subprocess.run(['/usr/bin/python3', '/home/kali/Desktop/sensorCommunicationAvailable.py'])
        sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

    wifiAvailable = sensor_communication[0]
    loraAvailable = sensor_communication[1]
    loraConnected = sensor_communication[3]
    curr_ip_address = sensor_communication[4]
    curr_upload_if = sensor_communication[5]
    curr_detect_if = sensor_communication[6]

except sqlite3.Error as error:
    print("Failed to read upload technologies from database.", error)
    finish_cycle_and_exit(1, reason="db_read_error", error=str(error))

_final_upload_technology = current_upload_technology

# debug
# cwifi.execute("UPDATE SensorConfiguration SET Upload_Technology='wifi', Active_LoRa_Network=NULL")
# connwifi.commit()
# sys.exit(0)
# wifiAvailable = False
# wifiConnected = False

# ── Phase 1: Detection — timed precisely ──────────────────────────────────────
#
#   wifi_check_ms: time for check_wifi_connection() to return.
#   This is the variable part of T_detect that is not explained by the
#   cron interval alone. Typically a few hundred ms (ICMP ping or TCP probe).
#
_wifi_check_ms = None
_lora_check_ms = None

if wifiAvailable:
    _t0_wifi = _time.monotonic()
    wifiConnected = check_wifi_connection()
    _wifi_check_ms = round((_time.monotonic() - _t0_wifi) * 1000, 2)
else:
    set_wifi_connected(False)
    wifiConnected = False


#wifiConnected = False
#set_wifi_connected(False)
#loraAvailable = False
#set_lora_available(True)
#set_lora_connected(False)


log_event(
    "connectivity_check",
    link="wifi",
    connected=wifiConnected,
    check_ms=_wifi_check_ms,
)

# Check LoRa connection only when it is the active uplink.
# lora_check_ms is logged for symmetric decomposition.
if sensor_configuration is not None and current_upload_technology == "lora":
    if loraAvailable:
        _t0_lora = _time.monotonic()
        if current_lora_network:
            loraConnected = check_lora_network_status(current_lora_network)
            print(f"[CHECK] LoRa network {current_lora_network} status: {'joined' if loraConnected else 'failed'}")
        else:
            loraConnected = check_lora_connection_no_Join()
            print("[CHECK] Active_LoRa_Network is NULL, using legacy check")
        _lora_check_ms = round((_time.monotonic() - _t0_lora) * 1000, 2)
    else:
        set_lora_connected(False)
        loraConnected = False

    log_event(
        "connectivity_check",
        link="lora",
        connected=loraConnected,
        network=current_lora_network,
        check_ms=_lora_check_ms,
    )

# ── Interface bookkeeping (unchanged, not part of handover timing) ────────────
upload_interface, detection_interface = check_upload_detection_interfaces(False)

if curr_upload_if != upload_interface:
    cwifi.execute(
        """UPDATE SensorCommunication SET Upload_Interface=?, Last_Update=CURRENT_TIMESTAMP""",
        (upload_interface,)
    )

if wifiConnected:
    try:
        ip_addr = ni.ifaddresses(upload_interface)[ni.AF_INET][0]['addr']
        if str(curr_ip_address) != str(ip_addr):
            cwifi.execute(
                """UPDATE SensorCommunication SET IP_Address=?, Last_Update=CURRENT_TIMESTAMP""",
                (ip_addr,)
            )
    except Exception:
        pass

if curr_detect_if != detection_interface:
    cwifi.execute(
        """UPDATE SensorCommunication SET Detection_Interface=?, Last_Update=CURRENT_TIMESTAMP""",
        (detection_interface,)
    )
    print("Detection interfaces are different!")

    if sensor_configuration is not None:
        status = sensor_configuration[4]
        uploadPeriodicity = sensor_configuration[10]
        rebootPeriodicity = sensor_configuration[13]
        rebootTime = sensor_configuration[14]

        write_crontab_file(status, detection_interface, uploadPeriodicity, rebootPeriodicity, rebootTime)

# ── Phase 2: Decision — determine if handover is needed ───────────────────────
#
#   Detection is now complete. From this point on we know whether a handover
#   is required.
#
#   Intra-WiFi failover: when the active Wi-Fi AP fails, try other configured
#   sensor-wifi-* profiles BEFORE falling back to LoRaWAN. This also applies
#   when on LoRa: actively probe Wi-Fi profiles to detect recovery.
#
needs_handover = False
_handover_reason = "no_active_tech"
_wifi_failover_ms = None
_wifi_failover_profile = None

if current_upload_technology == "wifi":
    if not wifiConnected:
        log_event(
            "link_failure_detected",
            link="wifi",
            reason="wifi_server_unreachable",
            wifi_check_ms=_wifi_check_ms,
        )

        # ── Intra-WiFi failover: try other configured APs first ───────────
        _t0_wifi_fo = _time.monotonic()
        wifi_fo_ok, _wifi_failover_profile = try_wifi_failover(cursor=cwifi, skip_current=True)
        _wifi_failover_ms = round((_time.monotonic() - _t0_wifi_fo) * 1000, 2)

        if wifi_fo_ok:
            # Recovered via another Wi-Fi AP — no LoRa handover needed
            wifiConnected = True
            set_wifi_connected(True)
            update_wifi_state_after_profile_change(cwifi)
            _final_upload_technology = "wifi"
            log_event(
                "wifi_intra_failover_complete",
                profile=_wifi_failover_profile,
                duration_ms=_wifi_failover_ms,
            )
            print(f"[CHECK] Wi-Fi intra-RAN failover OK → {_wifi_failover_profile} "
                  f"({_wifi_failover_ms}ms)")
        else:
            # All Wi-Fi profiles exhausted — fall through to LoRa
            wifiConnected = False
            cwifi.execute(
                """UPDATE SensorCommunication SET WifiConnected=?, Last_Update=CURRENT_TIMESTAMP""",
                (False,)
            )
            needs_handover = True
            _handover_reason = "wifi_lost"
            log_event(
                "wifi_intra_failover_failed",
                duration_ms=_wifi_failover_ms,
            )
            print(f"[CHECK] Wi-Fi intra-RAN failover failed ({_wifi_failover_ms}ms), "
                  f"triggering LoRa handover")

elif current_upload_technology == "lora":
    if wifiConnected:
        # NetworkManager auto-reconnected to a Wi-Fi AP (fast path)
        needs_handover = True
        _handover_reason = "wifi_available"
        print("[CHECK] Preferred uplink WiFi is available again, returning from LoRa to WiFi")
    else:
        # ── Actively probe configured Wi-Fi profiles for recovery ─────────
        _t0_wifi_fo = _time.monotonic()
        wifi_fo_ok, _wifi_failover_profile = try_wifi_failover(cursor=cwifi, skip_current=False)
        _wifi_failover_ms = round((_time.monotonic() - _t0_wifi_fo) * 1000, 2)

        if wifi_fo_ok:
            wifiConnected = True
            set_wifi_connected(True)
            needs_handover = True
            _handover_reason = "wifi_available"
            log_event(
                "wifi_intra_failover_complete",
                profile=_wifi_failover_profile,
                duration_ms=_wifi_failover_ms,
                from_lora=True,
            )
            print(f"[CHECK] Wi-Fi recovered via {_wifi_failover_profile} "
                  f"({_wifi_failover_ms}ms), returning from LoRa")
        elif not loraConnected:
            needs_handover = True
            _handover_reason = "lora_lost"
            if current_lora_network:
                print(f"[CHECK] LoRa network {current_lora_network} failed, "
                      f"triggering handover cascade")
            else:
                print("[CHECK] LoRa connection lost, triggering handover")

elif current_upload_technology == "none":
    # ── Try Wi-Fi recovery before cascading ───────────────────────────────
    _t0_wifi_fo = _time.monotonic()
    wifi_fo_ok, _wifi_failover_profile = try_wifi_failover(cursor=cwifi, skip_current=False)
    _wifi_failover_ms = round((_time.monotonic() - _t0_wifi_fo) * 1000, 2)

    if wifi_fo_ok:
        wifiConnected = True
        set_wifi_connected(True)

    needs_handover = True
    _handover_reason = "no_active_tech"
    print("[CHECK] No active technology, attempting handover cascade")

# ── Phase 3: Handover decision and state update ───────────────────────────────
#
#   The duration_ms stored in handover_complete measures only the interval
#   between handover_triggered and handover_complete.
#
#   The T_switch reported in the paper is the full execution time of this
#   communication-check cycle:
#
#       T_switch = cycle_complete - cycle_start
#
#   That value is emitted in the cycle_complete event as cycle_ms.
#
if needs_handover:
    _t0_switch = _time.monotonic()

    log_event(
        "handover_triggered",
        from_link=current_upload_technology,
        reason=_handover_reason,
        wifi_check_ms=_wifi_check_ms,
        lora_check_ms=_lora_check_ms,
        wifi_failover_ms=_wifi_failover_ms,
        wifi_failover_profile=_wifi_failover_profile,
    )

    if is_uart_locked():
        log_event("handover_deferred", reason="uart_locked_by_sendCrowdingData")
        print("[CHECK] UART locked by sendCrowdingData, deferring handover to next cycle")

    else:
        new_tech, new_network = decide_upload_technology(cursor=cwifi)
        _switch_ms = round((_time.monotonic() - _t0_switch) * 1000, 2)
        _final_upload_technology = new_tech

        log_event(
            "handover_complete",
            to_link=new_tech,
            to_network=new_network,
            duration_ms=_switch_ms,
            wifi_check_ms=_wifi_check_ms,
            lora_check_ms=_lora_check_ms,
            wifi_failover_ms=_wifi_failover_ms,
        )

        print(f"[CHECK] Handover completed — New technology: {new_tech} "
              f"(duration_ms={_switch_ms}ms, wifi_check={_wifi_check_ms}ms)")

        if new_network:
            print(f"[CHECK] Active LoRa network: {new_network}")
        elif new_tech == 'none':
            print("[CHECK] Handover failed — no connectivity available")

        lora_connected_flag = (new_tech == 'lora')
        cwifi.execute(
            """UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""",
            (lora_connected_flag,)
        )

else:
    log_event(
        "connectivity_ok",
        link=_final_upload_technology,
        wifi_check_ms=_wifi_check_ms,
        lora_check_ms=_lora_check_ms,
    )
    print(f"[CHECK] Current technology '{_final_upload_technology}' operational, no handover needed")

# ── Commit and close ──────────────────────────────────────────────────────────
connwifi.commit()
cwifi.close()
connwifi.close()

log_event(
    "cycle_complete",
    cycle_ms=round((_time.monotonic() - _t0_cycle) * 1000, 2),
    final_upload_technology=_final_upload_technology,
    handover_attempted=needs_handover,
)

release_comm_check_lock()