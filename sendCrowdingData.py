import joblib
import pandas as pd
import numpy as np
import sqlite3
import datetime as dt
import matplotlib.pyplot as plt; plt.rcdefaults()
import time
import sys
import os
import struct

# Suppress TensorFlow Warnings
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0' 
os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3' 
import logging
logging.getLogger("tensorflow").setLevel(logging.ERROR)
from tensorflow.keras.models import load_model

from swARM_at_custom.swARM_at.RAK3172 import RAK3172
from sensorFunctions import *
from sensorFunctions import downlink_cb
from communication_manager import CommunicationManager
from event_logger import log_event
from uart_lock import acquire_uart_lock, release_uart_lock, get_uart_lock_info

MODEL_PATH = '/home/kali/Desktop/Sniffer/wifi_lstm_regressor.keras'
SCALER_PATH = '/home/kali/Desktop/Sniffer/wifi_lstm_scaler.pkl'
HISTORY_FILE = '/home/kali/Desktop/Sniffer/lstm_feature_history.csv'
FEATURE_COLS = ['Total_Packets', 'Total_Bursts', 'Unique_MACs', 'Unique_Fingerprints', 'Packets_Per_Fingerprint', 'Bursts_Per_Fingerprint']
TIME_STEPS = 3

SEND_EXTENDED_FINGERPRINTS = False

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

def handle_gap_fill_request(rak, payload_hex, current_seq, network_name):
    """
    Processa um downlink FPort 5 do servidor com last_seq.
    Lê do buffer as medições em falta e envia via FPort 4.
    """
    try:
        last_seq_server = int(payload_hex[:2], 16)

        # Servidor confirmou que tem tudo até last_seq_server
        set_last_confirmed_seq(last_seq_server)

        gap = (current_seq - last_seq_server) % 256

        print(
            f"[GAP-FILL] Received request: server_last_seq={last_seq_server} "
            f"current_seq={current_seq} gap={gap}"
        )

        if gap == 0 or gap > 256:
            print("[GAP-FILL] No gap or invalid, ignoring.")
            return

        measurements = buffer_get_range(last_seq_server, current_seq)

        if not measurements:
            print("[GAP-FILL] No measurements in buffer for requested range.")
            return

        REPLAY_MAX = 10
        batch = measurements[:REPLAY_MAX]
        batch_size = len(batch)
        seq_oldest = batch[0][0]
        ts_oldest  = batch[0][1]

        replay_payload = struct.pack(">BI", seq_oldest, ts_oldest)
        for (_, _, devices) in batch:
            replay_payload += struct.pack(">H", min(int(devices), 65535))

        replay_hex = replay_payload.hex()
        print(
            f"[GAP-FILL] Sending {batch_size} measurements, "
            f"SEQ_oldest={seq_oldest} TS_oldest={ts_oldest} hex={replay_hex}"
        )

        time.sleep(2)

        sent = rak.send_lorawan_data(4, replay_hex)

        if sent:
            log_event(
                "gap_fill_sent",
                link="lora",
                network=network_name,
                batch_size=batch_size,
                seq_oldest=seq_oldest,
                ts_oldest=ts_oldest,
                server_last_seq=last_seq_server,
            )
            print(f"[GAP-FILL] {batch_size} medições enviadas com sucesso.")
        else:
            log_event(
                "gap_fill_failed",
                link="lora",
                network=network_name,
                batch_size=batch_size,
            )
            print("[GAP-FILL] Falha no envio.")

    except Exception as e:
        print(f"[GAP-FILL] Erro: {e}")

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

try:
    scaler = joblib.load(SCALER_PATH)
    model = load_model(MODEL_PATH)
except Exception as e:
    print(f"Failed to load RF model: {e}")
    sys.exit(1)

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
        # slidingWindow = sensor_configuration[0][11]
        slidingWindow = 15

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
dataAtual_unix = int(dataAtual_aware.timestamp())

detected_devices = 0

