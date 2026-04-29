import sqlite3
import os
from uuid import getnode
from sensorFunctions import *
from datetime import datetime

DB_PATH = "/home/kali/Desktop/DB/SensorConfiguration.db"

try:
    import tomllib
except ImportError:
    import toml as tomllib



def load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)

def apply_config_from_toml(toml_path: str):
    if not os.path.isfile(toml_path):
        raise FileNotFoundError(f"Ficheiro {toml_path} não encontrado")

    cfg = load_toml(toml_path)
    connectivity = cfg.get("Connectivity", [])
    sensor = cfg.get("sensor", {})

    # Get cloud address from first Wi-Fi connectivity entry (if any)
    wifi_cloud_address = None
    for c in connectivity:
        if c.get("type") == "wifi":
            wifi_cloud_address = c.get("cloud_address") or c.get("mqtt_address")
            if wifi_cloud_address:
                break



    conn = sqlite3.connect(DB_PATH, timeout=30)
    cursor = conn.cursor()


    rebootPeriodicity = sensor.get("Reboot Periodicity")

    if   rebootPeriodicity == "0": rebootPeriodicity = 'daily'
    elif rebootPeriodicity == "1": rebootPeriodicity = 'everytwodays'
    elif rebootPeriodicity == "2": rebootPeriodicity = 'everythreedays'
    elif rebootPeriodicity == "3": rebootPeriodicity = 'weekly'
    elif rebootPeriodicity == "4": rebootPeriodicity = 'monthly'
    elif rebootPeriodicity == "5": rebootPeriodicity = 'noreboot'

    reboot_time = sensor.get("Reboot Time")
    if reboot_time == "" or reboot_time is None:
        reboot_time = 0
    
    

    cursor.execute("SELECT COUNT(*) FROM SensorConfiguration")
    exists = cursor.fetchone()[0] > 0

    if not exists:
        cursor.execute("""
            INSERT INTO SensorConfiguration (
                Sensor_UUID, Sensor_Name, Latitude, Longitude, Status, Power_Filtration,
                Cloud_IP_Address, InfluxDB_Org, InfluxDB_Bucket, Authorization_Token,
                Upload_Periodicity, Sliding_Window, Reboot_Periodicity, Reboot_Time, Last_Update
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (
            getnode(), sensor.get("Sensor Name"), sensor.get("Latitude"), sensor.get("Longitude"),
            sensor.get("Status", "enabled"), sensor.get("Power Filtration"),
            wifi_cloud_address, sensor.get("InfluxDB Organization"),
            sensor.get("InfluxDB Bucket"), sensor.get("InfluxDB Auth Token"),
            sensor.get("Upload Periodicity"), sensor.get("Sliding Window"),
            rebootPeriodicity, reboot_time
        ))
        conn.commit()

    else:
        # Update SensorConfiguration
        print("[Config] Updating SensorConfiguration...")
        cursor.execute("""
            UPDATE SensorConfiguration
            SET Sensor_UUID=?, Sensor_Name=?, Latitude=?, Longitude=?, Status=?, Power_Filtration=?,
                Cloud_IP_Address=?, InfluxDB_Org=?, InfluxDB_Bucket=?, Authorization_Token=?,
                Upload_Periodicity=?, Sliding_Window=?, Reboot_Periodicity=?, Reboot_Time=?,
                Last_Update=CURRENT_TIMESTAMP
        """, (
            getnode(), sensor.get("Sensor Name"), sensor.get("Latitude"), sensor.get("Longitude"),
            sensor.get("Status", "enabled"), sensor.get("Power Filtration"),
            wifi_cloud_address, sensor.get("InfluxDB Organization"),
            sensor.get("InfluxDB Bucket"), sensor.get("InfluxDB Auth Token"),
            sensor.get("Upload Periodicity"), sensor.get("Sliding Window"),
            rebootPeriodicity, reboot_time
        ))
        conn.commit()

    # Update SensorCommunication
    wifi_available = any(c.get("type") == "wifi" for c in connectivity)
    lora_available = any("lora" in c.get("type", "") for c in connectivity)


    cursor.execute("SELECT COUNT(*) FROM SensorCommunication")
    comm_exists = cursor.fetchone()[0] > 0
   
    if not comm_exists:
        cursor.execute("""
            INSERT INTO SensorCommunication
            (WifiAvailable, WifiConnected, LoRaAvailable, LoRaConnected, Last_Update)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (int(wifi_available), int(wifi_available), int(lora_available), 0))
    else:
        cursor.execute("""
            UPDATE SensorCommunication
            SET WifiAvailable=?, WifiConnected=?, LoRaAvailable=?, LoRaConnected=?, Last_Update=CURRENT_TIMESTAMP
        """, (int(wifi_available), int(wifi_available), int(lora_available), 0))

    conn.commit()

    # Update LoRaNetworks 
    print("[Config] Updating LoRaNetworks...")
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS LoRaNetworks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        app_eui TEXT,
        app_key TEXT,
        dev_eui TEXT,
        available INTEGER DEFAULT 0,
        connected INTEGER DEFAULT 0,
        priority INTEGER DEFAULT 0,
        last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    
    cursor.execute("DELETE FROM LoRaNetworks")
    
    lora_networks = [c for c in connectivity if "lora" in c.get("type", "")]
    for i, net in enumerate(lora_networks, start=1):
        name = net.get("name", net.get("type"))
        cursor.execute("""
            INSERT INTO LoRaNetworks
            (name, app_eui, app_key, dev_eui, available, connected, priority, last_update)
            VALUES (?, ?, ?, ?, 1, 0, ?, CURRENT_TIMESTAMP)
        """, (
            name,
            net.get("app_eui"), net.get("app_key"), net.get("dev_eui"),
            i
        ))
        print(f"   - {name}  (priority={i})")

    conn.commit()

    # Configure cronjobs and power filtration
    power_fil = sensor.get("Power Filtration")
    if power_fil:
        change_power_filtration(int(power_fil))  # to be remove after scapy update


    # Cronjobs
    status = sensor.get("Status", "enabled")
    reboot_per = sensor.get("Reboot Periodicity")
    reboot_time = sensor.get("Reboot Time", "")
    upload_period = sensor.get("Upload Periodicity")
    _ , detectionInterface = check_upload_detection_interfaces(False)
    write_crontab_file(status, detectionInterface , upload_period, reboot_per, reboot_time)

    if int(wifi_available) == 1 or int(lora_available) == 1:
        
        print("[Upload] Sending Sensor Location...")
        try:
            
            subprocess.run(["pkill", "-f", "sendCrowdingData.py"])
            #running sensor communication check script
            subprocess.run(["python3", SENSOR_COMMUNICATION_AVAILABLE_FILEPATH])
            time.sleep(3)
            
            # send location only when join marker exists
            if os.path.exists("/tmp/rak_njs"):
                subprocess.run(["python3", SENSOR_SEND_LOCATION_FILEPATH])
            else:
                print("[Upload] /tmp/rak_njs not found, skipping sensor location upload.")
            
        except Exception as e:
            print(f"[ERROR] Failed to send Location Data {e}")

    conn.close()

    print(f"[OK] Successful Local configuration ({datetime.now().strftime('%H:%M:%S')})")
    
    # Publish sensor state to MQTT broker
    publish_sensor_state(
    cfg=cfg,
    mqtt_host="t.monicrowd.sensinglab.eu",
    mqtt_port=1883 
)
    # Publish sensor networks to MQTT broker
    publish_sensor_networks(
    cfg=cfg,
    mqtt_host="t.monicrowd.sensinglab.eu",
    mqtt_port=1883
)



if __name__ == "__main__":
    default_path = "/home/kali/Desktop/sensor-config-site/data/sensor_config.toml"
    apply_config_from_toml(default_path)
    
    #subprocess.run(["python3", SENSOR_COMMUNICATION_AVAILABLE_FILEPATH])
    

