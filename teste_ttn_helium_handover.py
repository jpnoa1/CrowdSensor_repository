from swARM_at_custom.swARM_at.RAK3172 import RAK3172

PORT = "/dev/ttyAMA0"
BAUD = 115200

rak = RAK3172(port=PORT, baud_rate=BAUD)
rak.connect()

# ===============================
# ESCOLHE A REDE AQUI:
rede = "ttn"   # "ttn" ou "helium"
# ===============================

if rede == "ttn":
    rak.set_dev_eui("AC1F09FFFE15A753")
    rak.set_app_eui("AC1F09FFF8683172")
    rak.set_app_key("AC1F09FFFE15A753AC1F09FFF8683172")

elif rede == "helium":
    rak.set_dev_eui("0b8b1bd48d5e9373")
    rak.set_app_eui("30ba2a7487ac4225")
    rak.set_app_key("447909aeae9d7fd18bb18c4c688dfaf5")

else:
    print("Rede inválida.")
    rak.disconnect()
    exit()

print(f"[INFO] Configuração aplicada para {rede.upper()}")
print(rak.get_dev_eui()+", "+rak.get_app_eui()+", "+rak.get_app_key()   )
# ===============================
# JOIN
# ===============================
print("[INFO] A fazer JOIN...")
resp=rak.join_network(1,0,8,8)
#print("JOIN RESPONSE:", resp)

status = rak.check_join_status()
print("JOIN STATUS:", status)

# ===============================
# ENVIO DA MENSAGEM "C,14"
# ===============================
mensagem = "C,14"
payload_hex = mensagem.encode().hex()

print("[INFO] A enviar payload:", mensagem, "→", payload_hex)

#result = rak.send_lorawan_data(2, payload_hex)
#print("RESULTADO DO ENVIO:", result)

rak.disconnect()
print("[INFO] Script concluído.")
