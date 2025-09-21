import sqlite3
import subprocess
import netifaces as ni

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

#Check if Wi-Fi and LoRa upload are available
#wifiAvailable = check_wifi_available()
#para teste
wifiAvailable = False
loraAvailable = check_lora_available()                           #LoRaWAN manually set to 'unavailable' as the LoRaWAN communication is already 
                                             #not fully implemented on this sensor version for the Raspberry Pi 5.

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
    loraConnected = check_lora_connection() 
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

            sensor_uuid = sensor_configuration[0]

            #Decide upload technology
            if wifiAvailable and wifiConnected:
                uploadTechnology = 'wifi'
            elif loraAvailable:
                uploadTechnology = 'lora'
            else:
                uploadTechnology = 'none'

            #Check if upload technology changed
            current_upload_tech = sensor_configuration[12]

            if current_upload_tech != uploadTechnology:    
                print(f"Upload communication changed from '{current_upload_tech}' to '{uploadTechnology}'. Updating database.")
                cwifi.execute("""UPDATE SensorConfiguration SET Upload_Technology=?, Last_Update=CURRENT_TIMESTAMP""", (uploadTechnology,))

            else:
                print(f"Upload technology remained the same: '{current_upload_tech}'. No changes made.")


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


    #Commit changes
    connwifi.commit()
    cwifi.close()

except sqlite3.Error as error:
    print("Failed to save communication technologies in local database.", error)

finally:
    if connwifi:
        connwifi.close()

#mudei
#if loraAvailable == True:
#    heliumNodeSetup()
