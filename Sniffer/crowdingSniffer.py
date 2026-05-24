from scapy.all import Dot11Elt, Dot11, RadioTap, sniff
from t1ha0 import ffi, lib
import sqlite3
import signal
import sys
import time
sys.path.append('/home/kali/Desktop')
from sensorFunctions import publish_mqtt_message

PID_FILE = "/home/kali/Desktop/Sniffer/sniffer.pid"
CALIBRATION_MAC = "14:49:D4:88:A4:2F"

commit_counter = 0
last_commit_time = time.monotonic()

COMMIT_BATCH_SIZE = 50
COMMIT_MAX_INTERVAL = 15  # seconds

dr_con = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
dr_cur = dr_con.cursor()

dr_con.execute("PRAGMA journal_mode = WAL")
dr_con.execute("PRAGMA cache_size = -64000")  # 64MB cache

sc_con = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
sc_cur = sc_con.cursor()

PACKET_POWER_FILTRATION = sc_cur.execute("Select Power_Filtration from SensorConfiguration;").fetchone()[0]

#For sensor calibration info
sensor_info = sc_cur.execute("SELECT Sensor_UUID, Sensor_Name FROM SensorConfiguration;").fetchone()
SENSOR_UUID = sensor_info[0] if sensor_info else "UNKNOWN_UUID"
SENSOR_NAME = sensor_info[1] if sensor_info else "UNKNOWN_NAME"

sc_con.close()

OUI_DICT = {}
with open("/home/kali/Desktop/Sniffer/wireshark-oui-list.txt", 'r') as file:
    for line in file:
        splits = line.split('\t')
        OUI_DICT[splits[0].strip()] = splits[1].strip()

def frame_processing(pkt):

    mac = pkt[Dot11].addr2.upper()
    oui = mac[:8]
    global commit_counter, last_commit_time

    if mac == CALIBRATION_MAC:
        try:
            rssi = pkt[RadioTap].dBm_AntSignal
        except Exception:
            return


        ts_ms = int(time.time() * 1000)
        ts_ns = ts_ms * 1_000_000

        payload = (
            f"calibration,"
            f"sensor_uuid={SENSOR_UUID},sensor_name={SENSOR_NAME} "
            f"rssi={rssi} {ts_ns}"
        )

        topic = "calibration/rssi"

        publish_mqtt_message(payload, topic)
        print(f"[CALIBRATION] {mac} | {rssi} dBm")
        return

    if(isMobileManufacturer(oui)):

        ie = pkt.getlayer(Dot11Elt)
        array_v = []

        while ie:
            if ie.ID in [1, 50, 107, 191]:          # Supported Rates, Extended Supported Rates, Interworking, VHT Capabilities
                array_v.extend([ie.ID, ie.len])
                array_v.extend(ie.info)
            elif ie.ID == 45:                       # HT Capabilities
                array_v.extend([ie.ID, ie.len])
                for i, c in enumerate(ie.info):
                    # Mask Index 1: 2nd bit (0x40) -> Inverse: 0xBF
                    if i == 1:
                        array_v.append(c & 0xBF)
                    else:
                        array_v.append(c)
            elif ie.ID == 127:                      # Extended Capabilities
                array_v.extend([ie.ID, ie.len])
                for i, c in enumerate(ie.info):
                    # Mask Index 3: 6th bit (0x04) -> Inverse: 0xFB
                    if i == 3:
                        array_v.append(c & 0xFB)
                    else:
                        array_v.append(c)
            elif ie.ID == 221:                      # Vendor Specific
                array_v.extend([ie.ID, ie.len])
                is_microsoft = False
                is_epigram = False

                if len(ie.info) >= 3:
                    # Microsoft (00:50:F2)
                    if ie.info[0] == 0x00 and ie.info[1] == 0x50 and ie.info[2] == 0xF2:
                        is_microsoft = True
                    # Epigram (00:90:4C)
                    elif ie.info[0] == 0x00 and ie.info[1] == 0x90 and ie.info[2] == 0x4C:
                        is_epigram = True
                
                for i, c in enumerate(ie.info):
                    # Mask Index 5: 8th bit (0x01) -> Inverse: 0xFE
                    if is_microsoft and i == 5:
                        array_v.append(c & 0xFE)
                    # Mask Index 8: 2nd, 3rd bits (0x60) -> Inverse: 0x9F
                    elif is_epigram and i == 8:
                        array_v.append(c & 0x9F)
                    # Mask Index 9: 4th bit (0x10) -> Inverse: 0xEF
                    elif is_epigram and i == 9:
                        array_v.append(c & 0xEF)
                    else:
                        array_v.append(c)
                        
            ie = ie.payload

        fingerprint = hex(lib.t1ha0(bytes(array_v), len(array_v), 3))[2:]

        current_unix_time = time.time()

        dr_cur.execute("INSERT INTO Probe_Requests VALUES(?, ?, ?)", (fingerprint, pkt.addr2, current_unix_time))  

        # Batch commit por nÃºmero de registos ou por tempo
        commit_counter += 1
        now = time.monotonic()

        if (commit_counter >= COMMIT_BATCH_SIZE or (now - last_commit_time) >= COMMIT_MAX_INTERVAL):
            dr_con.commit()
            commit_counter = 0
            last_commit_time = now  

        return

def isMobileManufacturer(oui):
    result = OUI_DICT.get(oui)

    if result:
        return True
    elif(int(oui[1],16) & 0x2 != 0):    # In case the manufacturer isn't found, check if the MAC is randomized
        return True
    return False

def signal_term_handler(signal, frame):
    global commit_counter

    open(PID_FILE, "w").close()

    if commit_counter > 0:
        dr_con.commit()
        commit_counter = 0

    dr_con.close()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_term_handler)

filter_str = "(wlan type mgt subtype probe-req)"

if PACKET_POWER_FILTRATION != 0:
    filter_str += f" && radio [22] > {256 + PACKET_POWER_FILTRATION}"

sniff(
    count=0,
    filter=filter_str,
    prn=frame_processing,
    iface="wlan1",
    store=0,
    monitor=True)
    