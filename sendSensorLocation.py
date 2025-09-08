import sqlite3
import datetime as dt
import pytz
import matplotlib.pyplot as plt; plt.rcdefaults()
import os
import json

from sensorFunctions import *


# Read sensor configuration from database

try:
    connwifi= sqlite3.connect('/home/kali/Desktop/DB/SensorConfiguration.db' , timeout=30)
    cwifi = connwifi.cursor()

    sensor_configuration = cwifi.execute("""SELECT * FROM SensorConfiguration""").fetchall()

    #Sensor configuration
    if len(sensor_configuration) != 0:
        sensor_UUID = sensor_configuration[0][0]
        sensor_name = sensor_configuration[0][1]
        latitude = sensor_configuration[0][2]
        longitude = sensor_configuration[0][3]
        cloud_ip_addr = sensor_configuration[0][6]
        influx_org = sensor_configuration[0][7]
        influx_bucket = sensor_configuration[0][8]
        influx_token = sensor_configuration[0][9]
        uploadTechnology = sensor_configuration[0][12]

        if uploadTechnology.lower() == "wifi":
            ip_address = cwifi.execute("""SELECT IP_Address FROM SensorCommunication""").fetchone()[0]


    else:
        print("Failed to read sensor configuration from local database. Please make sure to \nconfigure a sensor configuration by running the 'sensorConfiguration.py' script first.")
        exit(0)

except sqlite3.Error as error:
    print("Failed to read sensor configuration from local database.")
    exit(0)


dataAtual=dt.datetime.now(pytz.utc).replace(tzinfo=None)

print(latitude, longitude)
location = {
"latitude": latitude,
"longitude": longitude
}

json_location = json.dumps(location)

# Send sensor location to InfluxDB
if uploadTechnology.lower() == "wifi":
    #publish_mqtt_message(json_location, f"mqtt/wifi/sensorLocation/{influxdb_bucket}/{ip_address}/{sensorName}")

    cmd =f"curl -i   --request POST \"http://{cloud_ip_addr}:8086/api/v2/write?org={influx_org}&bucket={influx_bucket}&precision=s\"  \
                     --header \"Authorization: Token {influx_token}\"  \
                     --header \"Content-Type: text/plain; charset=utf-8\"  \
                     --header \"Accept: application/json\" \
                     --data-binary 'sensorLocation,sensor_UUID={sensor_UUID},sensor_name={sensor_name} latitude={latitude},longitude={longitude} {str(int( (dataAtual - dt.datetime(1970,1,1)).total_seconds()))}'"
    os.system(cmd)

    print(f"Location '({latitude},{longitude})' was sent to the cloud server for sensor '{sensor_name}'.")

elif uploadTechnology.lower() == "lora":

    serialPort = serial.Serial("/dev/ttyUSB0", 115200, timeout=2)
    LoRaWAN = asr6501(serialPort, logging.DEBUG)

    try:
        # 2) Restaurar config e garantir join
        LoRaWAN.restoreMacConfiguration()
        
        print(str(LoRaWAN.getStatus())+"este e o estado da rede")
        if LoRaWAN.getStatus() == 5:  # Not joined
            print("A ligar à TTN via OTAA…")
            if not LoRaWAN.join():
                print("Falha no join TTN.")
                serialPort.close()
                exit(1)
            LoRaWAN.saveMacConfiguration()
            print("Join concluído.")

        # 3) Preparar payload curta em texto
        #    fPort 3 para mensagens de localização
        LoRaWAN.setApplicationPort(3)
        time.sleep(0.5)
        # Formato CSV curto: "L,<lat>,<lon>,<uuid>"
        # Mantém compacto: 5 casas decimais ~1.1 m
        payload = f"L,{float(latitude):.5f},{float(longitude):.5f},{sensor_UUID}"
        # Se quiseres incluir nome: cuidado com comprimento (opcional)
        # payload = f"L,{latitude:.5f},{longitude:.5f},{sensor_UUID},{sensor_name[:12]}"

        print(f"A enviar via LoRa: {payload}")
        sent = LoRaWAN.sendPayload(payload, confirm=0, nbtrials=8)
        if not sent:
            print("Falha a enviar via LoRa; vou tentar re-join e terminar.")
            LoRaWAN.join()
        else:
            print("Localização enviada via LoRa.")
        
    except Exception as e:
        print(f"Erro durante envio via LoRa: {e}")
    finally:
        try:
            serialPort.close()
        except:
            pass