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
    """index ფაილი CSV-ია, კომენტარები '#'-ით.

    ⚠ ჩანაწერი = ᲤᲐᲘᲚᲘ, არა პლატფორმა. ერთ ბუის თვეების მიხედვით
    ათობით ფაილი აქვს (GL_PR_PF_6903240_202112.nc, ..._202203.nc).
    ამიტომ dedup პლატფორმის კოდზე ხდება და არა ფაილის სახელზე.
    """
    rows, header = [], None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                cells = [c.strip() for c in line.lstrip("#").split(",")]
                # სათაურად ვთვლით მხოლოდ იმ კომენტარს, სადაც ცნობადი
                # სვეტებია — თორემ ნებისმიერი აღწერითი ხაზი გადააწერდა.
                if any("lat" in c.lower() for c in cells):
                    header = cells
                continue
            if header is None:
                continue
            parts = [c.strip() for c in line.split(",")]
            if len(parts) < len(header):
                parts += [""] * (len(header) - len(parts))
            rows.append(dict(zip(header, parts)))
    return header, rows


def col(row, *names):
    """სვეტის მოძებნა სახელის ნაწილით, რეგისტრის გარეშე."""
    for k, v in row.items():
        kl = (k or "").lower()
        if any(n in kl for n in names):
            if v not in (None, ""):
                return v
    return ""


def platform_id(fname):
    """GL_PR_PF_6903240_202112.nc → GL_PR_PF_6903240"""
    base = fname.split("/")[-1].replace(".nc", "")
    bits = base.split("_")
    if bits and bits[-1].isdigit() and len(bits[-1]) == 6:   # YYYYMM
        bits = bits[:-1]
    return "_".join(bits)


def to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    radius = float(sys.argv[1]) if len(sys.argv) > 1 else 300.0
    print(f"პროდუქტი: {PRODUCT}")
    print(f"ცენტრი: ფოთი {POTI[0]}, {POTI[1]} · რადიუსი: {radius:.0f} კმ\n")

    plats = {}
    with tempfile.TemporaryDirectory() as tmp:
        idx = fetch_index(tmp)
        if not idx:
            sys.exit("ინდექს-ფაილი ვერ მოიძებნა.")

        for path in idx:
            header, rows = parse_index(path)
            print(f"[{pathlib.Path(path).name}] სტრიქონი: {len(rows)}")
            print(f"  ამოცნობილი სვეტები: {header}\n")
            for r in rows:
                fname = col(r, "file_name", "file") or col(r, "catalog_id")
                if not fname:
                    continue
                lat = to_float(col(r, "lat_min", "latitude", "lat"))
                lon = to_float(col(r, "lon_min", "longitude", "lon"))
                if lat is None or lon is None:
                    continue
                d = haversine(POTI, (lat, lon))
                if d > radius:
                    continue

                pid = platform_id(fname)
                params = (col(r, "parameter") or "").upper()
                has_wave = any(v in params for v in WAVE_VARS)
                end = col(r, "time_coverage_end", "date_update")

                cur = plats.get(pid)
                if cur is None or has_wave and not cur["wave"] or end > cur["end"]:
                    plats[pid] = {"d": d, "lat": lat, "lon": lon,
                                  "wave": has_wave or (cur or {}).get("wave", False),
                                  "end": max(end, (cur or {}).get("end", "")),
                                  "params": params or (cur or {}).get("params", ""),
                                  "type": pid.split("_")[2] if len(pid.split("_")) > 2 else "?"}

    if not plats:
        print(f"{radius:.0f} კმ-ში პლატფორმა არ არის. საკითხი იხურება.")
        return

    waves = {k: v for k, v in plats.items() if v["wave"]}
    print(f"პლატფორმა სულ: {len(plats)} · ტალღის მონაცემით: {len(waves)}\n")

    if waves:
        print("═══ ტალღის მზომი პლატფორმები ═══")
        for pid, v in sorted(waves.items(), key=lambda x: x[1]["d"]):
            print(f"🌊 {v['d']:6.1f} კმ  {v['lat']:7.3f}, {v['lon']:7.3f}  {pid}")
            print(f"           ბოლო: {v['end']}  ცვლადები: {v['params'][:90]}")
    else:
        print("ტალღის მზომი პლატფორმა არ არის.")
        print("ტიპების განაწილება (ბოლო სვეტი პლატფორმის კოდიდან):")
        from collections import Counter
        for t, n in Counter(v["type"] for v in plats.values()).most_common():
            print(f"  {t}: {n}")
        print("\nMO = ღუზაზე მდგარი (ჩვეულებრივ ტალღას ზომავს)")
        print("PF = პროფილირებადი (ტემპერატურა/მარილიანობა, ტალღა არა)")
        near = sorted(plats.items(), key=lambda x: x[1]["d"])[:5]
        print("\nუახლოესი 5 პლატფორმა:")
        for pid, v in near:
            print(f"  {v['d']:6.1f} კმ  {pid}  ცვლადები: {v['params'][:70]}")


if __name__ == "__main__":
    main()
