import subprocess
import sqlite3
from datetime import datetime
import sys

from sensorFunctions import *


#           sensorConfiguration.py
#
#   This script allows to setup all sensor configurations
#   configurations by running only this script.
#
#
#   The configurations are divided in three
#   different parts:
#   (1) Sensor General Configuration; -> Configuration of (Sensor UUID, Sensor Name, Sensor Location)
#   (2) Sensor Upload Configuration; -> Configuration of InfluxDB upload parameters (Cloud Server IP Address, Org Name, Bucket Name, Authorization token) 
#   (3) Sensor Tasks Configuration. -> Configuration of cronjobs for automatic time-scheduling tasks.
#
#   Author: Tomas Mestre Santos
#   Date: 26-01-2024
#

print("------------------------------------------------------------------")
print("------                 SENSOR CONFIGURATION                 ------")
print("------------------------------------------------------------------")
print("-   This script allows to setup the sensor configurations.       -")
print("-                                                                -")
print("-   You can choose either to configure only the basic            -")
print("-   configurations, or all sensor configurations.                -")
print("-                                                                -")
print("-   Configuration Modes:                                         -")
print("-     (1) Fast -> Configure only the sensor name (Recommended).  -")
print("-                                                                -")
print("-     (2) Complete -> Configure all sensor configurations:       -")
print("-               - Sensor General Configuration;                  -")
print("-               - Sensor Upload Configuration;                   -")
print("-               - Sensor Tasks Configuration.                    -")
print("-                                                                -")
print("-                                                                -")
print("-   Choose the configuration mode to configure the sensor.       -")
print("-                                                                -")
print("------------------------------------------------------------------\n")

try:

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

except sqlite3.Error as error:
    print("Error while connecting to database.", error)


# Check if sensor has previous configuration
sensor_configuration_row = cwifi.execute("""SELECT COUNT(*) FROM SensorConfiguration""").fetchone()[0]

print("Checking current sensor configuration...")


