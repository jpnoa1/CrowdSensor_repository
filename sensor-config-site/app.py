"""
Minimal Flask app for local sensor configuration.

Responsibilities:
- Serve a simple configuration form.
- Validate and persist settings to TOML.
- Persist a list of connectivity entries ([[Connectivity]]).
- After saving, apply the configuration locally, switch from hotspot to Wi-Fi,
  and synchronize with the cloud only when MQTT is reachable.
"""

from __future__ import annotations

import os
import re
import toml
import socket
import threading
import ipaddress
import subprocess
from uuid import getnode
from time import sleep
from typing import Dict, List, Tuple

import sys
sys.path.insert(0, "/home/kali/Desktop")

from flask import Flask, render_template, request, redirect, url_for, after_this_request


# --- Flask setup ----------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")


# --- Paths & constants ----------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "sensor_config.toml")

WLAN_IFACE = os.environ.get("WLAN_IFACE", "wlan0")
NMCLI_BIN = os.environ.get("NMCLI_BIN", "/usr/bin/nmcli")
SYSTEMCTL = os.environ.get("SYSTEMCTL", "/bin/systemctl")
SETUP_SERVICE = os.environ.get("SETUP_SERVICE", "sensor-setup.service")

SENSOR_CONFIG_REMOTE = "/home/kali/Desktop/sensorConfigurationRemotely.py"
PENDING_SYNC_FILE = "/home/kali/Desktop/.pending_cloud_sync"

DEFAULT_MQTT_HOST = "t.monicrowd.sensinglab.eu"
DEFAULT_MQTT_PORT = 8883

USE_REBOOT = True

os.makedirs(DATA_DIR, exist_ok=True)


REBOOT_OPTIONS = {
    "0": "Daily",
    "1": "Every two days",
    "2": "Every three days",
    "3": "Weekly",
    "4": "Monthly",
    "5": "No Reboot",
}


DEFAULTS: Dict[str, str] = {
    "Sensor Name": "",
    "Latitude": "38.7369",
    "Longitude": "-9.1427",
    "Status": "Active",
    "Power Filtration": "0",
    "InfluxDB Organization": "my-org",
    "InfluxDB Bucket": "my-bucket",
    "InfluxDB Auth Token": "token",
    "MQTT Username": "",
    "MQTT Password": "",
    "Upload Periodicity": "5",
    "Sliding Window": "60",
    "Location Send Mode": "boot",
    "Reboot Periodicity": "5",
    "Reboot Time": "03:00",
    
    
}


# --- Simple validators ----------------------------------------------------------
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}$")


def _to_float(s: str):
    try:
        return float(s)
    except Exception:
        return None


def _to_int(s: str):
    try:
        return int(s)
    except Exception:
        return None


def _is_host_or_ip(s: str) -> bool:
    """Accept hostnames or IPv4/IPv6 addresses."""
    s = s.strip()

    if not s:
        return False

    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        pass

    return bool(HOSTNAME_RE.match(s))


# --- TOML load/save -------------------------------------------------------------
def load_all() -> Tuple[Dict[str, str], List[dict]]:
    """Load sensor section and connectivity list; merge defaults for missing keys."""
    if not os.path.exists(CONFIG_PATH):
        return DEFAULTS.copy(), []

    data = toml.load(CONFIG_PATH)

    sensor = {
        **DEFAULTS,
        **data.get("sensor", {}),
    }

    connectivity = data.get("Connectivity", []) or data.get("connectivity", [])

    return sensor, connectivity


def save_all(sensor_cfg: Dict[str, str], connectivity: List[dict]) -> None:
    """Write both sections to TOML."""
    payload = {
        "sensor": sensor_cfg,
        "Connectivity": connectivity,
    }

    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        toml.dump(payload, f)


