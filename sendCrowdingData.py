import sqlite3
import datetime as dt
import matplotlib.pyplot as plt; plt.rcdefaults()
import os
import pytz
import time

from swARM_at_custom.swARM_at.RAK3172 import RAK3172
from sensorFunctions import *
from sensorFunctions import downlink_cb
from communication_manager import CommunicationManager


# Debug switch (set to False to silence debug prints)
DEBUG_COMM = True

def dprint(msg):
    if DEBUG_COMM:
        print(f"[COMM-DEBUG] {msg}")

time.sleep(5)  # Initial delay to allow other processes to start and populate DBs
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
        print("Sensor is not currently configured. It is required a cloud IP address to connect to the cloud server via MQTT.\nPlease run the 'sensorConfiguration.py' script to configure the sensor.")
        exit(0)

except sqlite3.Error:
    print("Failed to read sensor configuration from local database.")
    exit(0)


dataAtual = dt.datetime.now(pytz.utc).replace(tzinfo=None)
dataAnalizar = dataAtual - dt.timedelta(minutes=int(slidingWindow))


# Get number of devices detected from database
try:
    conndev = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
    cdev = conndev.cursor()

    # Device counting - Data packets
    rows_data_packets = cdev.execute(
        """SELECT COUNT(*) FROM Data_Packets WHERE ((First_Record >= ? and First_Record <= ?) or (Last_Time_Found > ? and Last_Time_Found <= ?))""",
        (dataAnalizar, dataAtual, dataAnalizar, dataAtual)
    ).fetchall()

    # Device counting - Probe Requests
    rows_probe_requests = cdev.execute(
        """SELECT COUNT(*) FROM Probe_Requests WHERE ((First_Record >= ? and First_Record <= ?) or (Last_Time_Found > ? and Last_Time_Found <= ?))""",
        (dataAnalizar, dataAtual, dataAnalizar, dataAtual)
    ).fetchall()

    # Device counting - All
    detected_devices = rows_data_packets[0][0] + rows_probe_requests[0][0]

    cdev.close()
    conndev.close()

except sqlite3.Error:
    print("Failed to read number of devices detected from local database.")
    detected_devices = 0


wifi_topic = f"sttoolkit-test/mqtt/wifi/numdetections/{influxdb_bucket}/{ip_address}/{sensorName}/{sensorUUID}"
dprint(f"sensor={sensorName} uuid={sensorUUID}")
dprint(f"detected_devices={detected_devices} window_min={slidingWindow}")
dprint(f"wifi_topic={wifi_topic}")

manager = CommunicationManager(cwifi, wifi_topic)
upload_technology, selected_lora_network = manager.load_cached_uplink()
dprint(f"cached_uplink -> upload_technology={upload_technology}, selected_lora_network={selected_lora_network}")

# Keep backward compatibility with legacy LoRa flow using selected network.
if selected_lora_network:
    active_lora_network = selected_lora_network

dataAtual_unix = int(dataAtual.timestamp())
dprint(f"current_measurement ts={dataAtual_unix} devices={detected_devices}")

# Keep LoRa send path unchanged for now; manager handles Wi-Fi and no-connectivity cases.
sent_over_wifi = False
if upload_technology != "lora":
    sent_over_wifi = manager.send_current_measurement(dataAtual_unix, int(detected_devices))
    dprint(f"send_current_measurement -> sent_over_wifi={sent_over_wifi}")
else:
    dprint("Skipping manager send because upload_technology=lora (legacy LoRa flow)")

if sent_over_wifi:
    replayed = manager.replay_pending_wifi(max_items=100)
    dprint(f"replay_pending_wifi -> replayed={replayed}")
else:
    dprint("No replay executed (current send failed or uplink not wifi)")

print(f"[INFO] Current upload technology: {upload_technology}")
# Upload via LoRa (legacy flow kept, gated by manager decision)
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

    rak = RAK3172("/dev/ttyAMA0", 115200)
    rak.connect()

    print(f"[UPLOAD] Configuring RAK3172 for {active_lora_network}")
    rak.set_dev_eui(dev_eui)
    rak.set_app_eui(app_eui)
    rak.set_app_key(app_key)

    joined = check_lora_network_status(active_lora_network)
    dprint(f"LoRa joined={joined}")
    print(f"[UPLOAD] Join status check: {'joined' if joined else 'not joined'}")

    if not joined:
        print(f"[UPLOAD] Not joined to {active_lora_network}, marking as failed")
        mark_lora_network_failed(active_lora_network)
        print("[UPLOAD] Handover will be triggered on next sensorCommunicationCheck cycle")
        rak.disconnect()
        exit(0)

    message = f"C,{detected_devices}"
    payload_hex = message.encode().hex()

    print(f"[UPLOAD] Sending payload: {message} (hex: {payload_hex})")
    sent = rak.send_lorawan_data(2, payload_hex)
    dprint(f"LoRa send result={sent}")

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

        print(f"[UPLOAD] Listening for downlinks for {upload_periodicity} min...")
        t_end = time.time() + (upload_periodicity * 60) - 25

        try:
            while time.time() < t_end:
                port, payload = rak.receive_data_C()
                if port and payload:
                    dprint(f"LoRa downlink raw port={port} payload={payload}")
                    downlink_cb("C", port, len(payload), payload)
                time.sleep(1)

        except KeyboardInterrupt:
            print("[UPLOAD] Interrupted by user, exiting...")
        except Exception as e:
            print(f"[UPLOAD] LoRa communication error: {e}")
            mark_lora_network_failed(active_lora_network)

    rak.disconnect()

elif upload_technology == "none":
    print("WARNING: No communication available for sending crowding measurements! \n\
        Please check the network conectivity for uploading data to the cloud server.")

cwifi.close()
connwifi.close()