# Get extracted information from database
try:
    dr_con = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)

    now = time.time()
    
    current_start = now - (slidingWindow * 60)
    current_end = now
    
    previous_start = current_start - (upload_periodicity * 60)
    previous_end = current_end - (upload_periodicity * 60)

    norm_new_fingerprints = 0.0
    norm_disappeared_fingerprints = 0.0

    df_all = pd.read_sql_query("SELECT * FROM Probe_Requests WHERE Timestamp >= ?", dr_con, params=(previous_start,))

    if not df_all.empty:

        # 1. Split data into the Current Window and Previous Window
        df_current = df_all[
            (df_all['Timestamp'] >= current_start) &
            (df_all['Timestamp'] <= current_end)
        ]

        df_previous = df_all[
            (df_all['Timestamp'] >= previous_start) &
            (df_all['Timestamp'] < previous_end)
        ]

        total_current_packets = len(df_current)
        total_previous_packets = len(df_previous)

        
        # FEATURE 1: FREQUENCY DELTAS (New & Disappeared)
        

        # Count the occurrences of each fingerprint in both windows
        counts_current = df_current['Fingerprint'].value_counts()
        counts_previous = df_previous['Fingerprint'].value_counts()

        # Combine them into a single table. Fill missing ones with 0.
        df_counts = pd.DataFrame({
            'current': counts_current,
            'previous': counts_previous
        }).fillna(0)

        # Calculate mathematical differences.
        # .clip(lower=0) ensures we don't get negative counts.
        total_new = (df_counts['current'] - df_counts['previous']).clip(lower=0).sum()
        total_disappeared = (df_counts['previous'] - df_counts['current']).clip(lower=0).sum()

        # New fingerprints are normalized by the CURRENT traffic
        if total_current_packets > 0:
            norm_new_fingerprints = total_new / total_current_packets
        else:
            norm_new_fingerprints = 0.0

        # Disappeared fingerprints are normalized by the PREVIOUS traffic
        if total_previous_packets > 0:
            norm_disappeared_fingerprints = total_disappeared / total_previous_packets
        else:
            norm_disappeared_fingerprints = 0.0

        print(f"[DEBUG] total_current_packets = {total_current_packets}")
        print(f"[DEBUG] total_previous_packets = {total_previous_packets}")
        print(f"[DEBUG] total_new = {total_new}")
        print(f"[DEBUG] total_disappeared = {total_disappeared}")
        print(f"[DEBUG] norm_new_fingerprints = {norm_new_fingerprints}")
        print(f"[DEBUG] norm_disappeared_fingerprints = {norm_disappeared_fingerprints}")

        
        # FEATURE 2: CURRENT WINDOW FEATURES FOR ML MODEL
        
        current_features = {
        'Total_Packets': 0,
        'Total_Bursts': 0,
        'Unique_MACs': 0, 
        'Unique_Fingerprints': 0,
        'Packets_Per_Fingerprint': 0.0,
        'Bursts_Per_Fingerprint': 0.0
        }

        if total_current_packets > 0:
            unique_macs = df_current['MAC'].nunique()
            unique_fingerprints = df_current['Fingerprint'].nunique()
            total_bursts = int(df_current.get('Is_New_Burst', pd.Series([0])).sum())

            if unique_fingerprints > 0:
                packets_per_fingerprint = total_current_packets / unique_fingerprints
                bursts_per_fingerprint = total_bursts / unique_fingerprints
            else:
                packets_per_fingerprint = 0.0
                bursts_per_fingerprint = 0.0

            current_features = {
            'Total_Packets': total_current_packets,
            'Total_Bursts': total_bursts,
            'Unique_MACs': unique_macs,
            'Unique_Fingerprints': unique_fingerprints,
            'Packets_Per_Fingerprint': packets_per_fingerprint,
            'Bursts_Per_Fingerprint': bursts_per_fingerprint
            }

            # Manage History Buffer
            if os.path.exists(HISTORY_FILE):
                df_history = pd.read_csv(HISTORY_FILE)
                df_history = pd.concat([df_history, pd.DataFrame([current_features])], ignore_index=True)
            else:
                df_history = pd.DataFrame([current_features])

            # Keep only the last TIME_STEPS rows
            if len(df_history) > TIME_STEPS:
                df_history = df_history.tail(TIME_STEPS).reset_index(drop=True)

            df_history.to_csv(HISTORY_FILE, index=False)

            # Prepare for LSTM
            pad_needed = TIME_STEPS - len(df_history)
            if pad_needed > 0:
                padding = pd.DataFrame([current_features] * pad_needed)
                df_sequence = pd.concat([padding, df_history], ignore_index=True)
            else:
                df_sequence = df_history

            raw_sequence_array = df_sequence[FEATURE_COLS].values

            # Scale the 3x6 matrix
            sequence_scaled = scaler.transform(raw_sequence_array)
            
            # Reshape to 3D: (1 sample, 3 time steps, 6 features)
            X_live_reshaped = sequence_scaled.reshape(1, TIME_STEPS, sequence_scaled.shape[1])

            raw_prediction = model.predict(X_live_reshaped, verbose=0)[0][0]
            detected_devices = max(0, int(np.round(raw_prediction)))

    dr_con.close()