# --- Connectivity parsing -------------------------------------------------------
def parse_connectivity(form) -> tuple[list[dict], list[str]]:
    """
    Build [[Connectivity]] from form inputs.
    Wi-Fi: supports multiple entries.
    TTN / Helium: single entry each, gated by enable toggles.
    """
    errs = []
    conn = []

    # --- Wi-Fi multiple entries -------------------------------------------------
    ssids = [s.strip() for s in form.getlist("WiFi SSID")]
    pwds = [s.strip() for s in form.getlist("WiFi Password")]
    mqtt_addrs = [s.strip() for s in form.getlist("WiFi MQTT Address")]
    mqtt_ports = [s.strip() for s in form.getlist("MQTT Port")]

    for ssid, pwd, addr, port in zip(ssids, pwds, mqtt_addrs, mqtt_ports):
        # Ignore fully empty Wi-Fi rows.
        if not ssid and not pwd and not addr and not port:
            continue

        conn.append({
            "type": "wifi",
            "ssid": ssid,
            "password": pwd,
            "mqtt_address": addr,
            "mqtt_port": port if port else "8883",
        })

    # --- TTN --------------------------------------------------------------------
    if form.get("ttn_enabled") == "1":
        app_eui = (form.get("TTN App EUI", "") or "").strip()
        app_key = (form.get("TTN App Key", "") or "").strip()
        dev_eui = (form.get("TTN Dev EUI", "") or "").strip()
        device_id = (form.get("TTN Device ID", "") or "").strip()

        conn.append({
            "type": "lorattn",
            "device_id": device_id,
            "app_eui": app_eui,
            "app_key": app_key,
            "dev_eui": dev_eui,
        })

    # --- Helium -----------------------------------------------------------------
    if form.get("helium_enabled") == "1":
        dev_id = (form.get("Helium Device ID", "") or "").strip()
        app_eui = (form.get("Helium App EUI", "") or "").strip()
        app_key = (form.get("Helium App Key", "") or "").strip()
        dev_eui = (form.get("Helium Dev EUI", "") or "").strip()

        conn.append({
            "type": "lorahelium",
            "device_id": dev_id,
            "app_eui": app_eui,
            "app_key": app_key,
            "dev_eui": dev_eui,
        })

    return conn, errs


# --- Validation -----------------------------------------------------------------
def validate_sensor(cfg: Dict[str, str]) -> List[str]:
    """Validate scalar sensor fields."""
    errors: List[str] = []

    for key in DEFAULTS.keys():
        if key == "Reboot Time" and cfg.get("Reboot Periodicity") == "5":
            continue

        if key == "Sensor Name":
            sensor_name = str(cfg.get(key, "")).strip()

            if not sensor_name:
                errors.append("Sensor Name is required.")
            elif len(sensor_name) > 50:
                errors.append("Sensor Name must be 50 characters or less.")

            continue

        if not str(cfg.get(key, "")).strip():
            errors.append(f"{key} is required.")

    lat = _to_float(cfg.get("Latitude", ""))
    lon = _to_float(cfg.get("Longitude", ""))

    if lat is None or not (-90 <= lat <= 90):
        errors.append("Latitude must be a number between -90 and 90.")

    if lon is None or not (-180 <= lon <= 180):
        errors.append("Longitude must be a number between -180 and 180.")

    if cfg.get("Status") not in ("Active", "Disabled"):
        errors.append("Status must be 'Active' or 'Disabled'.")

    if _to_float(cfg.get("Power Filtration", "")) is None:
        errors.append("Power Filtration must be a numeric value (dB).")

    if (_to_int(cfg.get("Upload Periodicity", "")) or 0) <= 0:
        errors.append("Upload Periodicity must be a positive integer (minutes).")

    if (_to_int(cfg.get("Sliding Window", "")) or 0) <= 0:
        errors.append("Sliding Window must be a positive integer (minutes).")

    if cfg.get("Location Send Mode") not in ("boot", "periodic_5min", "periodic_upload_window"):
        errors.append("Location Send Mode must be 'boot', 'periodic_5min', or 'periodic_upload_window'.")

    rp = cfg.get("Reboot Periodicity", "")

    if rp not in REBOOT_OPTIONS:
        errors.append("Reboot Periodicity must be one of 0..5.")
    elif rp != "5" and not _HHMM.match(cfg.get("Reboot Time", "")):
        errors.append("Reboot Time must be HH:MM (24h).")

    return errors


