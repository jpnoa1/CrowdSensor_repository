import sqlite3
import datetime as dt
import pytz
import matplotlib.pyplot as plt; plt.rcdefaults()
import os
import json
import sys

from sensorFunctions import *

if not os.path.exists(BOOT_COMPLETE_FILE):
    print("[LOCATION] Boot initialization not complete yet. Exiting.")
    sys.exit(0)

_, available_released = wait_for_script_lock(
    COMM_AVAILABLE_LOCK_FILE,
    max_wait_sec=60,
    poll_sec=2,
    log_prefix="[LOCATION]"
)
if not available_released:
    print("[LOCATION] sensorCommunicationAvailable.py still running. Proceeding carefully.")

# Read sensor configuration from database

try:
    connwifi= sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db' , timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

    #Sensor configuration
    if len(sensor_configuration) != 0:
        sensor_UUID = sensor_configuration[0][0]
        sensor_name = sensor_configuration[0][1]
        latitude = sensor_configuration[0][2]
        longitude = sensor_configuration[0][3]
        cloud_ip_addr = sensor_configuration[0][6]
        influx_org = sensor_configuration[0][7]
        influx_bucket = sensor_configuration[0][8]
        influx_token = sensor_configuration[0][9]
        uploadTechnology = sensor_configuration[0][12]

        #if uploadTechnology.lower() == "wifi":
        #    ip_address = cwifi.execute("""SELECT IP_Address FROM SensorCommunication""").fetchone()[0]


    else:
        print("Failed to read sensor configuration from local database. Please make sure to \nconfigure a sensor configuration by running the 'sensorConfiguration.py' script first.")
        exit(0)

except sqlite3.Error as error:
    print("Failed to read sensor configuration from local database.")
    exit(0)

finally:
    if connwifi:
        cwifi.close()
        connwifi.close()


dataAtual=dt.datetime.now(pytz.utc).replace(tzinfo=None)

print(latitude, longitude)
location = {
"latitude": latitude,
"longitude": longitude
}

json_location = json.dumps(location)

# Send sensor location to InfluxDB
if uploadTechnology.lower() == "wifi":
   
    publish_location_mqtt_message(json_location, f"sttoolkit-test/mqtt/wifi/v2/sensorLocation/{sensor_UUID}")
    print(f"Location '({latitude},{longitude})' was sent to the cloud server for sensor '{sensor_name}'.")

elif uploadTechnology.lower() == "lora":

    rak = RAK3172("/dev/ttyAMA0", 115200)
    rak.connect()

    try:
        time.sleep(0.5)
        # CSV Format  "L,<lat>,<lon>,<uuid>"
        payload = f"L,{float(latitude):.5f},{float(longitude):.5f}"
    
        print(f"A enviar via LoRa: {payload}")
        payload_hex = payload.encode().hex()
        sent = rak.send_lorawan_data(2, payload_hex)
        if sent:
            print("Localização enviada com sucesso via LoRa.")
        else:
            print("Falha ao enviar localização via LoRa.")
        
    except Exception as e:
        print(f"Erro durante envio via LoRa: {e}")
    finally:
        try:
            rak.disconnect()
        except:
            pass