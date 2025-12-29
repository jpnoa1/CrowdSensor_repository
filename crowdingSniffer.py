from _t1ha0_module import ffi, lib
from scapy.all import *
import sqlite3
import signal
import sys

from sensorFunctions import publish_mqtt_message

PID_FILE = "/home/kali/Desktop/sniffer.pid"
CALIBRATION_MAC = "14:49:D4:88:A4:2F"

dr_con = sqlite3.connect('/home/kali/Desktop/MemoryDB/DeviceRecords.db', timeout=30)
dr_cur = dr_con.cursor()

dr_con.execute("PRAGMA journal_mode = WAL")
dr_con.execute("PRAGMA cache_size = -64000")  # 64MB cache

sc_con = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
sc_cur = sc_con.cursor()

PACKET_POWER_FILTRATION = sc_cur.execute("Select Power_Filtration from SensorConfiguration;").fetchone()[0]
sc_con.close()

OUI_DICT = {}
with open("/home/kali/Desktop/wireshark-oui-list.txt", 'r') as file:
    for line in file:
        splits = line.split('\t')
        OUI_DICT[splits[0].strip()] = splits[1].strip()

def frame_processing(frame):

    mac = frame[Dot11].addr2.upper()                # (frame[Dot11].addr2" -> Transmitter/Source Address)
    oui = mac[:8]

    if mac == CALIBRATION_MAC:                      
        try:
            rssi = frame[RadioTap].dBm_AntSignal    
        except Exception:
            return                                  

        ts = int(time.time() * 1000)                
        payload = f"{ts},{rssi}"                    
        topic = "calibration/rssi"                  

        publish_mqtt_message(payload, topic)        
        print(f"[CALIBRATION] {mac} | {rssi} dBm")  
        return                                      

    result, manuf = isMobileManufacturer(oui)

    if(result):

        if(frame[Dot11].type == 0):                 # MANAGEMENT FRAMES

            if(int(mac[1],16) & 0x2 == 0):          # Check if 3rd bit from 2nd byte is '0' to verify if MAC isn't randomized
                
                footprint_mac = hex(lib.t1ha0(mac.encode('ASCII'), len(mac), 11))[2:].upper()
                putInToProbeRequestsDB(footprint_mac, manuf)
                print("Probe Request | Source: " + mac + " | Footprint: " + footprint_mac + " | SEQ: " + str(frame[Dot11].SC >> 4) + " | Power: " + str(frame[RadioTap].dBm_AntSignal) + " dBm | Manuf: " + manuf )
                return

            else:

                ie = frame.getlayer(Dot11Elt)
                array_v = []

                while ie:
                    if(ie.ID == 1):                 # Supported Rates
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 50):              # Extended Supported Rates
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 3):               # DS Parameter Set
                        array_v.append(ie.ID)
                        #array_v.append(ie.len)
                    
                    elif(ie.ID == 45):              # HT Capabilities
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for i, c in enumerate(ie.info):
                            if(i != 4):
                                array_v.append(c)
                            else:
                                array_v.append(ord('0'))
                    
                    elif(ie.ID == 127):             # Extended Capabilities
                        array_v.append(ie.ID)
                        #array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 191):             # VHT Capabilities
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 70):              # RM Enabled Capabilities 
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 107):             # Interworking
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 59):              # Supported Operating Classes
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for c in ie.info:
                            array_v.append(c)
                    
                    elif(ie.ID == 221):             # Vendor Specific
                        array_v.append(ie.ID)
                        array_v.append(ie.len)
                        for i, c in enumerate(ie.info):
                            if(i != 5 and i != 7):
                                array_v.append(c)
                            else:
                                array_v.append(ord('0'))
                    
                    ie = ie.payload
                
                footprint_mac = hex(lib.t1ha0(bytes(array_v), len(array_v), 3))[2:].upper()
                putInToProbeRequestsDB(footprint_mac, manuf)
                #print("Probe Request | Source: " + mac + " | Footprint: " + footprint_mac + " | SEQ: " + str(frame[Dot11].SC >> 4) + " | Power: " + str(frame[RadioTap].dBm_AntSignal) + " dBm | Manuf: " + manuf )
                return
        else:                                       # DATA FRAMES
            
            if("to-DS" in str(frame[Dot11].FCfield)):

                footprint_mac = hex(lib.t1ha0(mac.encode('ASCII'), len(mac), 11))[2:].upper()
                putInToDataPacketsDB(footprint_mac, manuf)
                #print("Data Packet   | Source: " + mac + " | Footprint: " + footprint_mac + " | SEQ: " + str(frame[Dot11].SC >> 4) + " | Power: " + str(frame[RadioTap].dBm_AntSignal) + " dBm | Manuf: " + manuf )
                return

def putInToProbeRequestsDB(id, manuf):
    res = dr_cur.execute("SELECT * FROM Probe_Requests WHERE ID='" + id + "';")
    if res.fetchone():
        dr_cur.execute("UPDATE Probe_Requests SET Last_Time_Found=current_timestamp WHERE ID='" + id + "';")
    else:
        dr_cur.execute("INSERT INTO Probe_Requests VALUES( 'Probe Request' , '" + id + "' , current_timestamp , current_timestamp , '" + manuf + "');")
    dr_con.commit()

def putInToDataPacketsDB(id, manuf):
    res = dr_cur.execute("SELECT * FROM Data_Packets WHERE ID='" + id + "';")
    if res.fetchone():
        dr_cur.execute("UPDATE Data_Packets SET Last_Time_Found=current_timestamp WHERE ID='" + id + "';")
    else:
        dr_cur.execute("INSERT INTO Data_Packets VALUES( 'Data Packet' , '" + id + "' , current_timestamp , current_timestamp , '" + manuf + "');")
    dr_con.commit()

def isMobileManufacturer(oui):
    manuf = "Unknown"
    result = OUI_DICT.get(oui)

    if result:
        manuf = result
        return True, manuf
    elif(int(oui[1],16) & 0x2 != 0):                # In case the manufacturer isn't found, check if the MAC is randomized
        return True, manuf
    return False, manuf

def signal_term_handler(signal, frame):
    open(PID_FILE, "w").close()
    dr_con.close()
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_term_handler)

#conf.layers.filter([RadioTap, Dot11, Dot11Elt])    # Enable filtering: only RadioTap, Dot11 and Dot11Elt will be dissected

filter_str = "(wlan type data) || (wlan type mgt subtype probe-req)"

if PACKET_POWER_FILTRATION != 0:
    filter_str += f" && radio [22] > {256 + PACKET_POWER_FILTRATION}"

sniff(
    count=0,
    filter=filter_str,
    prn=frame_processing,
    iface="wlan1",
    store=0,
    monitor=True)