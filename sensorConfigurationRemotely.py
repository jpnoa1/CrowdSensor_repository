import os
import time
import socket
import sqlite3
import argparse

from uuid import getnode
from datetime import datetime

from sensorFunctions import *


DB_PATH = "/home/kali/Desktop/DB/SensorConfiguration.db"

CONFIG_PATH = "/home/kali/Desktop/sensor-config-site/data/sensor_config.toml"

PENDING_SYNC_FILE = "/home/kali/Desktop/.pending_cloud_sync"

DEFAULT_MQTT_HOST = "t.monicrowd.sensinglab.eu"
DEFAULT_MQTT_PORT = 8883


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
    """
    Converts the value received from the web form/TOML into the format expected
    by write_crontab_file().
    """
    value = str(value).strip() if value is not None else "5"

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

    # If the value is already normalized, keep it.
    if value in ("daily", "everytwodays", "everythreedays", "weekly", "monthly", "noreboot"):
        return value

    return "noreboot"


def normalize_reboot_time(value):
    """
    Converts reboot time to an hour suitable for cron.
    Accepts:
      - ""
      - None
      - "03:00"
      - "3"
      - 3
    """
    if value is None or value == "":
        return 0

    value = str(value).strip()

    if ":" in value:
        hour = value.split(":")[0]
        try:
            return int(hour)
        except ValueError:
            return 0

    try:
        return int(value)
    except ValueError:
        return 0


def normalize_location_send_mode(value):
    """
    Normalizes the location reporting mode.
    Valid values:
      - boot
      - periodic_5min
    """
    value = str(value).strip() if value is not None else "boot"

    if value in ("boot", "periodic_5min", "periodic_upload_window"):
        return value

    print(f"[Config][WARN] Invalid Location Send Mode '{value}'. Falling back to 'boot'.")
    return "boot"


def apply_config_from_toml(toml_path: str, publish_cloud: bool = True):
    if not os.path.isfile(toml_path):
        raise FileNotFoundError(f"Ficheiro {toml_path} não encontrado")

    cfg = load_toml(toml_path)

    connectivity = cfg.get("Connectivity", [])
    sensor = cfg.get("sensor", {})

    mqtt_host, _ = get_mqtt_target(cfg)
    wifi_cloud_address = mqtt_host

    reboot_periodicity = normalize_reboot_periodicity(
        sensor.get("Reboot Periodicity", "5")
    )

    reboot_time = normalize_reboot_time(
        sensor.get("Reboot Time", "")
    )

    location_send_mode = normalize_location_send_mode(
        sensor.get("Location Send Mode", "boot")
    )
    mqtt_username = sensor.get("MQTT Username", "")
    mqtt_password = sensor.get("MQTT Password", "")

    conn = sqlite3.connect(DB_PATH, timeout=30)
    cursor = conn.cursor()

    try:
        # --- SensorConfiguration ----------------------------------------------
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
                    MQTT_Username,
                    MQTT_Password,
                    Upload_Periodicity,
                    Sliding_Window,
                    Reboot_Periodicity,
                    Reboot_Time,
                    Location_Send_Mode,
                    Last_Update
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (
                getnode(),
                sensor.get("Sensor Name"),
                sensor.get("Latitude"),
                sensor.get("Longitude"),
                sensor.get("Status", "Active"),
                sensor.get("Power Filtration"),
                wifi_cloud_address,
                sensor.get("InfluxDB Organization"),
                sensor.get("InfluxDB Bucket"),
                sensor.get("InfluxDB Auth Token"),
                mqtt_username,
                mqtt_password,
                sensor.get("Upload Periodicity"),
                sensor.get("Sliding Window"),
                reboot_periodicity,
                reboot_time,
                location_send_mode,
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
                    MQTT_Username=?,
                    MQTT_Password=?,
                    Upload_Periodicity=?,
                    Sliding_Window=?,
                    Reboot_Periodicity=?,
                    Reboot_Time=?,
                    Location_Send_Mode=?,
                    Last_Update=CURRENT_TIMESTAMP
            """, (
                getnode(),
                sensor.get("Sensor Name"),
                sensor.get("Latitude"),
                sensor.get("Longitude"),
                sensor.get("Status", "Active"),
                sensor.get("Power Filtration"),
                wifi_cloud_address,
                sensor.get("InfluxDB Organization"),
                sensor.get("InfluxDB Bucket"),
                sensor.get("InfluxDB Auth Token"),
                mqtt_username,
                mqtt_password,
                sensor.get("Upload Periodicity"),
                sensor.get("Sliding Window"),
                reboot_periodicity,
                reboot_time,
                location_send_mode,
            ))
            conn.commit()

        # --- SensorCommunication ----------------------------------------------
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

        # --- LoRaNetworks ------------------------------------------------------
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

        # --- WiFiNetworks ------------------------------------------------------
        #   Mirrors LoRaNetworks for visibility and logging.
        #   Actual profile activation is handled by NetworkManager
        #   (sensor-wifi-* profiles), but this table lets the backend
        #   and Grafana show which Wi-Fi networks are configured.
        print("[Config] Updating WiFiNetworks...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS WiFiNetworks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ssid TEXT,
                profile_name TEXT,
                mqtt_address TEXT,
                mqtt_port INTEGER DEFAULT 1883,
                priority INTEGER DEFAULT 0,
                last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cursor.execute("DELETE FROM WiFiNetworks")

        wifi_networks = [
            c for c in connectivity
            if c.get("type") == "wifi" and c.get("ssid")
        ]

        for i, net in enumerate(wifi_networks, start=1):
            import re
            ssid = net.get("ssid", "wifi")
            safe_ssid = re.sub(r"[^a-zA-Z0-9_.-]+", "_", ssid).strip("_")[:40] or "wifi"
            profile_name = f"sensor-wifi-{i}-{safe_ssid}"
            mqtt_addr = net.get("mqtt_address") or net.get("cloud_address") or DEFAULT_MQTT_HOST
            mqtt_port = net.get("mqtt_port") or DEFAULT_MQTT_PORT

            cursor.execute("""
                INSERT INTO WiFiNetworks
                (ssid, profile_name, mqtt_address, mqtt_port, priority, last_update)
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """, (ssid, profile_name, mqtt_addr, int(mqtt_port), i))

            print(f"   - {ssid} → {profile_name}  (priority={i})")

        conn.commit()

        # --- Power filtration --------------------------------------------------
        power_fil = sensor.get("Power Filtration")

        if power_fil not in (None, ""):
            try:
                change_power_filtration(int(float(power_fil)))
            except ValueError:
                print(f"[Config][WARN] Invalid Power Filtration value: {power_fil}")

        # --- Cronjobs ----------------------------------------------------------
        status = sensor.get("Status", "Active")
        upload_period = sensor.get("Upload Periodicity")

        _, detection_interface = check_upload_detection_interfaces(False)

        write_crontab_file(
            status,
            detection_interface,
            upload_period,
            reboot_periodicity,
            reboot_time,
            location_send_mode,
        )

        # --- Location upload ---------------------------------------------------
        # Do not send location here.
        # Location is sent:
        #   1) once at boot by sensorCommunicationAvailable.py, if Status == Active;
        #   2) periodically by cron if Location_Send_Mode == periodic_5min.
        print(f"[Config] Location Send Mode: {location_send_mode}")

        print(f"[OK] Successful Local configuration ({datetime.now().strftime('%H:%M:%S')})")

    finally:
        conn.close()

    # --- Cloud publication -----------------------------------------------------
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