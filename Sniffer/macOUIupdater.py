# Script para atualizar ficheiro de texto com lista de OUIs de fabricantes do Wireshark de forma a ficar com OUIs de manufatores de dispositivos móveis

import urllib.request

url = "https://www.wireshark.org/download/automated/data/manuf"
output_file = "/home/kali/Desktop/Sniffer/wireshark-oui-list.txt"

print(f"Downloading from {url}...")
urllib.request.urlretrieve(url, output_file)
print("Download complete!")

MOBILE_MANUFACTURERS = set()
with open("/home/kali/Desktop/Sniffer/Mobile_device_manufacturers.txt") as file:
    MOBILE_MANUFACTURERS.update(line.strip().upper() for line in file)

f = open(r'/home/kali/Desktop/Sniffer/wireshark-oui-list.txt', "r+", encoding='utf-8')

new_file = []

for line in f:
  if line.startswith('#') or not line.strip():
    continue

  splits = line.split('\t')

  splits_twodots = splits[0].split(':')

  if( len(splits_twodots) < 4 ):  # 24 bits

    if(any(mobile_manuf in splits[2].strip().upper() for mobile_manuf in MOBILE_MANUFACTURERS)):
    
      new_file.append(splits[0].strip() + '\t' + splits[2].strip() + '\n')

with open(r"/home/kali/Desktop/Sniffer/wireshark-oui-list.txt", "w+", encoding='utf-8') as f:
  for i in new_file:
    f.write(i)