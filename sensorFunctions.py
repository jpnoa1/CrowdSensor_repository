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
MQTT_PORT = 1883
MQTT_USERNAME = 'tmmss1'
MQTT_PASSWORD = 'tomasantos00'
TOPIC_NETWORKS = "monicrowd/sensors/networks"

#Number of configuration parameters (uuid, name, etc...)
SENSOR_CONFIG_PARAMETERS_NUMB = 15
DEFAULT_CONFIG_PARAMETERS_NUMB = 12

PID_FILE = "/home/kali/Desktop/sniffer.pid"

#Raspberry Pi OUIs List
rpi_oui = ["dc:a6:32", "b8:27:eb", "28:cd:c1", "2c:cf:67", "3a:35:41", "d8:3a:dd", "e4:5f:01"]

#Lora
LORA_SERIAL_PORT = "/dev/ttyAMA0"
COMM_AVAILABLE_LOCK_FILE = "/tmp/sensorCommunicationAvailable.lock"
BOOT_COMPLETE_FILE = "/tmp/sensor_boot_complete"



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

def heliumNodeSetup():
    #for your first session after boot, you will need to do a hard-reset instead of a reset lora command to activate the module
    #cmd = "sudo rak811 hard-reset"
    #os.system(cmd)

    cmd = "sudo rak811 -v reset lora"
    os.system(cmd)

    cmd = "sudo rak811 -v set-config app_eui=190E110342012981 app_key=CBDF9117D3E1A7F9AA11166ED97BF8F6"
    os.system(cmd)

    cmd = "sudo rak811 -v dr 2"
    os.system(cmd)

    cmd = "sudo rak811 -v join-otaa"
    output = subprocess.check_output(cmd, shell=True)

    if "Joined in OTAA mode" in str(output):
        print("Connected to Helium network.")
        set_lora_connected(True)
        return True
    else:
        print("Not connected to Helium network.")
        set_lora_connected(False)
        return False
    
def connect_mqtt():
    client_id = f'python-mqtt-{random.randint(0, 1000)}'

    #Get cloud ip address
    try:

        connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
        cwifi = connwifi.cursor()

        cloud_ip_addr = cwifi.execute("""SELECT Cloud_IP_Address FROM SensorConfiguration""").fetchone()[0]

        cwifi.close()
        connwifi.close()

    except sqlite3.Error as error:
        print("Failed to get data from database.", error)

    def on_connect(client, userdata, flags, rc, properties):
        if rc == 0:
            print("Connected to MQTT Broker!")
        else:
            print("Failed to connect, return code %d\n", rc)
            
    # Set Connecting Client ID
    client = mqtt_client.Client(client_id=client_id, callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2)
    client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
    client.on_connect = on_connect
    client.connect(cloud_ip_addr, MQTT_PORT) 
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
def write_crontab_file(status, detection_if, upload_periodicity, reboot_periodicity, reboot_time):
    # Write tasks configuration file        
    print("Creating new tasks configuration file...")
    f = open(CONFIGURED_CRONJOBS_FILEPATH, 'w')
    print("New configuration file created.")
    print("")

    print("Writing tasks to configuration file...")

    f.write("# This file allows users to configure the sensor tasks to be run\n")
    f.write("# automatically on pre-determined time-shedules.\n")
    f.write("#\n")
    f.write("# SENSOR CONFIGURED TASKS: \n")
    f.write("#\n")
    f.write("# Check available communication technologies and interfaces\n")
    f.write("@reboot sleep 15 && /usr/bin/python3 /home/kali/Desktop/sensorCommunicationAvailable.py\n")
    f.write("# Periodic check of communication technologies and interfaces\n")
    f.write("*/5 * * * * /usr/bin/python3 /home/kali/Desktop/sensorCommunicationCheck.py\n")
    #f.write("# Monitor battery powerbank\n")
    #f.write("* * * * * /usr/bin/python3 /home/kali/Desktop/bat_powerbank.py\n")
    
    if status == "Active":
        f.write("# Wi-Fi detection of devices\n")
        #f.write("*/10 * * * * timeout -k 1 590s sudo airodump-ng --background 1 " + str(detection_if + "\n"))
        #f.write("*/10 * * * * sleep 595 && sudo pkill airodump-ng\n")
        f.write("@reboot sleep 90 && sudo /usr/bin/python3 /home/kali/Desktop/sensorStartup.py\n")
        f.write("# Periodic upload of crowding data to the Cloud Server\n")
        f.write("*/" + str(upload_periodicity) + " * * * * /usr/bin/python3 /home/kali/Desktop/sendCrowdingData.py \n")
        f.write("# Periodic delete of outdated and unnecessary data from local database\n")
        f.write("0 * * * * /usr/bin/python3 /home/kali/Desktop/dataRetentionManager.py 30\n")
    elif status == "Disabled":
        f.write("# Wi-Fi detection of devices\n")
        #f.write("#*/10 * * * * timeout -k 1 590s sudo airodump-ng --background 1 " + str(detection_if + "\n"))
        f.write("#@reboot sleep 90 && sudo /usr/bin/python3 /home/kali/Desktop/sensorStartup.py\n")
        #f.write("#*/10 * * * * sleep 595 && sudo pkill airodump-ng\n")
        f.write("# Periodic upload of crowding data to the Cloud Server\n")
        f.write("#*/" + str(upload_periodicity) + " * * * * /usr/bin/python3 /home/kali/Desktop/sendCrowdingData.py \n")
        f.write("# Periodic delete of outdated and unnecessary data from local database\n")
        f.write("#0 * * * * /usr/bin/python3 /home/kali/Desktop/dataRetentionManager.py 30\n")
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
    #print(cmd)
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

