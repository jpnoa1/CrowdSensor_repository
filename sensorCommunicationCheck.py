import sqlite3
import os
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

#Get Lora upload available and current upload technology
try:

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchone()

    if sensor_configuration is not None:

        sensor_uuid = sensor_configuration[0]
        current_upload_technology = sensor_configuration[12]

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


#Check Wi-Fi connection
if wifiAvailable:
    wifiConnected = check_wifi_connection()
else:
    set_wifi_connected(False)
    wifiConnected = False

    
#Check LoRa upload connection
if sensor_configuration is not None and current_upload_technology == "lora":
    if loraAvailable:
        loraConnected = check_lora_connection()
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


#Commit changes
connwifi.commit()
cwifi.close()
connwifi.close()