except sqlite3.Error as e:
    print(f"Failed to read extracted information from local database: {e}")
except Exception as e:
    print(f"Error during ML prediction or data processing: {e}")

init_measurement_buffer()

# Sequence number for LoRa messages, to be included in payload 
lora_seq = get_and_increment_lora_seq()

# Save to buffer for potential replay (LoRa) or later upload (Wi-Fi failure)
buffer_insert(lora_seq, dataAtual_unix, int(detected_devices))

log_event(
    "buffer_insert",
    seq=lora_seq,
    unix_ts=dataAtual_unix,
    devices=int(detected_devices)
)

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




# Keep LoRa send path unchanged for now; manager handles Wi-Fi and no-connectivity cases.
sent_over_wifi = False

if upload_technology != "lora":
    if SEND_EXTENDED_FINGERPRINTS:
        sent_over_wifi = manager.send_current_measurement(
            dataAtual_unix, int(detected_devices),
            norm_new=norm_new_fingerprints,
            norm_disappeared=norm_disappeared_fingerprints,
            seq=lora_seq
        )
    else:
        sent_over_wifi = manager.send_current_measurement(
            dataAtual_unix, int(detected_devices),
            seq=lora_seq
        )
    
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
    confirmed = get_last_confirmed_seq()
    last_ack  = get_last_ack_seq()

    # Usar o mais conservador: last_confirmed_seq se existir
    replay_base = confirmed if confirmed is not None else last_ack

    if replay_base is None:
        set_last_ack_seq(lora_seq)
        set_last_confirmed_seq(lora_seq)
        dprint(f"First run: initialized ack={lora_seq} confirmed={lora_seq}")
    else:
        log_event("replay_started", link="wifi")
        replayed = manager.replay_from_buffer(replay_base, lora_seq)

        if replayed > 0:
            log_event("replay_complete", link="wifi", replayed=replayed)

        dprint(f"replay_from_buffer(base={replay_base}, current={lora_seq}) -> {replayed}")

        set_last_ack_seq(lora_seq)
        set_last_confirmed_seq(lora_seq)

else:
    dprint("No replay executed (current send failed or uplink not wifi)")


print(f"[INFO] Current upload technology: {upload_technology}")


# Upload via LoRa - legacy flow kept, gated by manager decision
# Upload via LoRa - legacy flow kept, gated by manager decision
if upload_technology == "lora":
    dprint(f"Entering LoRa block with network={active_lora_network}")

    # Verificar se o módulo está em duty cycle restriction
    duty_file = "/tmp/rak_duty_restricted"
    if os.path.exists(duty_file):
        try:
            ts_restricted = int(open(duty_file).read().strip())
            age_h = (time.time() - ts_restricted) / 3600
            if age_h < 24:
                print(
                    f"[UPLOAD] Module in duty cycle restriction "
                    f"({age_h:.1f}h ago) — measurement stored in buffer only"
                )
                log_event(
                    "message_stored",
                    unix_ts=dataAtual_unix,
                    devices=int(detected_devices),
                    reason="duty_cycle_active",
                    network=active_lora_network
                )
                # Saltar todo o bloco LoRa
                upload_technology = "duty_restricted"
            else:
                os.remove(duty_file)
                print("[UPLOAD] Duty cycle flag expired, attempting send")
        except Exception:
            try:
                os.remove(duty_file)
            except Exception:
                pass

