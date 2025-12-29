import subprocess
import os
import sqlite3

PID_FILE = "/home/kali/Desktop/sniffer.pid"

os.system("sudo iwconfig wlan1 channel 1")
os.system("sudo chown kali:kali /home/kali/Desktop/MemoryDB")

dr_con = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
dr_cur = dr_con.cursor()

dr_cur.execute("CREATE TABLE Data_Packets (Frame_Type TEXT, ID TEXT, First_Record DATETIME, Last_Time_Found DATETIME, Manufacturer TEXT);")
dr_cur.execute("CREATE TABLE Probe_Requests (Frame_Type TEXT, ID TEXT, First_Record DATETIME, Last_Time_Found DATETIME, Manufacturer TEXT);")
dr_con.commit()

# snifferProcess = subprocess.Popen(
#     ["sudo", "/usr/bin/python3", "/home/kali/Desktop/crowdingSniffer.py"]#,
#     #stdout=subprocess.PIPE,
#     #stderr=subprocess.PIPE,
#     #text=True
# )

#with open(PID_FILE, "w") as f:
#    f.write(str(snifferProcess.pid))

#hopperProcess = subprocess.Popen(
#    ["sudo", "/usr/bin/python3", "/home/kali/Desktop/channelHopper.py"]
#)