def publish_detections_mqtt_message(unix_timestamp, devices_detected: int, topic):
    
    client = connect_mqtt()

    msg_payload = {
        "timestamp": unix_timestamp,
        "devices_detected": int(devices_detected)
    }

    json_msg_payload = json.dumps(msg_payload, separators=(",", ":"))

    result = client.publish(topic, json_msg_payload)

    # result: [0, 1]
    status = result[0]
    if status == 0:
        print(f"Send `{msg_payload}` to topic `{topic}`.")
        return True
    else:
        print("\nFailed to publish mqtt message.")
        # Save measurement in database
        #store_pending_measurement(unix_timestamp, devices_detected)
        return False

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
    
def check_wifi_connection():        
    wifiConnected = False
    interfaces = ni.interfaces()

    for iface in interfaces:
        if iface != 'lo' and len(ni.ifaddresses(iface)) > 2:
            wifiConnected = True
            break

    if wifiConnected:
        set_wifi_connected(True)
        return True
    else:
        set_wifi_connected(False)
        return False
     
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


def decide_upload_technology(cursor=None):
    """
    Handover cascade: WiFi (priority 1) → LoRa networks (priority 2+)

    Args:
        cursor: Reuse existing DB connection to avoid locks

    Returns:
        tuple: (upload_tech, network_name)
               - upload_tech: 'wifi', 'lora', or 'none'
               - network_name: Active LoRa network name or None
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

        wifiAvailable = sensor_communication[0]
        loraAvailable = sensor_communication[1]
        wifiConnected = sensor_communication[2]

        upload_tech = 'none'
        active_network = None

        if wifiAvailable and wifiConnected:
            upload_tech = 'wifi'
            logger.info("[HANDOVER] Using WiFi for uploads")

        elif loraAvailable:
            logger.info("[HANDOVER] WiFi unavailable, evaluating LoRa networks")

            # 1) Tentar reutilizar a rede LoRa atualmente ativa, se já estiver joined
            current_cfg = cwifi.execute(
                """SELECT Active_LoRa_Network FROM SensorConfiguration"""
            ).fetchone()

            current_network = current_cfg[0] if current_cfg else None

            if current_network and check_lora_network_status(current_network):
                upload_tech = 'lora'
                active_network = current_network
                logger.info(f"[HANDOVER] Reusing already-joined LoRa network: {current_network}")

            else:
                # 2) Caso não exista rede ativa válida, ver redes por prioridade
                networks = cwifi.execute(
                    """SELECT name, app_eui, app_key, dev_eui FROM LoRaNetworks ORDER BY id ASC"""
                ).fetchall()

                if not networks:
                    logger.warning("[HANDOVER] No LoRa networks configured in database")
                else:
                    for net_name, app_eui, app_key, dev_eui in networks:
                        # Primeiro verificar se já existe estado cached de joined
                        if check_lora_network_status(net_name):
                            upload_tech = 'lora'
                            active_network = net_name
                            logger.info(f"[HANDOVER] Using cached joined network {net_name}")
                            break

                        # Só se não estiver joined é que tenta join
                        logger.info(f"[HANDOVER] Trying join on {net_name}...")
                        if try_join_lora_network(net_name, app_eui, app_key, dev_eui, join_attempts=3):
                            upload_tech = 'lora'
                            active_network = net_name
                            logger.info(f"[HANDOVER] Successfully using LoRa ({net_name})")
                            break
                        else:
                            logger.info(f"[HANDOVER] {net_name} failed, trying next network")

        if active_network:
            cwifi.execute(
                """UPDATE SensorConfiguration SET Upload_Technology=?, Active_LoRa_Network=?, Last_Update=CURRENT_TIMESTAMP""",
                (upload_tech, active_network)
            )
        else:
            cwifi.execute(
                """UPDATE SensorConfiguration SET Upload_Technology=?, Active_LoRa_Network=NULL, Last_Update=CURRENT_TIMESTAMP""",
                (upload_tech,)
            )

        if own_connection:
            connwifi.commit()

        if upload_tech == 'none':
            logger.error("[HANDOVER] No connectivity available - uploads disabled")

        return (upload_tech, active_network)

    except sqlite3.Error as error:
        logger.error(f"[HANDOVER] Database error during technology selection: {error}")
        return ('none', None)

    finally:
        if own_connection and connwifi:
            cwifi.close()
            connwifi.close()




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

def publish_sensor_state(cfg, mqtt_host, mqtt_port=1883):
    connectivity = cfg.get("Connectivity", []) or cfg.get("connectivity", [])

    device_id_ttn = ""
    device_name_helium = ""
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
        client = mqtt_client.Client()
        client.username_pw_set("tmmss1", "tomasantos00")
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


def publish_sensor_networks(cfg, mqtt_host, mqtt_port=1883):
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
        client = mqtt_client.Client()
        client.username_pw_set("tmmss1", "tomasantos00")
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

def enable_gps():
    print("[GPS] Re-enabling GPS USB device and services...")

    try:
        usb_path = "1-1"
        subprocess.run(
            ["bash", "-c", f"echo '{usb_path}' | sudo tee /sys/bus/usb/drivers/usb/bind"],
            check=False
        )

        subprocess.run(["sudo", "systemctl", "start", "gpsd.socket"], check=False)
        subprocess.run(["sudo", "systemctl", "start", "gpsd.service"], check=False)

        print("[GPS] GPS re-enabled.")

    except Exception as e:
        print(f"[GPS] Failed to enable GPS: {e}")

import subprocess
import os

def disable_gps():
    print("[GPS] Disabling GPS services and unbinding USB device...")

    try:
        # 1) Stop gpsd
        subprocess.run(["sudo", "systemctl", "stop", "gpsd.socket"], check=False)
        subprocess.run(["sudo", "systemctl", "stop", "gpsd.service"], check=False)

        # 2) Kill any process still using ttyACM0
        subprocess.run(["sudo", "fuser", "-k", "/dev/ttyACM0"], check=False)

        # 3) Unbind GPS USB device
        usb_path = "1-1"
        subprocess.run(
            ["bash", "-c", f"echo '{usb_path}' | sudo tee /sys/bus/usb/drivers/usb/unbind"],
            check=False
        )

        print("[GPS] GPS stopped and USB device unbound.")

    except Exception as e:
        print(f"[GPS] Failed to disable GPS: {e}")