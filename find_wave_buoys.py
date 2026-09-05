#!/usr/bin/env python3
"""შავი ზღვის in-situ პლატფორმები, დალაგებული ფოთიდან მანძილით.

პროდუქტი: INSITU_BLK_PHYBGCWAV_DISCRETE_MYNRT_013_034

⚠ ᲛᲮᲝᲚᲝᲓ ᲫᲘᲔᲑᲐᲐ. კონსენსუსს, ზღვრებს და წონებს არ ეხება.

გაშვება:
    python3 find_wave_buoys.py            # რადიუსი 300 კმ
    python3 find_wave_buoys.py 100
"""

import math
import pathlib
import sys
import tempfile
from collections import Counter

import copernicusmarine as cm

POTI = (42.15, 41.67)
PRODUCT = "INSITU_BLK_PHYBGCWAV_DISCRETE_MYNRT_013_034"
DATASET = "cmems_obs-ins_blk_phybgcwav_mynrt_na_irr"

# ტალღის ცვლადები — SeaDataNet/Copernicus კოდები.
# ⚠ პირველ ვერსიაში მხოლოდ VHM0-ს ჯგუფი ეწერა და ფოთის სადგური
#   გამორჩა: მას VHZA და VZMX აქვს. სია ახლა სრულია.
WAVE_VARS = {
    "VHM0",   # significant wave height (spectral, Hm0)
    "VAVH",   # significant wave height (ზედა 1/3-ის საშუალო)
    "VGHS",   # significant wave height (სპექტრული პარტიცია)
    "VHZA",   # sea_surface_wave_mean_height — ᲡᲐᲨᲣᲐᲚᲝ სიმაღლე (≠ Hs)
    "VEMH",   # maximum wave height (სპექტრული შეფასება)
    "VZMX",   # ᲛᲐᲥᲡᲘᲛᲐᲚᲣᲠᲘ ტალღის სიმაღლე
    "VTPK", "VTM02", "VTZA", "VAVT", "VGTA",   # პერიოდები
    "VMDR", "VPED",                             # მიმართულებები
    "VEPK",
}

VAR_NOTE = {
    "VHM0": "მნიშვნელოვანი სიმაღლე Hm0",
    "VAVH": "მნიშვნელოვანი სიმაღლე (ზედა 1/3 საშუალო)",
    "VHZA": "ᲡᲐᲨᲣᲐᲚᲝ სიმაღლე (≈0.6×Hs)",
    "VZMX": "ᲛᲐᲥᲡᲘᲛᲐᲚᲣᲠᲘ სიმაღლე (≈1.6-2×Hs)",
    "VEMH": "მაქსიმალური სიმაღლე (სპექტრული)",
    "VTPK": "პიკური პერიოდი",
    "VTM02": "საშუალო პერიოდი",
    "VTZA": "ნულოვანი გადაკვეთის პერიოდი",
    "VMDR": "საშუალო მიმართულება",
}

# ქარი — მდგრადი ქარის კალიბრაციისთვისაც გამოსადეგი
WIND_VARS = {"WSPD", "WDIR", "GSPD", "WSPE", "WSPN"}