if upload_technology == "lora":

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

                if SEND_EXTENDED_FINGERPRINTS:
                    norm_new_enc = min(int(round(norm_new_fingerprints * 10000)), 65535)
                    norm_dis_enc = min(int(round(norm_disappeared_fingerprints * 10000)), 65535)

                    if count <= 255:
                        payload_bytes = struct.pack(">BBHH", lora_seq, count, norm_new_enc, norm_dis_enc)
                    else:
                        payload_bytes = struct.pack(">BHHH", lora_seq, count, norm_new_enc, norm_dis_enc)
                else:
                    if count <= 255:
                        payload_bytes = struct.pack(">BB", lora_seq, count)
                    else:
                        payload_bytes = struct.pack(">BH", lora_seq, count)

                payload_hex = payload_bytes.hex()
                print(f"[UPLOAD] Sending payload: count={count} ({len(payload_bytes)} byte(s)) (hex: {payload_hex})")
                sent = rak.send_lorawan_data(1, payload_hex)  # Port 1 = crowding

                if not sent:
                    print(
                        f"[UPLOAD] Failed to send via {active_lora_network} "
                        f"(duty cycle restriction — skipping until next success)"
                    )
                    try:
                        with open("/tmp/rak_duty_restricted", "w") as f:
                            f.write(str(int(time.time())))
                    except Exception:
                        pass
                    log_event(
                        "message_stored",
                        unix_ts=dataAtual_unix,
                        devices=int(detected_devices),
                        reason="lora_send_failed_duty_cycle",
                        network=active_lora_network
                    )

                else:
                    print(f"[UPLOAD] Successfully sent via {active_lora_network}")
                    try:
                        if os.path.exists("/tmp/rak_duty_restricted"):
                            os.remove("/tmp/rak_duty_restricted")
                    except Exception:
                        pass
                                        # ── Replay proativo do buffer ──────────────────────
                    last_ack = get_last_ack_seq()

                    if last_ack is None:
                        # Firt run 
                        set_last_ack_seq(lora_seq)
                        dprint(f"First LoRa run: initialized last_ack_seq={lora_seq}")

                    else:
                        gap = (lora_seq - last_ack) % 256

                        if gap > 1:
                            # Há medições não confirmadas entre last_ack e lora_seq
                            # lora_seq já foi enviado no FPort 1; replay é last_ack+1 até lora_seq-1
                            prev_seq = (lora_seq - 1) % 256
                            pending = buffer_get_range(last_ack, prev_seq)

                            if pending:
                                # Limitar a 10 por pacote FPort 4
                                LORA_REPLAY_MAX = 10
                                batch = pending[:LORA_REPLAY_MAX]
                                batch_size = len(batch)
                                seq_oldest = batch[0][0]  # seq do primeiro

                                ts_oldest = batch[0][1]
                                replay_payload = struct.pack(">BI", seq_oldest, ts_oldest)
                                for (_, _, devices) in batch:
                                    replay_payload += struct.pack(">H", min(int(devices), 65535))

                                replay_hex = replay_payload.hex()
                                print(
                                    f"[UPLOAD][REPLAY] proactive batch={batch_size} "
                                    f"SEQ_oldest={seq_oldest} hex={replay_hex}"
                                )

                                time.sleep(3)

                                sent_replay = rak.send_lorawan_data(4, replay_hex)

                                if sent_replay:
                                    log_event(
                                        "lora_replay_sent",
                                        link="lora",
                                        network=active_lora_network,
                                        batch_size=batch_size,
                                        seq_oldest=seq_oldest,
                                    )
                                    print(f"[UPLOAD][REPLAY] {batch_size} medições enviadas.")
                                else:
                                    log_event(
                                        "lora_replay_failed",
                                        link="lora",
                                        network=active_lora_network,
                                        batch_size=batch_size,
                                    )
                                    print("[UPLOAD][REPLAY] Falha — servidor pedirá via downlink.")
                            else:
                                dprint("Buffer vazio para o range pedido.")

                        # Atualizar last_ack_seq independentemente do replay
                        set_last_ack_seq(lora_seq)
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
                                if int(port) == 5:
                                    # ── Gap fill request do servidor ──
                                    handle_gap_fill_request(
                                        rak, payload, lora_seq, active_lora_network
                                    )
                                else:
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
