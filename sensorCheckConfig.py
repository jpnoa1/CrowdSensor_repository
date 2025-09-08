import sqlite3
from datetime import datetime
import pytz
from termcolor import colored



def convert_to_timezone(utc_str, utc_format, timezone):
    local_tz = pytz.timezone('Europe/Lisbon')
    utc_dt = datetime.strptime(utc_str, utc_format)
    local_dt = utc_dt.replace(tzinfo=pytz.utc).astimezone(local_tz)

    return local_dt



try:

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

except sqlite3.Error as error:
    print("Error while connecting to database.", error)




def show_sensor_configuration():
    print("----------------------------------------------------------------")
    print("-----                 SENSOR CONFIGURATION                 -----")
    print("----------------------------------------------------------------")

    general_information_rows = cwifi.execute("""SELECT COUNT(*) FROM SensorConfiguration""").fetchall()[0][0]

    # Check if sensor has previous configuration

    if general_information_rows == 1:

        current_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

        print("Sensor UUID:            " + str(current_configuration[0][0]))
        print("Sensor Name:            " + str(current_configuration[0][1]))
        print("Sensor Location:        " + str(current_configuration[0][2]) + ", " + str(current_configuration[0][3]))
        if str(current_configuration[0][4]) == "Active":
            print("Status:                 " + colored("Active", 'green'))
        elif str(current_configuration[0][4]) == "Disabled":
            print("Status:                 " + colored("Disabled", 'red'))
        print("Power Filtration:       " + str(current_configuration[0][5]) + " dB")
        print("Cloud IP Address:       " + str(current_configuration[0][6]))
        print("InfluxDB Organization:  " + str(current_configuration[0][7]))
        print("InfluxDB Bucket:        " + str(current_configuration[0][8]))
        print("Authorization token:    " + str(current_configuration[0][9]))
        print("Messages periodicity:   Every " + str(current_configuration[0][10]) + " minutes")
        print("Sliding Window:         Last " + str(current_configuration[0][11]) + " minutes")
        if current_configuration[0][12] != 'none':
            print("Upload Technology:      " + str(current_configuration[0][12]))
        else:
            print("Upload Technology:      " + colored("Not Available", 'yellow'))
        print("Reboot Periodicity:     " + str(current_configuration[0][13]))
        print("Reboot Time:            " + str(datetime.strptime(str(current_configuration[0][14]), "%H").strftime("%I:%M %p")))
        print("Last Update:            " + str(convert_to_timezone(current_configuration[0][15], '%Y-%m-%d %H:%M:%S', 'Europe/Lisbon'))[:-6])

    elif general_information_rows == 0:

        print("This sensor does not have a current configuration. \nRun the 'sensorConfiguration.py' script to configure the sensor.")
        

    print("----------------------------------------------------------------\n")
    
def show_default_configuration():
    print("----------------------------------------------------------------")
    print("-----             SENSOR DEFAULT CONFIGURATION             -----")
    print("----------------------------------------------------------------")
    print("-- These are only default values, not actual configurations.  --")
    print("----------------------------------------------------------------")

    general_information_rows = cwifi.execute("""SELECT COUNT(*) FROM SensorDefaultConfiguration""").fetchall()[0][0]

    if general_information_rows == 1:

        current_configuration = cwifi.execute("""SELECT * FROM SensorDefaultConfiguration""").fetchall()

        print("Sensor Location:        " + str(current_configuration[0][0]) + ", " + str(current_configuration[0][1]))
        print("Status:                 " + str(current_configuration[0][2]))
        print("Power Filtration:       " + str(current_configuration[0][3]) + " dB")
        print("Cloud IP Address:       " + str(current_configuration[0][4]))
        print("InfluxDB Organization:  " + str(current_configuration[0][5]))
        print("InfluxDB Bucket:        " + str(current_configuration[0][6]))
        print("Authorization token:    " + str(current_configuration[0][7]))
        print("Messages periodicity:   Every " + str(current_configuration[0][8]) + " minutes")
        print("Sliding Window:         Last " + str(current_configuration[0][9]) + " minutes")
        print("Reboot Periodicity:     " + str(current_configuration[0][10]))
        print("Reboot Time:            " + str(datetime.strptime(str(current_configuration[0][11]), "%H").strftime("%I:%M %p")))
        print("Last Update:            " + str(convert_to_timezone(current_configuration[0][12], '%Y-%m-%d %H:%M:%S', 'Europe/Lisbon'))[:-6])

    elif general_information_rows == 0:

        print("This sensor does not have a current default configuration. ")
        

    print("----------------------------------------------------------------\n")
    
def show_communications():
    print("----------------------------------------------------------------")
    print("-----               SENSOR COMMUNICATIONS                  -----")
    print("----------------------------------------------------------------")

    sensor_communication_rows = cwifi.execute("""SELECT COUNT(*) FROM SensorCommunication""").fetchall()[0][0]

    if sensor_communication_rows == 1:

        current_communication = cwifi.execute("""SELECT * FROM SensorCommunication""").fetchall()

        if current_communication[0][0] == 1: 
            print("Upload via Wi-Fi:       " + colored("Available", 'green'))
        elif current_communication[0][0] == 0:
            print("Upload via Wi-Fi:       " + colored("Not Available", 'yellow'))
        else:
            print("Upload via Wi-Fi:    <Incorrect value>")

        if current_communication[0][1] == 1: 
            print("Upload via LoRa:        " + colored("Available", 'green'))
        elif current_communication[0][1] == 0:
            print("Upload via LoRa:        " + colored("Not Available", 'yellow'))
        else:
            print("Upload via Wi-Fi:    <Incorrect value>")

        if current_communication[0][2] == 1: 
            print("Upload via Wi-Fi:       " + colored("Connected", 'green'))
        elif current_communication[0][2] == 0:
            print("Upload via Wi-Fi:       " + colored("Not Connected", 'yellow'))
        else:
            print("Upload via Wi-Fi:       <Incorrect value>")

        if current_communication[0][3] == 1: 
            print("Upload via LoRa:        " + colored("Connected", 'green'))
        elif current_communication[0][3] == 0:
            print("Upload via LoRa:        " + colored("Not Connected", 'yellow'))
        else:
            print("Upload via Wi-Fi:       <Incorrect value>")

        print("IP Address:             " + str(current_communication[0][4]))
        print("Upload Interface:       " + str(current_communication[0][5]))
        print("Detection Interface:    " + str(current_communication[0][6]))
        print("Last Update:            " + str(convert_to_timezone(current_communication[0][7], '%Y-%m-%d %H:%M:%S', 'Europe/Lisbon'))[:-6])


    elif sensor_communication_rows == 0:

        print("This sensor does not have checked the communications available. ")
        

    print("----------------------------------------------------------------\n")



show_sensor_configuration()
show_default_configuration()
show_communications()



cwifi.close()
connwifi.close()