import sqlite3
import datetime as dt
import pytz
import matplotlib.pyplot as plt; plt.rcdefaults()
import os
import json
import sys
import time

from sensorFunctions import *
from uart_lock import acquire_uart_lock, release_uart_lock, get_uart_lock_info


def wait_for_lora_uart_lock(caller, max_wait_sec=15, poll_sec=2):
    waited = 0

    while waited <= max_wait_sec:
        if acquire_uart_lock(caller):
            return True

        info = get_uart_lock_info()
        print(f"[LOCATION] Waiting for LoRa UART lock... waited={waited}s info={info}")

        time.sleep(poll_sec)
        waited += poll_sec

    return False


if not os.path.exists(BOOT_COMPLETE_FILE):
    print("[LOCATION] Boot initialization not complete yet. Exiting.")
    sys.exit(0)


_, available_released = wait_for_script_lock(
    COMM_AVAILABLE_LOCK_FILE,
    max_wait_sec=20,
    poll_sec=2,
    log_prefix="[LOCATION]"
)

if not available_released:
    print("[LOCATION] sensorCommunicationAvailable.py still running. Proceeding carefully.")


# Read sensor configuration from database
try:
    connwifi = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

    if len(sensor_configuration) != 0:
        sensor_UUID = sensor_configuration[0][0]
        sensor_name = sensor_configuration[0][1]
        latitude = sensor_configuration[0][2]
        longitude = sensor_configuration[0][3]
        cloud_ip_addr = sensor_configuration[0][6]
        influx_org = sensor_configuration[0][7]
        influx_bucket = sensor_configuration[0][8]
        influx_token = sensor_configuration[0][9]
        uploadTechnology = sensor_configuration[0][12]
        status = sensor_configuration[0][4]

        locationSendMode = "boot"

        try:
            row = cwifi.execute(
                """SELECT COALESCE(Location_Send_Mode, 'boot') FROM SensorConfiguration"""
            ).fetchone()

            if row and row[0]:
                locationSendMode = row[0]

        except sqlite3.Error:
            locationSendMode = "boot"

    else:
        print(
            "Failed to read sensor configuration from local database. "
            "Please make sure to configure a sensor configuration by running "
            "the 'sensorConfiguration.py' script first."
        )
        sys.exit(0)

except sqlite3.Error:
    print("Failed to read sensor configuration from local database.")
    sys.exit(0)

finally:
    if connwifi:
        cwifi.close()
        connwifi.close()


if status != "Active":
    print("[LOCATION] Sensor is disabled. Skipping location upload.")
    sys.exit(0)


called_from_boot = "--boot" in sys.argv
uploadTechnology = (uploadTechnology or "").lower()
locationSendMode = (locationSendMode or "boot").strip()


uart_locked = False


# If periodic location mode is active and the active upload technology is LoRa,
# reserve the UART before trying to refresh GPS.
# Otherwise sendCrowdingData.py may acquire the UART while this script is waiting for GPS.
if uploadTechnology == "lora" and locationSendMode == "periodic_5min" and not called_from_boot:
    uart_locked = wait_for_lora_uart_lock(
        caller="sendSensorLocation_lora_pre_gps",
        max_wait_sec=15,
        poll_sec=2
    )

    if not uart_locked:
        print("[LOCATION] LoRa UART busy before GPS refresh. Location upload skipped.")
        sys.exit(0)


# Refresh GPS only when periodic location mode is configured and this is not the boot call.
# During boot, sensorCommunicationAvailable.py already tries to get GPS and updates the DB.
if locationSendMode == "periodic_5min" and not called_from_boot:
    print("[LOCATION] Periodic location mode active. Trying to refresh GPS position...")

    try:
        enable_gps()

        gps_position = try_get_boot_gps_position(
            max_wait_sec=10,
            warmup_sec=2,
            min_good_samples=3,
            eph_max=12.0
        )

        if gps_position is not None:
            gps_lat, gps_lon, gps_quality = gps_position

            latitude = gps_lat
            longitude = gps_lon

            try:
                conn_update = sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db', timeout=30)
                cur_update = conn_update.cursor()

                cur_update.execute(
                    """UPDATE SensorConfiguration 
                       SET Latitude=?, Longitude=?, Last_Update=CURRENT_TIMESTAMP""",
                    (latitude, longitude)
                )

                conn_update.commit()
                cur_update.close()
                conn_update.close()

                print(f"[LOCATION][GPS] Updated position from GPS ({gps_quality}): {latitude}, {longitude}")

            except sqlite3.Error as error:
                print(f"[LOCATION][GPS] Failed to update GPS position in DB: {error}")

        else:
            print("[LOCATION][GPS] No valid GPS fix. Using last known DB position.")

    except Exception as e:
        print(f"[LOCATION][GPS] Error while refreshing GPS position: {e}")

    finally:
        disable_gps()

else:
    print("[LOCATION] Using stored DB location.")


dataAtual = dt.datetime.now(pytz.utc).replace(tzinfo=None)

print(latitude, longitude)

location = {
    "latitude": latitude,
    "longitude": longitude
}

json_location = json.dumps(location)


# Send sensor location to InfluxDB / MQTT
if uploadTechnology == "wifi":

    publish_location_mqtt_message(
        json_location,
        f"sttoolkit-test/mqtt/wifi/v2/sensorLocation/{sensor_UUID}"
    )

    print(f"Location '({latitude},{longitude})' was sent to the cloud server for sensor '{sensor_name}'.")


elif uploadTechnology == "lora":

    # If the lock was not acquired before GPS refresh, acquire it now.
    # This happens during boot calls or when Location_Send_Mode is "boot".
    if not uart_locked:
        uart_locked = wait_for_lora_uart_lock(
            caller="sendSensorLocation_lora",
            max_wait_sec=10,
            poll_sec=2
        )

        if not uart_locked:
            print("[LOCATION] LoRa UART busy. Location upload skipped.")
            sys.exit(0)

    rak = RAK3172("/dev/ttyAMA0", 115200)

    try:
        rak.connect()

        time.sleep(0.5)

        # CSV Format: "L,<lat>,<lon>"
        payload = f"L,{float(latitude):.5f},{float(longitude):.5f}"

        print(f"A enviar via LoRa: {payload}")

        payload_hex = payload.encode().hex()
        sent = rak.send_lorawan_data(2, payload_hex)

        if sent:
            print("Localização enviada com sucesso via LoRa.")

            # Keep the UART locked briefly after the uplink so another script
            # does not immediately consume messages related to this LoRa exchange.
            time.sleep(8)

        else:
            print("Falha ao enviar localização via LoRa.")

    except Exception as e:
        print(f"Erro durante envio via LoRa: {e}")

    finally:
        try:
            rak.disconnect()
        except Exception:
            pass

        if uart_locked:
            release_uart_lock()


else:
    print(f"[LOCATION] Unknown upload technology '{uploadTechnology}'. Location not sent.")