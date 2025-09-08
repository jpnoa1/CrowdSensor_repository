
# Script para alterar ficheiro de texto com lista de OUIs de fabricantes do Wireshark de forma a ficar com a formatacao correta para ser lido pelo airodump-ng

# Passos:
# 1 - Ir ao URL (https://gitlab.com/wireshark/wireshark/-/raw/master/manuf)
# 2 - Selectionar todo o conteudo (CTR + A)
# 3 - Copiar e guardar num ficheiro de texto
# 4 - Executar este script para deixa-lo com a formatacao correta para ser lido pelo airodump-ng

import sys
import os

cmd ='curl "https://www.wireshark.org/download/automated/data/manuf" > wireshark-oui-list.txt'
print(cmd)
os.system(cmd)


f = open(r'/home/kali/Desktop/wireshark-oui-list.txt', "r+", encoding='utf-8')

new_file = []

for line in f:
  splits = line.split('\t')

  splits_twodots = splits[0].split(':')

  if( len(splits_twodots) < 4 ):

    new_mac = splits[0].replace(':','-')

    if( len(splits) == 2 ):
        new_file.append(new_mac + '   (hex)\t' + splits[1])
       

    if( len(splits) == 3 ):
        new_file.append(new_mac + '   (hex)\t' + splits[2])
    

    if( len(splits) == 4 ):
        if( splits[2] == ""):
            new_file.append(new_mac + '   (hex)\t' + splits[3])
        else:
           new_file.append(new_mac + '   (hex)\t' + splits[2] + '\n')
  

with open(r"/home/kali/Desktop/wireshark-oui-list.txt", "w+", encoding='utf-8') as f:
  for i in new_file:
    f.write(i)



          