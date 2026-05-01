"""
Minimal Flask app for local sensor configuration.

Responsibilities:
- Serve a simple configuration form.
- Validate and persist settings to TOML.
- Persist a list of connectivity entries ([[Connectivity]]).
- After saving, optionally connect to Wi-Fi and exit setup (restart systemd service).

Notes:
- Keep this file self-contained; no external deps beyond Flask + toml.
- Keep NetworkManager available (nmcli) for Wi-Fi operations.
"""

from __future__ import annotations
import os
import re
import toml
import ipaddress
import subprocess
from time import sleep
from typing import Dict, List, Tuple
from flask import Flask, render_template, request, redirect, url_for, after_this_request

# --- Flask setup ----------------------------------------------------------------
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")

# --- Paths & constants ----------------------------------------------------------
BASE_DIR   = os.path.abspath(os.path.dirname(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "sensor_config.toml")

WLAN_IFACE   = os.environ.get("WLAN_IFACE", "wlan0")
NMCLI_BIN    = os.environ.get("NMCLI_BIN", "/usr/bin/nmcli")
SYSTEMCTL    = os.environ.get("SYSTEMCTL", "/bin/systemctl")
SETUP_SERVICE = os.environ.get("SETUP_SERVICE", "sensor-setup.service")
USE_REBOOT   = True  # set True if you prefer a full reboot instead of restarting the service

os.makedirs(DATA_DIR, exist_ok=True)

REBOOT_OPTIONS = {"0":"Daily","1":"Every two days","2":"Every three days","3":"Weekly","4":"Monthly","5":"No Reboot"}

DEFAULTS: Dict[str, str] = {
    "Sensor Name": "",
    "Latitude": "38.7369",
    "Longitude": "-9.1427",
    "Status": "Active",
    "Power Filtration": "0",
    "InfluxDB Organization": "my-org",
    "InfluxDB Bucket": "my-bucket",
    "InfluxDB Auth Token": "token",
    "Upload Periodicity": "5",
    "Sliding Window": "60",
    "Reboot Periodicity": "5",
    "Reboot Time": "03:00",
}

# Simple validators
_HHMM = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

def _to_float(s: str):
    try: return float(s)
    except: return None

def _to_int(s: str):
    try: return int(s)
    except: return None

HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9.-]{1,253}$")

def _is_host_or_ip(s: str) -> bool:
    """Accept hostnames or IPv4/IPv6 addresses."""
    s = s.strip()
    if not s:
        return False

    # Try IP literal
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        pass

    # Try DNS hostname
    if HOSTNAME_RE.match(s):
        return True

    return False


def load_all() -> Tuple[Dict[str, str], List[dict]]:
    """Load sensor section and connectivity list; merge defaults for missing keys."""
    if not os.path.exists(CONFIG_PATH):
        return DEFAULTS.copy(), []
    data = toml.load(CONFIG_PATH)
    sensor = {**DEFAULTS, **data.get("sensor", {})}
    connectivity = data.get("Connectivity", []) or data.get("connectivity", [])
    return sensor, connectivity

def save_all(sensor_cfg: Dict[str, str], connectivity: List[dict]) -> None:
    """Write both sections to TOML."""
    payload = {"sensor": sensor_cfg, "Connectivity": connectivity}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        toml.dump(payload, f)


