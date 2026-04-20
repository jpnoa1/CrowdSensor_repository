import sqlite3
import subprocess
import netifaces as ni
import time 
from sensorFunctions import *

#                          sensorCheckCommunicationAvailable.py
#
#   This script is responsible for automatically detecting the communication technologies 
#   available and the upload and detection interfaces on the sensor, and inserting/updating
#   that information in the sensor local database.
#
#   It is aimed to run on the sensor boot, so that the sensor can always be aware of the
#   communication technologies and the interfaces for uploading data and for detection.
#
#   Author: Tomas Santos
#   Date: 05-03-2024
#

connwifi = None
lock_acquired = acquire_script_lock(COMM_AVAILABLE_LOCK_FILE, "BOOT")
if not lock_acquired:
    exit(0)

#Check if Wi-Fi and LoRa upload are available
wifiAvailable = check_wifi_available()
#para teste
wifiAvailable = False
loraAvailable = check_lora_available()                           
print("loraAvailable:"+str(loraAvailable))

#Check Wi-Fi and LoRa upload connections
if wifiAvailable:
    wifiConnected = check_wifi_connection()
    #wifiConnected = False
else:
    wifiConnected = False


#set_lora_available(False)
#set_lora_connected(False)
      #Upload via LoRa can take too long (minutes in some cases), only checking LoRa connection after updating database

#If LoRa available, get dev_eui from LoRa board
if loraAvailable:
    dev_eui = get_dev_eui()
    # Don't check connection here - let decide_upload_technology() handle join
    loraConnected = False
else:
    loraConnected = False
    dev_eui = ""


#Get upload and detection interfaces
upload_interface, detection_interface = check_upload_detection_interfaces(True)

print(wifiConnected)
if wifiConnected:
    ip_address = ni.ifaddresses(upload_interface)[ni.AF_INET][0]['addr']
else:#mudei
    ip_address = "nd"
#Check previous communication technologies available on the local database
try:

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()
    upload_tech = "none"
    sensor_configured = False

    sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

    if sensor_communication is None:

        #Insert new row 
        print("There is no row in 'SensorCommunication' table. Inserting new row in table 'SensorCommunication'.")
        sensor_communication = cwifi.execute("""INSERT INTO SensorCommunication VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""", (wifiAvailable, loraAvailable, wifiConnected, loraConnected, ip_address, upload_interface, detection_interface,))
    
    else:

        #Update row
        cwifi.execute("""UPDATE SensorCommunication SET WifiAvailable=?, LoRaAvailable=?, WifiConnected=?, LoRaConnected=?, IP_Address=?, Upload_Interface=?, Detection_Interface=?, Last_Update=CURRENT_TIMESTAMP""", (wifiAvailable, loraAvailable, wifiConnected, loraConnected, ip_address, upload_interface, detection_interface,))

        sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchone()

        #Check if sensor has already a configuration
        if sensor_configuration is None:
            print("Sensor is not currently configured. No more actions performed.")
        else:
            sensor_configured = True

            sensor_uuid = sensor_configuration[0]
            current_upload_tech = sensor_configuration[12]
            
            current_lora_network = None
            if len(sensor_configuration) > 16:
                current_lora_network = sensor_configuration[16]
            
            print(f"[BOOT] Current configuration - Technology: {current_upload_tech}, LoRa Network: {current_lora_network}")
            
            upload_tech, active_lora_network = decide_upload_technology(cursor=cwifi)
            
            print(f"[BOOT] Handover cascade completed - Selected: {upload_tech}")
            if active_lora_network:
                print(f"[BOOT] Active LoRa network: {active_lora_network}")
            elif upload_tech == 'none':
                print(f"[BOOT] No connectivity available - uploads disabled")
            
            if current_upload_tech != upload_tech:
                print(f"[BOOT] Upload technology changed: '{current_upload_tech}' → '{upload_tech}'")
            else:
                print(f"[BOOT] Upload technology unchanged: '{upload_tech}'")
            
            lora_connected_flag = (upload_tech == 'lora')
            cwifi.execute(
                """UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""",
                (lora_connected_flag,)
            )


            #Check if detection interface changed
            current_detec_if = sensor_configuration[3]
            
            if current_detec_if != detection_interface:
                cwifi.execute("""UPDATE SensorCommunication SET Detection_Interface=?, Last_Update=CURRENT_TIMESTAMP""", (detection_interface,))

                #Rewrite crontab tasks file
                if sensor_configuration is not None:
                    status = sensor_configuration[4]
                    uploadPeriodicity = sensor_configuration[10]
                    rebootPeriodicity = sensor_configuration[13]
                    rebootTime = sensor_configuration[14]

                    write_crontab_file(status, detection_interface, uploadPeriodicity, rebootPeriodicity, rebootTime)

            gps_position = try_get_boot_gps_position(max_wait_sec=120, warmup_sec=5, min_good_samples=4, eph_max=12.0)
            if gps_position is not None:
                gps_lat, gps_lon, gps_quality = gps_position
                cwifi.execute(
                    """UPDATE SensorConfiguration SET Latitude=?, Longitude=?, Last_Update=CURRENT_TIMESTAMP""",
                    (gps_lat, gps_lon)
                )
                print(f"[GPS] Updated SensorConfiguration location from GPS ({gps_quality}).")
            else:
                print("[GPS] Keeping current DB location (fallback).")


    #Commit changes
    connwifi.commit()

    try:
        with open(BOOT_COMPLETE_FILE, "w") as f:
            f.write("1")
    except Exception:
        pass

    if sensor_configured and upload_tech != "none":
        print("[BOOT] Sending sensor location after startup checks...")
        try:
            subprocess.run(["/usr/bin/python3", SENSOR_SEND_LOCATION_FILEPATH], check=False)
        except Exception as e:
            print(f"[BOOT] Failed to send sensor location: {e}")

    cwifi.close()

except sqlite3.Error as error:
    print("Failed to save communication technologies in local database.", error)

finally:
    if connwifi:
        connwifi.close()

    release_script_lock(COMM_AVAILABLE_LOCK_FILE)

#mudei
#if loraAvailable == True:
#    heliumNodeSetup()
