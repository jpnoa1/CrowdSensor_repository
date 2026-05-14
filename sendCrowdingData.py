import sqlite3
import datetime as dt
import matplotlib.pyplot as plt; plt.rcdefaults()
import pytz
import time
import os
import struct

from swARM_at_custom.swARM_at.RAK3172 import RAK3172
from sensorFunctions import *
from sensorFunctions import downlink_cb
from communication_manager import CommunicationManager
from event_logger import log_event
from uart_lock import acquire_uart_lock, release_uart_lock, get_uart_lock_info


COMM_CHECK_LOCK_FILE = "/tmp/sensor_communication_check.lock"
COMM_CHECK_LOCK_MAX_AGE_SEC = 70

def wait_for_comm_check_lock(max_wait_sec=120, poll_sec=2):
    waited = 0

    while os.path.exists(COMM_CHECK_LOCK_FILE) and waited <= max_wait_sec:
        try:
            lock_age = time.time() - os.path.getmtime(COMM_CHECK_LOCK_FILE)

            if lock_age > COMM_CHECK_LOCK_MAX_AGE_SEC:
                print("[UPLOAD] Removing stale sensorCommunicationCheck.py lock.")
                os.remove(COMM_CHECK_LOCK_FILE)
                break

        except Exception:
            pass

        print(f"[UPLOAD] Waiting for sensorCommunicationCheck.py to finish... waited={waited}s")
        time.sleep(poll_sec)
        waited += poll_sec

    return not os.path.exists(COMM_CHECK_LOCK_FILE), waited


_t0_send_cycle = time.monotonic()
log_event("send_cycle_start")

# Debug switch: set to False to silence debug prints
DEBUG_COMM = True

def dprint(msg):
    if DEBUG_COMM:
        print(f"[COMM-DEBUG] {msg}")

def wait_for_lora_uart_lock(caller, max_wait_sec=60, poll_sec=3
):
    waited = 0

    while waited <= max_wait_sec:
        if acquire_uart_lock(caller):
            return True, waited

        info = get_uart_lock_info()
        print(f"[UPLOAD] Waiting for LoRa UART lock... waited={waited}s info={info}")

        time.sleep(poll_sec)
        waited += poll_sec

    return False, waited

if not os.path.exists(BOOT_COMPLETE_FILE):
    dprint("Boot initialization not complete yet. Exiting.")
    exit(0)

check_released, waited_for_check = wait_for_comm_check_lock(
    max_wait_sec=120,
    poll_sec=2
)

if not check_released:
    print("[UPLOAD] sensorCommunicationCheck.py still running after timeout. Exiting to avoid stale DB state.")
    exit(0)



time.sleep(5)  # Initial delay to allow other processes to start and populate DBs

waited_for_comm_available, available_released = wait_for_script_lock(
    COMM_AVAILABLE_LOCK_FILE,
    max_wait_sec=90,
    poll_sec=2,
    log_prefix="[COMM-DEBUG]"
)

if not available_released:
    dprint("Lock still active after timeout; continuing carefully.")

dprint(f"waited_for_comm_available={waited_for_comm_available}s")


# Read sensor configuration from database
try:
    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

    # Sensor configuration
    if len(sensor_configuration) != 0:
        sensorUUID = sensor_configuration[0][0]
        sensorName = sensor_configuration[0][1]
        influxdb_bucket = sensor_configuration[0][8]
        upload_periodicity = sensor_configuration[0][10]
        slidingWindow = sensor_configuration[0][11]

        active_lora_network = None
        if len(sensor_configuration[0]) > 16:
            active_lora_network = sensor_configuration[0][16]

        ip_address = cwifi.execute("""SELECT IP_Address FROM SensorCommunication""").fetchone()[0]

    else:
        print(
            "Sensor is not currently configured. It is required a cloud IP address "
            "to connect to the cloud server via MQTT.\nPlease run the "
            "'sensorConfiguration.py' script to configure the sensor."
        )
        exit(0)

except sqlite3.Error:
    print("Failed to read sensor configuration from local database.")
    exit(0)

dataAtual_aware = dt.datetime.now(dt.timezone.utc)

dataAtual = dataAtual_aware.replace(tzinfo=None)
dataAnalizar = dataAtual - dt.timedelta(minutes=int(slidingWindow))


