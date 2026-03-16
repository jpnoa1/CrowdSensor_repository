#!/usr/bin/env python3
import json
import os
import sys
import time
import threading
import subprocess
from uuid import getnode

try:
    import tomllib
except ImportError:
    import toml as tomllib

try:
    import tomli_w
except ImportError:
    tomli_w = None

import paho.mqtt.client as mqtt
sys.path.append("/home/kali/Desktop/")
import sensorConfigurationRemotely


MQTT_HOST = "t.monicrowd.sensinglab.eu"
MQTT_PORT = 1883
MQTT_USER = "tmmss1"
MQTT_PASS = "tomasantos00"

SENSOR_UUID = str(getnode())
CMD_TOPIC = f"monicrowd/sensors/cmd/{SENSOR_UUID}"
ACK_TOPIC = f"monicrowd/sensors/ack/{SENSOR_UUID}"

TOML_PATH = "/home/kali/Desktop/sensor-config-site/data/sensor_config.toml"  # <-- CHANGE THIS
LOCK_PATH = "/tmp/sensor_config.lock"


# -----------------------
# Helpers: TOML load/save
# -----------------------
def load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)

def atomic_write_toml(path: str, data: dict):
    """
    Writes TOML atomically: write temp file then replace.
    Requires tomli_w. If you don't have it, fallback to 'toml' package.
    """
    tmp = path + ".tmp"
    if tomli_w is not None:
        content = tomli_w.dumps(data)
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    else:
        # fallback: requires "toml" package installed
        import toml
        with open(tmp, "w", encoding="utf-8") as f:
            toml.dump(data, f)
            f.flush()
            os.fsync(f.fileno())

    os.replace(tmp, path)


def with_lock(fn):
    """
    Simple lock using a lock file. Prevents concurrent config applies.
    """
    def wrapper(*args, **kwargs):
        # naive lock (good enough for single device)
        while True:
            try:
                fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                break
            except FileExistsError:
                time.sleep(0.2)

        try:
            return fn(*args, **kwargs)
        finally:
            try:
                os.remove(LOCK_PATH)
            except FileNotFoundError:
                pass

    return wrapper


def publish_ack(mqttc, job_id, result="ok", error=None):
    payload = {
        "job_id": job_id,
        "uuid": SENSOR_UUID,
        "result": result,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if error:
        payload["error"] = str(error)

    mqttc.publish(ACK_TOPIC, json.dumps(payload), qos=0, retain=False)


# -----------------------
# Config apply logic
# -----------------------
@with_lock
def apply_config_patch(patch: dict):
    """
    patch format example:
    {
      "sensor": {"Power Filtration": "-55", "Upload Periodicity": "10"},
      "Connectivity": [ ... ]  # optional full replacement if you want
    }
    """
    cfg = load_toml(TOML_PATH)

    # merge sensor section
    if "sensor" in patch and isinstance(patch["sensor"], dict):
        cfg.setdefault("sensor", {})
        for k, v in patch["sensor"].items():
            cfg["sensor"][k] = v

    # merge connectivity (optional strategy):
    # safest: only allow full replacement if explicitly provided
    if "Connectivity" in patch:
        if isinstance(patch["Connectivity"], list):
            cfg["Connectivity"] = patch["Connectivity"]

    atomic_write_toml(TOML_PATH, cfg)

    # run your existing configuration pipeline
    apply_config_from_toml(TOML_PATH)


def do_reboot():
    # choose one:
    subprocess.Popen(["sudo", "reboot"])

def do_shutdown():
    subprocess.Popen(["sudo", "shutdown", "-h", "now"])


# -----------------------
# MQTT callbacks
# -----------------------
def handle_command(mqttc, cmd: dict):
    job_id = cmd.get("job_id")
    ctype = (cmd.get("type") or "").strip().lower()
    payload = cmd.get("payload", {})

    if not job_id:
        # no job id => cannot track, ignore
        return

    try:
        if ctype == "set_config":
            if not isinstance(payload, dict):
                raise ValueError("payload must be an object")
            apply_config_patch(payload)
            publish_ack(mqttc, job_id, "ok")

        elif ctype == "disable":
            # just set Status in TOML and apply
            apply_config_patch({"sensor": {"Status": "Disabled"}})
            publish_ack(mqttc, job_id, "ok")

        elif ctype == "activate":
            apply_config_patch({"sensor": {"Status": "Active"}})
            publish_ack(mqttc, job_id, "ok")

        elif ctype == "reboot":
            publish_ack(mqttc, job_id, "ok")
            do_reboot()

        elif ctype == "shutdown":
            publish_ack(mqttc, job_id, "ok")
            do_shutdown()

        else:
            publish_ack(mqttc, job_id, "error", error=f"unknown command_type: {ctype}")

    except Exception as e:
        publish_ack(mqttc, job_id, "error", error=e)


def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[OK][MQTT] connected")
        client.subscribe(CMD_TOPIC, qos=0)
        print(f"[OK][MQTT] subscribed {CMD_TOPIC}")
    else:
        print(f"[ERROR][MQTT] connect failed rc={rc}")


def on_message(client, userdata, msg):
    try:
        cmd = json.loads(msg.payload.decode("utf-8", errors="replace"))
    except Exception:
        print("[WARN] invalid json cmd")
        return

    # run command in background so we don't block MQTT network loop
    t = threading.Thread(target=handle_command, args=(client, cmd), daemon=True)
    t.start()


def main():
    print(f"[OK] Downlink listener starting for uuid={SENSOR_UUID}")

    c = mqtt.Client()
    c.username_pw_set(MQTT_USER, MQTT_PASS)
    c.on_connect = on_connect
    c.on_message = on_message
    c.connect(MQTT_HOST, MQTT_PORT, 60)
    c.loop_forever()


if __name__ == "__main__":
    main()