def haversine(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_platform_index(outdir):
    """index_platform.txt — პლატფორმების დონის ინდექსი.

    ფაილების ინდექსებს (index_latest / history / monthly) სჯობს:
    ერთი სტრიქონი = ერთი პლატფორმა, და შეიცავს last_date_observation-ს.
    """
    try:
        cm.get(
            dataset_id=DATASET,
            filter="*index_platform*",
            output_directory=str(outdir),
            no_directories=True,
            index_parts=True,
            overwrite=True,
            disable_progress_bar=True,
        )
    except Exception as e:
        print(f"index_platform ვერ ჩამოიტვირთა: {e}")
    return sorted(pathlib.Path(outdir).glob("*index_platform*"))


def parse_index(path):
    rows, header = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                cells = [c.strip() for c in line.lstrip("#").split(",")]
                if any("platform_code" in c.lower() for c in cells):
                    header = cells
                continue
            if header is None:
                continue
            parts = [c.strip() for c in line.split(",")]
            if len(parts) < len(header):
                parts += [""] * (len(header) - len(parts))
            rows.append(dict(zip(header, parts)))
    return header, rows


def main():
    radius = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    print(f"პროდუქტი: {PRODUCT}")
    print(f"ცენტრი: ფოთი {POTI[0]}, {POTI[1]} · რადიუსი: {radius:.0f} კმ\n")

    found = []
    with tempfile.TemporaryDirectory() as tmp:
        idx = fetch_platform_index(tmp)
        if not idx:
            sys.exit("index_platform.txt ვერ მოიძებნა.")

        for path in idx:
            header, rows = parse_index(path)
            print(f"[{path.name}] პლატფორმა: {len(rows)}")
            print(f"  სვეტები: {header}\n")
            for r in rows:
                lat = to_float(r.get("last_latitude_observation"))
                lon = to_float(r.get("last_longitude_observation"))
                if lat is None or lon is None:
                    continue
                d = haversine(POTI, (lat, lon))
                if d > radius:
                    continue
                params = set((r.get("parameters") or "").upper().replace(",", " ").split())
                found.append({
                    "d": d, "lat": lat, "lon": lon,
                    "code": r.get("platform_code", "?"),
                    "inst": (r.get("institution") or "?")[:34],
                    "last": r.get("last_date_observation", "?"),
                    "params": params,
                    "wave": params & WAVE_VARS,
                    "wind": params & WIND_VARS,
                })

    if not found:
        print(f"{radius:.0f} კმ-ში პლატფორმა არ არის. საკითხი იხურება.")
        return

    found.sort(key=lambda x: x["d"])
    waves = [f for f in found if f["wave"]]
    winds = [f for f in found if f["wind"]]
    print(f"პლატფორმა სულ: {len(found)} · ტალღით: {len(waves)} · ქარით: {len(winds)}\n")

    if waves:
        print("═══════ ᲢᲐᲚᲦᲘᲡ ᲛᲖᲝᲛᲘ ᲞᲚᲐᲢᲤᲝᲠᲛᲔᲑᲘ ═══════")
        for f in waves:
            print(f"\n🌊 {f['code']}  —  {f['d']:.1f} კმ  ({f['lat']:.3f}, {f['lon']:.3f})")
            print(f"   ორგანიზაცია: {f['inst']}")
            print(f"   ბოლო დაკვირვება: {f['last']}")
            for v in sorted(f["wave"]):
                print(f"     {v:6s} {VAR_NOTE.get(v, '')}")
            if f["wind"]:
                print(f"   ქარი: {' '.join(sorted(f['wind']))}")
    else:
        print("ტალღის მზომი პლატფორმა არ არის.")

    if winds:
        print("\n═══════ ᲥᲐᲠᲘᲡ ᲛᲖᲝᲛᲘ ᲞᲚᲐᲢᲤᲝᲠᲛᲔᲑᲘ ═══════")
        for f in winds:
            print(f"💨 {f['d']:6.1f} კმ  {f['code']:24s} ბოლო: {f['last']}  "
                  f"{' '.join(sorted(f['wind']))}")

    print("\n═══════ ᲣᲐᲮᲚᲝᲔᲡᲘ 10 ═══════")
    for f in found[:10]:
        mark = "🌊" if f["wave"] else ("💨" if f["wind"] else "  ")
        print(f"{mark} {f['d']:6.1f} კმ  {f['code']:24s} ბოლო: {f['last']:12s} "
              f"{' '.join(sorted(f['params']))[:70]}")

    print("\nტიპები:", dict(Counter(
        f["code"].split("_")[2] if len(f["code"].split("_")) > 2 else "?"
        for f in found)))


if __name__ == "__main__":
    main()