# Get number of devices detected from database
try:
    conndev = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
    cdev = conndev.cursor()

    # Device counting - Data packets
    rows_data_packets = cdev.execute(
        """SELECT COUNT(*) FROM Data_Packets 
           WHERE ((First_Record >= ? and First_Record <= ?) 
           or (Last_Time_Found > ? and Last_Time_Found <= ?))""",
        (dataAnalizar, dataAtual, dataAnalizar, dataAtual)
    ).fetchall()

    # Device counting - Probe Requests
    rows_probe_requests = cdev.execute(
        """SELECT COUNT(*) FROM Probe_Requests 
           WHERE ((First_Record >= ? and First_Record <= ?) 
           or (Last_Time_Found > ? and Last_Time_Found <= ?))""",
        (dataAnalizar, dataAtual, dataAnalizar, dataAtual)
    ).fetchall()

    # Device counting - All
    detected_devices = rows_data_packets[0][0] + rows_probe_requests[0][0]

    cdev.close()
    conndev.close()

except sqlite3.Error:
    print("Failed to read number of devices detected from local database.")
    detected_devices = 0


# wifi_topic = f"sttoolkit-test/mqtt/wifi/numdetections/{influxdb_bucket}/{ip_address}/{sensorName}/{sensorUUID}"
wifi_topic = f"sttoolkit-test/mqtt/wifi/v2/numdetections/{sensorUUID}"


manager = CommunicationManager(cwifi, wifi_topic)
upload_technology, selected_lora_network = manager.load_cached_uplink()

log_event(
    "send_selected_technology",
    upload_technology=upload_technology,
    selected_lora_network=selected_lora_network,
    active_lora_network=active_lora_network,
    devices=int(detected_devices)
)

# Keep backward compatibility with legacy LoRa flow using selected network.
if selected_lora_network:
    active_lora_network = selected_lora_network

dataAtual_unix = int(dataAtual_aware.timestamp())


# Keep LoRa send path unchanged for now; manager handles Wi-Fi and no-connectivity cases.
sent_over_wifi = False

if upload_technology != "lora":
    sent_over_wifi = manager.send_current_measurement(dataAtual_unix, int(detected_devices))
    
    if sent_over_wifi:
        log_event(
            "message_sent",
            link="wifi",
            unix_ts=dataAtual_unix,
            devices=int(detected_devices)
        )

    elif upload_technology == "wifi":
        log_event(
            "message_stored",
            unix_ts=dataAtual_unix,
            devices=int(detected_devices),
            reason="wifi_send_failed"
        )

else:
    dprint("Skipping manager send because upload_technology=lora (legacy LoRa flow)")


if sent_over_wifi:
    log_event(
            "replay_started",
            link="wifi",
            
        )
    replayed = manager.replay_pending_wifi(max_items=100)

    if replayed > 0:
        log_event(
            "replay_complete",
            link="wifi",
            replayed=replayed
        )

    dprint(f"replay_pending_wifi -> replayed={replayed}")

else:
    dprint("No replay executed (current send failed or uplink not wifi)")


print(f"[INFO] Current upload technology: {upload_technology}")


