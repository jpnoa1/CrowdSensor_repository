import sqlite3
import sys
import time

try:
    retention_minutes = int(sys.argv[1])
except IndexError:
    retention_minutes = 30 

cutoff_time = time.time() - (retention_minutes * 60)

try:
    dr_con = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
    dr_cur = dr_con.cursor()
    
    dr_cur.execute("DELETE FROM Probe_Requests WHERE Timestamp < ?", (cutoff_time,))
    
    dr_con.commit()

except sqlite3.Error as e:
    print(f"Database error during data retention cleanup: {e}")

finally:
    if 'dr_con' in locals() and dr_con:
        dr_con.close()