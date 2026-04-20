import datetime
import csv
import os

log_file = '/home/kali/Desktop/powerbank_log_autonomia_detection_debian_powerbank_only_lora.csv'

# Check if file exists, if not, create and add header
if not os.path.exists(log_file):
    with open(log_file, 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(['Timestamp', 'Voltage (V)', 'Capacity (%)', 'Status'])

# Append the current timestamp with placeholder values
with open(log_file, 'a', newline='') as file:
    writer = csv.writer(file)
    now = datetime.datetime.now()
    writer.writerow([now.strftime("%H:%M:%S"), '', '', 'OK'])