# Upload via LoRa - legacy flow kept, gated by manager decision
# Upload via LoRa - legacy flow kept, gated by manager decision
if upload_technology == "lora":
    dprint(f"Entering LoRa block with network={active_lora_network}")

    if not active_lora_network:
        print("[UPLOAD] Upload_Technology is 'lora' but Active_LoRa_Network is NULL!")
        print("[UPLOAD] Run sensorCommunicationCheck.py to trigger handover")
        exit(1)

    print(f"[UPLOAD] Using LoRa ({active_lora_network}) to send data (count: {detected_devices})")

    try:
        network_creds = cwifi.execute(
            """SELECT app_eui, app_key, dev_eui FROM LoRaNetworks WHERE name=?""",
            (active_lora_network,)
        ).fetchone()

        if network_creds is None:
            print(f"[UPLOAD] Network '{active_lora_network}' not found in LoRaNetworks table!")
            exit(1)

        app_eui, app_key, dev_eui = network_creds
        print(f"[UPLOAD] Using credentials for {active_lora_network} (Dev EUI: {dev_eui})")

    except sqlite3.Error as error:
        print(f"[UPLOAD] Failed to read LoRa credentials: {error}")
        exit(1)

    uart_locked, waited_for_uart_lock = wait_for_lora_uart_lock(
        caller="sendCrowdingData_lora",
        max_wait_sec=120,
        poll_sec=2
    )

    if not uart_locked:
        print("[UPLOAD] LoRa UART busy. Storing measurement and exiting this cycle.")

        store_pending_measurement(dataAtual_unix, detected_devices)

        log_event(
            "message_stored",
            unix_ts=dataAtual_unix,
            devices=int(detected_devices),
            reason="uart_locked",
            network=active_lora_network
        )

    else:
        rak = None

        try:
            rak = RAK3172("/dev/ttyAMA0", 115200)
            rak.connect()

            print(f"[UPLOAD] Configuring RAK3172 for {active_lora_network}")
            rak.set_dev_eui(dev_eui)
            rak.set_app_eui(app_eui)
            rak.set_app_key(app_key)

            joined = check_lora_network_status(active_lora_network)

            if not joined:
                print(f"[UPLOAD] Not joined to {active_lora_network}, marking as failed")
                mark_lora_network_failed(active_lora_network)

                cwifi.execute(
                    """UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""",
                    (False,)
                )

                connwifi.commit()

                print("[UPLOAD] Handover will be triggered on next sensorCommunicationCheck cycle")

            else:
                count = int(detected_devices)

                if count <= 255:
                    payload_bytes = struct.pack(">B", count)  # 1 byte
                else:
                    payload_bytes = struct.pack(">H", count)  # 2 bytes

                payload_hex = payload_bytes.hex()
                print(f"[UPLOAD] Sending payload: count={count} ({len(payload_bytes)} byte(s)) (hex: {payload_hex})")
                sent = rak.send_lorawan_data(1, payload_hex)  # Port 1 = crowding

                if sent:
                    log_event(
                        "message_sent",
                        link="lora",
                        network=active_lora_network,
                        unix_ts=dataAtual_unix,
                        devices=int(detected_devices)
                    )

                else:
                    log_event(
                        "message_stored",
                        unix_ts=dataAtual_unix,
                        devices=int(detected_devices),
                        reason="lora_send_failed",
                        network=active_lora_network
                    )

                if not sent:
                    print(f"[UPLOAD] Failed to send via {active_lora_network}")
                    store_pending_measurement(dataAtual_unix, detected_devices)
                    mark_lora_network_failed(active_lora_network)

                    cwifi.execute(
                        """UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""",
                        (False,)
                    )

                    connwifi.commit()
                    print("[UPLOAD] Network marked as failed, handover will be attempted by sensorCommunicationCheck.py")

                else:
                    print(f"[UPLOAD] Successfully sent via {active_lora_network}")

                    # Remove lock-wait and startup-lock-wait from the Class C listen window.
                    downlink_seconds = (
                        (int(upload_periodicity) * 60)
                        - 25
                        - waited_for_check
                        - waited_for_comm_available
                        - waited_for_uart_lock
                    )

                    if downlink_seconds < 0:
                        downlink_seconds = 0

                    print(
                        f"[UPLOAD] Listening for downlinks for {downlink_seconds:.0f}s "
                        f"(adjusted for wait={waited_for_comm_available}s)..."
                    )

                    t_end = time.time() + downlink_seconds

                    log_event(
                        "lora_classC_start",
                        network=active_lora_network,
                        listen_seconds=downlink_seconds,
                        uart_locked=True
                    )

                    try:
                        while time.time() < t_end:
                            port, payload = rak.receive_data_C()

                            if port and payload:
                                downlink_cb("C", port, len(payload), payload)

                                log_event(
                                    "downlink_received",
                                    port=port,
                                    payload_hex=payload
                                )

                            time.sleep(1)

                    except KeyboardInterrupt:
                        print("[UPLOAD] Interrupted by user, exiting...")

                    except Exception as e:
                        print(f"[UPLOAD] LoRa communication error: {e}")
                        mark_lora_network_failed(active_lora_network)

                    finally:
                        log_event(
                            "lora_classC_end",
                            network=active_lora_network
                        )

        finally:
            if rak is not None:
                try:
                    rak.disconnect()
                except Exception:
                    pass

            release_uart_lock()
    


elif upload_technology == "none":
    print(
        "WARNING: No communication available for sending crowding measurements! \n"
        "        Please check the network conectivity for uploading data to the cloud server."
    )


cwifi.close()
connwifi.close()

log_event(
    "send_cycle_complete",
    cycle_ms=round((time.monotonic() - _t0_send_cycle) * 1000, 2)
)