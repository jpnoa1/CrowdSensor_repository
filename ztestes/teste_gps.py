#!/usr/bin/env python3
import time
import math
import gps
import numpy as np

def get_best_static_position(
    warmup=5,
    window=20,
    min_good_samples=5,
    eph_max=8.0
):
    """
    Lê amostras do gpsd e calcula uma posição estática robusta.
    Usa amostras 'boas' se existirem suficientes, caso contrário faz fallback.
    """

    print("[*] A ligar ao gpsd...")
    session = gps.gps(mode=gps.WATCH_ENABLE | gps.WATCH_NEWSTYLE)

    # Warm-up
    print(f"[*] Warm-up de {warmup} segundos...")
    t0 = time.time()
    while time.time() - t0 < warmup:
        try:
            session.next()
        except Exception:
            pass
        time.sleep(0.2)

    print(f"[*] A recolher amostras durante {window} segundos...")
    all_samples = []
    good_samples = []

    t0 = time.time()
    while time.time() - t0 < window:
        try:
            report = session.next()

            if report["class"] != "TPV":
                continue

            mode = getattr(report, "mode", 0)
            lat = getattr(report, "lat", None)
            lon = getattr(report, "lon", None)
            eph = getattr(report, "eph", None)

            if mode < 2 or lat is None or lon is None:
                continue

            all_samples.append((lat, lon, eph))

            if eph is not None and eph <= eph_max:
                good_samples.append((lat, lon, eph))

            eph_txt = f"{eph:.2f} m" if eph is not None else "N/A"
            print(f"  lat={lat:.7f} lon={lon:.7f} eph={eph_txt}")

        except KeyboardInterrupt:
            print("\n[!] Interrompido pelo utilizador")
            break
        except Exception:
            pass

        time.sleep(0.25)

    if len(good_samples) >= min_good_samples:
        chosen = good_samples
        quality = "GOOD"
    elif len(all_samples) >= 3:
        chosen = all_samples
        quality = "FALLBACK"
    else:
        print("[ERRO] Não há amostras suficientes para estimar posição.")
        return None

    lats = np.array([s[0] for s in chosen])
    lons = np.array([s[1] for s in chosen])

    # Centro robusto inicial
    lat_med = np.median(lats)
    lon_med = np.median(lons)

    # Converter diferenças para metros (aproximação local)
    dx = (lons - lon_med) * 111320 * math.cos(math.radians(lat_med))
    dy = (lats - lat_med) * 110540
    dist = np.sqrt(dx**2 + dy**2)

    # Remoção simples de outliers
    mad = np.median(np.abs(dist - np.median(dist)))

    if mad > 0:
        mask = dist < max(5.0, 3 * mad)
    else:
        mask = np.ones_like(dist, dtype=bool)

    lats_f = lats[mask]
    lons_f = lons[mask]

    lat_final = float(np.mean(lats_f))
    lon_final = float(np.mean(lons_f))

    print("\n========== RESULTADO ==========")
    print(f"Amostras totais:      {len(all_samples)}")
    print(f"Amostras boas:        {len(good_samples)}")
    print(f"Amostras utilizadas:  {len(lats_f)}")
    print(f"Qualidade:            {quality}")
    print(f"Latitude final:       {lat_final:.7f}")
    print(f"Longitude final:      {lon_final:.7f}")
    print("================================")

    return lat_final, lon_final, quality


if __name__ == "__main__":
    result = get_best_static_position(
        warmup=5,
        window=20,
        min_good_samples=5,
        eph_max=8.0
    )

    if result is None:
        print("[DONE] Sem posição final.")
    else:
        lat, lon, quality = result
        print(f"[DONE] {lat:.7f}, {lon:.7f} ({quality})")