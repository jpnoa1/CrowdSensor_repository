# Crowd_Detection_STToolkit

This repository contains the source code and configuration files for a **crowd density monitoring system** designed to operate autonomously using **LoRaWAN** and/or **Wi-Fi** communication.  
It integrates configurable sensor nodes capable of detecting nearby mobile devices, managing data locally, and transmitting aggregated information to a central server for analysis.

---

## 🧠 Project Overview

The system is designed for **real-time crowd estimation** in public spaces using device fingerprinting and signal detection techniques.  
It operates under a flexible communication architecture that allows dynamic switching between **Wi-Fi** and **LoRaWAN** networks depending on connectivity and bandwidth availability.

Core features include:
- Automatic device detection and counting.
- Configurable data retention and upload periodicity.
- Auto-healing and reconfiguration via downlinks.
- Local data persistence using SQLite.
- Full operational automation through `cron` jobs.

---

## ⚙️ System Architecture

Each sensor node consists of:
- **Main scripts** responsible for data collection and transmission (`sendCrowdingData.py`, `sendSensorLocation.py`).
- **Configuration handlers** (`sensorConfiguration.py`, `sensorCheckConfig.py`, `sensorFunctions.py`).
- **Communication management** via the custom library [`swARM_at_custom`](./swARM_at_custom), adapted from `swARM_at` to provide LoRa AT-command control (RAK3172, RAK4270, ASR6501 modules).
- **Data persistence** (`dataRetentionManager.py`) and **integrity checking** mechanisms.
- **Automated startup and monitoring** through scheduled `cron` tasks defined in `cronjobs_configured.txt`.

---

## 🛰 Communication Workflow

1. **Data Collection** — Device probes are captured via Wi-Fi sniffing or BLE scanning.  
2. **Local Processing** — Data is filtered, deduplicated, and stored locally in SQLite.  
3. **Transmission** — The sensor automatically selects the most suitable communication interface:
   - **LoRa** (via `RAK3172`, `RAK4270`, `ASR6501`) for low-bandwidth, long-range links.
   - **Wi-Fi** for higher-speed uplink when available.
4. **Remote Configuration** — Downlinks allow remote updates to operational parameters or commands such as reboot and mode change.

---

## 🧰 Dependencies

This project relies on:
- Python ≥ 3.10  
- [PySerial](https://pypi.org/project/pyserial/) — for serial communication  
- SQLite3 — built-in Python module  
- Matplotlib — for local data visualization (optional)  
- Pytz — for timezone handling  

Install dependencies (if not already included):
```bash
pip install pyserial matplotlib pytz
