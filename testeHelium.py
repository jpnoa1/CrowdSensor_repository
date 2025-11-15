import time
import toml
from swARM_at_custom.swARM_at.RAK3172 import RAK3172


PORT = "/dev/ttyAMA0"   
BAUD = 115200
PAYLOAD = "48656C6C6F"  # "Hello" 


message = f"C,14"

payload_hex = message.encode().hex()

rak = RAK3172(port=PORT, baud_rate=BAUD)
rak.connect()


print("\n--- A configurar módulo para Helium ---")


rak.set_dev_eui("AC1F09FFFE15A753")

rak.set_app_eui("AC1F09FFF8683172")

rak.set_app_key("AC1F09FFFE15A753AC1F09FFF8683172")




print(" Parâmetros Helium aplicados")

# === JOIN À REDE ===
print("\n A tentar fazer join à Helium...")

if rak.send_lorawan_data(2, payload_hex):
    print(" Mensagem enviada com sucesso!")
else:
        print("Falha ao enviar a mensagem.")


rak.disconnect()
