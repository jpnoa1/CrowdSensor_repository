
# Script para alterar ficheiro de texto com lista de OUIs de fabricantes do Wireshark de forma a ficar com a formatacao correta para ser lido pelo airodump-ng

# Passos:
# 1 - Ir ao URL (https://gitlab.com/wireshark/wireshark/-/raw/master/manuf)
# 2 - Selectionar todo o conteudo (CTR + A)
# 3 - Copiar e guardar num ficheiro de texto
# 4 - Executar este script para deixa-lo com a formatacao correta para ser lido pelo airodump-ng

import sys
import os

cmd ='curl "https://www.wireshark.org/download/automated/data/manuf" > /home/kali/Desktop/wireshark-oui-list.txt'
print(cmd)
os.system(cmd)

# fairphone_ouis = {"54:08:3B:C0", "E8:78:29:C0", "F0:12:04:C0"}

MOBILE_MANUFACTURERS = set()
with open("/home/kali/Desktop/Mobile_device_manufacturers.txt") as file:
    MOBILE_MANUFACTURERS.update(line.strip().upper() for line in file)

f = open(r'/home/kali/Desktop/wireshark-oui-list.txt', "r+", encoding='utf-8')

new_file = []

for i in range(10):
  next(f)

for line in f:
  splits = line.split('\t')

  splits_twodots = splits[0].split(':')

  if( len(splits_twodots) < 4 ):  # 24 bits

    if(any(mobile_manuf in splits[2].strip().upper() for mobile_manuf in MOBILE_MANUFACTURERS)):
    
      new_file.append(splits[0].strip() + '\t' + splits[2].strip() + '\n')

  # elif( splits[0][:11] in fairphone_ouis ):                           # FairPhone 28 bits
  
  #   new_file.append(splits[0][:11] + '\t' + splits[2].strip() + '\n')


with open(r"/home/kali/Desktop/wireshark-oui-list.txt", "w+", encoding='utf-8') as f:
  for i in new_file:
    f.write(i)