def validate_connectivity(conn: List[dict]) -> List[str]:
    """
    Validate all connectivity entries:
    - At least one network.
    - Wi-Fi: SSID, password, mqtt_address and mqtt_port.
    - TTN: AppEUI, AppKey, DevEUI.
    - Helium: DeviceID + AppEUI, AppKey, DevEUI.
    """
    errors: List[str] = []

    if not conn:
        return ["Please add at least one connectivity option."]

    for i, c in enumerate(conn, start=1):
        t = c.get("type")

        if t == "wifi":
            ssid = c.get("ssid", "")
            pwd = c.get("password", "")
            mqtt_addr = c.get("mqtt_address", "")
            mqtt_port = c.get("mqtt_port", "8883")

            if not ssid:
                errors.append(f"Wi-Fi #{i}: SSID is required.")

            if not pwd:
                errors.append(f"Wi-Fi #{i}: Password is required.")

            if not mqtt_addr:
                errors.append(f"Wi-Fi #{i}: MQTT Address is required.")
            elif not _is_host_or_ip(mqtt_addr):
                errors.append(f"Wi-Fi #{i}: MQTT Address must be a valid hostname or IP.")

            port_int = _to_int(mqtt_port)

            if port_int is None or not (1 <= port_int <= 65535):
                errors.append(f"Wi-Fi #{i}: MQTT Port must be a number between 1 and 65535.")

        elif t == "lorattn":
            app_eui = c.get("app_eui", "")
            app_key = c.get("app_key", "")
            dev_eui = c.get("dev_eui", "")

            hex16 = re.compile(r"^[A-Fa-f0-9]{16}$")
            hex32 = re.compile(r"^[A-Fa-f0-9]{32}$")

            if not hex16.match(app_eui):
                errors.append("TTN: App EUI must be 16 hex characters.")

            if not hex32.match(app_key):
                errors.append("TTN: App Key must be 32 hex characters.")

            if not hex16.match(dev_eui):
                errors.append("TTN: Dev EUI must be 16 hex characters.")

        elif t == "lorahelium":
            dev_id = (c.get("device_id") or "").strip()
            app_eui = (c.get("app_eui") or "").strip()
            app_key = (c.get("app_key") or "").strip()
            dev_eui = (c.get("dev_eui") or "").strip()

            if not dev_id:
                errors.append("LoRa Helium: Device ID is required.")

            if not (app_eui and app_key and dev_eui):
                errors.append("LoRa Helium: App EUI, App Key and Dev EUI are all required.")

        else:
            errors.append(f"Connectivity #{i}: unknown type '{t}'.")

    return errors


# --- Wi-Fi profile management ---------------------------------------------------
def _safe_profile_name(index: int, ssid: str) -> str:
    safe_ssid = re.sub(r"[^a-zA-Z0-9_.-]+", "_", ssid).strip("_")
    safe_ssid = safe_ssid[:40] if safe_ssid else "wifi"

    return f"sensor-wifi-{index}-{safe_ssid}"


def get_wifi_networks(conn: List[dict]) -> List[dict]:
    return [
        c for c in conn
        if c.get("type") == "wifi" and c.get("ssid") and c.get("password")
    ]


def delete_old_sensor_wifi_profiles() -> None:
    result = subprocess.run(
        [NMCLI_BIN, "-t", "-f", "NAME", "connection", "show"],
        text=True,
        capture_output=True,
        check=False,
    )

    for name in result.stdout.splitlines():
        if name.startswith("sensor-wifi-"):
            print(f"[WiFi] Deleting old profile: {name}")

            subprocess.run(
                ["sudo", NMCLI_BIN, "connection", "delete", name],
                check=False,
            )


