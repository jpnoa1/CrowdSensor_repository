#!/usr/bin/env python3
import time
import subprocess
import lgpio

PIN = 26       # Button GPIO
CHIP = 4       # gpiochip4 on Raspberry Pi 5
HOLD_TIME = 10         # Must press 10 seconds continuously
WINDOW_TIME = 40       # Only active during first 40 seconds of boot

HOTSPOT_SSID = "CrowdSensor-Setup"
HOTSPOT_PASSWORD = "kalikali"

print("[SetupButton] Boot window started (40 seconds).")
print("[SetupButton] Hold button for 10 seconds to enter configuration mode.")

# Open GPIO chip
h = lgpio.gpiochip_open(CHIP)
lgpio.gpio_claim_input(h, PIN, lgpio.SET_PULL_UP)

pressed_since = None
window_start = time.time()

try:
    while True:

        # End of the 40-second setup window
        if time.time() - window_start > WINDOW_TIME:
            print("[SetupButton] Window expired. No setup triggered.")
            break

        val = lgpio.gpio_read(h, PIN)

        if val == 0:  # Button pressed
            if pressed_since is None:
                pressed_since = time.time()

            held = time.time() - pressed_since
            print(f"[Button] Held {held:.1f}s", end="\r")

            if held >= HOLD_TIME:
                print("\n[SetupButton] Trigger detected!")
                print("[SetupButton] Enabling hotspot...")

                # Start hotspot (NetworkManager)
                subprocess.run([
                    "nmcli", "dev", "wifi", "hotspot",
                    "ifname", "wlan0",
                    "ssid", HOTSPOT_SSID,
                    "password", HOTSPOT_PASSWORD
                ], check=False)

                print("[SetupButton] Hotspot active.")
                print("[SetupButton] Launching configuration app...")
                time.sleep(3)
                # Start Flask app (non-blocking)
                subprocess.run([
                    "sudo",
                    "python3",
                    "/home/kali/Desktop/sensor-config-site/app.py"
                ])

                print("[SetupButton] Web UI running on 10.42.0.1:5000")
                
                break

        else:
            pressed_since = None

        time.sleep(0.1)

finally:
    lgpio.gpiochip_close(h)
    print("\n[SetupButton] GPIO closed.")
