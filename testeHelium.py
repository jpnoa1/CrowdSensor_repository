import time
import toml
from swARM_at_custom.swARM_at.RAK3172 import RAK3172


PORT = "/dev/ttyUSB0"   
BAUD = 115200
PAYLOAD = "48656C6C6F"  # "Hello" 


message = f"C,14"

payload_hex = message.encode().hex()

rak = RAK3172(port=PORT, baud_rate=BAUD)
rak.connect()


print("\n--- A configurar módulo para Helium ---")


rak.set_dev_eui("0b8b1bd48d5e9373")

rak.set_app_eui("30ba2a7487ac4225")

rak.set_app_key("447909aeae9d7fd18bb18c4c688dfaf5")


print(" Parâmetros Helium aplicados")

# === JOIN À REDE ===
print("\n A tentar fazer join à Helium...")

if rak.send_lorawan_data(2, payload_hex):
    print(" Mensagem enviada com sucesso!")
else:
        print("Falha ao enviar a mensagem.")


rak.disconnect()
