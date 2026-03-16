# CrowdSensor – System Services Configuration

This document records the custom systemd services used by the CrowdSensor
device so the configuration can be replicated on another system.

System: Raspberry Pi 5
OS used during development: Kali Linux

--------------------------------------------------

SERVICE: sensor-setup.service

Location:
/etc/systemd/system/sensor-setup.service

Purpose:
Launches the setup mode when the sensor configuration file does not exist.
Creates a Wi-Fi hotspot and runs the local configuration web interface.

Configuration:

[Unit]
Description=Setup mode: Wi-Fi Hotspot + Local Configuration Website
ConditionPathExists=!/home/kali/Desktop/sensor-config-site/data/sensor_config.toml
After=network-online.target

[Service]
Type=simple
User=kali
Group=kali
WorkingDirectory=/home/kali/Desktop/sensor-config-site

Environment=SSID=CrowdSensor-Setup
Environment=PASSWORD=kalikali

ExecStartPre=/usr/bin/nmcli dev wifi hotspot ifname wlan0 ssid ${SSID} password ${PASSWORD}
ExecStart=/usr/bin/python3 /home/kali/Desktop/sensor-config-site/app.py

ExecStopPost=/usr/bin/nmcli device disconnect wlan0 || true
ExecStopPost=/usr/bin/nmcli device up wlan0 || true

Restart=on-failure

[Install]
WantedBy=multi-user.target

--------------------------------------------------

SERVICE: setup-button-window.service

Location:
/etc/systemd/system/setup-button-window.service

Purpose:
Monitors the physical setup button during the early boot phase.

Configuration:

[Unit]
Description=Setup Button Window (only active during boot)
After=sysinit.target local-fs.target
Before=network-online.target
DefaultDependencies=no

[Service]
Type=simple
User=root
Group=root
WorkingDirectory=/home/kali/Desktop/scripts

ExecStart=/usr/bin/python3 /home/kali/Desktop/scripts/setup_button_window.py

Restart=no

[Install]
WantedBy=multi-user.target

--------------------------------------------------

ENABLING SERVICES

To enable both services:

sudo systemctl daemon-reload
sudo systemctl enable sensor-setup.service
sudo systemctl enable setup-button-window.service

--------------------------------------------------

SERVICE FILE LOCATION

Custom services are stored in:

/etc/systemd/system/

--------------------------------------------------