def parse_connectivity(form) -> tuple[list[dict], list[str]]:
    """
    Build [[Connectivity]] from form inputs.
    Wi-Fi: supports multiple (repeated form fields).
    TTN / Helium: single entry each, gated by enable toggles.
    """
    errs = []
    conn = []

    # --- Wi-Fi (multiple) ---
    ssids      = [s.strip() for s in form.getlist("WiFi SSID")]
    pwds       = [s.strip() for s in form.getlist("WiFi Password")]
    mqtt_addrs = [s.strip() for s in form.getlist("WiFi MQTT Address")]
    mqtt_ports = [s.strip() for s in form.getlist("MQTT Port")]

    for ssid, pwd, addr, port in zip(ssids, pwds, mqtt_addrs, mqtt_ports):
        if ssid or pwd or addr:
            conn.append({
                "type": "wifi",
                "ssid": ssid,
                "password": pwd,
                "mqtt_address": addr,
                "mqtt_port": port if port else "1883"
            })
        else:
            errs.append("Wi-Fi entries must include SSID, Password and MQTT Address.")

    # --- TTN (single, gated by toggle) ---
    if form.get("ttn_enabled") == "1":
        app_eui   = (form.get("TTN App EUI", "") or "").strip()
        app_key   = (form.get("TTN App Key", "") or "").strip()
        dev_eui   = (form.get("TTN Dev EUI", "") or "").strip()
        device_id = (form.get("TTN Device ID", "") or "").strip()
        conn.append({
            "type": "lorattn",
            "device_id": device_id,
            "app_eui": app_eui,
            "app_key": app_key,
            "dev_eui": dev_eui
        })

    # --- Helium (single, gated by toggle) ---
    if form.get("helium_enabled") == "1":
        dev_id  = (form.get("Helium Device ID", "") or "").strip()
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
    """Validate scalar sensor fields (non-connectivity)."""
    errors: List[str] = []

    # Required scalar fields
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

    # Lat/Lon numeric ranges
    lat = _to_float(cfg.get("Latitude", ""))
    lon = _to_float(cfg.get("Longitude", ""))
    if lat is None or not (-90 <= lat <= 90):
        errors.append("Latitude must be a number between -90 and 90.")
    if lon is None or not (-180 <= lon <= 180):
        errors.append("Longitude must be a number between -180 and 180.")

    # Status enum
    if cfg.get("Status") not in ("Active", "Disabled"):
        errors.append("Status must be 'Active' or 'Disabled'.")

    # Power filtration numeric
    if _to_float(cfg.get("Power Filtration", "")) is None:
        errors.append("Power Filtration must be a numeric value (dB).")

    # Upload windows
    if (_to_int(cfg.get("Upload Periodicity", "")) or 0) <= 0:
        errors.append("Upload Periodicity must be a positive integer (minutes).")
    if (_to_int(cfg.get("Sliding Window", "")) or 0) <= 0:
        errors.append("Sliding Window must be a positive integer (minutes).")

    # Reboot options
    rp = cfg.get("Reboot Periodicity", "")
    if rp not in REBOOT_OPTIONS:
        errors.append("Reboot Periodicity must be one of 0..5.")
    elif rp != "5" and not _HHMM.match(cfg.get("Reboot Time", "")):
        errors.append("Reboot Time must be HH:MM (24h).")

    return errors

def validate_connectivity(conn):
    """
    Validate all connectivity entries:
    - At least one network
    - Wi-Fi: SSID, password, mqtt_address (hostname/IP)
    - TTN: AppEUI, AppKey, DevEUI (hex formats)
    - Helium: DeviceID + AppEUI, AppKey, DevEUI
    """

    errors = []

    if not conn:
        return ["Please add at least one connectivity option."]

    for i, c in enumerate(conn, start=1):

        t = c.get("type")

        # --- Wi-Fi ---------------------------------------------------------
        if t == "wifi":
            ssid       = c.get("ssid", "")
            pwd        = c.get("password", "")
            mqtt_addr  = c.get("mqtt_address", "")
            mqtt_port  = c.get("mqtt_port", "1883")

            if not ssid:
                errors.append(f"Wi-Fi #{i}: SSID is required.")
            if not pwd:
                errors.append(f"Wi-Fi #{i}: Password is required.")
            if not mqtt_addr:
                errors.append(f"Wi-Fi #{i}: MQTT Address is required.")
            elif not _is_host_or_ip(mqtt_addr):
                errors.append(
                    f"Wi-Fi #{i}: MQTT Address must be a valid hostname or IP."
                )

            port_int = _to_int(mqtt_port)
            if port_int is None or not (1 <= port_int <= 65535):
                errors.append(f"Wi-Fi #{i}: MQTT Port must be a number between 1 and 65535.")

        # --- TTN ------------------------------------------------------------
        if t == "lorattn":
            a = c.get("app_eui", "")
            k = c.get("app_key", "")
            d = c.get("dev_eui", "")

            hex16 = re.compile(r"^[A-Fa-f0-9]{16}$")
            hex32 = re.compile(r"^[A-Fa-f0-9]{32}$")

            if not hex16.match(a):
                errors.append("TTN: App EUI must be 16 hex characters.")
            if not hex32.match(k):
                errors.append("TTN: App Key must be 32 hex characters.")
            if not hex16.match(d):
                errors.append("TTN: Dev EUI must be 16 hex characters.")

        # --- Helium ---------------------------------------------------------
        elif t == "lorahelium":
            dev_id  = (c.get("device_id") or "").strip()
            app_eui = (c.get("app_eui")   or "").strip()
            app_key = (c.get("app_key")   or "").strip()
            dev_eui = (c.get("dev_eui")   or "").strip()

            if not dev_id:
                errors.append("LoRa Helium: Device ID is required.")
            if not (app_eui and app_key and dev_eui):
                errors.append("LoRa Helium: App EUI, App Key and Dev EUI are all required.")

    return errors


