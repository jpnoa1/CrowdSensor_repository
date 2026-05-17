import subprocess
import os
import sqlite3
import logging

LOG_FILE = "/home/kali/Desktop/Sniffer/startup.log"
SNIFFER_LOG_FILE = "/home/kali/Desktop/Sniffer/sniffer_error.log"
PID_FILE = "/home/kali/Desktop/Sniffer/sniffer.pid"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logging.info("=== Starting sensorStartup.py ===")

try:
    res = subprocess.run(["sudo", "iwconfig", "wlan1", "channel", "1"], capture_output=True, text=True)
    if res.returncode != 0:
        logging.error(f"Wi-Fi setup failed: {res.stderr}")
    else:
        logging.info("Successfully set wlan1 to channel 1.")
except Exception as e:
    logging.error(f"Exception during Wi-Fi setup: {e}")

try:
    dr_con = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
    dr_cur = dr_con.cursor()

    dr_cur.execute("CREATE TABLE IF NOT EXISTS Probe_Requests (Fingerprint TEXT, MAC TEXT, Timestamp REAL);")
    dr_con.commit()
    dr_con.close()
    logging.info("Database table checked/created successfully.")
except Exception as e:
    logging.error(f"Database creation failed: {e}")

try:
    res = subprocess.run(["sudo", "chown", "-R", "kali:kali", "/home/kali/Desktop/MemoryDB"], capture_output=True, text=True)
    if res.returncode != 0:
        logging.error(f"Chown failed: {res.stderr}")
    else:
        logging.info("Permissions updated for MemoryDB.")
except Exception as e:
    logging.error(f"Exception during chown: {e}")

try:
    res = subprocess.run(["sudo", "chmod", "-R", "777", "/home/kali/Desktop/MemoryDB"], capture_output=True, text=True)
    if res.returncode != 0:
        logging.error(f"Chown failed: {res.stderr}")
    else:
        logging.info("Read/write access updated for MemoryDB.")
except Exception as e:
    logging.error(f"Exception during chown: {e}")

try:
    logging.info("Starting crowdingSniffer.py...")

    sniffer_log = open(SNIFFER_LOG_FILE, "w")

    snifferProcess = subprocess.Popen(
        ["sudo", "/usr/bin/python3", "/home/kali/Desktop/Sniffer/crowdingSniffer.py"],
        stdout=sniffer_log,
        stderr=sniffer_log
    )

    with open(PID_FILE, "w") as f:
       f.write(str(snifferProcess.pid))
       
    logging.info(f"Sniffer started successfully with PID: {snifferProcess.pid}")

except Exception as e:
    logging.error(f"Failed to start sniffer process: {e}")