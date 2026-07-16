import subprocess
import sqlite3
import os
import uuid
import netifaces as ni
import time
from paho.mqtt import client as mqtt_client
import random
import logging
from swARM_at_custom.swARM_at.RAK3172 import RAK3172
import json
from datetime import datetime
import ssl
from event_logger import log_event
import threading




logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sensor")


#           sensorConfiguration.py
#
#   This script allows to setup all sensor configurations
#   configurations by running only this script.
#
#
#   The configurations are divided in three
#   different parts:
#   (1) Sensor General Configuration; -> Configuration of (Sensor UUID, Sensor Name, Sensor Location, Status, Power Filtration)
#   (2) Sensor Upload Configuration; -> Configuration of InfluxDB upload parameters (Cloud Server IP Address, Org Name, Bucket Name, Authorization token) 
#   (3) Sensor Tasks Configuration. -> Configuration of cronjobs for automatic time-scheduling tasks.
#
#   Author: Tomas Mestre Santos
#   Date: 08-02-2024
#


# Filepath of python script for sending crowding data to InfluxDB
SENSOR_SEND_CROWDING_DATA_FILEPATH = "/home/kali/Desktop/sendCrowdingData.py"

# Filepath for python script for sending sensor location to InfluxDB
SENSOR_SEND_LOCATION_FILEPATH = "/home/kali/Desktop/sendSensorLocation.py"

# Filepath of python script for Checking upload technology 
SENSOR_COMMUNICATION_CHECK_FILEPATH = "/home/kali/Desktop/sensorCommunicationCheck.py"

# Filepath of python script for Checking and changing upload technology 
SENSOR_COMMUNICATION_AVAILABLE_FILEPATH = "/home/kali/Desktop/sensorCommunicationAvailable.py"

#Filepath to cronjobs output text file
DEFAULT_CRONJOBS_FILEPATH = "/home/kali/Desktop/cronjobs_default.txt"
CONFIGURED_CRONJOBS_FILEPATH = "/home/kali/Desktop/cronjobs_configured.txt"

# Filepath of airodump-ng.c detection software
#AIRODUMP_FILEPATH = "/home/kali/Desktop/aircrack-ng-1.7/src/airodump-ng/airodump-ng.c"

#MQTT Paramenters
MQTT_PORT = 8883
TOPIC_NETWORKS = "monicrowd/sensors/networks"

#Number of configuration parameters (uuid, name, etc...)
SENSOR_CONFIG_PARAMETERS_NUMB = 15
DEFAULT_CONFIG_PARAMETERS_NUMB = 12

PID_FILE = "/home/kali/Desktop/Sniffer/sniffer.pid"

#Raspberry Pi OUIs List
rpi_oui = ["dc:a6:32", "b8:27:eb", "28:cd:c1", "2c:cf:67", "3a:35:41", "d8:3a:dd", "e4:5f:01"]

#Lora
LORA_SERIAL_PORT = "/dev/ttyAMA0"
COMM_AVAILABLE_LOCK_FILE = "/tmp/sensorCommunicationAvailable.lock"
BOOT_COMPLETE_FILE = "/tmp/sensor_boot_complete"
NMCLI_BIN = "/usr/bin/nmcli"
WLAN_UPLOAD_IFACE = "wlan0"
LORA_SEQ_FILE = "/home/kali/Desktop/DB/lora_seq.txt"


def acquire_script_lock(lock_file=COMM_AVAILABLE_LOCK_FILE, script_name="script"):
    if os.path.exists(lock_file):
        try:
            with open(lock_file, "r") as f:
                old_pid = int((f.read() or "0").strip())
            os.kill(old_pid, 0)
            print(f"[{script_name}] Lock active (pid={old_pid}). Exiting.")
            return False
        except Exception:
            # stale/corrupt lock
            try:
                os.remove(lock_file)
            except Exception:
                pass

    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))
    return True


def release_script_lock(lock_file=COMM_AVAILABLE_LOCK_FILE):
    try:
        if os.path.exists(lock_file):
            os.remove(lock_file)
    except Exception:
        pass


def wait_for_script_lock(lock_file=COMM_AVAILABLE_LOCK_FILE, max_wait_sec=90, poll_sec=2, log_prefix="[WAIT]"):
    waited = 0
    while os.path.exists(lock_file) and waited < max_wait_sec:
        print(f"{log_prefix} Waiting for lock '{lock_file}'... ({waited}s)")
        time.sleep(poll_sec)
        waited += poll_sec

    released = not os.path.exists(lock_file)
    return waited, released


def _median(values):
    vals = sorted(values)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def try_get_boot_gps_position(max_wait_sec=120, warmup_sec=5, min_good_samples=4, eph_max=12.0):
    """Try to get a robust GPS position; return (lat, lon, quality) or None."""
    try:
        import gps
        import select
    except Exception as e:
        print(f"[GPS] gps module unavailable ({e}). Using DB location fallback.")
        return None

    try:
        session = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)
    except Exception as e:
        print(f"[GPS] Failed to connect to gpsd ({e}). Using DB location fallback.")
        return None

    print(f"[GPS] Warm-up for {warmup_sec}s...")
    warmup_end = time.time() + warmup_sec
    while time.time() < warmup_end:
        try:
            # Check if there is data to read without blocking
            if session.waiting(timeout=0.2):
                session.next()
        except Exception:
            pass
        time.sleep(0.05)

    print(f"[GPS] Collecting samples (timeout={max_wait_sec}s)...")
    all_samples = []
    good_samples = []
    end_time = time.time() + max_wait_sec

    while time.time() < end_time:
        try:
            if session.waiting(timeout=0.2):
                report = session.next()
                if report.get("class") == "TPV":
                    mode = getattr(report, "mode", 0)
                    lat = getattr(report, "lat", None)
                    lon = getattr(report, "lon", None)
                    eph = getattr(report, "eph", None)

                    if mode >= 2 and lat is not None and lon is not None:
                        all_samples.append((float(lat), float(lon), eph))

                        if eph is not None and float(eph) <= eph_max:
                            good_samples.append((float(lat), float(lon), eph))

                        if len(good_samples) >= min_good_samples:
                            break
        except Exception:
            pass
        
        # Don't need extra sleep because session.waiting(0.2) already delays if no data
        time.sleep(0.05)

    chosen = good_samples if len(good_samples) >= min_good_samples else all_samples
    if not chosen:
        print("[GPS] No valid GPS fix acquired in time. Using DB location fallback.")
        return None

    try:
        import numpy as np
        import math
        
        lats = np.array([s[0] for s in chosen])
        lons = np.array([s[1] for s in chosen])

        lat_med = np.median(lats)
        lon_med = np.median(lons)

        # Distâncias em metros (aproximação simples)
        dx = (lons - lon_med) * 111320 * math.cos(math.radians(lat_med))
        dy = (lats - lat_med) * 110540
        dist = np.sqrt(dx**2 + dy**2)
        
        # Median Absolute Deviation (MAD)
        mad = np.median(np.abs(dist - np.median(dist)))

        if mad > 0:
            # Filtra pontos que estejam demasiado longe da mediana
            mask = dist < max(5.0, 3 * mad)
            lat = float(np.mean(lats[mask]))
            lon = float(np.mean(lons[mask]))
        else:
            lat = float(lat_med)
            lon = float(lon_med)
            
    except ImportError:
        print("[GPS] Aviso: 'numpy' não encontrado. A usar cálculo por mediana simples.")
        lat = _median([s[0] for s in chosen])
        lon = _median([s[1] for s in chosen])

    quality = "GOOD" if chosen is good_samples else "FALLBACK"

    print(f"[GPS] Position acquired ({quality}): lat={lat:.7f}, lon={lon:.7f}")
    return lat, lon, quality

#Auxiliary functions

def valid_latlon(lat: float, lon: float):
    try:
        float(lat), float(lon)

        if (float(lat) >= -90 and float(lat) <= 90) and (float(lon) >= -180 and float(lon) < 180):
            return True
        else:
            return False    
        
    except ValueError:
        return False 

def valid_sensor_name(name):
    valid_name = True

    if name.strip() == '':
        print("Sensor name is empty.")
        valid_name = False
    else:
        for c in name:
            if not c.isalnum():
                valid_name = False
                print("Sensor name can only contain alphanumeric characters (alphabet letters and numbers).")
                break

    return valid_name

def validate_IP_address(ipAddress):

    valid_IP = True
 
    for i in ipAddress:

        if i.isalpha():
            print("The IP address should only contain numbers.")
            valid_IP = False
            break

    if valid_IP: 
        dot_count = 0

        for i in ipAddress:

            if i == ".":
                dot_count += 1

        if dot_count != 3:
            print("The IP address is not in the correct syntax.")
            valid_IP = False

        if valid_IP:

            ip_list = list(map(str, ipAddress.split('.')))  
        
            for element in ip_list:  
                if element=='' or (int(element) < 0 or int(element) > 255 or (element[0]=='0' and len(element)!=1)):  
                    print("The IP address is not in the correct syntax.")
                    valid_IP = False
                    break
    
    return valid_IP

def get_mqtt_credentials_from_db():
    try:
        conn = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=10)
        cursor = conn.cursor()

        row = cursor.execute("""
            SELECT MQTT_Username, MQTT_Password
            FROM SensorConfiguration
            LIMIT 1
        """).fetchone()

        cursor.close()
        conn.close()

        if not row:
            print("[MQTT][ERROR] No SensorConfiguration row found.")
            return None, None

        username, password = row

        username = str(username).strip() if username is not None else ""
        password = str(password).strip() if password is not None else ""

        if not username or not password:
            print("[MQTT][ERROR] MQTT credentials are empty in database.")
            return None, None

        return username, password

    except sqlite3.Error as error:
        print(f"[MQTT][ERROR] Failed to read MQTT credentials from database: {error}")
        return None, None
    
def connect_mqtt(timeout=10):
    client_id = f"python-mqtt-{uuid.uuid4().hex[:8]}"

    # Obter o endereço do servidor MQTT
    try:
        connwifi = sqlite3.connect(
            "/home/kali/Desktop/DB/SensorConfiguration.db",
            timeout=30
        )
        cwifi = connwifi.cursor()

        result = cwifi.execute(
            "SELECT Cloud_IP_Address FROM SensorConfiguration"
        ).fetchone()

        cwifi.close()
        connwifi.close()

        if result is None or not result[0]:
            raise RuntimeError(
                "Cloud_IP_Address is not configured in SensorConfiguration."
            )

        cloud_ip_addr = result[0]

    except sqlite3.Error as error:
        raise RuntimeError(
            f"Failed to get MQTT server address from database: {error}"
        ) from error

    connected_event = threading.Event()
    connection_result = {
        "success": False,
        "reason_code": None
    }

    def on_connect(client, userdata, flags, reason_code, properties):
        connection_result["reason_code"] = reason_code
        connection_result["success"] = reason_code == 0

        if reason_code == 0:
            print("Connected to MQTT Broker!")
        else:
            print(
                f"Failed to connect to MQTT Broker: "
                f"reason_code={reason_code}"
            )

        connected_event.set()

    client = mqtt_client.Client(
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
        client_id=client_id
    )

    mqtt_username, mqtt_password = get_mqtt_credentials_from_db()

    if not mqtt_username or not mqtt_password:
        raise RuntimeError(
            "MQTT credentials are not configured in the database."
        )

    client.username_pw_set(
        mqtt_username,
        mqtt_password
    )

    client.on_connect = on_connect

    client.tls_set(
        tls_version=ssl.PROTOCOL_TLS
    )

    connect_rc = client.connect(
        cloud_ip_addr,
        MQTT_PORT,
        keepalive=60
    )

    if connect_rc != mqtt_client.MQTT_ERR_SUCCESS:
        raise ConnectionError(
            f"MQTT TCP connection failed: rc={connect_rc}"
        )

    # Essencial para processar CONNACK, PUBACK e callbacks
    client.loop_start()

    # Esperar pela confirmação MQTT da ligação
    if not connected_event.wait(timeout=timeout):
        client.loop_stop()
        client.disconnect()

        raise TimeoutError(
            "MQTT connection timeout: CONNACK was not received."
        )

    if not connection_result["success"]:
        reason_code = connection_result["reason_code"]

        client.disconnect()
        client.loop_stop()

        raise ConnectionError(
            f"MQTT broker rejected the connection: {reason_code}"
        )

    return client