def configure_all_wifi_profiles(conn: List[dict]) -> List[str]:
    """
    Create NetworkManager profiles for all configured Wi-Fi networks.
    The order in the form/TOML defines priority.
    """
    wifi_networks = get_wifi_networks(conn)

    if not wifi_networks:
        print("[WiFi] No Wi-Fi networks configured.")
        return []

    delete_old_sensor_wifi_profiles()

    profile_names: List[str] = []

    for index, wifi in enumerate(wifi_networks, start=1):
        ssid = wifi["ssid"]
        password = wifi["password"]

        profile_name = _safe_profile_name(index, ssid)
        priority = 100 - ((index - 1) * 10)

        print(f"[WiFi] Creating profile {profile_name} for SSID={ssid}, priority={priority}")

        subprocess.run(
            [
                "sudo", NMCLI_BIN,
                "connection", "add",
                "type", "wifi",
                "ifname", WLAN_IFACE,
                "con-name", profile_name,
                "ssid", ssid,
            ],
            check=False,
        )

        subprocess.run(
            [
                "sudo", NMCLI_BIN,
                "connection", "modify", profile_name,
                "wifi-sec.key-mgmt", "wpa-psk",
                "wifi-sec.psk", password,
                "connection.autoconnect", "yes",
                "connection.autoconnect-priority", str(priority),
                "ipv4.method", "auto",
                "ipv6.method", "auto",
            ],
            check=False,
        )

        profile_names.append(profile_name)

    return profile_names


def release_wlan_from_hotspot() -> None:
    """
    Release wlan0 from current hotspot/setup connection.
    """
    print("[WiFi] Releasing wlan interface from setup/hotspot mode...")

    active = subprocess.run(
        [NMCLI_BIN, "-t", "-f", "NAME,DEVICE", "connection", "show", "--active"],
        text=True,
        capture_output=True,
        check=False,
    )

    for line in active.stdout.splitlines():
        parts = line.split(":")

        if len(parts) < 2:
            continue

        name, device = parts[0], parts[1]

        if device == WLAN_IFACE and not name.startswith("sensor-wifi-"):
            print(f"[WiFi] Bringing down active setup connection: {name}")

            subprocess.run(
                ["sudo", NMCLI_BIN, "connection", "modify", name, "connection.autoconnect", "no"],
                check=False,
            )

            subprocess.run(
                ["sudo", NMCLI_BIN, "connection", "down", name],
                check=False,
            )

    subprocess.run(
        ["sudo", NMCLI_BIN, "device", "disconnect", WLAN_IFACE],
        check=False,
    )

    sleep(2)

    subprocess.run(
        ["sudo", NMCLI_BIN, "radio", "wifi", "on"],
        check=False,
    )

    subprocess.run(
        ["sudo", NMCLI_BIN, "device", "set", WLAN_IFACE, "managed", "yes"],
        check=False,
    )

    sleep(2)


def connect_wifi_if_present(conn: List[dict]) -> bool:
    """
    Configure all Wi-Fi profiles, release hotspot, and connect
    using the same scan-first logic as sensorCommunicationCheck.
    """
    profile_names = configure_all_wifi_profiles(conn)

    if not profile_names:
        return False

    release_wlan_from_hotspot()

    # Give NM time to fully release wlan0
    sleep(3)

    # Reuse the same scan-first approach as sensorCommunicationCheck
    from sensorFunctions import try_wifi_failover, check_wifi_connection
    
    ok, profile = try_wifi_failover(skip_current=False)

    if ok:
        print(f"[WiFi] Connected via {profile}")
        return True

    print("[WiFi][ERROR] No visible configured network could connect.")
    return False


# --- MQTT / sync helpers --------------------------------------------------------
def get_mqtt_target_from_connectivity(conn: List[dict]) -> tuple[str, int]:
    """
    Gets MQTT host and port from the first Wi-Fi entry.
    Falls back to default broker.
    """
    for c in conn:
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


