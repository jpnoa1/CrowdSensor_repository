import sqlite3
import os
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

    conn = sqlite3.connect(DB_PATH, timeout=30)
    c = conn.cursor()

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

    # Update SensorConfiguration
    print("[Config] A atualizar SensorConfiguration...")
    c.execute("""
        UPDATE SensorConfiguration
        SET Latitude=?, Longitude=?, Status=?, Power_Filtration=?,
            Cloud_IP_Address=?, InfluxDB_Org=?, InfluxDB_Bucket=?, Authorization_Token=?,
            Upload_Periodicity=?, Sliding_Window=?, Reboot_Periodicity=?, Reboot_Time=?,
            Last_Update=CURRENT_TIMESTAMP
    """, (
        sensor.get("Latitude"), sensor.get("Longitude"), sensor.get("Status", "enabled"),
        sensor.get("Power Filtration"), sensor.get("Cloud IP Address"),
        sensor.get("InfluxDB Organization"), sensor.get("InfluxDB Bucket"),
        sensor.get("InfluxDB Auth Token"), sensor.get("Upload Periodicity"),
        sensor.get("Sliding Window"), rebootPeriodicity, reboot_time
    ))

    conn.commit()

    # Update SensorCommunication
    wifi_available = any(c.get("type") == "wifi" for c in connectivity)
    lora_available = any("lora" in c.get("type", "") for c in connectivity)

    c.execute("""
        UPDATE SensorCommunication
        SET WifiAvailable=?, LoRaAvailable=?, Last_Update=CURRENT_TIMESTAMP
    """, (int(wifi_available), int(lora_available)))
    conn.commit()

    # Update LoRaNetworks 
    print("[Config] Updating LoRaNetworks...")
    c.execute("""
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
    c.execute("DELETE FROM LoRaNetworks")


    
    lora_networks = [c for c in connectivity if "lora" in c.get("type", "")]
    for i, net in enumerate(lora_networks, start=1):
        name = net.get("name", net.get("type"))
        c.execute("""
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

    # --- 4) Configure cronjobs and power filtration
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

    conn.close()
    print(f"[OK] Configuração aplicada com sucesso ({datetime.now().strftime('%H:%M:%S')})")


if __name__ == "__main__":
    default_path = "/home/kali/Desktop/sensor-config-site/data/sensor_config.toml"
    print(f"[INFO] A aplicar configuração padrão: {default_path}")
    apply_config_from_toml(default_path)