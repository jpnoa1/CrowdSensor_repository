import os
import time

chans = [1,6,11]
wait = 0.5
i = 0

while True:
    os.system("sudo iwconfig wlan1 channel " + str(chans[i]))
    i = (i + 1) % len(chans)
    time.sleep(wait)