#Configuration prompts functions
def fast_config():
    print("------------------------------------------------------------------")
    print("------               FAST CONFIGURATION MODE                ------")
    print("------------------------------------------------------------------")

    # Generate a UUID based on Raspberry Pi's hardware MAC address
    uuid_from_mac_addr = uuid.getnode()
    print("Sensor unique identifier (UUID) created: " + str(uuid_from_mac_addr))

    #Sensor Name
    sensorName = input("Sensor name: ").strip()
    while valid_sensor_name(sensorName) is False:
        sensorName = input("Sensor name: ").strip()

    upload_technology = get_upload_technology()

    upload_interface, detection_interface = check_upload_detection_interfaces(False)

    return uuid_from_mac_addr, sensorName, upload_technology, upload_interface, detection_interface

def config_general():

    print("------------------------------------------------------------------")
    print("------             SENSOR GENERAL CONFIGURATION             ------")
    print("------------------------------------------------------------------")

    # Generate a UUID based on Raspberry Pi's hardware MAC address
    uuid_from_mac_addr = uuid.getnode()
    print("Sensor unique identifier (UUID) created: " + str(uuid_from_mac_addr))

    #Sensor Name
    sensorName = input("Sensor name: ").strip()
    while valid_sensor_name(sensorName) is False:
        sensorName = input("Sensor name: ").strip()
        
    # Sensor Location(Latitude, Longitude)
    latitude, longitude = (10000, 0)   # Initial invalid coordinates on purpose
    while valid_latlon(latitude, longitude) is False:

        lat, lon = input("Sensor location (latitude, longitude) separeted by commas [E.g.: (31.66, -9.34)]: ").split(",")

        latitude = lat.strip()
        longitude = lon.strip()

        if latitude == '' or longitude == '':
            print("Not enought values. 2 arguments expected, 1 inserted. Please try again.")
        else:
            if valid_latlon(latitude.strip(), longitude.strip()) is False:
                print("Invalid coordinates. Please try again.")

    # Status ('Active' or 'Disabled')
    status = ''
    while status not in ('Active', 'Disabled'):
        status = input("Status ('Active' or 'Disabled'): ").strip()

        if status not in ('Active', 'Disabled'):
            print("\tPlease enter 'Active' or 'Disabled'.")

    # Power Filtration
    power_filtration = '100'  # Initial invalid power filtration on purpose
    while not (power_filtration == "0" or (int(power_filtration) >= -100 and int(power_filtration) <= 0)):
        power_filtration = input("Power Filtration [-100, 0] dB (Insert '0' to ignore): ").strip()

        if power_filtration == "0":
            print("No Power Filtration")
            power_filtration = 0

        else:

            if not power_filtration[1:].isnumeric():
                print("\tPower filtration must be a number.")
                power_filtration = '100'    # Set power filtration to a number on purpose
                                
            elif power_filtration[0] == "-":
                if not (int(power_filtration) >= -100 and int(power_filtration) <= 0):
                    print("\tPower filtration must be between [-100, 0] dB.")
            else:
                print("\tPower filtration must be between [-100, 0] dB.")
                power_filtration = '100'    # Set power filtration to a number on purpose



    return uuid_from_mac_addr, sensorName, latitude, longitude, status, power_filtration

def config_influx():

    print("------------------------------------------------------------------")
    print("------             SENSOR UPLOAD CONFIGURATION              ------")
    print("------------------------------------------------------------------")

    cloudServerIPAddress = input("Cloud Server IP Address: ").strip()
    while validate_IP_address(cloudServerIPAddress) is not True:
        cloudServerIPAddress = input("Cloud Server IP Address: ").strip()
    influxDB_Org_Name = input("InfluxDB Organization name: ").strip()
    influxDB_Bucket = input("InfluxDB Bucket name: ").strip()
    authorization_Token = input("Authorization token: ").strip()


    return cloudServerIPAddress, influxDB_Org_Name, influxDB_Bucket, authorization_Token

def config_tasks():

    print("------------------------------------------------------------------")
    print("------               SENSOR TAKS CONFIGURATION              ------")
    print("------------------------------------------------------------------")

    uploadInterface, detectionInterface = check_upload_detection_interfaces(False)

    # Cron jobs configuration
    print("TASKS CONFIGURATION:")

    # Periodic upload of crowding data to the Cloud Server
    print("(1) Periodic upload of crowding data to the Cloud Server:")

    uploadPeriodicity = ''
    while not (uploadPeriodicity.isdigit() and (int(uploadPeriodicity) > 0 and int(uploadPeriodicity) < 60 )):

        uploadPeriodicity = input("\tUpload periodicity of messages (in minutes [1-59]): ").strip()

        if not uploadPeriodicity.isdigit():
            print("\tPeriodicity must be a number.")
        elif uploadPeriodicity.isdigit() and not (int(uploadPeriodicity) > 0 and int(uploadPeriodicity) < 60):
            print("\tPeriodicity must be between 1 and 59.")

    slidingWindow =''
    while not (slidingWindow.isdigit() and (int(slidingWindow) > 0 and int(slidingWindow) < 60)):

        slidingWindow = input("\tSliding window time (in minutes [1-59]): ").strip()

        if not slidingWindow.isdigit():
            print("\tSliding window must be a number.")
        elif slidingWindow.isdigit() and not (int(slidingWindow) > 0 and int(slidingWindow) < 60):
            print("\tSliding window must be between 1 and 59.")

    # Upload Technology
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        sensor_communication = cwifi.execute("""SELECT WifiAvailable, LoRaAvailable, WifiConnected, LoRaConnected FROM SensorCommunication""").fetchone()

        wifiAvailable = sensor_communication[0]
        loraAvailable = sensor_communication[1]
        wifiConnected = sensor_communication[2]
        loraConnected = sensor_communication[3]

        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to read data from database.", error)

    if wifiAvailable and wifiConnected:
        technology = 'wifi'
    elif loraAvailable and loraConnected:
        technology = 'lora'

    print(f"Upload technology '{technology}' automatically selected for data communication.")
    time.sleep(1)

    # Periodic delete of outdated and unnecessary data from local database
    '''
    print("(4) Periodic delete of outdated and unnecessary data: ")
    retentionPeriodicity = ''
    while not (retentionPeriodicity.isdigit() and (int(retentionPeriodicity) > 0 and int(retentionPeriodicity) < 24)):

        retention = input("\tRetention periodicity (in hours [1-23]): ").strip()

        if not retentionPeriodicity.isdigit():
            print("\tRetention periodicity must be a number.")
        elif retentionPeriodicity.isdigit() and not (int(retentionPeriodicity) > 0 and int(retentionPeriodicity) < 24):
            print("\tRetention periodicity must be between 1 and 23.")

    retentionPolicy = ''
    while not (retentionPolicy.isdigit() and (int(retentionPolicy) > 0 and int(retentionPolicy) < 60)):

        retentionPolicy = input("\tRetention policy (in minutes [1-59]): ").strip()

        if not retentionPolicy.isdigit():
            print("\tRetention policy must be a number.")
        elif retentionPolicy.isdigit() and not (int(retentionPolicy) > 0 and int(retentionPolicy) < 60):
            print("\tRetention policy must be between 1 and 59.")
    '''
    print("(2) Task for periodic delete of outdated and unnecessary data created.")
    time.sleep(1)

    # Weekly upload of OUI list
    print("(3) Task for Weekly upload of OUI list created.")
    time.sleep(1)

    # Daily reboot
    print("(4) Reboot:")
    print("\tReboot Periodicity: ")
    print("\t  0 - Daily")
    print("\t  1 - Every two days")
    print("\t  2 - Every three days")
    print("\t  3 - Weekly")
    print("\t  4 - Monthly")
    print("\t  5 - No Reboot")
    rebootPeriodicity = input("\tReboot Periodicity [0-5]: ").strip()
    while not (rebootPeriodicity.isdigit() and (int(rebootPeriodicity) >= 0 and int(rebootPeriodicity) < 6)) :
        rebootPeriodicity = input("\tReboot Periodicity [0-5]: ").strip()

        if not rebootPeriodicity.isdigit():
            print("\tPlease choose a number.")
        elif rebootPeriodicity.isdigit() and not (int(rebootPeriodicity) >= 0 and int(rebootPeriodicity) < 6):
            print("\tPlease enter a number between 0 and 5.")

    if   rebootPeriodicity == "0": rebootPeriodicity = 'daily'
    elif rebootPeriodicity == "1": rebootPeriodicity = 'everytwodays'
    elif rebootPeriodicity == "2": rebootPeriodicity = 'everythreedays'
    elif rebootPeriodicity == "3": rebootPeriodicity = 'weekly'
    elif rebootPeriodicity == "4": rebootPeriodicity = 'monthly'
    elif rebootPeriodicity == "5": rebootPeriodicity = 'noreboot'

    if rebootPeriodicity != 'noreboot':
        rebootTime = input("\tSensor reboot hour [0-23]: ").strip()
        while not (rebootTime.isdigit() and (int(rebootTime) >= 0 and int(rebootTime) < 24)) :
            rebootTime = input("\tSensor reboot hour [0-23]: ").strip()

            if not rebootTime.isdigit():
                print("\tReboot time must be a number.")
            elif rebootTime.isdigit() and not (int(rebootTime) >= 0 and int(rebootTime) < 24):
                print("\tPlease enter a number between 0 and 23.")
    else:
        rebootTime = 0
    print("Task for daily reboot created.")
    time.sleep(1)

    return uploadInterface,detectionInterface,uploadPeriodicity,slidingWindow,technology.lower(), rebootPeriodicity, rebootTime


#Custom-made functions
def write_crontab_file(status, detection_if, upload_periodicity, reboot_periodicity, reboot_time, location_send_mode="boot"):
    # Write tasks configuration file        
    print("Creating new tasks configuration file...")
    f = open(CONFIGURED_CRONJOBS_FILEPATH, 'w')
    print("New configuration file created.")
    print("")

    print("Writing tasks to configuration file...")

    if location_send_mode not in ("boot", "periodic_5min", "periodic_upload_window"):
        location_send_mode = "boot"

    f.write("# This file allows users to configure the sensor tasks to be run\n")
    f.write("# automatically on pre-determined time-shedules.\n")
    f.write("#\n")
    f.write("# SENSOR CONFIGURED TASKS: \n")
    f.write("#\n")
    f.write("# Check available communication technologies and interfaces\n")
    f.write("@reboot sleep 15 && /usr/bin/python3 /home/kali/Desktop/sensorCommunicationAvailable.py\n")
    f.write("# Periodic check of communication technologies and interfaces\n")
    f.write( "*/" + str(upload_periodicity) + " * * * * /usr/bin/python3 /home/kali/Desktop/sensorCommunicationCheck.py\n")
    #f.write("# Monitor battery powerbank\n")
    #f.write("* * * * * /usr/bin/python3 /home/kali/Desktop/bat_powerbank.py\n")
    
    if status == "Active":
        f.write("# Wi-Fi detection of devices\n")

        f.write("@reboot sleep 90 && sudo /usr/bin/python3 /home/kali/Desktop/sensorStartup.py\n")

        f.write("# Periodic upload of crowding data to the Cloud Server\n")
        f.write("*/" + str(upload_periodicity) + " * * * * /usr/bin/python3 /home/kali/Desktop/sendCrowdingData.py \n")

        f.write("# Periodic upload of sensor location\n")
        if location_send_mode == "periodic_5min":
            f.write("*/5 * * * *  /usr/bin/python3 /home/kali/Desktop/sendSensorLocation.py\n")
        elif location_send_mode == "periodic_upload_window":
            f.write("*/" + str(upload_periodicity) + " * * * * /usr/bin/python3 /home/kali/Desktop/sendSensorLocation.py\n")
        else:
            f.write("#*/5 * * * * /usr/bin/python3 /home/kali/Desktop/sendSensorLocation.py\n")

        f.write("# Periodic delete of outdated and unnecessary data from local database\n")
        f.write("0 * * * * /usr/bin/python3 /home/kali/Desktop/Sniffer/dataRetentionManager.py 30\n")

    elif status == "Disabled":
        f.write("# Wi-Fi detection of devices\n")

        f.write("#@reboot sleep 90 && sudo /usr/bin/python3 /home/kali/Desktop/sensorStartup.py\n")

        f.write("# Periodic upload of crowding data to the Cloud Server\n")
        f.write("#*/" + str(upload_periodicity) + " * * * * /usr/bin/python3 /home/kali/Desktop/sendCrowdingData.py \n")

        f.write("# Periodic upload of sensor location\n")
        f.write("#*/5 * * * * sleep 40 && /usr/bin/python3 /home/kali/Desktop/sendSensorLocation.py\n")

        f.write("# Periodic delete of outdated and unnecessary data from local database\n")
        f.write("#0 * * * * /usr/bin/python3 /home/kali/Desktop/Sniffer/dataRetentionManager.py 30\n")

    f.write("# Periodic upload of OUI list\n")
    f.write("0 0 * * 0 /usr/bin/python3 /home/kali/Desktop/macOUIupdater.py\n")

    f.write("# Reboot\n")
    if reboot_periodicity == "daily":
        f.write("0 " + str(reboot_time) + " * * * sudo reboot\n")
    elif reboot_periodicity == "everytwodays":
        f.write("0 " + str(reboot_time) + " */2 * * sudo reboot\n")
    elif reboot_periodicity == "everythreedays":
        f.write("0 " + str(reboot_time) + " */3 * * sudo reboot\n")
    elif reboot_periodicity == "weekly":
        f.write("0 " + str(reboot_time) + " * * 0 sudo reboot\n")
    elif reboot_periodicity == "monthly":
        f.write("0 " + str(reboot_time) + " 1 * * sudo reboot\n")
    elif reboot_periodicity == "noreboot":
        f.write("#0 " + str(reboot_time) + " * * * sudo reboot\n")

    f.close()

    print("Tasks configuration file sucessfully writen.")

    # Load tasks configuration file to crontab
    cmd = "crontab -u kali " + CONFIGURED_CRONJOBS_FILEPATH
    os.system(cmd)

    print("Configuration saved sucessfully.")
        
