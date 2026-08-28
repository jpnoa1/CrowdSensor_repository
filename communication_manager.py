from sensorFunctions import (
    publish_detections_mqtt_message,
    store_pending_measurement,
    get_1st_pending_measurement,
    remove_1st_pending_measurement,
    buffer_get_range,
)



from event_logger import log_event


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

    def send_current_measurement(self, unix_ts, detected_devices,
                                    norm_new=None, norm_disappeared=None, seq=None):
            """Send the latest measurement or store it when Wi-Fi is unavailable/fails."""
            if self.current_uplink == "wifi":
                mqtt_confirmation = publish_detections_mqtt_message(
                    unix_ts,
                    int(detected_devices),
                    self.wifi_topic,
                    norm_new=norm_new,
                    norm_disappeared=norm_disappeared,
                    seq=seq
                )
                if mqtt_confirmation:
                    log_event(
                        "mqtt_publish_ok",
                        topic=self.wifi_topic,
                        unix_ts=unix_ts
                    )
                    return True
                log_event(
                    "mqtt_publish_fail",
                    topic=self.wifi_topic,
                    unix_ts=unix_ts
                )
                
                return False
            
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
                unix_ts,
                devices_detected,
                self.wifi_topic
            )

            if mqtt_pend_confirmation:
                remove_1st_pending_measurement()

                log_event(
                    "replay_sent",
                    unix_ts=unix_ts,
                    devices=devices_detected
                )

                replayed += 1
                continue

            break

        return replayed

    def replay_from_buffer(self, last_ack_seq, current_seq, max_items=50):
        """Replay de medições não confirmadas do buffer via WiFi."""
        if self.current_uplink != "wifi":
            return 0

        
        prev_seq = (current_seq - 1) % 256

        if prev_seq == last_ack_seq:
            return 0  # No gap

        measurements = buffer_get_range(last_ack_seq, prev_seq)

        if not measurements:
            return 0

        measurements = measurements[:max_items]

        replayed = 0
        for (seq, unix_ts, devices_detected) in measurements:
            mqtt_confirmation = publish_detections_mqtt_message(
                unix_ts,
                devices_detected,
                self.wifi_topic,
                seq=seq
            )

            if mqtt_confirmation:
                log_event(
                    "replay_sent",
                    unix_ts=unix_ts,
                    devices=devices_detected,
                    seq=seq
                )
                replayed += 1
            else:
                break  

        return replayed