import sqlite3
import subprocess
import netifaces as ni
import sys

from sensorFunctions import *

#                   sensorCommunicationCheck.py
#
#   This script is responsible for checking the available upload
#   technologies and if they have connection.
#
#   It is aimed to run periodically to check if the network connection 
#   was lost or not.
#
#
#   Author: Tomas Mestre Santos
#   Date: 09-03-2024
#

if not os.path.exists(BOOT_COMPLETE_FILE):
    print("[CHECK] Boot initialization not complete yet. Exiting.")
    sys.exit(0)

_, available_released = wait_for_script_lock(
    COMM_AVAILABLE_LOCK_FILE,
    max_wait_sec=90,
    poll_sec=2,
    log_prefix="[CHECK]"
)

if not available_released:
    print("[CHECK] sensorCommunicationAvailable.py still running. Skipping this cycle to avoid serial contention.")
    sys.exit(0)

#Get Lora upload available and current upload technology
try:

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchone()

    if sensor_configuration is not None:

        sensor_uuid = sensor_configuration[0]
        current_upload_technology = sensor_configuration[12]
        
        current_lora_network = None
        if len(sensor_configuration) > 16:
            current_lora_network = sensor_configuration[16]
        
        print(f"[CHECK] Current technology: '{current_upload_technology}', LoRa network: {current_lora_network}")

    sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

    if sensor_communication is None:

        # Run 'sensorCommunicationAvailable.py' script
        subprocess.run(['/usr/bin/python3', '/home/kali/Desktop/sensorCommunicationAvailable.py'])

        sensor_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchone()

    wifiAvailable = sensor_communication[0]
    loraAvailable = sensor_communication[1]
    loraConnected = sensor_communication[3]
    curr_ip_address = sensor_communication[4]
    curr_upload_if = sensor_communication[5]
    curr_detect_if = sensor_communication[6]
        
    
except sqlite3.Error as error:
    print("Failed to read upload technologies from database.", error)


#debug 
#cwifi.execute("UPDATE SensorConfiguration SET Upload_Technology='wifi', Active_LoRa_Network=NULL")
#connwifi.commit()
#sys.exit(0) # Termina o script aqui para impedir que ele faça as verificações de hardware e reverta para lora/wifi
wifiAvailable= False
#Check Wi-Fi connection
if wifiAvailable:
    wifiConnected = check_wifi_connection()
else:
    set_wifi_connected(False)
    wifiConnected = False

    
#Check LoRa upload connection
if sensor_configuration is not None and current_upload_technology == "lora":
    if loraAvailable:
        if current_lora_network:
            loraConnected = check_lora_network_status(current_lora_network)
            print(f"[CHECK] LoRa network {current_lora_network} status: {'joined' if loraConnected else 'failed'}")
        else:
            loraConnected = check_lora_connection_no_Join()
            print("[CHECK] Active_LoRa_Network is NULL, using legacy check")
    else:
        set_lora_connected(False)
        loraConnected = False



#Check upload and detection interfaces
upload_interface, detection_interface = check_upload_detection_interfaces(False)

if curr_upload_if != upload_interface:
    cwifi.execute("""UPDATE SensorCommunication SET Upload_Interface=?, Last_Update=CURRENT_TIMESTAMP""", (upload_interface,))

    
    
if wifiConnected: 
    ip_addr = ni.ifaddresses(upload_interface)[ni.AF_INET][0]['addr']

    if str(curr_ip_address) != str(ip_addr):
        cwifi.execute("""UPDATE SensorCommunication SET IP_Address=?, Last_Update=CURRENT_TIMESTAMP""", (ip_addr,))

if curr_detect_if != detection_interface:
    cwifi.execute("""UPDATE SensorCommunication SET Detection_Interface=?, Last_Update=CURRENT_TIMESTAMP""", (detection_interface,))
    print("Detection interfaces are different!")

    #Rewrite crontab tasks file
    if sensor_configuration is not None:
        status = sensor_configuration[4]
        uploadPeriodicity = sensor_configuration[10]
        rebootPeriodicity = sensor_configuration[13]
        rebootTime = sensor_configuration[14]

        write_crontab_file(status, detection_interface, uploadPeriodicity, rebootPeriodicity, rebootTime)

needs_handover = False

if current_upload_technology == "wifi":
    if not wifiConnected:
        needs_handover = True
        print("[CHECK] WiFi connection lost, triggering handover")
elif current_upload_technology == "lora":
    if wifiConnected:
        needs_handover = True
        print("[CHECK] Preferred uplink WiFi is available again, returning from LoRa to WiFi")

    elif not loraConnected:
        needs_handover = True
        if current_lora_network:
            print(f"[CHECK] LoRa network {current_lora_network} failed, triggering handover cascade")
        else:
            print("[CHECK] LoRa connection lost, triggering handover")
elif current_upload_technology == "none":
    needs_handover = True
    print("[CHECK] No active technology, attempting handover cascade")

if needs_handover:
    new_tech, new_network = decide_upload_technology(cursor=cwifi)
    
    print(f"[CHECK] Handover completed - New technology: {new_tech}")
    if new_network:
        print(f"[CHECK] Active LoRa network: {new_network}")
    elif new_tech == 'none':
        print("[CHECK] Handover failed - no connectivity available")
    
    lora_connected_flag = (new_tech == 'lora')
    cwifi.execute(
        """UPDATE SensorCommunication SET LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP""",
        (lora_connected_flag,)
    )
else:
    print(f"[CHECK] Current technology '{current_upload_technology}' operational, no handover needed")

#Commit changes
connwifi.commit()
cwifi.close()
connwifi.close()





