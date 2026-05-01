import os
import time
import socket
import sqlite3
import argparse
import subprocess

from uuid import getnode
from datetime import datetime

from sensorFunctions import *


DB_PATH = "/home/kali/Desktop/DB/SensorConfiguration.db"

CONFIG_PATH = "/home/kali/Desktop/sensor-config-site/data/sensor_config.toml"

PENDING_SYNC_FILE = "/home/kali/Desktop/.pending_cloud_sync"

DEFAULT_MQTT_HOST = "t.monicrowd.sensinglab.eu"
DEFAULT_MQTT_PORT = 1883


try:
    import tomllib
    TOML_BINARY_MODE = True
except ImportError:
    import toml as tomllib
    TOML_BINARY_MODE = False


def load_toml(path: str) -> dict:
    if TOML_BINARY_MODE:
        with open(path, "rb") as f:
            return tomllib.load(f)

    with open(path, "r", encoding="utf-8") as f:
        return tomllib.load(f)


def mark_pending_sync() -> None:
    with open(PENDING_SYNC_FILE, "w") as f:
        f.write(datetime.now().isoformat())


def clear_pending_sync() -> None:
    if os.path.exists(PENDING_SYNC_FILE):
        os.remove(PENDING_SYNC_FILE)


def get_mqtt_target(cfg: dict) -> tuple[str, int]:
    """
    Gets MQTT host and port from the first Wi-Fi entry.
    Falls back to default broker.
    """
    connectivity = cfg.get("Connectivity", [])

    for c in connectivity:
        if c.get("type") == "wifi":
            host = c.get("mqtt_address") or c.get("cloud_address") or DEFAULT_MQTT_HOST
            port = c.get("mqtt_port") or DEFAULT_MQTT_PORT

            try:
                port = int(port)
            except ValueError:
                port = DEFAULT_MQTT_PORT

            return host, port

    return DEFAULT_MQTT_HOST, DEFAULT_MQTT_PORT


