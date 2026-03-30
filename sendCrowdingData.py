

import sqlite3
import datetime as dt
from time import sleep
import matplotlib.pyplot as plt; plt.rcdefaults()
import subprocess
import os
import pytz
import uuid
import netifaces as ni

from swARM_at_custom.swARM_at.RAK3172 import RAK3172
import serial
import sys
from sensorFunctions import *
from sensorFunctions import downlink_cb

# Read sensor configuration from database

try:
    connwifi= sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db' , timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

    #Sensor configuration
    if len(sensor_configuration) != 0:
        sensorUUID = sensor_configuration[0][0]
        sensorName = sensor_configuration[0][1]
        influxdb_bucket = sensor_configuration[0][8]
        upload_periodicity = sensor_configuration[0][10]
        slidingWindow = sensor_configuration[0][11]
        uploadTechnology = sensor_configuration[0][12]
        
        active_lora_network = None
        if len(sensor_configuration[0]) > 16:
            active_lora_network = sensor_configuration[0][16]

        if uploadTechnology.lower() == "wifi":
            ip_address = cwifi.execute("""SELECT IP_Address FROM SensorCommunication""").fetchone()[0]
            wifi_connected = cwifi.execute("""SELECT WifiConnected FROM SensorCommunication""").fetchone()[0]

    else:
        print("Sensor is not currently configured. It is required a cloud IP address to connect to the cloud server via MQTT.\nPlease run the 'sensorConfiguration.py' script to configure the sensor.")
        exit(0)

except sqlite3.Error as error:
    print("Failed to read sensor configuration from local database.")
    exit(0)


dataAtual=dt.datetime.now(pytz.utc).replace(tzinfo=None)
dataAnalizar= dataAtual - dt.timedelta(minutes=int(slidingWindow))


# Get number of devices detected from database
try:

    conndev= sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db' , timeout=30)
    cdev = conndev.cursor()

    # Device counting - Data packets
    rows_data_packets = cdev.execute("""SELECT COUNT(*) FROM Data_Packets WHERE ((First_Record >= ? and First_Record <= ?) or (Last_Time_Found > ? and Last_Time_Found <= ?))""", (dataAnalizar, dataAtual, dataAnalizar, dataAtual)).fetchall()

    # Device counting - Probe Requests
    rows_probe_requests = cdev.execute("""SELECT COUNT(*) FROM Probe_Requests WHERE ((First_Record >= ? and First_Record <= ?) or (Last_Time_Found > ? and Last_Time_Found <= ?))""", (dataAnalizar, dataAtual, dataAnalizar, dataAtual)).fetchall()

    # Device counting - All
    detected_devices = rows_data_packets[0][0] + rows_probe_requests[0][0]

    cdev.close()
    conndev.close()

except sqlite3.Error as error:
    print("Failed to read number of devices detected from local database.")


def downlink_cb(port, payload):
    print(f"[CALLBACK] Downlink received on port {port}: {payload}")
    try:
        text = bytes.fromhex(payload).decode("utf-8")
    except Exception:
        text = ""
    print(f"[CALLBACK] Decoded text: '{text}'")

    if text == "r":
        print("[CALLBACK] Command: rebooting system...")
        os.system("sudo reboot")
    elif text == "a":
        print("[CALLBACK] Command: activate detection")
    elif text == "dis":
        print("[CALLBACK] Command: disable detection")
    else:
        print("[CALLBACK] Unknown command received.")


# Upload via Wi-Fi
if uploadTechnology.lower() == "wifi" and wifi_connected:

    dataAtual_unix = int(dataAtual.timestamp())

    mqtt_confirmation = publish_detections_mqtt_message(dataAtual_unix, detected_devices, f"sttoolkit-test/mqtt/wifi/numdetections/{influxdb_bucket}/{ip_address}/{sensorName}/{sensorUUID}")

    if mqtt_confirmation is True:

        # Check if exists a pending measurement to send
        while get_1st_pending_measurement() is not None:

            # Send first pending measurement from database, and wait for its confirmation
            unix_ts = get_1st_pending_measurement()[0]
            devices_detected = get_1st_pending_measurement()[1]

            mqtt_pend_confirmation = publish_detections_mqtt_message(unix_ts, devices_detected, f"sttoolkit-test/mqtt/wifi/numdetections/{influxdb_bucket}/{ip_address}/{sensorName}/{sensorUUID}")

            if mqtt_pend_confirmation is True:
                # Remove first pending measurement from database
                remove_1st_pending_measurement()
                continue
            else:
                break

# Upload via LoRa

elif uploadTechnology.lower() == "lora":
    
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
    sent = rak.send_lorawan_data(port=2, data=payload_hex)
    
    if not sent:
        print(f"[UPLOAD] Failed to send via {active_lora_network}")
        mark_lora_network_failed(active_lora_network)
        print("[UPLOAD] Network marked as failed, handover will be attempted")
    else:
        print(f"[UPLOAD] Successfully sent via {active_lora_network}")
        
        print(f"[UPLOAD] Listening for downlinks for {upload_periodicity} min...")
        t_end = time.time() + (upload_periodicity * 60) - 25
        #t_end = 20
        try:
            while time.time() < t_end:

                port, payload = rak.receive_data_C()
                if port and payload:
                    try:
                        text = bytes.fromhex(payload).decode("utf-8")
                    except Exception:
                        text = ""
                    print(f"Downlink received on port {port}: '{text}'")

                    if text == "r":
                        print("Command: rebooting system...")
                        os.system("sudo reboot")
                    elif text == "a":
                        print("Command: activate detection")
                    elif text == "dis":
                        print("Command: disable detection")
                    else:
                        print("Unknown command received.")
                time.sleep(1)
                
        except KeyboardInterrupt:
            print("[UPLOAD] Interrupted by user, exiting...")
        except Exception as e:
            print(f"[UPLOAD] LoRa communication error: {e}")
            mark_lora_network_failed(active_lora_network)
    
    rak.disconnect()



# If no communication technology is available
else:
    print("WARNING: No communication available for sending crowding measurements! \n\
        Please check the network conectivity for uploading data to the cloud server.")

    dataAtual_unix = int(dataAtual.timestamp())

    print("\nFailed to publish mqtt message.")
    print("\nSaving detection in database to send later, when conection available.")
    #save measurement in database
    store_pending_measurement(dataAtual_unix, detected_devices)

cwifi.close()
connwifi.close()






