#!/usr/bin/env python3
import time
import lgpio

CHIP = 4        # gpiochip4 = header GPIO no Raspberry Pi 5
LED = 21        # GPIO21 (pino físico 40)

h = lgpio.gpiochip_open(CHIP)

# Configura o GPIO como saída
lgpio.gpio_claim_output(h, LED)

print("A piscar LED no GPIO21... CTRL+C para parar")

try:
    while True:
        lgpio.gpio_write(h, LED, 1)   # LED ON
        time.sleep(0.5)
        lgpio.gpio_write(h, LED, 0)   # LED OFF
        time.sleep(0.5)
except KeyboardInterrupt:
    pass
finally:
    lgpio.gpiochip_close(h)
    print("GPIO fechado.")