def mqtt_reachable(host: str, port: int, timeout: int = 5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def publish_config_to_cloud(cfg: dict) -> bool:
    """
    Publishes sensor state and sensor networks to MQTT.
    If it fails, marks sync as pending.
    """
    mqtt_host, mqtt_port = get_mqtt_target(cfg)

    print(f"[Cloud] MQTT target: {mqtt_host}:{mqtt_port}")

    if not mqtt_reachable(mqtt_host, mqtt_port):
        print("[Cloud][ERROR] MQTT broker is not reachable.")
        mark_pending_sync()
        return False

    try:
        print("[Cloud] Publishing sensor state...")

        publish_sensor_state(
            cfg=cfg,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
        )

        print("[Cloud] Publishing sensor networks...")

        publish_sensor_networks(
            cfg=cfg,
            mqtt_host=mqtt_host,
            mqtt_port=mqtt_port,
        )

        clear_pending_sync()

        print("[Cloud] Configuration published successfully.")
        return True

    except Exception as e:
        print(f"[Cloud][ERROR] Failed to publish configuration: {e}")
        mark_pending_sync()
        return False


def normalize_reboot_periodicity(value):
    if value == "0":
        return "daily"
    if value == "1":
        return "everytwodays"
    if value == "2":
        return "everythreedays"
    if value == "3":
        return "weekly"
    if value == "4":
        return "monthly"
    if value == "5":
        return "noreboot"

    return value


def apply_config_from_toml(toml_path: str, publish_cloud: bool = True):
    if not os.path.isfile(toml_path):
        raise FileNotFoundError(f"Ficheiro {toml_path} não encontrado")

    cfg = load_toml(toml_path)

    connectivity = cfg.get("Connectivity", [])
    sensor = cfg.get("sensor", {})

    mqtt_host, _ = get_mqtt_target(cfg)
    wifi_cloud_address = mqtt_host

    conn = sqlite3.connect(DB_PATH, timeout=30)
    cursor = conn.cursor()

    try:
        reboot_periodicity = normalize_reboot_periodicity(
            sensor.get("Reboot Periodicity")
        )

        reboot_time = sensor.get("Reboot Time")

        if reboot_time == "" or reboot_time is None:
            reboot_time = 0

        cursor.execute("SELECT COUNT(*) FROM SensorConfiguration")
        exists = cursor.fetchone()[0] > 0

        if not exists:
            print("[Config] Inserting SensorConfiguration...")

            cursor.execute("""
                INSERT INTO SensorConfiguration (
                    Sensor_UUID,
                    Sensor_Name,
                    Latitude,
                    Longitude,
                    Status,
                    Power_Filtration,
                    Cloud_IP_Address,
                    InfluxDB_Org,
                    InfluxDB_Bucket,
                    Authorization_Token,
                    Upload_Periodicity,
                    Sliding_Window,
                    Reboot_Periodicity,
                    Reboot_Time,
                    Last_Update
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                getnode(),
                sensor.get("Sensor Name"),
                sensor.get("Latitude"),
                sensor.get("Longitude"),
                sensor.get("Status", "enabled"),
                sensor.get("Power Filtration"),
                wifi_cloud_address,
                sensor.get("InfluxDB Organization"),
                sensor.get("InfluxDB Bucket"),
                sensor.get("InfluxDB Auth Token"),
                sensor.get("Upload Periodicity"),
                sensor.get("Sliding Window"),
                reboot_periodicity,
                reboot_time,
            ))

            conn.commit()

        else:
            print("[Config] Updating SensorConfiguration...")

            cursor.execute("""
                UPDATE SensorConfiguration
                SET Sensor_UUID=?,
                    Sensor_Name=?,
                    Latitude=?,
                    Longitude=?,
                    Status=?,
                    Power_Filtration=?,
                    Cloud_IP_Address=?,
                    InfluxDB_Org=?,
                    InfluxDB_Bucket=?,
                    Authorization_Token=?,
                    Upload_Periodicity=?,
                    Sliding_Window=?,
                    Reboot_Periodicity=?,
                    Reboot_Time=?,
                    Last_Update=CURRENT_TIMESTAMP
            """, (
                getnode(),
                sensor.get("Sensor Name"),
                sensor.get("Latitude"),
                sensor.get("Longitude"),
                sensor.get("Status", "enabled"),
                sensor.get("Power Filtration"),
                wifi_cloud_address,
                sensor.get("InfluxDB Organization"),
                sensor.get("InfluxDB Bucket"),
                sensor.get("InfluxDB Auth Token"),
                sensor.get("Upload Periodicity"),
                sensor.get("Sliding Window"),
                reboot_periodicity,
                reboot_time,
            ))

            conn.commit()

        # --- Update SensorCommunication ----------------------------------------
        wifi_available = any(c.get("type") == "wifi" for c in connectivity)
        lora_available = any("lora" in c.get("type", "") for c in connectivity)

        # In local-only mode, we have not switched from hotspot to Wi-Fi yet.
        wifi_connected = int(wifi_available) if publish_cloud else 0

        cursor.execute("SELECT COUNT(*) FROM SensorCommunication")
        comm_exists = cursor.fetchone()[0] > 0

        if not comm_exists:
            print("[Config] Inserting SensorCommunication...")

            cursor.execute("""
                INSERT INTO SensorCommunication
                (
                    WifiAvailable,
                    WifiConnected,
                    LoRaAvailable,
                    LoRaConnected,
                    Last_Update
                )
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                int(wifi_available),
                int(wifi_connected),
                int(lora_available),
                0,
            ))

        else:
            print("[Config] Updating SensorCommunication...")

            cursor.execute("""
                UPDATE SensorCommunication
                SET WifiAvailable=?,
                    WifiConnected=?,
                    LoRaAvailable=?,
                    LoRaConnected=?,
                    Last_Update=CURRENT_TIMESTAMP
            """, (
                int(wifi_available),
                int(wifi_connected),
                int(lora_available),
                0,
            ))

        conn.commit()

        # --- Update LoRaNetworks ------------------------------------------------
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

        lora_networks = [
            c for c in connectivity
            if "lora" in c.get("type", "")
        ]

        for i, net in enumerate(lora_networks, start=1):
            name = net.get("name", net.get("type"))

            cursor.execute("""
                INSERT INTO LoRaNetworks
                (
                    name,
                    app_eui,
                    app_key,
                    dev_eui,
                    available,
                    connected,
                    priority,
                    last_update
                )
                VALUES (?, ?, ?, ?, 1, 0, ?, CURRENT_TIMESTAMP)
            """, (
                name,
                net.get("app_eui"),
                net.get("app_key"),
                net.get("dev_eui"),
                i,
            ))

            print(f"   - {name}  (priority={i})")

        conn.commit()

        # --- Power filtration ---------------------------------------------------
        power_fil = sensor.get("Power Filtration")

        if power_fil:
            change_power_filtration(int(power_fil))

        # --- Cronjobs -----------------------------------------------------------
        status = sensor.get("Status", "enabled")
        reboot_per = sensor.get("Reboot Periodicity")
        reboot_time = sensor.get("Reboot Time", "")
        upload_period = sensor.get("Upload Periodicity")

        _, detection_interface = check_upload_detection_interfaces(False)

        write_crontab_file(
            status,
            detection_interface,
            upload_period,
            reboot_per,
            reboot_time,
        )

        # --- Location upload ----------------------------------------------------
        if publish_cloud and (int(wifi_available) == 1 or int(lora_available) == 1):
            print("[Upload] Sending Sensor Location...")

            try:
                subprocess.run(
                    ["pkill", "-f", "sendCrowdingData.py"],
                    check=False,
                )

                subprocess.run(
                    ["python3", SENSOR_COMMUNICATION_AVAILABLE_FILEPATH],
                    check=False,
                )

                time.sleep(3)

                if os.path.exists("/tmp/rak_njs"):
                    subprocess.run(
                        ["python3", SENSOR_SEND_LOCATION_FILEPATH],
                        check=False,
                    )
                else:
                    print("[Upload] /tmp/rak_njs not found, skipping sensor location upload.")

            except Exception as e:
                print(f"[ERROR] Failed to send Location Data {e}")

        else:
            print("[Upload] Skipping network upload during local-only setup.")

        print(f"[OK] Successful Local configuration ({datetime.now().strftime('%H:%M:%S')})")

    finally:
        conn.close()

    # --- Cloud publication ------------------------------------------------------
    if publish_cloud:
        publish_config_to_cloud(cfg)
    else:
        print("[Cloud] Skipping cloud publish during local-only setup.")
        mark_pending_sync()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Apply configuration locally without publishing to cloud.",
    )

    parser.add_argument(
        "--sync-only",
        action="store_true",
        help="Only publish the saved configuration to cloud.",
    )

    args = parser.parse_args()

    if args.sync_only:
        cfg = load_toml(CONFIG_PATH)
        ok = publish_config_to_cloud(cfg)

        if ok:
            raise SystemExit(0)

        raise SystemExit(1)

    else:
        apply_config_from_toml(
            CONFIG_PATH,
            publish_cloud=not args.local_only,
        )