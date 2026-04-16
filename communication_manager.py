from sensorFunctions import (
    publish_detections_mqtt_message,
    store_pending_measurement,
    get_1st_pending_measurement,
    remove_1st_pending_measurement,
)


class CommunicationManager:
    def __init__(self, config_cursor, wifi_topic):
        self.config_cursor = config_cursor
        self.wifi_topic = wifi_topic
        self.current_uplink = None
        self.active_lora_network = None

    def load_cached_uplink(self):
        row = self.config_cursor.execute(
            """SELECT Upload_Technology, Active_LoRa_Network FROM SensorConfiguration"""
        ).fetchone()

        if row is None:
            self.current_uplink = "none"
            self.active_lora_network = None
        else:
            self.current_uplink = (row[0] or "none").lower()
            self.active_lora_network = row[1]

        return self.current_uplink, self.active_lora_network

    def send_current_measurement(self, unix_ts, detected_devices):
        """Send the latest measurement or store it when Wi-Fi is unavailable/fails."""
        if self.current_uplink == "wifi":
            mqtt_confirmation = publish_detections_mqtt_message(unix_ts, int(detected_devices), self.wifi_topic)
            if mqtt_confirmation:
                return True

            store_pending_measurement(unix_ts, int(detected_devices))
            return False

        # Keep pending storage centralized when upload path is unavailable for now.
        store_pending_measurement(unix_ts, int(detected_devices))
        return False

    def replay_pending_wifi(self, max_items=100):
        """Replay pending measurements in chronological order while Wi-Fi sends succeed."""
        if self.current_uplink != "wifi":
            return 0

        replayed = 0
        while replayed < max_items:
            pending_row = get_1st_pending_measurement()
            if pending_row is None:
                break

            unix_ts, devices_detected = pending_row
            mqtt_pend_confirmation = publish_detections_mqtt_message(
                unix_ts, devices_detected, self.wifi_topic
            )

            if mqtt_pend_confirmation:
                remove_1st_pending_measurement()
                replayed += 1
                continue

            break

        return replayed
