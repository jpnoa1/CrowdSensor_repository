import time
from sensorFunctions import try_get_boot_gps_position

print("[TEST] A iniciar teste de GPS...")

gps_position = try_get_boot_gps_position(
    max_wait_sec=15,
    warmup_sec=5,
    min_good_samples=4,
    eph_max=12.0
)

if gps_position is not None:
    gps_lat, gps_lon, gps_quality = gps_position
    print("[TEST] GPS obtido com sucesso.")
    print(f"Latitude: {gps_lat}")
    print(f"Longitude: {gps_lon}")
    print(f"Qualidade: {gps_quality}")
else:
    print("[TEST] Não foi possível obter posição GPS.")