# --- Wi-Fi connect & teardown ---------------------------------------------------
def connect_wifi_if_present(conn: List[dict]) -> None:
    """Connect to the first Wi-Fi entry (if any) using NetworkManager."""
    wifi = next((c for c in conn if c.get("type") == "wifi" and c.get("ssid") and c.get("password")), None)
    if not wifi:
        return
    try:
        subprocess.run(
            ["sudo", NMCLI_BIN, "device", "wifi", "connect", wifi["ssid"], "password", wifi["password"], "ifname", WLAN_IFACE],
            check=False
        )
    except Exception as exc:
        print("nmcli connect error:", exc)

def exit_setup_mode() -> None:
    """Stop setup service cleanly. A restart will run ConditionPathExists again and skip."""
    if USE_REBOOT:
        subprocess.run(["sudo", "/sbin/reboot"], check=False)
    else:
        subprocess.run(["sudo", SYSTEMCTL, "restart", SETUP_SERVICE], check=False)

# --- Routes ---------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    sensor, connectivity = load_all()
    return render_template("index.html", cfg=sensor, connectivity=connectivity, errors=[])


@app.route("/save", methods=["POST"])
def save():
    # 1) Parse scalar fields
    sensor_fields = list(DEFAULTS.keys())
    sensor_cfg = {k: (request.form.get(k, "") or "").strip() for k in sensor_fields}

    # 2) Parse connectivity blocks
    connectivity, parse_errs = parse_connectivity(request.form)

    # 3) Validate
    errors = parse_errs + validate_sensor(sensor_cfg) + validate_connectivity(connectivity)
    if errors:
        return render_template("index.html", cfg=sensor_cfg, connectivity=connectivity, errors=errors), 400

    # 4) Persist
    save_all(sensor_cfg, connectivity)

    # 5) Redirect to success page (post-save actions happen after response)
    return redirect(url_for("success"))

@app.route("/success")
def success():
    sensor_cfg, connectivity = load_all()

    @after_this_request
    def _post_send(response):
        # Give the browser time to render the success page
        try:
            print("[Flask] Applying sensor configuration...")
            subprocess.run(
                ["python3", "/home/kali/Desktop/sensorConfigurationRemotely.py"],
                check=False
            )
        except Exception as exc:
            print("[Flask] Error calling apply_config_from_toml:", exc)

        sleep(5)
        # Try Wi-Fi if provided; then exit setup mode (restart service or reboot)
        connect_wifi_if_present(connectivity)
        sleep(5)
        exit_setup_mode()
        return response

    return render_template("success.html", cfg=sensor_cfg, path=CONFIG_PATH)

# --- Entrypoint -----------------------------------------------------------------
if __name__ == "__main__":
    # For production use, run under systemd (service) and keep debug disabled.
    app.run(host="0.0.0.0", port=5000, debug=False)