# If sensor does not have previous configuration, create it
if sensor_configuration_row == 0:

    print("There is no sensor configuration.\n")


    #Confirm procedure
    confirm("Do you wish to create a new sensor configuration (Y/n)?")
    print("\nCreating new sensor configuration.")
    print("------------------------------------------------------------------")

    #Check upload technology
    wifi_lora_connected = cwifi.execute("""SELECT LoRaAvailable, WifiConnected, LoRaConnected FROM SensorCommunication""").fetchone()
    #mudar
    #loraAvailable = wifi_lora_connected[0]
    wifiConnected = wifi_lora_connected[1]
    #loraConnected = wifi_lora_connected[2]

    #If wifiConnected, allow configuration using 'Fast' or 'Complete' mode
    if wifiConnected:

        #Check configuration mode
        configuration_mode = check_config_mode()

        if configuration_mode == "1":

            #########################################################################################
            ###############################  FAST CONFIGURATION MODE  ###############################
            #########################################################################################

            #Check sensor UUID and sensor name
            uuid_from_mac_addr, sensorName, uploadTechnology, uploadInterface, detectionInterface = fast_config()

            #Confirm accepting default configuration values from 'SensorDefaultConfig' table
            print("\nThis configuration mode assumes the remaining default values:")

            sensor_default_config = cwifi.execute("""SELECT * FROM SensorDefaultConfiguration""").fetchall()

            df_latitude = sensor_default_config[0][0]
            df_longitude = sensor_default_config[0][1]
            df_status = sensor_default_config[0][2]
            df_power_filtration = sensor_default_config[0][3]
            df_cloud_ip_address = sensor_default_config[0][4]
            df_influxdb_org = sensor_default_config[0][5]
            df_influxdb_bucket = sensor_default_config[0][6]
            df_auth_token = sensor_default_config[0][7]
            df_upload_periodicity = sensor_default_config[0][8]
            df_sliding_window = sensor_default_config[0][9]
            df_reboot_periodicity = sensor_default_config[0][10]
            df_reboot_time = sensor_default_config[0][11]

            print(f"Latitude:              {df_latitude}")
            print(f"Longitude:             {df_longitude}")
            print(f"Status:                {df_status}")
            print(f"Power Filtration:      {df_power_filtration} dB")
            print(f"Cloud IP Address:      {df_cloud_ip_address}")
            print(f"InfluxDB Organization: {df_influxdb_org}")
            print(f"InfluxDB Bucket:       {df_influxdb_bucket}")
            print(f"InfluxDB Auth Token:   {df_auth_token}")
            print(f"Upload Periodicity:    Every {df_upload_periodicity} minutes")
            print(f"Sliding Window:        Last {df_sliding_window} minutes")
            print(f"Reboot Periodicity:    {df_reboot_periodicity}")
            print(f"Reboot Time:           " + str(datetime.strptime(str(df_reboot_time), "%H").strftime("%I:%M %p")))


        elif configuration_mode == "2":

            #########################################################################################
            ############################ (1) SENSOR GENERAL CONFIGURATION ###########################
            #########################################################################################

            uuid_from_mac_addr, sensorName, latitude, longitude, status, power_filtration = config_general()

            #########################################################################################
            ############################# (2) SENSOR UPLOAD CONFIGURATION ###########################
            #########################################################################################

            cloudServerIPAddress, influxDB_Org_Name, influxDB_Bucket, authorization_Token = config_influx()

            #########################################################################################
            ############################# (3) SENSOR TASKS CONFIGURATION ############################
            #########################################################################################

            uploadInterface, detectionInterface, uploadPeriodicity, slidingWindow, technology, rebootPeriodicity, rebootTime = config_tasks()

    elif loraConnected:

        print("Upload only available via LoRa. 'Fast' configuration mode automatically selected.")

        configuration_mode = "1"

        #########################################################################################
        ###############################  FAST CONFIGURATION MODE  ###############################
        #########################################################################################

        #Check sensor UUID and sensor name
        uuid_from_mac_addr, sensorName, uploadTechnology, uploadInterface, detectionInterface = fast_config()

        #Confirm accepting default configuration values from 'SensorDefaultConfig' table
        print("\nThis configuration mode assumes the remaining default values:")

        sensor_default_config = cwifi.execute("""SELECT * FROM SensorDefaultConfiguration""").fetchall()

        df_latitude = sensor_default_config[0][0]
        df_longitude = sensor_default_config[0][1]
        df_status = sensor_default_config[0][2]
        df_power_filtration = sensor_default_config[0][3]
        df_cloud_ip_address = sensor_default_config[0][4]
        df_influxdb_org = sensor_default_config[0][5]
        df_influxdb_bucket = sensor_default_config[0][6]
        df_auth_token = sensor_default_config[0][7]
        df_upload_periodicity = sensor_default_config[0][8]
        df_sliding_window = sensor_default_config[0][9]
        df_reboot_periodicity = sensor_default_config[0][10]
        df_reboot_time = sensor_default_config[0][11]

        print(f"Latitude:              {df_latitude}")
        print(f"Longitude:             {df_longitude}")
        print(f"Status:                {df_status}")
        print(f"Power Filtration:      {df_power_filtration} dB")
        print(f"Cloud IP Address:      {df_cloud_ip_address}")
        print(f"InfluxDB Organization: {df_influxdb_org}")
        print(f"InfluxDB Bucket:       {df_influxdb_bucket}")
        print(f"InfluxDB Auth Token:   {df_auth_token}")
        print(f"Upload Periodicity:    Every {df_upload_periodicity} minutes")
        print(f"Sliding Window:        Last {df_sliding_window} minutes")
        print(f"Reboot Periodicity:    {df_reboot_periodicity}")
        print(f"Reboot Time:           {df_reboot_time}")


    print("")

    # Confirm changes
    if configuration_mode == "1":
        confirm("Do you want to save this new current sensor configuration with default values (Y/n)?")
    elif configuration_mode == "2":
        confirm("Do you want to save this new current sensor configuration (Y/n)?")

    # Save new configuration into database
    try:
        if configuration_mode == "1":
            cwifi.execute("""INSERT INTO SensorConfiguration (Sensor_UUID, Sensor_Name, Latitude, Longitude, Status, Power_Filtration, Cloud_IP_Address, InfluxDB_Org, InfluxDB_Bucket, Authorization_Token, Upload_Periodicity, Sliding_Window, Upload_Technology, Reboot_Periodicity, Reboot_Time, Last_Update) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",  (uuid_from_mac_addr, sensorName, df_latitude, df_longitude, df_status, df_power_filtration, df_cloud_ip_address, df_influxdb_org, df_influxdb_bucket, df_auth_token, df_upload_periodicity, df_sliding_window, uploadTechnology, df_reboot_periodicity, df_reboot_time))
        elif configuration_mode == "2":
            cwifi.execute("""INSERT INTO SensorConfiguration (Sensor_UUID, Sensor_Name, Latitude, Longitude, Status, Power_Filtration, Cloud_IP_Address, InfluxDB_Org, InfluxDB_Bucket, Authorization_Token, Upload_Periodicity, Sliding_Window, Upload_Technology, Reboot_Periodicity, Reboot_Time, Last_Update) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""", (uuid_from_mac_addr, sensorName, latitude, longitude, status, power_filtration, cloudServerIPAddress, influxDB_Org_Name, influxDB_Bucket, authorization_Token, uploadPeriodicity, slidingWindow, technology, rebootPeriodicity, rebootTime))

        connwifi.commit()
        cwifi.close()

    except sqlite3.Error as error:
        print("Failed to save configuration into local database.", error)

    finally:
        if connwifi:
            connwifi.close()
            print("Configuration saved in local database.")


    #Start monitor mode on detection interface
    if detectionInterface[-3:].lower() == "mon":
        start_monitor_interface = detectionInterface[:-3]
    else:
        start_monitor_interface = detectionInterface

    cmd = f"sudo airmon-ng start {start_monitor_interface}"
    subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8")
    print(f"Started monitor mode on interface '{start_monitor_interface}'.")


    # Write tasks crontab configuration file and compile airodump-ng with power filtration value
    if configuration_mode == "1":
        write_crontab_file(df_status, detectionInterface, df_upload_periodicity, df_reboot_periodicity, df_reboot_time)
        change_power_filtration(df_power_filtration)

    elif configuration_mode == "2":
        write_crontab_file(status, detectionInterface, uploadPeriodicity, rebootPeriodicity, rebootTime)
        change_power_filtration(power_filtration)


    if (configuration_mode == "1" and uploadTechnology == 'wifi') or (configuration_mode == "2" and technology == 'wifi'):
        #Send sensor location measurement to InfluxDB
        subprocess.run(["python", SENSOR_SEND_LOCATION_FILEPATH])
        print("Sensor location updated.")



    sys.exit(0)


# If sensor has previous configuration, give possibility to update it 
elif sensor_configuration_row > 0:

    print("There is an existing sensor configuration: \n")

    current_sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration """).fetchall()

    #Current sensor name and location, to be checked further if changed
    old_name = current_sensor_configuration[0][1]
    old_latitude = current_sensor_configuration[0][2]
    old_longitude = current_sensor_configuration[0][3]
    old_power_filtration = current_sensor_configuration[0][5]

    print("Sensor UUID:           " + str(current_sensor_configuration[0][0]))
    print("Sensor Name:           " + str(current_sensor_configuration[0][1]))
    print("Sensor Location:       " + str(current_sensor_configuration[0][2]) + ", " + str(current_sensor_configuration[0][3]))
    print("Status:                " + str(current_sensor_configuration[0][4]))
    print("Power Filtration:      " + str(current_sensor_configuration[0][5]) + " dB")
    print("Cloud IP Address:      " + str(current_sensor_configuration[0][6]))
    print("InfluxDB Organization: " + str(current_sensor_configuration[0][7]))
    print("InfluxDB Bucket:       " + str(current_sensor_configuration[0][8]))
    print("Authorization Token:   " + str(current_sensor_configuration[0][9]))
    print("Upload Periodicity:    Every " + str(current_sensor_configuration[0][10]) + " minutes")
    print("Sliding Window:        Last " + str(current_sensor_configuration[0][11]) + " minutes")
    print("Upload Technology:     " + str(current_sensor_configuration[0][12]))
    print("Reboot Periodicity:    " + str(current_sensor_configuration[0][13]))
    print("Reboot Time:           " + str(datetime.strptime(str(current_sensor_configuration[0][14]), "%H").strftime("%I:%M %p")))
    print("Last Update:           " + str(current_sensor_configuration[0][15]))
    print("")


    #Confirm procedure
    confirm("Do you want to update this sensor configuration (Y/n)?")
    print("------------------------------------------------------------------")


    #Check configuration mode 
    configuration_mode = check_config_mode()

    if configuration_mode == "1":

        #########################################################################################
        ###############################  FAST CONFIGURATION MODE  ###############################
        #########################################################################################

        #Check sensor UUID and sensor name
        uuid_from_mac_addr, sensorName, uploadTechnology, uploadInterface, detectionInterface = fast_config()



    elif configuration_mode == "2":

        #########################################################################################
        ############################ (1) SENSOR GENERAL CONFIGURATION ###########################
        #########################################################################################

        uuid_from_mac_addr, sensorName, latitude, longitude, status, power_filtration = config_general()

        #########################################################################################
        ############################# (2) SENSOR UPLOAD CONFIGURATION ###########################
        #########################################################################################

        cloudServerIPAddress, influxDB_Org_Name, influxDB_Bucket, authorization_Token = config_influx()

        #########################################################################################
        ############################# (3) SENSOR TASKS CONFIGURATION ############################
        #########################################################################################

        uploadInterface, detectionInterface, uploadPeriodicity, slidingWindow, technology, rebootPeriodicity, rebootTime = config_tasks()

    print("")

    # Confirm changes
    confirmation = ''
    while confirmation not in ("Y", "y", "yes", "Yes", "YES", "n","N","No", "no", "NO"):

        confirmation = input("Do you want to update the current sensor configuration (Y/n)?").strip()

        if confirmation == "Yes" or confirmation == "Y" or confirmation == "yes" or confirmation == "y" or confirmation == "YES":


            # Old, New and Changed empty config lists
            old_values = [None for _ in range(SENSOR_CONFIG_PARAMETERS_NUMB)]
            new_values = [None for _ in range(SENSOR_CONFIG_PARAMETERS_NUMB)]
            changed_values = [None for _ in range(SENSOR_CONFIG_PARAMETERS_NUMB)]

            #Get old default values from payload
            old_values = list(current_sensor_configuration[0])
            old_values.pop()

            #Get new configuration values from script configuration
            new_values[0] = uuid_from_mac_addr
            new_values[1] = sensorName

            #Get new configuration values from script configuration
            if configuration_mode == "2":
                new_values[0] = uuid_from_mac_addr
                new_values[1] = sensorName
                new_values[2] = latitude
                new_values[3] = longitude
                new_values[4] = status
                new_values[5] = power_filtration
                new_values[6] = cloudServerIPAddress
                new_values[7] = influxDB_Org_Name
                new_values[8] = influxDB_Bucket
                new_values[9] = authorization_Token
                new_values[10] = uploadPeriodicity
                new_values[11] = slidingWindow
                new_values[12] = technology
                new_values[13] = rebootPeriodicity
                new_values[14] = rebootTime

            print(f"OLD VALUES: {old_values}")
            print(f"NEW VALUES: {new_values}")
            print("")

            #Compare values that changed
            if configuration_mode == "1":
                for i in range(2):
                    if str(old_values[i]) != str(new_values[i]):
                        changed_values[i] = new_values[i]

            elif configuration_mode == "2":
                for i in range(SENSOR_CONFIG_PARAMETERS_NUMB):
                    if str(old_values[i]) != str(new_values[i]):
                        changed_values[i] = new_values[i]

            print(f"CHANGED VALUES: {changed_values}")


            #Payload construction
            payload = str(uuid_from_mac_addr)

            for value in changed_values:
                if value != None:
                    payload += str(value)

                payload += ','

            payload = payload[:-1]

            print(payload)


            # Save new configuration into database
            try:
                if configuration_mode == "1":
                    cwifi.execute("""UPDATE SensorConfiguration SET Sensor_UUID=?, Sensor_Name=?, Last_Update=CURRENT_TIMESTAMP""", (uuid_from_mac_addr, sensorName,))
                elif configuration_mode == "2":
                    cwifi.execute("""DELETE FROM SensorConfiguration WHERE 1=1 """)
                    cwifi.execute("""INSERT INTO SensorConfiguration (Sensor_UUID, Sensor_Name, Latitude, Longitude, Status, Power_Filtration, Cloud_IP_Address, InfluxDB_Org, InfluxDB_Bucket, Authorization_Token, Upload_Periodicity, Sliding_Window, Upload_Technology, Reboot_Periodicity, Reboot_Time, Last_Update) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""", (uuid_from_mac_addr, sensorName, latitude, longitude, status, power_filtration, cloudServerIPAddress, influxDB_Org_Name, influxDB_Bucket, authorization_Token, uploadPeriodicity, slidingWindow, technology, rebootPeriodicity, rebootTime))

                connwifi.commit()
                cwifi.close()

            except sqlite3.Error as error:
                print("Failed to update configuration in local database.", error)

            finally:
                if connwifi:
                    connwifi.close()
                    print("Configuration sucessfully updated.")


            # Write tasks crontab configuration file
            if configuration_mode == "1":

                #Grab remaining configuration parameters from database
                try:

                    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
                    cwifi = connwifi.cursor()

                    configuration_row = cwifi.execute("""SELECT Status,Upload_Periodicity,Sliding_Window,Upload_Technology,Reboot_Periodicity,Reboot_Time FROM SensorConfiguration """).fetchall()

                    status_sensor = configuration_row[0][0]
                    upload_periodicity = configuration_row[0][1]
                    sliding_window = configuration_row[0][2]
                    upload_technology = configuration_row[0][3]
                    reboot_periodicity = configuration_row[0][4]
                    reboot_time = configuration_row[0][5]

                except sqlite3.Error as error:
                    print("Error while connecting to database.", error)

                write_crontab_file(status_sensor, detectionInterface, upload_periodicity, reboot_periodicity, reboot_time)

            elif configuration_mode == "2":
                write_crontab_file(status, detectionInterface, uploadPeriodicity, rebootPeriodicity, rebootTime)


            #When in 'Fast' configuration mode, chek if name changed
            #If location and name changed, send sensor location measurement to InfluxDB for instant update on dashboard
            if old_name != sensorName:
                subprocess.run(["python", SENSOR_SEND_CROWDING_DATA_FILEPATH])
                print("Sensor location and name updated.")


            #When in 'Complete' configuration mode, check if sensor location and name changed
            if configuration_mode == "2":

                #If packet power filtration changed, recompile airodump-ng with new value
                if old_power_filtration != power_filtration:
                    change_power_filtration(power_filtration)
                    print("Sensor power filtration updated.")


                if technology == "wifi":
                    #If location changed, send sensor location measurement to InfluxDB
                    if (old_latitude != latitude or old_longitude != longitude):
                        subprocess.run(["python", SENSOR_SEND_LOCATION_FILEPATH])
                        print("Sensor location updated.")

                    #If location and name changed, send sensor location measurement to InfluxDB for instant update on dashboard
                    if (old_latitude != latitude or old_longitude != longitude) and old_name != sensorName:
                        subprocess.run(["python", SENSOR_SEND_CROWDING_DATA_FILEPATH])
                        print("Sensor location and name updated.")


            break


        if confirmation == "n" or confirmation == "N" or confirmation == "no" or confirmation == "No" or confirmation == "NO":
            print("Configuration not updated. Exit program.")
            print("------------------------------------------------------------------\n")
            exit(0)
        else:
            print("Please enter yes/no.")

