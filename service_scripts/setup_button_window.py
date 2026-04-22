#!/usr/bin/env python3
import time
import subprocess
import lgpio

PIN = 12       # Button GPIO
CHIP = 4       # gpiochip4 on Raspberry Pi 5
HOLD_TIME = 5         # Must press 5 seconds continuously
WINDOW_TIME = 40       # Only active during first 40 seconds of boot

HOTSPOT_SSID = "CrowdSensor-Setup"
HOTSPOT_PASSWORD = "kalikali"

def wait_for_network_manager(timeout=60):
    """Wait for NetworkManager to be ready, return True if ready within timeout"""
    print("[SetupButton] Waiting for NetworkManager to be ready...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "NetworkManager.service"],
                capture_output=True, text=True, timeout=2
            )
            if result.stdout.strip() == "active":
                print("[SetupButton] NetworkManager is ready.")
                return True
        except Exception as e:
            pass
        time.sleep(1)
    print("[SetupButton] ERROR: NetworkManager not ready after timeout!")
    return False

print("[SetupButton] Boot window started (40 seconds).")
print("[SetupButton] Hold button for 5 seconds to enter configuration mode.")

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
                
                # Wait for NetworkManager to be ready
                if not wait_for_network_manager(timeout=60):
                    print("[SetupButton] Cannot start hotspot without NetworkManager. Exiting.")
                    break
                
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