def wait_for_mqtt(host: str, port: int, max_attempts: int = 12, delay: int = 5) -> bool:
    for attempt in range(1, max_attempts + 1):
        if mqtt_reachable(host, port):
            print(f"[MQTT] Broker reachable. Attempt {attempt}/{max_attempts}.")
            return True

        print(f"[MQTT] Broker not reachable. Attempt {attempt}/{max_attempts}.")
        sleep(delay)

    return False


def mark_pending_sync() -> None:
    with open(PENDING_SYNC_FILE, "w") as f:
        f.write("pending")


def exit_setup_mode() -> None:
    """
    Exit setup mode.
    By default, reboots the Raspberry so normal services start cleanly.
    """
    if USE_REBOOT:
        subprocess.run(["sudo", "/sbin/reboot"], check=False)
    else:
        subprocess.run(["sudo", SYSTEMCTL, "restart", SETUP_SERVICE], check=False)


def post_success_handover(connectivity: List[dict]) -> None:
    """
    After the success page is returned:
    1. Apply config locally without publishing.
    2. Configure all Wi-Fi profiles.
    3. Switch from hotspot to the best available Wi-Fi.
    4. Wait for MQTT.
    5. Apply config again and publish.
    6. Exit setup mode.
    """
    try:
        print("[Setup] Applying configuration locally first...")

        subprocess.run(
            ["python3", SENSOR_CONFIG_REMOTE, "--local-only"],
            check=False,
        )

        print("[Setup] Switching from hotspot to configured Wi-Fi...")

        wifi_connected = connect_wifi_if_present(connectivity)
        sleep(5)
        mqtt_host, mqtt_port = get_mqtt_target_from_connectivity(connectivity)

        if wifi_connected and wait_for_mqtt(mqtt_host, mqtt_port):
            print("[Setup] Wi-Fi and MQTT available. Applying and publishing configuration...")

            subprocess.run(
                ["python3", SENSOR_CONFIG_REMOTE],
                check=False,
            )

        else:
            print("[Setup][ERROR] Wi-Fi/MQTT unavailable. Configuration saved locally, sync pending.")
            mark_pending_sync()

        sleep(2)

        print("[Setup] Exiting setup mode...")
        exit_setup_mode()

    except Exception as exc:
        print("[Setup][ERROR] post_success_handover failed:", exc)
        mark_pending_sync()


# --- Routes ---------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    sensor, connectivity = load_all()

    return render_template(
        "index.html",
        cfg=sensor,
        connectivity=connectivity,
        errors=[],
        sensor_uuid=str(getnode()),
    )


@app.route("/save", methods=["POST"])
def save():
    sensor_fields = list(DEFAULTS.keys())
    sensor_cfg = {
        k: (request.form.get(k, "") or "").strip()
        for k in sensor_fields
    }

    connectivity, parse_errs = parse_connectivity(request.form)

    errors = parse_errs + validate_sensor(sensor_cfg) + validate_connectivity(connectivity)

    if errors:
        return render_template(
            "index.html",
            cfg=sensor_cfg,
            connectivity=connectivity,
            errors=errors,
            sensor_uuid=str(getnode()),
        ), 400

    save_all(sensor_cfg, connectivity)

    return redirect(url_for("success"))


@app.route("/success")
def success():
    sensor_cfg, connectivity = load_all()

    @after_this_request
    def _post_send(response):
        try:
            print("[Flask] Starting post-success handover thread...")

            t = threading.Thread(
                target=post_success_handover,
                args=(connectivity,),
                daemon=False,
            )

            t.start()

        except Exception as exc:
            print("[Flask] Error starting handover thread:", exc)

        return response

    return render_template(
        "success.html",
        cfg=sensor_cfg,
        path=CONFIG_PATH,
    )


# --- Entrypoint -----------------------------------------------------------------
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
    )