def check_upload_detection_interfaces(start_monitor_mode:bool):
    # Selection of upload and detection interfaces
    
    #
    # NOTA: Este script assume que existirao apenas 2 interfaces wifi (wlan's) ligadas, em que 
    #       uma e a do RaspberryPi (dc:a6:32) e que a outra sera precisamente a interface do
    #       dongle de wifi externo (antenas Alfa Networks). Desta forma, deteta-se qual e a
    #       wlan correspondente a interface de detecao por exclusao de partes (se nao e a do
    #       Raspberry Pi, entao sera a outra!)
    #
    
    print("Checking network interfaces...")

    interfaces = ni.interfaces()

    upload_interface = None

    for iface in interfaces:

        if iface == "eth0" and len(ni.ifaddresses(iface)) > 2: 
            upload_interface = iface
            break

        elif iface[:4] == "wlan":
                
            if ni.AF_INET in ni.ifaddresses(iface):
                upload_interface = iface
                break                


    detection_interface = None
    cmd = "sudo airmon-ng"
    output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8")


    lines = output.splitlines()[3:]
    lines.remove("")

    interfaces = []

    for line in lines:
        interface = line.split("\t")
        interface.remove("")
        interfaces.append(interface)

    if len(interfaces) < 2:
        print("WARNING! You do not have connected an external Wi-Fi dongle. \nPlease connect an external Wi-Fi dongle to the sensor and then run this script again.")
        print("----------------------------------------------------------------")
        
    else:

        for interface in interfaces:
            dongle_manuf = interface[-1:][0].lower()

            if "realtek" in dongle_manuf:
                start_monitor_interface = interface[1]
                detection_interface = interface[1]
                break
            
            elif "mediatek" in dongle_manuf:
                start_monitor_interface = interface[1]

                if interface[1][-3:] == "mon":
                    detection_interface = interface[1]
                    start_monitor_mode = False
                else:
                    detection_interface = interface[1] + "mon"

                break

        if start_monitor_mode == True:
            cmd = f"sudo airmon-ng start {start_monitor_interface}"
            subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8")
            print(f"Started monitor mode on interface '{start_monitor_interface}'.")
        
    if upload_interface is not None: 
        print("Interface '" + upload_interface + "' automatically selected for uploading data.")
    if detection_interface is not None: 
        print("Interface '" + detection_interface + "' automatically selected for detecting devices.")

    return upload_interface, detection_interface

    
def publish_mqtt_message(msg_payload, topic):
    client = connect_mqtt()

    result = client.publish(topic, msg_payload)

    # result: [0, 1]
    status = result[0]
    if status == 0:
        print(f"Send `{msg_payload}` to topic `{topic}`.")
        return True
    else:
        print("\nFailed to publish mqtt message.")
        return False

import json
import paho.mqtt.client as mqtt

from timeline_logger import log_timeline


def publish_detections_mqtt_message(
        unix_timestamp,
        devices_detected: int,
        topic,
        norm_new=None,
        norm_disappeared=None,
        seq=None):

    client = None

    msg_payload = {
        "timestamp": unix_timestamp,
        "devices_detected": int(devices_detected)
    }

    if seq is not None:
        msg_payload["seq"] = int(seq)

    if norm_new is not None:
        msg_payload["norm_new_fingerprints"] = round(
            norm_new,
            4
        )

    if norm_disappeared is not None:
        msg_payload["norm_disappeared_fingerprints"] = round(
            norm_disappeared,
            4
        )

    json_msg_payload = json.dumps(
        msg_payload,
        separators=(",", ":")
    )

    try:
        client = connect_mqtt(timeout=10)

        # O cliente já está ligado neste ponto.
        log_timeline(
            "send_start",
            seq,
            "wifi"
        )

        result = client.publish(
            topic,
            json_msg_payload,
            qos=1
        )

        if result.rc != mqtt_client.MQTT_ERR_SUCCESS:
            print(
                f"Failed to queue MQTT message: rc={result.rc}"
            )

            log_timeline(
                "send_failed",
                seq,
                "wifi"
            )

            return False

        result.wait_for_publish(timeout=10)

        if not result.is_published():
            print("MQTT PUBACK timeout.")

            log_timeline(
                "send_failed",
                seq,
                "wifi"
            )

            return False

        # Em QoS 1, a publicação fica concluída após o PUBACK.
        log_timeline(
            "ack_received",
            seq,
            "wifi"
        )

        print(
            f"Sent `{msg_payload}` to topic `{topic}` "
            f"with QoS 1; PUBACK received."
        )

        return True

    except Exception as error:
        print(f"MQTT publication failed: {error}")

        if seq is not None:
            log_timeline(
                "send_failed",
                seq,
                "wifi"
            )

        return False

    finally:
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

            try:
                client.loop_stop()
            except Exception:
                pass

# Insert pending measurement in database
def store_pending_measurement(unix_timestamp, devices_detected):
    conn = sqlite3.connect('/home/kali/Desktop/DB/StoredMeasurements.db' , timeout=30)
    cursor = conn.cursor()
    cursor.execute("""INSERT INTO PendingMeasurements VALUES (?, ?) """, (unix_timestamp, devices_detected))
    conn.commit()
    cursor.close()
    conn.close()
    print(f"Measurement '({unix_timestamp},{devices_detected})' stored in the database.")


# Get first pending measurement from database
def get_1st_pending_measurement():
    conn = sqlite3.connect('/home/kali/Desktop/DB/StoredMeasurements.db' , timeout=30)
    cursor = conn.cursor()
    
    first_row = cursor.execute("""SELECT * FROM PendingMeasurements ORDER BY Timestamp ASC LIMIT 1 """).fetchone()

    conn.commit()
    cursor.close()
    conn.close()

    if first_row is None:
        # No pending measurements, database is empty
        print("There are no pending measurements, database is empty.")
        return None
    else:
        return first_row
        

# Remove first pending measurement from database
def remove_1st_pending_measurement():
    conn = sqlite3.connect('/home/kali/Desktop/DB/StoredMeasurements.db' , timeout=30)
    cursor = conn.cursor()
    
    cursor.execute("""DELETE FROM PendingMeasurements WHERE Timestamp IN (SELECT Timestamp FROM PendingMeasurements ORDER BY Timestamp ASC LIMIT 1)""")

    conn.commit()
    cursor.close()
    conn.close()





def check_config_mode():
    configuration_mode = ''
    while configuration_mode not in ("1","2"):

        configuration_mode = input("Please choose a configuration mode (1/2):").strip()

        if not (configuration_mode == "1" or configuration_mode == "2"):
            print("Please enter a configuration mode.")

    return configuration_mode

def confirm(question):
    confirmation = ''
    while confirmation not in ("Y", "y", "yes", "Yes", "YES", "n","N","No", "no", "NO"):

        confirmation = input(question).strip()

        if confirmation == "Yes" or confirmation == "Y" or confirmation == "yes" or confirmation == "y" or confirmation == "YES":
            break

        elif confirmation == "n" or confirmation == "N" or confirmation == "no" or confirmation == "No" or confirmation == "NO":
            print("Exit program.")
            print("------------------------------------------------------------------")
            exit(0)
        else:
            print("Please enter yes/no.")


