import sqlite3
import os
import subprocess
import netifaces as ni
import time

from sensorFunctions import *
from event_logger import log_event

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
PENDING_SYNC_FILE = "/home/kali/Desktop/.pending_cloud_sync"
SENSOR_CONFIG_REMOTE = "/home/kali/Desktop/sensorConfigurationRemotely.py"

lock_acquired = acquire_script_lock(COMM_AVAILABLE_LOCK_FILE, "BOOT")

if not lock_acquired:
    exit(0)


# Check if Wi-Fi and LoRa upload are available
wifiAvailable = check_wifi_available()

# para teste
wifiAvailable = False
# loraAvailable = False

# set_lora_available(False)
# set_lora_connected(False)

loraAvailable = check_lora_available()

# Check Wi-Fi and LoRa upload connections
if wifiAvailable:
    wifiConnected = check_wifi_connection()
    # wifiConnected = False
else:
    wifiConnected = False


# set_lora_available(False)
# set_lora_connected(False)

# If LoRa available, get dev_eui from LoRa board
if loraAvailable:
    dev_eui = get_dev_eui()
    # Don't check connection here - let decide_upload_technology() handle join
    loraConnected = False
else:
    loraConnected = False
    dev_eui = ""


# Get upload and detection interfaces
upload_interface, detection_interface = check_upload_detection_interfaces(True)

print(wifiConnected)

if wifiConnected:
    ip_address = ni.ifaddresses(upload_interface)[ni.AF_INET][0]['addr']

    if os.path.exists(PENDING_SYNC_FILE):
        print("[SYNC] Pending cloud sync found. Running --sync-only...")

    try:
        result = subprocess.run(
            ["/usr/bin/python3", SENSOR_CONFIG_REMOTE, "--sync-only"],
            text=True,
            capture_output=True,
            timeout=10,
            check=False
        )

        print("[SYNC] stdout:")
        print(result.stdout)

        print("[SYNC] stderr:")
        print(result.stderr)

        if result.returncode == 0:
            print("[SYNC] sync-only completed.")
        else:
            print(f"[SYNC][ERROR] sync-only failed with code {result.returncode}.")

    except subprocess.TimeoutExpired:
        print("[SYNC][ERROR] sync-only timed out.")

    except Exception as e:
        print(f"[SYNC][ERROR] Failed to run sync-only: {e}")

else:
    ip_address = "nd"


