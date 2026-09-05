#!/usr/bin/env python3
"""შავი ზღვის in-situ პლატფორმები, დალაგებული ფოთიდან მანძილით.

პროდუქტი: INSITU_BLK_PHYBGCWAV_DISCRETE_MYNRT_013_034
(Black Sea NRT in-situ, ხარისხის კონტროლგავლილი, საათობრივი განახლება,
მიღებიდან საშუალოდ 24-48 სთ დაგვიანებით)

მიზანი — ერთი კითხვის დახურვა: არის თუ არა ფოთის გონივრულ მანძილზე
ტალღის მზომი პლატფორმა. თუ არა, საკითხი იხურება და დროს არ ვკარგავთ.

⚠ ეს ᲛᲮᲝᲚᲝᲓ ᲫᲘᲔᲑᲐᲐ. კონსენსუსს, ზღვრებს და წონებს არ ეხება.
   in-situ 24-48 საათით იგვიანებს — ოპერაციულ ჩვენებაში ვერ შევა.
   ღირებულება კალიბრაციაშია: MTA-ს გარდა მეორე გაზომილი ეტალონი.

გაშვება:
    pip install copernicusmarine
    export COPERNICUSMARINE_SERVICE_USERNAME=...
    export COPERNICUSMARINE_SERVICE_PASSWORD=...
    python3 find_wave_buoys.py            # ნაგულისხმევი რადიუსი 300 კმ
    python3 find_wave_buoys.py 500        # სხვა რადიუსი
"""

import math
import pathlib
import sys
import tempfile

import copernicusmarine as cm

POTI = (42.15, 41.67)
PRODUCT = "INSITU_BLK_PHYBGCWAV_DISCRETE_MYNRT_013_034"

# ტალღის ცვლადები CF/Copernicus-ის აღნიშვნით
WAVE_VARS = {"VHM0", "VTM02", "VTPK", "VMDR", "VAVH", "VTM10"}


def haversine(a, b):
    """მანძილი კმ-ში ორ (lat, lon) წყვილს შორის."""
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def fetch_index(outdir):
    """ინდექს-ფაილების ჩამოტვირთვა.

    in-situ პროდუქტებში პლატფორმების ჩამონათვალი index_*.txt ფაილებშია.
    სახელები ვერსიებს შორის იცვლებოდა, ამიტომ რამდენიმეს ვცდით.
    """
    got = []
    for pattern in ("*index_latest*", "*index_history*", "*index_monthly*"):
        try:
            cm.get(
                dataset_id=f"cmems_obs-ins_blk_phybgcwav_mynrt_na_irr",
                filter=pattern,
                output_directory=str(outdir),
                no_directories=True,
                index_parts=True,
                overwrite=True,
                disable_progress_bar=True,
            )
        except Exception as e:
            print(f"  [{pattern}] ვერ ჩამოიტვირთა: {e}")
            continue
        got += list(pathlib.Path(outdir).glob("*index*"))
    return sorted(set(got))


def parse_index(path):
    """index ფაილი CSV-ია, კომენტარები '#'-ით. აბრუნებს row-dict-ების სიას."""
    rows, header = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                # ბოლო კომენტარი ჩვეულებრივ სვეტების სათაურია
                header = [c.strip() for c in line.lstrip("#").split(",")]
                continue
            if header is None:
                continue
            parts = [c.strip() for c in line.split(",")]
            if len(parts) < len(header):
                parts += [""] * (len(header) - len(parts))
            rows.append(dict(zip(header, parts)))
    return rows


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    radius = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    print(f"პროდუქტი: {PRODUCT}")
    print(f"ცენტრი: ფოთი {POTI[0]}, {POTI[1]} · რადიუსი: {radius:.0f} კმ\n")

    with tempfile.TemporaryDirectory() as tmp:
        idx = fetch_index(tmp)
        if not idx:
            sys.exit("ინდექს-ფაილი ვერ მოიძებნა — იხ. შენიშვნა ქვემოთ.")

        seen, hits = set(), []
        for path in idx:
            for r in parse_index(path):
                name = r.get("file_name") or r.get("catalog_id") or ""
                if name in seen:
                    continue
                seen.add(name)

                lat = to_float(r.get("geospatial_lat_min"))
                lon = to_float(r.get("geospatial_lon_min"))
                if lat is None or lon is None:
                    continue

                params = (r.get("parameters") or "").upper()
                has_wave = any(v in params for v in WAVE_VARS)

                d = haversine(POTI, (lat, lon))
                if d <= radius:
                    hits.append((d, lat, lon, has_wave, r))

    if not hits:
        print(f"{radius:.0f} კმ-ში პლატფორმა არ არის. საკითხი იხურება.")
        return

    hits.sort(key=lambda x: x[0])
    print(f"ნაპოვნია {len(hits)} პლატფორმა "
          f"({sum(1 for h in hits if h[3])} ტალღის მონაცემით):\n")
    for d, lat, lon, has_wave, r in hits:
        mark = "🌊" if has_wave else "  "
        name = (r.get("file_name") or "?").split("/")[-1]
        print(f"{mark} {d:6.1f} კმ  {lat:7.3f}, {lon:7.3f}  "
              f"{r.get('provider','?')[:28]:28s}  {name}")
        if has_wave:
            print(f"              ბოლო მონაცემი: {r.get('time_coverage_end','?')}  "
                  f"რეჟიმი: {r.get('data_mode','?')}")

    print("\n🌊 = ტალღის პარამეტრი დევს. სწორედ ესენი გვაინტერესებს.")
    print("თუ ასეთი არცერთია, in-situ ამ რეგიონში ტალღას არ ზომავს.")


if __name__ == "__main__":
    main()