def change_power_filtration(power_filtration):

    if not (int(power_filtration) >= -100 and int(power_filtration) <= 0):
        print("Value inserted in not correct. Packet filtration must be inside [-100, 0] dB. ('0' for no Packet Filtration).")
        exit(0)

    else:

        # Stop the current Wi-fi detection processes
        # cmd = "sudo pkill airodump-ng"
        # os.system(cmd)
        with open(PID_FILE, "r") as f:
            pid = f.read().strip()

        if pid:
            os.system("sudo kill " + pid)

        try:
            process = subprocess.Popen(
                ["sudo", "python3", "crowdingSniffer.py"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            with open(PID_FILE, "w") as f:
                f.write(str(process.pid))

        except Exception as e:
            print(f"Error starting crowdingSniffer.py: {e}")

            
def compare_db_with_cronjobs():
    
    #Obter parametros da base de dados local

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

    
    if len(sensor_configuration) > 0:
        status_db = sensor_configuration[0][4]
        upload_periodicity_db = sensor_configuration[0][10]
        reboot_periodicity_db = sensor_configuration[0][13]
        reboot_time_db = sensor_configuration[0][14]

        sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchall()
        
        detection_interface_db = sensor_communication[0][6]

        cwifi.close()
        connwifi.close()

        #Obter parametros dos cronjobs
        cmd = "crontab -u kali -l"
        output_lines = subprocess.check_output(cmd, shell=True).decode("utf-8").splitlines(keepends=False)


        first_chars_status = []

        for i in range(len(output_lines)):

            # status
            if i == 10 or i == 11 or i == 13:
                first_chars_status.append(output_lines[i][0])

            # detection interface
            if i == 10: 
                detection_interface_cron = output_lines[i].split(" ")[-1]

            # upload periodicity
            elif i == 13:
                upload_periodicity_cron = int(output_lines[i].split(" ")[0][2:])

            # reboot periodicity and reboot time
            elif i == 19:

                if output_lines[i][0] == "#":
                    reboot_periodicity_cron == "noreboot"
                else:
                    cron_day = str(output_lines[i].split(" ")[2])
                    cron_day_of_week = str(output_lines[i].split(" ")[4])

                    if cron_day == "*":
                        if cron_day_of_week == "*":
                            reboot_periodicity_cron = "daily"
                        elif cron_day_of_week == "0":
                            reboot_periodicity_cron = "weekly"
                    elif cron_day == "*/2":
                        reboot_periodicity_cron = "everytwodays"
                    elif cron_day == "*/3":
                        reboot_periodicity_cron = "everythreedays"
                    elif cron_day == "1":
                        reboot_periodicity_cron = "monthly"        
                        
                reboot_time_cron = int(output_lines[i].split(" ")[1])


        status_cron = "Disabled"
        for char in first_chars_status:
            if char != "#":
                status_cron="Active"
                break
        

        #Comparar todos os parâmetros
        if status_db != status_cron or \
        detection_interface_db != detection_interface_cron or \
        upload_periodicity_db != upload_periodicity_cron or \
        reboot_periodicity_db != reboot_periodicity_cron or \
        reboot_time_db != reboot_time_cron:
            #Se houver um parametro diferente, invocar funcao 'write_crontab_file' com parametros da base de dados
            print("Different parameters from db to cronjobs. Rewritting tasks configuration file with parameters from database.")
            write_crontab_file(status_db, detection_interface_db, upload_periodicity_db, reboot_periodicity_db, reboot_time_db)

    else:
        print("Sensor is not configured. Cronjobs will not be compared.")


#Communication handover mechanism

def check_wifi_available():
    response = os.system("ping -c 1 127.0.0.1")
    if response == 0:
        set_wifi_available(True)
        return True
    else:
        set_wifi_available(False)
        return False
    
def get_cloud_host_from_db():
    try:
        conn = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=10)
        cursor = conn.cursor()

        row = cursor.execute("""
            SELECT Cloud_IP_Address
            FROM SensorConfiguration
            LIMIT 1
        """).fetchone()

        conn.close()

        if row and row[0]:
            return str(row[0]).strip()

        return None

    except Exception:
        return None


def check_wifi_connection():
    host = get_cloud_host_from_db()

    if not host:
        set_wifi_connected(False)
        return False

    try:
        result = subprocess.run(
            ["nc", "-z", "-w", "3", host, str(MQTT_PORT)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )

        wifi_connected = result.returncode == 0

    except Exception:
        wifi_connected = False

    set_wifi_connected(wifi_connected)
    return wifi_connected
     
def get_dev_eui():
    rak = RAK3172(LORA_SERIAL_PORT, 115200)
    rak.connect()
    ok=rak.get_dev_eui()
    rak.disconnect()
    return ok

def check_lora_available() -> bool:
    rak = RAK3172(LORA_SERIAL_PORT, 115200)
    try:
        rak.connect()
        dev_eui = rak.get_dev_eui()
        print(f"LoRa Device EUI: {dev_eui}")
        return bool(dev_eui and str(dev_eui).strip().lower() != "none")
    finally:
        rak.disconnect()

def check_lora_connection_no_Join() -> bool:
    try:
        with open("/tmp/rak_njs", "r") as f:
            val = f.read().strip()
            print(f"Read LoRa status from /tmp/rak_njs: {val}")

        if val == "1":
            print("Device is joined to the network.")
            return True
        elif val == "0":
            print("Device not joined to the network.Trying to rejoin...")
            return False
    except FileNotFoundError:
        return False

    except Exception as e:
        print(f"Error reading LoRa status: {e}")
        return False
    
def check_lora_connection():
    """Check LoRa join status by reading the last saved state file."""
    STATUS_FILE = "/tmp/rak_njs"
    
    try:
        rak = RAK3172(LORA_SERIAL_PORT, 115200)
        rak.connect()
        with open(STATUS_FILE, "r") as f:
            val = f.read().strip()
        print(f"Read LoRa status from {STATUS_FILE}: {val}")

        if val == "1":
            print("Device is joined to the network.")
            return True
        elif val == "0":
            print("Device not joined to the network.Trying to rejoin...")

            ok=rak.join_network(1,0,8,8)
            return ok
        else:
            print("Invalid content in status file.")
            return False

    except FileNotFoundError:
        print("Status file not found — assuming not joined.")
        ok=rak.join_network(1,0,8,8)
        time.sleep(3)
        return ok

    except Exception as e:
        print(f"Error reading LoRa status: {e}")
        return False

def reestablish_wifi_connection():
    #Get upload interface
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        upload_interface = cwifi.execute("""SELECT Upload_Interface FROM SensorCommunication""").fetchone()[0]

        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to read upload interface from database.", error)

    #Restart upload interface
    cmd = f"sudo ip link set dev {upload_interface} down"
    os.system(cmd)

    cmd = f"sudo ip link set dev {upload_interface} up"
    os.system(cmd)

    #Check network connection
    if check_wifi_connection() == True:
        set_wifi_connected(True)
        decide_upload_technology()
        return True
    else:
        return False
 
def reestablish_lora_connection():

    if heliumNodeSetup() == True:
        decide_upload_technology()
        return True
    else:
        return False
 
def set_wifi_available(wifiAvailable:bool):
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        sensor_comm = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone() 

        if sensor_comm is None:
            cwifi.execute("""INSERT INTO SensorCommunication (WifiAvailable, Last_Update) VALUES (?, CURRENT_TIMESTAMP)""", (wifiAvailable,))
        else:
            cwifi.execute("""UPDATE SensorCommunication SET WifiAvailable=?, Last_Update=CURRENT_TIMESTAMP""", (wifiAvailable,))

        connwifi.commit()
        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to update database.", error)

def set_wifi_connected(wifiAvailable:bool):
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        cwifi.execute("""UPDATE SensorCommunication SET WifiConnected=?, Last_Update=CURRENT_TIMESTAMP""", (wifiAvailable,))

        connwifi.commit()
        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to update database.", error)

def set_lora_available(loraAvailable:bool):
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        sensor_comm = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

        if sensor_comm is None:
            cwifi.execute("""INSERT INTO SensorCommunication (LoRaAvailable, Last_Update) VALUES (?, CURRENT_TIMESTAMP)""", (loraAvailable,))
        else:
            cwifi.execute("""UPDATE SensorCommunication SET LoRaAvailable=?, Last_Update=CURRENT_TIMESTAMP""", (loraAvailable,))

        connwifi.commit()
        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to update database.", error)

def set_lora_connected(loraAvailable:bool):
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        cwifi.execute("""UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""", (loraAvailable,))

        connwifi.commit()
        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to update database.", error)

def set_upload_technology(upload_technology, cursor=None):
    own_connection = (cursor is None)
    try:
        if cursor is None:
            connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
            cwifi = connwifi.cursor()
        else:
            cwifi = cursor

        cwifi.execute("""UPDATE SensorConfiguration SET Upload_Technology=?, Last_Update=CURRENT_TIMESTAMP""", (upload_technology,))

        if own_connection:
            connwifi.commit()
            cwifi.close()
            connwifi.close()

    except sqlite3.Error as error:
        print("Failed to update database.", error)

def get_upload_technology():

    print("Checking upload technology...")

    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        sensor_communication = cwifi.execute("""SELECT WifiAvailable, LoRaAvailable, WifiConnected, LoRaConnected FROM SensorCommunication""").fetchone()

        wifiAvailable = sensor_communication[0]
        loraAvailable = sensor_communication[1]
        wifiConnected = sensor_communication[2]
        loraConnected = sensor_communication[3]

        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to get data from database.", error)

    if wifiAvailable and wifiConnected:
        print("Upload via 'wifi' automatically selected for data communication.")
        return 'wifi'
    elif loraAvailable and loraConnected:
        print("Upload via 'lora' automatically selected for data communication.")
        return 'lora'

def try_join_lora_network(network_name, app_eui, app_key, dev_eui, join_attempts=2):
    """
    Attempt to join a specific LoRa network with network-specific credentials
    
    Args:
        network_name: Network identifier (e.g., "TTN", "Helium")
        app_eui: Application EUI (16 hex chars)
        app_key: Application Key (32 hex chars)
        dev_eui: Device EUI (16 hex chars, network-specific)
        join_attempts: Number of join attempts (default 2)
    
    Returns:
        bool: True if joined successfully, False otherwise
    """
    rak = RAK3172(LORA_SERIAL_PORT, 115200)
    
    try:
        rak.connect()
        logger.info(f"[LORA] Configuring RAK3172 for {network_name}")
        
        rak.set_dev_eui(dev_eui)
        rak.set_app_eui(app_eui)
        rak.set_app_key(app_key)
        
        logger.info(f"[LORA] Attempting join to {network_name} (Dev EUI: {dev_eui})")
        joined = rak.join_network(
            join=1, 
            auto_join=0, 
            reattempt_interval=8, 
            join_attempts=join_attempts
        )
        
        if joined:
            logger.info(f"[LORA] Successfully joined {network_name}")
            
            with open(f"/tmp/rak_njs_{network_name.lower()}", "w") as f:
                f.write("1")
            
            with open("/tmp/rak_njs", "w") as f:
                f.write("1")
            
            with open("/tmp/rak_network", "w") as f:
                f.write(network_name)
            
            return True
        else:
            logger.warning(f"[LORA] Failed to join {network_name}")
            
            with open(f"/tmp/rak_njs_{network_name.lower()}", "w") as f:
                f.write("0")
            
            return False
    
    except Exception as e:
        logger.error(f"[LORA] Error joining {network_name}: {e}")
        return False
    
    finally:
        rak.disconnect()


def mark_lora_network_failed(network_name):
    """
    Mark a LoRa network as failed (triggers handover on next check)
    
    Args:
        network_name: Network identifier (e.g., "TTN", "Helium")
    """
    try:
        with open(f"/tmp/rak_njs_{network_name.lower()}", "w") as f:
            f.write("0")
        logger.warning(f"[LORA] Network {network_name} marked as failed")
    except Exception as e:
        logger.error(f"[LORA] Failed to mark {network_name} as failed: {e}")


def check_lora_network_status(network_name):
    """
    Check if a specific LoRa network is currently joined
    
    Args:
        network_name: Network identifier (e.g., "TTN", "Helium")
    
    Returns:
        bool: True if network is joined, False otherwise
    """
    status_file = f"/tmp/rak_njs_{network_name.lower()}"
    
    if not os.path.exists(status_file):
        return False
    
    try:
        with open(status_file, "r") as f:
            status = f.read().strip()
            return status == "1"
    except:
        return False

def check_lora_network_joinable(network_name):
    """
    Check if a LoRa network was previously able to join (state 0 or 1).
    Used for direct re-join decisions (Tier 2).
    States: 1=joined+selected, 0=joined but not selected, -1=join failed
    """
    status_file = f"/tmp/rak_njs_{network_name.lower()}"
    if not os.path.exists(status_file):
        return False
    try:
        with open(status_file, "r") as f:
            return f.read().strip() in ("0", "1")
    except:
        return False

#check link quality on both networks and select the best one (if both are available) for data upload

def run_link_check(ser=None, network=None, close_after=True):
    """
    Obtém métricas de qualidade do link LoRaWAN.
 
    Estratégia:
      1. Tentar LinkCheck (AT+LINKCHECK=1 + uplink) → métricas completas
      2. Se LinkCheck falhar (result=1 ou timeout) → fallback com confirmed
         uplink (AT+CFM=1) + AT+RSSI/AT+SNR → métricas parciais
 
    Returns:
        dict com result, margin, gwcnt, rssi, snr — ou None
    """
    import serial as _serial
 
    own_serial = ser is None
 
    def _send_at(s, cmd, timeout=5):
        full_cmd = f"{cmd}\r\n"
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        s.write(full_cmd.encode())
        logger.info(f"[LINKCHECK][AT] >> {cmd}")
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            if s.in_waiting:
                try:
                    line = s.readline().decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if line:
                    logger.info(f"[LINKCHECK][AT] << {line}")
                    lines.append(line)
                    if line in ("OK", "ERROR"):
                        break
            time.sleep(0.05)
        return lines
 
    def _wait_for_event(s, prefix, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if s.in_waiting:
                try:
                    line = s.readline().decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if line:
                    logger.info(f"[LINKCHECK][UART] {line}")
                    if line.startswith(prefix):
                        return line
            time.sleep(0.05)
        return None
 
    def _read_rssi_snr(s):
        """Lê RSSI e SNR do último downlink recebido via AT commands."""
        rssi = None
        snr = None
 
        rssi_lines = _send_at(s, "AT+RSSI=?", timeout=3)
        for line in rssi_lines:
            if "RSSI" in line.upper() and "=" in line:
                try:
                    rssi = int(line.split("=")[-1].strip())
                except ValueError:
                    pass
 
        snr_lines = _send_at(s, "AT+SNR=?", timeout=3)
        for line in snr_lines:
            if "SNR" in line.upper() and "=" in line:
                try:
                    snr = int(line.split("=")[-1].strip())
                except ValueError:
                    pass
 
        return rssi, snr
 
    try:
        if own_serial:
            ser = _serial.Serial(port=LORA_SERIAL_PORT, baudrate=115200, timeout=1)
 
        logger.info(f"[LINKCHECK] Starting link quality check" + (f" for {network}" if network else ""))
 
        # ── Tentativa 1: LinkCheck MAC command ──
        _send_at(ser, "AT+CFM=0", timeout=3)   # unconfirmed para o LinkCheck
        _send_at(ser, "AT+LINKCHECK=1", timeout=5)
        _send_at(ser, "AT+SEND=2:01", timeout=5)
 
        event = _wait_for_event(ser, "+EVT:LINKCHECK", timeout=20)
 
        if event:
            payload = event.split("LINKCHECK:")[-1].strip().replace(" ", "")
            sep = "," if "," in payload else ":"
            parts = payload.split(sep)
 
            if len(parts) >= 5:
                metrics = {
                    "result": int(parts[0]),
                    "margin": int(parts[1]),
                    "gwcnt":  int(parts[2]),
                    "rssi":   int(parts[3]),
                    "snr":    int(parts[4]),
                }
 
                # LinkCheck válido?
                if metrics["result"] == 0 and metrics["gwcnt"] > 0:
                    logger.info(
                        f"[LINKCHECK] ✓ LinkCheck OK: margin={metrics['margin']}dB, "
                        f"gwcnt={metrics['gwcnt']}, rssi={metrics['rssi']}dBm, "
                        f"snr={metrics['snr']}dB"
                    )
                    try:
                        log_event(
                            "lora_linkcheck_result",
                            network=network,
                            method="linkcheck",
                            result=metrics["result"],
                            margin_db=metrics["margin"],
                            gwcnt=metrics["gwcnt"],
                            rssi_dbm=metrics["rssi"],
                            snr_db=metrics["snr"],
                        )
                    except Exception:
                        pass
                    return metrics
 
                logger.info(
                    f"[LINKCHECK] LinkCheck returned result={metrics['result']}, "
                    f"gwcnt={metrics['gwcnt']} — trying confirmed uplink fallback"
                )
 
        else:
            logger.warning("[LINKCHECK] No LinkCheck response — trying confirmed uplink fallback")
 
        # ── Tentativa 2: Confirmed uplink + RSSI/SNR ──
        # Se o servidor confirmou o uplink, temos um downlink recebido
        # e podemos ler RSSI/SNR dele
        _send_at(ser, "AT+LINKCHECK=0", timeout=3)   # desativar LinkCheck
        _send_at(ser, "AT+CFM=1", timeout=3)          # modo confirmado
        _send_at(ser, "AT+SEND=2:02", timeout=5)
 
        # Esperar por SEND_CONFIRMED_OK ou TX_DONE
        confirmed = False
        deadline = time.time() + 15
        while time.time() < deadline:
            if ser.in_waiting:
                try:
                    line = ser.readline().decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if line:
                    logger.info(f"[LINKCHECK][UART] {line}")
                    if "SEND_CONFIRMED_OK" in line:
                        confirmed = True
                        break
                    if "SEND_CONFIRMED_FAILED" in line:
                        logger.warning("[LINKCHECK] Confirmed uplink failed")
                        break
            time.sleep(0.05)
 
        if confirmed:
            rssi, snr = _read_rssi_snr(ser)
            if rssi is not None and rssi != 0:
                metrics = {
                    "result": 0,        # confirmação = link OK
                    "margin": 0,        # não disponível via este método
                    "gwcnt":  1,        # pelo menos 1 gateway (confirmou)
                    "rssi":   rssi,
                    "snr":    snr if snr is not None else 0,
                }
                logger.info(
                    f"[LINKCHECK] ✓ Confirmed uplink OK: rssi={rssi}dBm, snr={snr}dB"
                )
                try:
                    log_event(
                        "lora_linkcheck_result",
                        network=network,
                        method="confirmed_uplink",
                        result=0,
                        margin_db=0,
                        gwcnt=1,
                        rssi_dbm=rssi,
                        snr_db=snr,
                    )
                except Exception:
                    pass
 
                # Voltar a unconfirmed para operação normal
                _send_at(ser, "AT+CFM=0", timeout=3)
                return metrics
 
        # Nenhum método devolveu métricas úteis
        _send_at(ser, "AT+CFM=0", timeout=3)
        logger.warning("[LINKCHECK] No link quality data available")
        return None
 
    except Exception as e:
        logger.error(f"[LINKCHECK] Error: {e}")
        return None
 
    finally:
        if own_serial and close_after:
            try:
                ser.close()
            except Exception:
                pass
 
 
def evaluate_lora_networks(networks):
    """
    Avalia redes LoRaWAN: Helium primeiro, TTN por último.
 
    A ordenação é feita no código — redes com "ttn" no nome vão para o fim.
    Independente dos IDs na tabela LoRaNetworks.
 
    Se TTN ganhar: já está joined (última testada), usar diretamente.
    Se Helium ganhar: join final ao Helium (sem rate limiting).
    """
    import serial as _serial
 
    if not networks:
        logger.error("[LORA-EVAL] No LoRa networks configured")
        return None, None
 
    # ── Ordenar: TTN por último ──
    # Redes com "ttn" no nome vão para o fim da lista.
    # Todas as outras mantêm a ordem original (por id da DB).
    sorted_networks = sorted(networks, key=lambda n: (1 if "ttn" in n[0].lower() else 0))
 
    logger.info(
        f"[LORA-EVAL] Evaluation order: {' → '.join(n[0] for n in sorted_networks)}"
    )
 
    EVAL_JOIN_ATTEMPTS = 2
    EVAL_JOIN_INTERVAL = 12
    EVAL_JOIN_TIMEOUT = (EVAL_JOIN_INTERVAL * EVAL_JOIN_ATTEMPTS) + 15
    FINAL_JOIN_ATTEMPTS = 4
    FINAL_JOIN_INTERVAL = 12
    FINAL_JOIN_TIMEOUT = (FINAL_JOIN_INTERVAL * FINAL_JOIN_ATTEMPTS) + 15
 
    # ── Helpers AT ──
 
    def _send_at(s, cmd, timeout=5):
        full_cmd = f"{cmd}\r\n"
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        s.write(full_cmd.encode())
        logger.info(f"[LORA-AT] >> {cmd}")
        lines = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            if s.in_waiting:
                try:
                    line = s.readline().decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if line:
                    logger.info(f"[LORA-AT] << {line}")
                    lines.append(line)
                    if line in ("OK", "ERROR"):
                        break
            time.sleep(0.05)
        return lines
 
    def _reset_and_wait(s):
        """Soft reset: parar joins + limpar buffer, sem ATZ."""
        logger.info("[LORA-EVAL] Soft reset (no ATZ)")
        _send_at(s, "AT+JOIN=0:0", timeout=3)
        time.sleep(1)
        try:
            s.reset_input_buffer()
            s.reset_output_buffer()
        except Exception:
            pass
 
    def _stop_joins(s):
        _send_at(s, "AT+JOIN=0:0", timeout=3)
        time.sleep(0.5)
 
    def _configure(s, net_name, app_eui, app_key, dev_eui):
        logger.info(f"[LORA-EVAL] Configuring {net_name}")
        _send_at(s, "AT+CLASS=A", timeout=5)
        _send_at(s, f"AT+DEVEUI={dev_eui}", timeout=5)
        _send_at(s, f"AT+APPEUI={app_eui}", timeout=5)
        _send_at(s, f"AT+APPKEY={app_key}", timeout=5)
 
    def _join(s, net_name, attempts, interval, timeout):
        logger.info(f"[LORA-EVAL] Joining {net_name} (attempts={attempts}, interval={interval}s)")
        cmd = f"AT+JOIN=1:0:{interval}:{attempts}"
        try:
            s.reset_input_buffer()
        except Exception:
            pass
        s.write(f"{cmd}\r\n".encode())
        logger.info(f"[LORA-AT] >> {cmd}")
 
        deadline = time.time() + timeout
        while time.time() < deadline:
            if s.in_waiting:
                try:
                    line = s.readline().decode("utf-8", errors="replace").strip()
                except Exception:
                    continue
                if line:
                    logger.info(f"[LORA-AT] << {line}")
                    if "+EVT:JOINED" in line:
                        logger.info(f"[LORA-EVAL] {net_name}: join OK")
                        return True
                    if "JOIN_FAILED" in line or "JOIN FAILED" in line:
                        logger.warning(f"[LORA-EVAL] {net_name}: attempt failed, still waiting")
                    if "Restricted_Wait" in line:
                        try:
                            wait_ms = int(line.split("Restricted_Wait_")[1].split("_ms")[0])
                            wait_h = wait_ms / 3600000
                            logger.error(
                                f"[LORA-EVAL] {net_name}: duty cycle restriction "
                                f"({wait_h:.1f}h remaining)"
                            )
                        except (IndexError, ValueError):
                            logger.error(f"[LORA-EVAL] {net_name}: duty cycle restriction")
                        return "restricted"
                    if "AT_BUSY_ERROR" in line:
                        time.sleep(2)
            time.sleep(0.2)
        logger.warning(f"[LORA-EVAL] {net_name}: join failed after all attempts")
        return False
 
    def _mark_final(net_name):
        try:
            with open("/tmp/rak_njs", "w") as f:
                f.write("1")
            with open(f"/tmp/rak_njs_{net_name.lower()}", "w") as f:
                f.write("1")
            with open("/tmp/rak_network", "w") as f:
                f.write(net_name)
        except Exception as e:
            logger.warning(f"[LORA-EVAL] Could not write state files: {e}")
    # ── Abrir porta serial ──
    try:
        ser = _serial.Serial(port=LORA_SERIAL_PORT, baudrate=115200, timeout=1)
    except Exception as e:
        logger.error(f"[LORA-EVAL] Could not open {LORA_SERIAL_PORT}: {e}")
        return None, None
 
    try:
        _stop_joins(ser)
        _reset_and_wait(ser)
 
        results = []
        last_joined_name = None
 
        # ══════════════════════════════════════════════════════════════
        # FASE 1: Avaliar cada rede (Helium primeiro, TTN último)
        # ══════════════════════════════════════════════════════════════
        for i, (net_name, app_eui, app_key, dev_eui) in enumerate(sorted_networks):
            logger.info(f"[LORA-EVAL] ═══ Evaluating {net_name} ({i+1}/{len(sorted_networks)}) ═══")
 
            if i > 0:
                _stop_joins(ser)
 
            _configure(ser, net_name, app_eui, app_key, dev_eui)
            joined = _join(ser, net_name, EVAL_JOIN_ATTEMPTS, EVAL_JOIN_INTERVAL, EVAL_JOIN_TIMEOUT)

            # Passo 5: duty cycle bloqueia módulo inteiro → abortar
            if joined == "restricted":
                logger.error("[LORA-EVAL] Module in duty cycle restriction, aborting evaluation")
                return None, "restricted"

            # Passo 3: escrever estado intermédio (0=joined, -1=failed)
            try:
                state = "0" if joined else "-1"
                with open(f"/tmp/rak_njs_{net_name.lower()}", "w") as f:
                    f.write(state)
            except Exception:
                pass

            metrics = None
            if joined:
                last_joined_name = net_name
                time.sleep(3)
                metrics = run_link_check(ser=ser, network=net_name, close_after=False)
 
            results.append({
                "name":    net_name,
                "app_eui": app_eui,
                "app_key": app_key,
                "dev_eui": dev_eui,
                "joined":  joined,
                "metrics": metrics,
            })
 
        joined_results = [r for r in results if r["joined"]]
 
        if not joined_results:
            logger.error("[LORA-EVAL] No LoRa network could be joined")
            return None, None
 
        # ══════════════════════════════════════════════════════════════
        # FASE 2: Selecionar melhor rede
        # ══════════════════════════════════════════════════════════════
        # Separar redes com e sem métricas válidas
        with_metrics = [
            r for r in joined_results
            if r["metrics"]
            and r["metrics"].get("result") == 0
            and r["metrics"].get("gwcnt", 0) > 0
        ]
        without_metrics = [
            r for r in joined_results
            if r not in with_metrics
        ]

        if len(with_metrics) >= 2:
            # Lexicographic selection com hysteresis margin
            # RSSI decide se a diferença for significativa (>= RSSI_MARGIN)
            # Caso contrário, SNR desempata
            RSSI_MARGIN = 6.0  # dBm

            # Ordenar por RSSI decrescente
            sorted_by_rssi = sorted(
                with_metrics,
                key=lambda r: r["metrics"]["rssi"],
                reverse=True
            )
            top = sorted_by_rssi[0]
            second = sorted_by_rssi[1]

            rssi_diff = top["metrics"]["rssi"] - second["metrics"]["rssi"]

            if rssi_diff >= RSSI_MARGIN:
                best = top
                logger.info(
                    f"[LORA-EVAL] Selected {best['name']} via RSSI "
                    f"(rssi={best['metrics']['rssi']}dBm, "
                    f"snr={best['metrics']['snr']}dB, "
                    f"Δrssi={rssi_diff:.1f}dB >= margin={RSSI_MARGIN}dB)"
                )
            else:
                best = max(with_metrics, key=lambda r: r["metrics"]["snr"])
                logger.info(
                    f"[LORA-EVAL] Selected {best['name']} via SNR tiebreak "
                    f"(rssi={best['metrics']['rssi']}dBm, "
                    f"snr={best['metrics']['snr']}dB, "
                    f"Δrssi={rssi_diff:.1f}dB < margin={RSSI_MARGIN}dB)"
                )


        elif len(with_metrics) == 1 and len(without_metrics) >= 1:
            # Só uma tem stats → preferir TTN se estiver joined (sem stats),
            # senão usar a que tem stats
            ttn_fallback = next(
                (r for r in without_metrics if "ttn" in r["name"].lower()),
                None,
            )
            if ttn_fallback:
                best = ttn_fallback
                logger.info(
                    f"[LORA-EVAL] Selected {best['name']} (joined, preferred "
                    f"over {with_metrics[0]['name']} which had stats but lower priority)"
                )
            else:
                best = with_metrics[0]
                logger.info(
                    f"[LORA-EVAL] Selected {best['name']} via link quality "
                    f"(rssi={best['metrics']['rssi']}dBm, "
                    f"snr={best['metrics']['snr']}dB)"
                )
        else:
            # Nenhuma tem stats → preferir TTN se joined, senão última joined
            ttn_fallback = next(
                (r for r in joined_results if "ttn" in r["name"].lower()),
                None,
            )
            if ttn_fallback:
                best = ttn_fallback
            else:
                best = joined_results[-1]
            logger.warning(
                f"[LORA-EVAL] No valid link quality data; using {best['name']}"
            )
 
        # Log comparação
        for r in joined_results:
            tag = " ← SELECTED" if r["name"] == best["name"] else ""
            m = r["metrics"]
            if m and m.get("gwcnt", 0) > 0:
                log_event(
                    "lora_network_evaluated",
                    network=r["name"],
                    selected=(r["name"] == best["name"]),
                    method=m.get("method", "linkcheck"),
                    margin_db=m.get("margin"),
                    gwcnt=m.get("gwcnt"),
                    rssi_dbm=m.get("rssi"),
                    snr_db=m.get("snr"),
                )
                logger.info(
                    f"[LORA-EVAL] {r['name']}: rssi={m['rssi']}dBm, "
                    f"snr={m['snr']}dB, gw={m['gwcnt']}{tag}"
                )
            else:
                logger.info(f"[LORA-EVAL] {r['name']}: joined (no link quality data){tag}")
 
        # ══════════════════════════════════════════════════════════════
        # FASE 3: Join final — só se necessário
        # ══════════════════════════════════════════════════════════════
        if best["name"] == last_joined_name:
            logger.info(f"[LORA-EVAL] ✓ {best['name']} already joined — skipping final join")
            _mark_final(best["name"])
            _send_at(ser, "AT+CLASS=C", timeout=5)
            return best["name"], best.get("metrics")
 
        logger.info(f"[LORA-EVAL] ═══ Final join: {best['name']} ═══")
        _stop_joins(ser)
        _reset_and_wait(ser)
        _configure(ser, best["name"], best["app_eui"], best["app_key"], best["dev_eui"])
        final_ok = _join(ser, best["name"], FINAL_JOIN_ATTEMPTS, FINAL_JOIN_INTERVAL, FINAL_JOIN_TIMEOUT)
 
        if not final_ok:
            logger.error(f"[LORA-EVAL] Final join FAILED for {best['name']}")
            if last_joined_name and last_joined_name != best["name"]:
                alt = next((r for r in joined_results if r["name"] == last_joined_name), None)
                if alt:
                    logger.info(f"[LORA-EVAL] Falling back to {alt['name']}")
                    _stop_joins(ser)
                    _reset_and_wait(ser)
                    _configure(ser, alt["name"], alt["app_eui"], alt["app_key"], alt["dev_eui"])
                    final_ok = _join(ser, alt["name"], FINAL_JOIN_ATTEMPTS, FINAL_JOIN_INTERVAL, FINAL_JOIN_TIMEOUT)
                    if final_ok:
                        best = alt
 
        if not final_ok:
            logger.error("[LORA-EVAL] All final join attempts failed")
            return None, None
 
        _mark_final(best["name"])
        _send_at(ser, "AT+CLASS=C", timeout=5)
        logger.info(f"[LORA-EVAL] ✓ Ready on {best['name']}")
        return best["name"], best.get("metrics")
 
    except Exception as e:
        logger.error(f"[LORA-EVAL] Error: {e}")
        return None, None
 
    finally:
        try:
            ser.close()
        except Exception:
            pass

 
 
def decide_upload_technology(cursor=None):
    """
    Handover cascade: WiFi (priority 1) → LoRa (priority 2).

    LoRa activation:
      Tier 1 — Fast path /tmp: rede cached como joined → reutilizar (~0ms)
      Full evaluation: apenas no boot ou quando nenhuma rede cached (~80s)
      Duty cycle: flag /tmp/rak_duty_restricted → manter rede ativa sem enviar
    """
    own_connection = (cursor is None)

    if cursor is None:
        try:
            connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
            cwifi = connwifi.cursor()
        except sqlite3.Error as error:
            logger.error(f"[HANDOVER] Database connection error: {error}")
            return ('none', None)
    else:
        cwifi = cursor
        connwifi = None

    try:
        sensor_communication = cwifi.execute(
            """SELECT WifiAvailable, LoRaAvailable, WifiConnected FROM SensorCommunication"""
        ).fetchone()

        if not sensor_communication:
            logger.error("[HANDOVER] SensorCommunication table is empty")
            return ('none', None)

        wifiAvailable = sensor_communication[0]
        loraAvailable = sensor_communication[1]
        wifiConnected = sensor_communication[2]

        upload_tech = 'none'
        active_network = None

        if wifiAvailable and wifiConnected:
            upload_tech = 'wifi'
            logger.info("[HANDOVER] Using WiFi for uploads")
            cwifi.execute(
                """UPDATE SensorConfiguration
                   SET Upload_Technology=?, Last_Update=CURRENT_TIMESTAMP""",
                (upload_tech,)
            )

        elif loraAvailable:
            logger.info("[HANDOVER] WiFi unavailable, evaluating LoRa networks")

            # ── Duty cycle check ──
            duty_file = "/tmp/rak_duty_restricted"
            if os.path.exists(duty_file):
                try:
                    ts_restricted = int(open(duty_file).read().strip())
                    age_h = (time.time() - ts_restricted) / 3600
                    if age_h < 24:
                        current_cfg = cwifi.execute(
                            """SELECT Active_LoRa_Network FROM SensorConfiguration"""
                        ).fetchone()
                        current_network = current_cfg[0] if current_cfg else None
                        if current_network:
                            upload_tech = 'lora'
                            active_network = current_network
                            logger.warning(
                                f"[HANDOVER] Duty cycle restriction ({age_h:.1f}h ago) "
                                f"— keeping {current_network} active"
                            )
                    else:
                        os.remove(duty_file)
                        logger.info("[HANDOVER] Duty cycle flag expired")
                except Exception:
                    try:
                        os.remove(duty_file)
                    except Exception:
                        pass

            if upload_tech == 'none':
                current_cfg = cwifi.execute(
                    """SELECT Active_LoRa_Network FROM SensorConfiguration"""
                ).fetchone()
                current_network = current_cfg[0] if current_cfg else None

                # ── Tier 1: Fast path /tmp ──
                if current_network and check_lora_network_status(current_network):
                    upload_tech = 'lora'
                    active_network = current_network
                    logger.info(f"[HANDOVER] Tier 1: reusing joined network: {current_network}")
                else:
                    networks = cwifi.execute(
                        """SELECT name, app_eui, app_key, dev_eui
                           FROM LoRaNetworks ORDER BY id ASC"""
                    ).fetchall()

                    if not networks:
                        logger.warning("[HANDOVER] No LoRa networks configured")
                    else:
                        # Verificar qualquer rede cached
                        for net_name, _, _, _ in networks:
                            if check_lora_network_status(net_name):
                                upload_tech = 'lora'
                                active_network = net_name
                                logger.info(
                                    f"[HANDOVER] Tier 1: reusing cached network: {net_name}"
                                )
                                break
                        else:
                            # ── Full evaluation (boot ou nenhuma cached) ──
                            logger.info("[HANDOVER] Full evaluation")
                            best_name, best_metrics = evaluate_lora_networks(networks)

                            if best_metrics == "restricted":
                                logger.error("[HANDOVER] Duty cycle restriction")
                                try:
                                    with open("/tmp/rak_duty_restricted", "w") as f:
                                        f.write(str(int(time.time())))
                                except Exception:
                                    pass
                            elif best_name:
                                upload_tech = 'lora'
                                active_network = best_name
                                if best_metrics:
                                    logger.info(
                                        f"[HANDOVER] Selected {best_name} "
                                        f"(rssi={best_metrics.get('rssi')}dBm, "
                                        f"snr={best_metrics.get('snr')}dB)"
                                    )
                                else:
                                    logger.info(
                                        f"[HANDOVER] Selected {best_name} "
                                        f"(join OK, no metrics)"
                                    )
                            else:
                                logger.error("[HANDOVER] Full evaluation failed")

            # Atualizar DB
            if upload_tech != 'wifi':
                if active_network:
                    cwifi.execute(
                        """UPDATE SensorConfiguration
                           SET Upload_Technology=?, Active_LoRa_Network=?,
                               Last_Update=CURRENT_TIMESTAMP""",
                        (upload_tech, active_network)
                    )
                else:
                    cwifi.execute(
                        """UPDATE SensorConfiguration
                           SET Upload_Technology=?, Last_Update=CURRENT_TIMESTAMP""",
                        (upload_tech,)
                    )

        else:
            logger.error("[HANDOVER] No connectivity available")
            cwifi.execute(
                """UPDATE SensorConfiguration
                   SET Upload_Technology=?, Last_Update=CURRENT_TIMESTAMP""",
                (upload_tech,)
            )

        if own_connection:
            connwifi.commit()

        if upload_tech == 'none':
            logger.error("[HANDOVER] No connectivity available - uploads disabled")

        return (upload_tech, active_network)

    except sqlite3.Error as error:
        logger.error(f"[HANDOVER] Database error: {error}")
        return ('none', None)

    finally:
        if own_connection and connwifi:
            cwifi.close()
            connwifi.close()


def get_sensor_wifi_profiles():
    """
    Return configured sensor-wifi-* NetworkManager profiles sorted by priority.

    Returns:
        list of (profile_name: str, priority_index: int)
    """
    try:
        result = subprocess.run(
            [NMCLI_BIN, "-t", "-f", "NAME", "connection", "show"],
            text=True, capture_output=True, check=False, timeout=10,
        )
    except Exception as e:
        logger.error(f"[WIFI-FAILOVER] nmcli error: {e}")
        return []

    profiles = []

    for name in result.stdout.splitlines():
        name = name.strip()

        if not name.startswith("sensor-wifi-"):
            continue

        parts = name.split("-", 3)

        try:
            idx = int(parts[2])
        except (IndexError, ValueError):
            idx = 999

        profiles.append((name, idx))

    profiles.sort(key=lambda p: p[1])
    return profiles


def get_active_wifi_profile():
    """Return the name of the currently active Wi-Fi profile on wlan0, or None."""
    try:
        result = subprocess.run(
            [NMCLI_BIN, "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
            text=True, capture_output=True, check=False, timeout=10,
        )
    except Exception:
        return None

    for line in result.stdout.splitlines():
        parts = line.strip().split(":")

        if len(parts) >= 2 and parts[1] == WLAN_UPLOAD_IFACE:
            return parts[0]

    return None


def get_wifi_profile_ssid(profile_name):
    """
    Return the SSID associated with a NetworkManager Wi-Fi profile.

    Falls back to parsing profile names such as:
        sensor-wifi-1-Management_Network
    """
    try:
        result = subprocess.run(
            [
                NMCLI_BIN,
                "-g", "802-11-wireless.ssid",
                "connection", "show", "id", profile_name,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )

        ssid = result.stdout.strip()

        if ssid:
            return ssid

    except Exception:
        pass

    parts = profile_name.split("-", 3)

    if len(parts) == 4:
        return parts[3]

    return None


def get_visible_wifi_ssids(rescan=True):
    """
    Return the set of SSIDs currently visible on wlan0.
    """
    try:
        cmd = [
            NMCLI_BIN,
            "-t",
            "-f", "SSID",
            "dev", "wifi", "list",
            "ifname", WLAN_UPLOAD_IFACE,
        ]

        if rescan:
            cmd.extend(["--rescan", "yes"])

        result = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False,
            timeout=8,
        )

        ssids = set()

        for line in result.stdout.splitlines():
            ssid = line.strip()

            if not ssid:
                continue

            ssids.add(ssid)

        return ssids

    except Exception as e:
        logger.error(f"[WIFI-FAILOVER] Wi-Fi scan error: {e}")
        return set()


def try_activate_wifi_profile(profile_name, timeout_sec=15):
    """
    Activate a specific NetworkManager Wi-Fi profile on wlan0.

    Returns:
        bool: True if nmcli reports successful activation
    """
    from event_logger import log_event

    try:
        result = subprocess.run(
            [
                "sudo", "-n", NMCLI_BIN,
                "--wait", str(timeout_sec),
                "connection", "up", "id", profile_name,
                "ifname", WLAN_UPLOAD_IFACE,
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_sec + 2,
        )

        ok = result.returncode == 0

        log_event(
            "wifi_profile_activate_result",
            profile=profile_name,
            success=ok,
            returncode=result.returncode,
            stdout=result.stdout[-300:] if result.stdout else "",
            stderr=result.stderr[-300:] if result.stderr else "",
        )

        if not ok:
            logger.error(
                f"[WIFI-FAILOVER] Could not activate {profile_name}. "
                f"returncode={result.returncode}, stderr={result.stderr.strip()}"
            )

        return ok

    except Exception as e:
        logger.error(f"[WIFI-FAILOVER] nmcli activation error for {profile_name}: {e}")

        log_event(
            "wifi_profile_activate_exception",
            profile=profile_name,
            error=str(e),
        )

        return False


def try_wifi_failover(cursor=None, skip_current=False):
    """
    Try all configured sensor-wifi-* profiles in priority order.

    For each candidate:
        1. if already connected to Wi-Fi and the server is reachable, return immediately;
        2. check whether the profile SSID is visible in the current Wi-Fi scan;
        3. if the profile is already active, only verify server reachability;
        4. activate NetworkManager profile only if visible and not already active;
        5. verify end-to-end reachability using check_wifi_connection(),
           which performs netcat to the Cloud_IP_Address configured in DB.

    Returns:
        (success: bool, profile_name: str | None)
    """
    from event_logger import log_event
    
    profiles = get_sensor_wifi_profiles()

    if not profiles:
        logger.info("[WIFI-FAILOVER] No sensor-wifi-* profiles configured")
        log_event("wifi_failover_no_profiles")
        return False, None

    log_event(
        "wifi_failover_profiles_loaded",
        candidates=len(profiles),
        profiles=[p[0] for p in profiles],
        priorities=[p[1] for p in profiles],
    )

    active_profile = get_active_wifi_profile()

    if active_profile and not skip_current:
        active_ssid = get_wifi_profile_ssid(active_profile)

        _t0_nc = time.monotonic()
        nc_ok = check_wifi_connection()
        nc_ms = round((time.monotonic() - _t0_nc) * 1000, 2)

        log_event(
            "wifi_reachability_probe",
            profile=active_profile,
            ssid=active_ssid,
            priority=None,
            attempt=1,
            success=nc_ok,
            duration_ms=nc_ms,
            already_active=True,
            pre_scan=True,
        )

        if nc_ok:
            logger.info(f"[WIFI-FAILOVER] Already connected via {active_profile}; server reachable")

            log_event(
                "wifi_failover_already_connected",
                profile=active_profile,
                ssid=active_ssid,
                reachability_ms=nc_ms,
            )

            return True, active_profile

    visible_ssids = get_visible_wifi_ssids(rescan=True)

    log_event(
        "wifi_scan_complete",
        visible_count=len(visible_ssids),
        visible_ssids=list(visible_ssids),
    )

    visible_candidates = []

    for profile_name, priority in profiles:
        profile_ssid = get_wifi_profile_ssid(profile_name)

        if profile_ssid and profile_ssid in visible_ssids:
            visible_candidates.append({
                "profile": profile_name,
                "ssid": profile_ssid,
                "priority": priority,
            })

    log_event(
        "wifi_failover_visible_candidates",
        configured_candidates=len(profiles),
        visible_candidates=len(visible_candidates),
        candidates=visible_candidates,
    )

    log_event(
        "wifi_failover_start",
        candidates=len(profiles),
        active_profile=active_profile,
        skip_current=skip_current,
    )

    for profile_name, priority in profiles:

        profile_ssid = get_wifi_profile_ssid(profile_name)

        if not profile_ssid:
            logger.info(f"[WIFI-FAILOVER] Skipping {profile_name}: could not determine SSID")

            log_event(
                "wifi_failover_skip_no_ssid",
                profile=profile_name,
                priority=priority,
            )

            continue

        if profile_ssid not in visible_ssids:
            logger.info(f"[WIFI-FAILOVER] Skipping {profile_name}: SSID '{profile_ssid}' not visible")

            log_event(
                "wifi_failover_skip_not_visible",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
            )

            continue

        if skip_current and active_profile and profile_name == active_profile:
            logger.info(f"[WIFI-FAILOVER] Skipping current profile {profile_name}")

            log_event(
                "wifi_failover_skip_current",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
            )

            continue

        logger.info(f"[WIFI-FAILOVER] Trying {profile_name} (priority={priority}, ssid={profile_ssid})")

        log_event(
            "wifi_failover_attempt",
            profile=profile_name,
            ssid=profile_ssid,
            priority=priority,
        )

        if active_profile and profile_name == active_profile:
            logger.info(f"[WIFI-FAILOVER] Profile {profile_name} already active; checking server reachability only")

            log_event(
                "wifi_failover_active_profile_check",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
            )

            _t0_nc = time.monotonic()
            nc_ok = check_wifi_connection()
            nc_ms = round((time.monotonic() - _t0_nc) * 1000, 2)

            log_event(
                "wifi_reachability_probe",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
                attempt=1,
                success=nc_ok,
                duration_ms=nc_ms,
                already_active=True,
                pre_scan=False,
            )

            if nc_ok:
                logger.info(f"[WIFI-FAILOVER] Active profile {profile_name} has server reachability")

                log_event(
                    "wifi_failover_ok",
                    profile=profile_name,
                    ssid=profile_ssid,
                    priority=priority,
                    already_active=True,
                    reachability_ms=nc_ms,
                )

                return True, profile_name

            logger.info(f"[WIFI-FAILOVER] Active profile {profile_name} is not server reachable")

            log_event(
                "wifi_failover_active_profile_unreachable",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
                reachability_ms=nc_ms,
            )

            continue

        _t0_activation = time.monotonic()
        activated = try_activate_wifi_profile(profile_name)
        activation_ms = round((time.monotonic() - _t0_activation) * 1000, 2)

        log_event(
            "wifi_profile_activation_timed",
            profile=profile_name,
            ssid=profile_ssid,
            priority=priority,
            success=activated,
            duration_ms=activation_ms,
        )

        if not activated:
            logger.info(f"[WIFI-FAILOVER] Could not activate {profile_name}")

            log_event(
                "wifi_failover_activate_fail",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
                activation_ms=activation_ms,
            )

            continue

        reachable = False
        last_nc_ms = None

        MAX_WIFI_REACHABILITY_ATTEMPTS = 2
        WIFI_RETRY_SLEEP_SEC = 1
        WIFI_POST_ACTIVATION_SETTLE_SEC = 1

        time.sleep(WIFI_POST_ACTIVATION_SETTLE_SEC)

        reachable = False
        last_nc_ms = None

        for attempt in range(1, MAX_WIFI_REACHABILITY_ATTEMPTS + 1):
            _t0_nc = time.monotonic()
            nc_ok = check_wifi_connection()
            nc_ms = round((time.monotonic() - _t0_nc) * 1000, 2)
            last_nc_ms = nc_ms

            log_event(
                "wifi_reachability_probe",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
                attempt=attempt,
                success=nc_ok,
                duration_ms=nc_ms,
                already_active=False,
                activation_ms=activation_ms,
            )

            if nc_ok:
                reachable = True
                break

            if attempt < MAX_WIFI_REACHABILITY_ATTEMPTS:
                log_event(
                    "wifi_failover_netcat_retry",
                    profile=profile_name,
                    ssid=profile_ssid,
                    priority=priority,
                    attempt=attempt,
                    reachability_ms=nc_ms,
                    next_retry_in_s=WIFI_RETRY_SLEEP_SEC,
                )

                time.sleep(WIFI_RETRY_SLEEP_SEC)

        if reachable:
            logger.info(f"[WIFI-FAILOVER] Connected and server reachable via {profile_name}")

            log_event(
                "wifi_failover_ok",
                profile=profile_name,
                ssid=profile_ssid,
                priority=priority,
                already_active=False,
                activation_ms=activation_ms,
                reachability_ms=last_nc_ms,
            )

            return True, profile_name

        logger.info(
            f"[WIFI-FAILOVER] {profile_name} activated but configured server unreachable"
        )

        log_event(
            "wifi_failover_netcat_fail",
            profile=profile_name,
            ssid=profile_ssid,
            priority=priority,
            activation_ms=activation_ms,
            reachability_ms=last_nc_ms,
        )

    logger.info("[WIFI-FAILOVER] All profiles exhausted")

    log_event(
        "wifi_failover_exhausted",
        candidates=len(profiles),
        visible_candidates=len(visible_candidates),
    )

    return False, None
    
def downlink_cb(mType, port, length, msgHex):
    logger.info(f"[DOWNLINK RAW] type={mType} port={port} len={length} hex={msgHex}")
    try:
        payload = bytes.fromhex(msgHex).decode("utf-8")
    except Exception as e:
        logger.error(f"Erro a decodificar downlink: {e}")
        return

    logger.info(f"[DOWNLINK TEXT] '{payload}'")
    if payload == "r":
        logger.info("=> reboot sensor")
        os.system("sudo reboot")
    elif payload == "a":
        logger.info("=> activate detection")
        #receive_active()
    elif payload == "dis":
        logger.info("=> disable detection")
        #receive_disable()
    else:
        logger.info(f"=> comando desconhecido '{payload}'")

def publish_location_mqtt_message(msg_payload, topic):
    client = connect_mqtt()

    result = client.publish(topic, msg_payload)

    # result: [0, 1]
    status = result[0]
    if status == 0:
        print(f"Send `{msg_payload}` to topic `{topic}`.")
        return True
    else:
        print("\nFailed to publish mqtt message.")
        return False
    
def normalize_connectivity_mode(connectivity_list):
    types = set()

    for c in connectivity_list:
        raw = (c.get("type") or "").strip().lower()

        if raw == "wifi":
            types.add("wifi")
        elif "lora" in raw:
            types.add("lora")

    if "wifi" in types and "lora" in types:
        return "wifi_lora"
    elif "wifi" in types:
        return "wifi"
    elif "lora" in types:
        return "lora"
    else:
        return None


def normalize_network_type(raw_type: str) -> str:
    t = (raw_type or "").strip().lower()

    if t == "wifi":
        return "wifi"
    if "lora" in t:
        return "lora"

    return t

def publish_sensor_state(cfg, mqtt_host, mqtt_port=8883):
    connectivity = cfg.get("Connectivity", []) or cfg.get("connectivity", [])

    device_id_ttn = None
    device_name_helium = None
    for c in connectivity:
        ctype = (c.get("type") or "").strip().lower()
        device_id = (c.get("device_id") or "").strip()

        if "ttn" in ctype and device_id and not device_id_ttn:
            device_id_ttn = device_id
        elif "helium" in ctype and device_id and not device_name_helium:
            device_name_helium = device_id

    payload = {
        "uuid": str(uuid.getnode()),
        "sensor_name": cfg["sensor"]["Sensor Name"],
        "latitude": float(cfg["sensor"]["Latitude"]),
        "longitude": float(cfg["sensor"]["Longitude"]),
        "status": cfg["sensor"]["Status"],
        "connectivity_mode": normalize_connectivity_mode(connectivity),
        "firmware_version": "1.0.0",
        "power_filtration_db": int(cfg["sensor"]["Power Filtration"]),
        "messages_periodicity_min": int(cfg["sensor"]["Upload Periodicity"]),
        "sliding_window_min": int(cfg["sensor"]["Sliding Window"]),
        "influxdb_bucket": cfg["sensor"].get("InfluxDB Bucket", ""),
        "device_id_ttn": device_id_ttn,
        "device_name_helium": device_name_helium,
        "timestamp": datetime.utcnow().isoformat() + "Z"
    }

    try:
        mqtt_username, mqtt_password = get_mqtt_credentials_from_db()

        if not mqtt_username or not mqtt_password:
            raise RuntimeError("MQTT credentials are not configured in the database.")

        client = mqtt_client.Client()
        client.username_pw_set(mqtt_username, mqtt_password)
        client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        client.connect(mqtt_host, mqtt_port, 60)
        client.loop_start()

        msg_info = client.publish(
            topic="monicrowd/sensors/state",
            payload=json.dumps(payload),
            qos=1,
            retain=True
        )
        msg_info.wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
        print("[OK] Sensor state published.")

    except Exception as e:
        print(f"[ERROR] Failed to publish sensor state: {e}")


def publish_sensor_networks(cfg, mqtt_host, mqtt_port=8883):
    connectivity = cfg.get("Connectivity", [])
    networks = []
    priority = 1

    for c in connectivity:
        raw_type = (c.get("type") or "").strip().lower()
        ctype = normalize_network_type(raw_type)
        if not ctype:
            continue

        entry = {
            "type": ctype,
            "name": c.get("name", raw_type),
            "priority": priority,
            "available": True,
            "connected": None
        }

        if ctype == "wifi":
            entry["ssid"] = c.get("ssid")
            entry["cloud_address"] = c.get("cloud_address") or c.get("mqtt_address")

        if ctype == "lora":
            entry["dev_eui"] = c.get("dev_eui")
            entry["app_eui"] = c.get("app_eui")
            entry["device_id"] = c.get("device_id")

        networks.append(entry)
        priority += 1

    payload = {
        "uuid": str(uuid.getnode()),
        "networks": networks
    }

    try:
        mqtt_username, mqtt_password = get_mqtt_credentials_from_db()

        if not mqtt_username or not mqtt_password:
            raise RuntimeError("MQTT credentials are not configured in the database.")

        client = mqtt_client.Client()
        client.username_pw_set(mqtt_username, mqtt_password)
        client.tls_set(tls_version=ssl.PROTOCOL_TLS)
        client.connect(mqtt_host, mqtt_port, 60)
        client.loop_start()

        msg_info = client.publish(
            "monicrowd/sensors/networks",
            json.dumps(payload),
            qos=1,
            retain=True
        )
        msg_info.wait_for_publish(timeout=5)
        client.loop_stop()
        client.disconnect()
        print(f"[OK][NET] Published {len(networks)} network(s)")
    except Exception as e:
        print(f"[ERROR] Failed to publish networks: {e}")

import subprocess
import glob
import time

# ---------- Auto-detection helpers ----------

def _find_gps_usb_path():
    """
    Detecta o USB path do GPS de duas formas:
    1. Via /sys/class/tty (só funciona se device já está bound)
    2. Via vendor ID em /sys/bus/usb/devices (funciona mesmo sem device)
    """
    # Método 1: device já existe, extrai path do sysfs
    for dev_name in ["ttyACM0", "ttyACM1", "ttyUSB0", "ttyUSB1"]:
        sysfs = f"/sys/class/tty/{dev_name}"
        try:
            real = subprocess.run(
                ["readlink", "-f", sysfs],
                capture_output=True, text=True
            ).stdout.strip()
            if "/tty/" not in real:
                continue
            parts = real.split("/")
            for p in parts:
                if "-" in p and ":" not in p and "usb" not in p and "platform" not in p:
                    if os.path.isdir(f"/sys/bus/usb/devices/{p}"):
                        print(f"[GPS] Detected USB path (method 1): {p}")
                        return p
        except Exception:
            continue

    # Método 2: device não existe ainda, procura por GPS vendor IDs conhecidos
    # u-blox: 1546, SiRF: 0856, Prolific: 067b, FTDI: 0403, CH340: 1a86, CP210x: 10c4
    GPS_VENDORS = {"1546", "0856", "067b", "0403", "1a86", "10c4"}

    try:
        usb_devices = [
            d for d in os.listdir("/sys/bus/usb/devices/")
            if "-" in d and ":" not in d and "usb" not in d
        ]
        for dev in sorted(usb_devices):
            vendor_path = f"/sys/bus/usb/devices/{dev}/idVendor"
            try:
                with open(vendor_path) as f:
                    vendor = f.read().strip().lower()
                if vendor in GPS_VENDORS:
                    print(f"[GPS] Detected USB path (method 2, vendor={vendor}): {dev}")
                    return dev
            except Exception:
                continue
    except Exception:
        pass

    return None


def _find_gps_device():
    """Detecta /dev/ttyACM* ou /dev/ttyUSB* do GPS."""
    candidates = sorted(glob.glob("/dev/ttyACM*")) + sorted(glob.glob("/dev/ttyUSB*"))
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        # Prefere ttyACM (cdc_acm) > ttyUSB
        return candidates[0]
    return None


# ---------- Enable / Disable GPS ----------

def enable_gps():
    print("[GPS] Re-enabling GPS USB device and services...")
    try:
        usb_path = _find_gps_usb_path()
        if usb_path:
            print(f"[GPS] Binding USB path: {usb_path}")
            subprocess.run(
                ["bash", "-c", f"echo '{usb_path}' | sudo tee /sys/bus/usb/drivers/usb/bind"],
                check=False
            )
            time.sleep(1)  # Espera o device aparecer
        else:
            print("[GPS] USB path not found, skipping bind (device may already be active).")

        subprocess.run(["sudo", "systemctl", "start", "gpsd.socket"], check=False)
        subprocess.run(["sudo", "systemctl", "start", "gpsd.service"], check=False)
        print("[GPS] GPS re-enabled.")
    except Exception as e:
        print(f"[GPS] Failed to enable GPS: {e}")


def disable_gps():
    print("[GPS] Disabling GPS services and unbinding USB device...")
    try:
        subprocess.run(["sudo", "systemctl", "stop", "gpsd.socket"], check=False)
        subprocess.run(["sudo", "systemctl", "stop", "gpsd.service"], check=False)

        # Kill any process on the actual GPS device
        gps_dev = _find_gps_device()
        if gps_dev:
            print(f"[GPS] Killing processes on {gps_dev}")
            subprocess.run(["sudo", "fuser", "-k", gps_dev], check=False)

        # Unbind USB
        usb_path = _find_gps_usb_path()
        if usb_path:
            print(f"[GPS] Unbinding USB path: {usb_path}")
            subprocess.run(
                ["bash", "-c", f"echo '{usb_path}' | sudo tee /sys/bus/usb/drivers/usb/unbind"],
                check=False
            )
        else:
            print("[GPS] USB path not found, skipping unbind.")

        print("[GPS] GPS stopped and USB device unbound.")
    except Exception as e:
        print(f"[GPS] Failed to disable GPS: {e}")


# ── LoRa Replay helpers ──────────────────────────────────────────




def get_and_increment_lora_seq():
    try:
        with open(LORA_SEQ_FILE, "r") as f:
            seq = int(f.read().strip()) % 256
    except (FileNotFoundError, ValueError):
        seq = 0

    with open(LORA_SEQ_FILE, "w") as f:
        f.write(str((seq + 1) % 256))

    return seq


def get_n_pending_measurements(n):
    conn = sqlite3.connect('/home/kali/Desktop/DB/StoredMeasurements.db', timeout=30)
    cursor = conn.cursor()

    rows = cursor.execute(
        """SELECT Timestamp, DevicesDetected FROM PendingMeasurements
           ORDER BY Timestamp ASC LIMIT ?""",
        (n,)
    ).fetchall()

    cursor.close()
    conn.close()
    return rows


def remove_n_pending_measurements(n):
    conn = sqlite3.connect('/home/kali/Desktop/DB/StoredMeasurements.db', timeout=30)
    cursor = conn.cursor()

    cursor.execute("""
        DELETE FROM PendingMeasurements
        WHERE rowid IN (
            SELECT rowid FROM PendingMeasurements
            ORDER BY Timestamp ASC LIMIT ?
        )
    """, (n,))

    conn.commit()
    cursor.close()
    conn.close()
    print(f"[PENDING] Removed {n} oldest pending measurements.")



# ══════════════════════════════════════════════════════════════════
# Measurement Buffer — circular, 256 slots, substitui PendingMeasurements
# ══════════════════════════════════════════════════════════════════

BUFFER_DB_PATH = '/home/kali/Desktop/DB/StoredMeasurements.db'
BUFFER_SIZE = 256
LAST_ACK_SEQ_FILE = "/home/kali/Desktop/DB/last_ack_seq.txt"
LORA_SEQ_FILE = "/home/kali/Desktop/DB/lora_seq.txt"


def init_measurement_buffer():
    """Cria a tabela MeasurementBuffer se não existir."""
    conn = sqlite3.connect(BUFFER_DB_PATH, timeout=30)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS MeasurementBuffer (
            seq INTEGER PRIMARY KEY,
            timestamp INTEGER NOT NULL,
            devices_detected INTEGER NOT NULL
        )
    """)
    conn.commit()
    cursor.close()
    conn.close()


def buffer_insert(seq, timestamp, devices_detected):
    """
    Insere/substitui uma medição no buffer circular.
    O seq é sempre mod 256, o INSERT OR REPLACE garante a circularidade.
    """
    conn = sqlite3.connect(BUFFER_DB_PATH, timeout=30)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO MeasurementBuffer (seq, timestamp, devices_detected) VALUES (?, ?, ?)",
        (seq % BUFFER_SIZE, timestamp, int(devices_detected))
    )
    conn.commit()
    cursor.close()
    conn.close()


def buffer_get_range(from_seq_exclusive, to_seq_inclusive):
    """
    Devolve medições do buffer para SEQs no intervalo (from_seq_exclusive, to_seq_inclusive].
    Lida com wrap-around do uint8.

    Returns:
        list de (seq, timestamp, devices_detected) ordenada por posição no gap.
    """
    conn = sqlite3.connect(BUFFER_DB_PATH, timeout=30)
    cursor = conn.cursor()

    results = []

    # Quantos SEQs no gap?
    gap = (to_seq_inclusive - from_seq_exclusive) % BUFFER_SIZE

    if gap == 0:
        cursor.close()
        conn.close()
        return results

    # Nunca mais do que BUFFER_SIZE
    if gap > BUFFER_SIZE:
        gap = BUFFER_SIZE

    for i in range(1, gap + 1):
        seq = (from_seq_exclusive + i) % BUFFER_SIZE
        row = cursor.execute(
            "SELECT seq, timestamp, devices_detected FROM MeasurementBuffer WHERE seq = ?",
            (seq,)
        ).fetchone()

        if row:
            results.append(row)

    cursor.close()
    conn.close()
    return results


# ── SEQ counter (persistente, uint8 0-255) ───────────────────────

def get_and_increment_lora_seq():
    """Devolve o SEQ atual e incrementa o contador em disco."""
    try:
        with open(LORA_SEQ_FILE, "r") as f:
            seq = int(f.read().strip()) % BUFFER_SIZE
    except (FileNotFoundError, ValueError):
        seq = 0

    with open(LORA_SEQ_FILE, "w") as f:
        f.write(str((seq + 1) % BUFFER_SIZE))

    return seq


def get_current_lora_seq():
    """Lê o SEQ atual sem incrementar (para uso no downlink handler)."""
    try:
        with open(LORA_SEQ_FILE, "r") as f:
            val = int(f.read().strip()) % BUFFER_SIZE
            # O ficheiro guarda o PRÓXIMO seq, logo o atual é val - 1
            return (val - 1) % BUFFER_SIZE
    except (FileNotFoundError, ValueError):
        return 0


# ── last_ack_seq (persistente) ───────────────────────────────────

def get_last_ack_seq():
    """
    Lê o último SEQ confirmado como recebido pelo servidor.
    Retorna None se nunca houve confirmação (primeiro arranque).
    """
    try:
        with open(LAST_ACK_SEQ_FILE, "r") as f:
            val = f.read().strip()
            if val == "" or val.lower() == "none":
                return None
            return int(val)
    except (FileNotFoundError, ValueError):
        return None


def set_last_ack_seq(seq):
    """Guarda o último SEQ confirmado."""
    with open(LAST_ACK_SEQ_FILE, "w") as f:
        f.write(str(seq))


# ── Funções legadas (manter para backward compat, remover depois) ─

def get_n_pending_measurements(n):
    conn = sqlite3.connect(BUFFER_DB_PATH, timeout=30)
    cursor = conn.cursor()
    rows = cursor.execute(
        """SELECT Timestamp, DevicesDetected FROM PendingMeasurements
           ORDER BY Timestamp ASC LIMIT ?""",
        (n,)
    ).fetchall()
    cursor.close()
    conn.close()
    return rows


def remove_n_pending_measurements(n):
    conn = sqlite3.connect(BUFFER_DB_PATH, timeout=30)
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM PendingMeasurements
        WHERE rowid IN (
            SELECT rowid FROM PendingMeasurements
            ORDER BY Timestamp ASC LIMIT ?
        )
    """, (n,))
    conn.commit()
    cursor.close()
    conn.close()

# ── last_confirmed_seq (conservador, só avança com confirmação) ──

LAST_CONFIRMED_SEQ_FILE = "/home/kali/Desktop/DB/last_confirmed_seq.txt"

def get_last_confirmed_seq():
    """Último SEQ que o servidor confirmou explicitamente via FPort 5."""
    try:
        with open(LAST_CONFIRMED_SEQ_FILE, "r") as f:
            val = f.read().strip()
            if val == "" or val.lower() == "none":
                return None
            return int(val)
    except (FileNotFoundError, ValueError):
        return None

def set_last_confirmed_seq(seq):
    """Guarda o último SEQ confirmado pelo servidor."""
    with open(LAST_CONFIRMED_SEQ_FILE, "w") as f:
        f.write(str(seq))