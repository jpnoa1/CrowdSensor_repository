import sqlite3
import datetime as dt
import pytz
import sys


if(len(sys.argv) < 2 ) :
    print("Argument not provided. Please specify an argument with the 'data retention period' time in minutes.")
    exit(0)

if(len(sys.argv) > 2 ) :
    print("Too many arguments required. Please specift only one argument with the 'data retention period' time, in minutes.")
    exit(0)

if not sys.argv[1].isdigit():
    print("The sliding window is not an integer. Please set the 'data retention period' time, in minutes, as an integer.")
    exit(0)
else:

    dataAtual = dt.datetime.now(pytz.utc).replace(tzinfo=None)
    dataAnalizar= dataAtual - dt.timedelta(minutes=int(sys.argv[1]))
    
    #Delele old and unnecessary data from the local database

    connwifi = sqlite3.connect('/home/kali/Desktop/DB/DeviceRecords.db', timeout=30)
    cwifi = connwifi.cursor()
    cwifi.execute("""DELETE FROM Data_Packets WHERE NOT ((First_Record >= ? and First_Record <= ?) or (Last_Time_Found > ? and Last_Time_Found <= ?))""", (dataAnalizar, dataAtual, dataAnalizar, dataAtual))
    connwifi.commit()
    cwifi.execute("""DELETE FROM Probe_Requests WHERE NOT ((First_Record >= ? and First_Record <= ?) or (Last_Time_Found > ? and Last_Time_Found <= ?))""", (dataAnalizar, dataAtual, dataAnalizar, dataAtual))
    connwifi.commit()

    cwifi.close()
    

