# Crowd_Detection_STToolkit

This repository contains the source code and configuration files for a **crowd density monitoring system** designed to operate autonomously using **LoRaWAN** and/or **Wi-Fi** communication.  
It integrates configurable sensor nodes capable of detecting nearby mobile devices, managing data locally, and transmitting aggregated information to a central server for analysis.

---

## Project Overview

The system is designed for **real-time crowd estimation** in public spaces using device fingerprinting and signal detection techniques.  
It operates under a flexible communication architecture that allows dynamic switching between **Wi-Fi** and **LoRaWAN** networks depending on connectivity and bandwidth availability.

Core features include:
- Automatic device detection and counting.
- Configurable data retention and upload periodicity.
- Auto-healing and reconfiguration via downlinks.
- Local data persistence using SQLite.
- Full operational automation through `cron` jobs.

---

## System Architecture

Each sensor node consists of:
- **Main scripts** responsible for data collection and transmission (`sendCrowdingData.py`, `sendSensorLocation.py`).
- **Configuration handlers** (`sensorConfiguration.py`, `sensorCheckConfig.py`, `sensorFunctions.py`).
- **Communication management** via the custom library [`swARM_at_custom`](./swARM_at_custom), adapted from `swARM_at` to provide LoRa AT-command control (RAK3172, RAK4270, ASR6501 modules).
- **Data persistence** (`dataRetentionManager.py`) and **integrity checking** mechanisms.
- **Automated startup and monitoring** through scheduled `cron` tasks defined in `cronjobs_configured.txt`.

---

## Communication Workflow

1. **Data Collection** — Device probes are captured via Wi-Fi sniffing or BLE scanning.  
2. **Local Processing** — Data is filtered, deduplicated, and stored locally in SQLite.  
3. **Transmission** — The sensor automatically selects the most suitable communication interface:
   - **LoRa** (via `RAK3172`, `RAK4270`, `ASR6501`) for low-bandwidth, long-range links.
   - **Wi-Fi** for higher-speed uplink when available.
4. **Remote Configuration** — Downlinks allow remote updates to operational parameters or commands such as reboot and mode change.

---

# 📡 Sensor Operation Manual

---

## 🟢 1. Powering On the Sensor

> The sensor is powered internally by a **powerbank**. When completely off (no power supply), the powerbank may enter a protection mode and not provide current automatically — it must be woken up first.

**Procedure:**

1. Briefly connect the **charging cable** to the powerbank (~2–3 seconds) to wake it from protection mode.
2. Disconnect the charger.
3. The powerbank will supply power to the **Raspberry Pi**, which will boot automatically.
4. Wait until the system is fully operational.

---

## 🔴 2. Full Shutdown (Raspberry Pi + Powerbank)

> Use this method when you wish to power off **everything** — the Raspberry Pi and the powerbank itself.

**Procedure:**

1. Press and hold the **power button** on the sensor enclosure.
2. Wait until the **red LED** on the enclosure window **remains steady for ≈ 2 seconds**.
3. Release the button.
4. The Raspberry Pi will shut down and the powerbank will cut power.

---

## 🟡 3. Soft Shutdown (Raspberry Pi Only)

> Use this method when you wish to safely shut down the **Raspberry Pi**, while keeping the powerbank active (e.g., for a quick restart without losing power).

**Procedure:**

1. **Double-press** (double click) the **power button** on the enclosure.
2. The Raspberry Pi will initiate a safe shutdown process (*soft shutdown*).
3. The powerbank will remain active.

---

## ⚙️ 4. Provisioning / Network Configuration

> Follow this procedure when you need to configure the sensor to connect to a new Wi-Fi network or update its settings.

**Procedure:**

1. **Power on the sensor** as described in section [1](#-1-powering-on-the-sensor).
2. Wait **≈ 15 seconds** for the system to fully boot.
3. Press and hold the **reset button** for **≈ 10 seconds**.
4. The sensor will activate its own **Wi-Fi hotspot**.
5. On another computer or device, **connect to the sensor's hotspot**.
6. Open a browser and navigate to the following address:

   ```
   http://10.42.0.1:5000/
   ```

7. Fill in the desired settings, including the **Wi-Fi networks** the sensor should connect to.
8. Confirm and save the configuration.
9. The sensor will **restart automatically** and begin sensing with the new settings.



## Dependencies

This project relies on:
- Python ≥ 3.10  
- [PySerial](https://pypi.org/project/pyserial/) — for serial communication  
- SQLite3 — built-in Python module  
- Matplotlib — for local data visualization (optional)  
- Pytz — for timezone handling  

Install dependencies (if not already included):
```bash
pip install pyserial matplotlib pytz
