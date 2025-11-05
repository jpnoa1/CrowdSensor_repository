

import sqlite3
import datetime as dt
from time import sleep
import matplotlib.pyplot as plt; plt.rcdefaults()
import subprocess
import os
import pytz
import uuid
import netifaces as ni

from swARM_at.RAK3172 import RAK3172
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

        if uploadTechnology.lower() == "wifi":
            ip_address = cwifi.execute("""SELECT IP_Address FROM SensorCommunication""").fetchone()[0]


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

    conndev= sqlite3.connect('/home/kali/Desktop/DB/DeviceRecords.db' , timeout=30)
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
if uploadTechnology.lower() == "wifi":
    publish_mqtt_message(detected_devices, f"sttoolkit/mqtt/wifi/numdetections/{influxdb_bucket}/{ip_address}/{sensorName}/{sensorUUID}")

# Upload via LoRa

elif uploadTechnology.lower() == "lora":

    # Initialize RAK3172
    rak = RAK3172("/dev/ttyUSB0", 115200)
    rak.connect()
    # Build payload (same logic as before)
    message = f"C,{detected_devices}"
    print(f"Payload to send: {message}")

    # Convert to hexadecimal (LoRaWAN expects hex payloads)
    payload_hex = message.encode().hex()

    # Try sending payload on port 2
    sent = rak.send_lorawan_data(2, payload_hex)

    if not sent:
        print("Failed to send via LoRa. Trying to re-join…")
        try:
            rak.join_network(join=1, auto_join=0, reattempt_interval=8, join_attempts=8)
            time.sleep(1)
            sent = rak.send_lorawan_data(2, payload_hex)
        except Exception as e:
            print(f"Unexpected error during join: {e}")
    else:
        print(f"Message sent successfully: {message}")

    # Listen for downlinks during the upload window
    if sent:
        print("Waiting for downlinks...")
        t_end = time.time() + (upload_periodicity * 60) - 25
        #t_end = 20
        try:
            while time.time() < t_end:

                port, payload = rak.receive_data()
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
            
            STATUS_FILE = "/tmp/rak_njs"

            joined = rak.check_join_status()
            print("este foi o joined"+str(joined))   # returns True/False
            with open(STATUS_FILE, "w") as f:  # write plain text: '1' or '0'
                f.write("1" if joined else "0")
            print(f"Join status saved to {STATUS_FILE}: {'1' if joined else '0'}")
                
        except KeyboardInterrupt:
            print("Interrupted by user, exiting…")
    
    rak.disconnect()



# If no communication technology is available
else:
    print("WARNING: No communication available for sending crowding measurements! \n\
        Please check the network conectivity for uploading data to the cloud server.")


cwifi.close()
connwifi.close()






