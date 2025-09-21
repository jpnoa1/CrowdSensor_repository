import sqlite3
import datetime as dt
import matplotlib.pyplot as plt; plt.rcdefaults()
import subprocess
import os
import pytz
import uuid
import netifaces as ni
from ASR6501 import asr6501,STATUS

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





# Upload via Wi-Fi
if uploadTechnology.lower() == "wifi":
    publish_mqtt_message(detected_devices, f"sttoolkit/mqtt/wifi/numdetections/{influxdb_bucket}/{ip_address}/{sensorName}/{sensorUUID}")

# Upload via LoRa

elif uploadTechnology.lower() == "lora":

    serialPort = serial.Serial("/dev/ttyUSB0", 115200, timeout=2)

    LoRaWAN = asr6501(serialPort, logging.DEBUG)

    LoRaWAN.setDownlinkCallback((downlink_cb))
    # building the message to TTN
    message = f"{detected_devices},{influxdb_bucket},{sensorName},{sensorUUID}"
    #ensure_join()
    #define the LoRaWAN application port
    LoRaWAN.setApplicationPort(2)
    #LoRaWAN.join()
    # sends the payload (0=unconfirmed, 1=confirmed)
    sent = LoRaWAN.sendPayload(message, confirm=0, nbtrials=8)
    print("envieii isto"+str(sent))
    #If message was not sent
    if not sent:
        print(" Failed to send via LoRa. Trying to re-join…")
        try:
            LoRaWAN.join()
        except Exception as e:
            print(f"Unexpected Error joining {e}")

    else:
        print(f" Message sent: {message}, waiting for downlinks")
        t_end = time.time() + (upload_periodicity * 60)-10
        try:
            while time.time() < t_end:
                LoRaWAN.checkForDownlink()
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("Interrupted by user, exiting…")


# If no communication technology is available
else:
    print("WARNING: No communication available for sending crowding measurements! \n\
        Please check the network conectivity for uploading data to the cloud server.")


cwifi.close()
connwifi.close()