# Check previous communication technologies available on the local database
try:
    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    upload_tech = "none"
    active_lora_network = None
    sensor_configured = False
    sensor_status = "Disabled"

    sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

    if sensor_communication is None:
        # Insert new row
        print("There is no row in 'SensorCommunication' table. Inserting new row in table 'SensorCommunication'.")

        cwifi.execute(
            """INSERT INTO SensorCommunication VALUES(?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
            (
                wifiAvailable,
                loraAvailable,
                wifiConnected,
                loraConnected,
                ip_address,
                upload_interface,
                detection_interface,
            )
        )

    else:
        
        previous_detection_interface = sensor_communication[6]

        # Update row
        cwifi.execute(
            """UPDATE SensorCommunication 
               SET WifiAvailable=?, 
                   LoRaAvailable=?, 
                   WifiConnected=?, 
                   LoRaConnected=?, 
                   IP_Address=?, 
                   Upload_Interface=?, 
                   Detection_Interface=?, 
                   Last_Update=CURRENT_TIMESTAMP""",
            (
                wifiAvailable,
                loraAvailable,
                wifiConnected,
                loraConnected,
                ip_address,
                upload_interface,
                detection_interface,
            )
        )

        sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchone()

        # Check if sensor has already a configuration
        if sensor_configuration is None:
            print("Sensor is not currently configured. No more actions performed.")

        else:
            sensor_configured = True

            sensor_uuid = sensor_configuration[0]
            sensor_status = sensor_configuration[4]
            current_upload_tech = sensor_configuration[12]

            current_lora_network = None
            if len(sensor_configuration) > 16:
                current_lora_network = sensor_configuration[16]

            locationSendMode = "boot"
            try:
                row = cwifi.execute(
                    """SELECT COALESCE(Location_Send_Mode, 'boot') FROM SensorConfiguration"""
                ).fetchone()

                if row and row[0]:
                    locationSendMode = row[0]

            except sqlite3.Error:
                locationSendMode = "boot"

            print(f"[BOOT] Current configuration - Technology: {current_upload_tech}, LoRa Network: {current_lora_network}")

            upload_tech, active_lora_network = decide_upload_technology(cursor=cwifi)

            log_event(
                "boot_handover_complete",
                upload_tech=upload_tech,
                lora_network=active_lora_network,
                wifi_available=wifiAvailable,
                wifi_connected=wifiConnected,
                lora_available=loraAvailable
            )

            print(f"[BOOT] Handover cascade completed - Selected: {upload_tech}")

            if active_lora_network:
                print(f"[BOOT] Active LoRa network: {active_lora_network}")
            elif upload_tech == 'none':
                print("[BOOT] No connectivity available - uploads disabled")

            if current_upload_tech != upload_tech:
                print(f"[BOOT] Upload technology changed: '{current_upload_tech}' → '{upload_tech}'")
            else:
                print(f"[BOOT] Upload technology unchanged: '{upload_tech}'")

            lora_connected_flag = (upload_tech == 'lora')

            cwifi.execute(
                """UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""",
                (lora_connected_flag,)
            )

            # Check if detection interface changed
            if previous_detection_interface != detection_interface:
                print(f"[BOOT] Detection interface changed: {previous_detection_interface} → {detection_interface}")

                cwifi.execute(
                    """UPDATE SensorCommunication SET Detection_Interface=?, Last_Update=CURRENT_TIMESTAMP""",
                    (detection_interface,)
                )

                # Rewrite crontab tasks file
                write_crontab_file(
                    sensor_status,
                    detection_interface,
                    sensor_configuration[10],
                    sensor_configuration[13],
                    sensor_configuration[14],
                    locationSendMode
                )

            gps_position = try_get_boot_gps_position(
                max_wait_sec=60,
                warmup_sec=5,
                min_good_samples=4,
                eph_max=12.0
            )

            if gps_position is not None:
                gps_lat, gps_lon, gps_quality = gps_position

                cwifi.execute(
                    """UPDATE SensorConfiguration SET Latitude=?, Longitude=?, Last_Update=CURRENT_TIMESTAMP""",
                    (gps_lat, gps_lon)
                )

                print(f"[GPS] Updated SensorConfiguration location from GPS ({gps_quality}).")

            else:
                print("[GPS] Keeping current DB location (fallback).")


    # Commit changes
    connwifi.commit()

    try:
        with open(BOOT_COMPLETE_FILE, "w") as f:
            f.write("1")

        log_event("boot_complete")

    except Exception:
        pass



    if sensor_configured and upload_tech != "none":
        if sensor_status == "Active":
            print("[BOOT] Sending sensor location after startup checks...")

            try:
                subprocess.run(["/usr/bin/python3", SENSOR_SEND_LOCATION_FILEPATH, "--boot"], check=False)

            except Exception as e:
                print(f"[BOOT] Failed to send sensor location: {e}")

        else:
            print("[BOOT] Sensor disabled. Skipping location upload.")

    cwifi.close()

    # GPS is only used at boot to acquire/update the deployment location.
    # Periodic location reporting reuses the coordinates stored in the local DB,
    # avoiding continuous GPS power consumption.
    disable_gps()

except sqlite3.Error as error:
    print("Failed to save communication technologies in local database.", error)

finally:
    if connwifi:
        connwifi.close()

    release_script_lock(COMM_AVAILABLE_LOCK_FILE)