import os
import re
from time import sleep
import toml
import ipaddress
import subprocess
from flask import Flask, render_template, request, redirect, url_for , after_this_request

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret")

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG_PATH = os.path.join(DATA_DIR, "sensor_config.toml")

REBOOT_OPTIONS = {
    "0": "Daily",
    "1": "Every two days",
    "2": "Every three days",
    "3": "Weekly",
    "4": "Monthly",
    "5": "No Reboot",
}

DEFAULTS = {
    "WiFi SSID": "",
    "WiFi Password": "",
    "TTN Device ID": "Your-TTN-Device-ID",
    "Latitude": "38.7369",
    "Longitude": "-9.1427",
    "Status": "enabled",
    "Power Filtration": "0",
    "Cloud IP Address": "127.0.0.1",
    "InfluxDB Organization": "my-org",
    "InfluxDB Bucket": "my-bucket",
    "InfluxDB Auth Token": "token",
    "Upload Periodicity": "5",
    "Sliding Window": "60",
    "Reboot Periodicity": "5",
    "Reboot Time": "03:00",
}

_hhmm = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
_ttn_id = re.compile(r"^[a-z0-9-]{2,64}$")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return DEFAULTS.copy()
    data = toml.load(CONFIG_PATH)
    return {**DEFAULTS, **data.get("sensor", {})}

def save_config(cfg: dict):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        toml.dump({"sensor": cfg}, f)

def _to_float(s): 
    try: return float(s)
    except: return None

def _to_int(s):
    try: return int(s)
    except: return None



def _ip_(s: str) -> bool:
    try:
        ipaddress.ip_address(s)  # IPv4 or IPv6
        return True
    except ValueError:
        return False

def validate(cfg: dict):
    errors = []

    # Required: everything except Reboot Time when Reboot Periodicity == "5"
    for key in DEFAULTS.keys():
        if key == "Reboot Time" and cfg.get("Reboot Periodicity") == "5" or key == "WiFi Password" or key == "WiFi SSID":
            continue
        if not str(cfg.get(key, "")).strip():
            errors.append(f"{key} is required.")

    # TTN Device ID
    if cfg.get("TTN Device ID") and not _ttn_id.match(cfg["TTN Device ID"]):
        errors.append("TTN Device ID must be 2–64 chars: lowercase letters, digits, hyphens.")

    # Latitude / Longitude
    lat = _to_float(cfg.get("Latitude", ""))
    lon = _to_float(cfg.get("Longitude", ""))
    if lat is None or not (-90 <= lat <= 90):
        errors.append("Latitude must be a number between -90 and 90.")
    if lon is None or not (-180 <= lon <= 180):
        errors.append("Longitude must be a number between -180 and 180.")

    # Status
    if cfg.get("Status") not in ("enabled", "disabled"):
        errors.append("Status must be 'enabled' or 'disabled'.")

    # Power Filtration (numeric; adjust limits if quiseres)
    pf = _to_float(cfg.get("Power Filtration", ""))
    if pf is None:
        errors.append("Power Filtration must be a numeric value (dB).")

    # Cloud IP / Hostname
    if not _ip_(cfg.get("Cloud IP Address", "")):
        errors.append("Cloud IP Address must be a valid IPv4/IPv6.")

    # Influx required strings (already checked non-empty above)

    # Periodicities
    up = _to_int(cfg.get("Upload Periodicity", ""))
    sw = _to_int(cfg.get("Sliding Window", ""))
    if up is None or up <= 0:
        errors.append("Upload Periodicity must be a positive integer (minutes).")
    if sw is None or sw <= 0:
        errors.append("Sliding Window must be a positive integer (minutes).")

    # Reboot Periodicity & Time
    rp = cfg.get("Reboot Periodicity", "")
    if rp not in REBOOT_OPTIONS:
        errors.append("Reboot Periodicity must be one of 0..5.")
    else:
        if rp != "5":
            if not _hhmm.match(cfg.get("Reboot Time", "")):
                errors.append("Reboot Time must be HH:MM (24h).")

    return errors

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", cfg=load_config(), errors=[])

@app.route("/save", methods=["POST"])
def save():
    fields = list(DEFAULTS.keys())
    cfg = {k: request.form.get(k, "").strip() for k in fields}
    errors = validate(cfg)
    if errors:
        return render_template("index.html", cfg=cfg, errors=errors), 400
    save_config(cfg)
    return redirect(url_for("success"))

@app.route("/success")
def success():
    cfg = load_config()
    ssid = cfg.get("WiFi SSID")
    password = cfg.get("WiFi Password")

    if ssid and password:
        print("entrein aqui")
        subprocess.run(
            ["sudo", "nmcli", "device", "wifi", "connect", ssid, "password", password, "ifname", "wlan0"]
        )

    
    
    
    @after_this_request
    def restart_service(response):
        
        sleep(10)
        
        subprocess.run(["sudo","systemctl", "restart", "sensor-setup.service"])
        
        return response
    
    return render_template("success.html", cfg=load_config(), path=CONFIG_PATH)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
