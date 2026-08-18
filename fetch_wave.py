#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_wave.py — Copernicus Marine BLKSEA ტალღის მაღალი გარჩევადობის წყარო.

პროდუქტი: BLKSEA_ANALYSISFORECAST_WAV_007_003
Dataset:  cmems_mod_blk_wav_anfc_2.5km_PT1H-i
მოდელი:   WAM Cycle 6, 2.5 კმ (1/40°), არაღრმა წყლის ვერსია

რატომ სჭირდება პორტალს: მოდელი ითვალისწინებს სიღრმის რეფრაქციას, ტალღის
მოტეხვას და სატელიტური SWH/ქარის ასიმილაციას. სწორედ ეს ფიზიკა აკლია
Open-Meteo Marine-სა და Stormglass-ს, რომელთა ბადეც 9–50 კმ-ია და ღრმა
წყლის მნიშვნელობას იძლევა. 2026-07-26-ის ცრუ-ნეგატივის (კონსენსუსი 1.09 მ
vs რეალური 1.55–2.50 მ) ძირითადი მიზეზი სწორედ ეს იყო.

═══ არქიტექტურა ═══
ეს სკრიპტი მთავარ pipeline-ში НЕ შედის. ის ცალკე workflow-ით ეშვება
დღეში 4-ჯერ და წერს `wave_copernicus.json`-ს რეპოზიტორიაში.
`fetch.py` მხოლოდ ამ ფაილს კითხულობს — მძიმე დამოკიდებულებები
(xarray, dask, netCDF4) მთავარ გაშვებაში არ ხვდება.

მიზეზები:
  · პროდუქტი დღეში ორჯერ ახლდება — საათობრივი ჩამოტვირთვა უაზროა
  · თუ ეს job ჩავარდა, fetch.py ბოლო კარგ JSON-ს კითხულობს
  · მთავარი pipeline `requests`-ზე რჩება და სწრაფია

═══ სტატუსი ═══
ჩართვისას Copernicus **მხოლოდ დაკვირვების რეჟიმშია** (EWAM/GWAM-ის მსგავსად) —
კონსენსუსზე გავლენას არ ახდენს. ეს იცავს "ოქტომბრამდე პარამეტრები არ
იცვლება" წესს და პარალელურად აგროვებს შესადარებელ მონაცემს.

გარემოს ცვლადები:
  COPERNICUSMARINE_SERVICE_USERNAME
  COPERNICUSMARINE_SERVICE_PASSWORD
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
from datetime import datetime, timedelta, timezone

logging.basicConfig(level=logging.INFO, format="[fetch_wave] %(message)s")
log = logging.getLogger("fetch_wave")

# ─────────────────────────── კონფიგი ───────────────────────────

DATASET_ID = "cmems_mod_blk_wav_anfc_2.5km_PT1H-i"

POTI_LAT = 42.15
POTI_LON = 41.67

# ბადე 0.025° — ±0.15° დაახლოებით ±12 კმ, ანუ ~12×12 უჯრა.
# საკმარისად პატარაა სწრაფი წამოღებისთვის და საკმარისად დიდი,
# რომ ხმელეთის ნიღბის შემთხვევაში ზღვის უჯრა მოიძებნოს.
BBOX = 0.15

FORECAST_HOURS = 48
OUTPUT_FILE = pathlib.Path("wave_copernicus.json")

TBILISI_TZ = timezone(timedelta(hours=4))   # UTC+4, DST არ აქვს

# CMEMS-ის ცვლადების სტანდარტული მოკლე სახელები.
# ჩვენს შიდა სახელებზე რუკა — მხოლოდ ის აიღება, რაც dataset-ში მართლა არსებობს.
# ⚠ ყურადღება ორ ცვლადზე — მათი სახელები ინტუიციის საწინააღმდეგოა:
#   VCMX = "Maximum crest trough wave height (Hc,max)"
#          → standard_name: sea_surface_wave_maximum_height
#          → სრული ტალღა ღრმულიდან მწვერვალამდე. ᲝᲞᲔᲠᲐᲪᲘᲣᲚᲐᲓ ᲔᲡ ᲒᲕᲭᲘᲠᲓᲔᲑᲐ.
#   VMXL = "Height of the highest crest"
#          → standard_name: sea_surface_wave_maximum_crest_height
#          → მხოლოდ მწვერვალი საშუალო დონიდან ზემოთ (≈ ნახევარი).
# პირველ ვერსიაში VMXL შეცდომით "wave_max_height"-ად იყო მონიშნული, რის
# გამოც მაქსიმუმი Hs-ზე ნაკლები გამოდიოდა (0.16 vs 0.28) — ფიზიკურად შეუძლებელი.
VAR_MAP = {
    "VHM0":      "wave_height",       # მნიშვნელოვანი სიმაღლე Hs
    "VTM10":     "wave_period",       # საშუალო პერიოდი (m-1/m0)
    "VTPK":      "peak_period",       # პიკური პერიოდი
    "VMDR":      "wave_direction",    # საიდან მოდის
    "VHM0_SW1":  "swell_height",      # პირველადი swell
    "VTM01_SW1": "swell_period",
    "VMDR_SW1":  "swell_direction",
    "VHM0_SW2":  "swell2_height",     # მეორეული swell — შავ ზღვაზე ხშირია
    "VTM01_SW2": "swell2_period",
    "VMDR_SW2":  "swell2_direction",
    "VHM0_WW":   "wind_wave_height",  # ქარის ტალღა
    "VTM01_WW":  "wind_wave_period",
    "VMDR_WW":   "wind_wave_direction",
    "VCMX":      "wave_max_height",   # Hc,max — სრული ტალღა (ოპერაციული)
    "VMXL":      "wave_crest_height", # უმაღლესი მწვერვალი
}

# ─────────────────────────── დამხმარეები ───────────────────────────


def _r(v, nd=2):
    """უსაფრთხო დამრგვალება — NaN/None ხდება None."""
    try:
        f = float(v)
        if f != f:          # NaN
            return None
        return round(f, nd)
    except (TypeError, ValueError):
        return None


def _write(payload: dict) -> None:
    OUTPUT_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    log.info(f"✓ {OUTPUT_FILE} ({len(payload.get('hourly', []))} საათი)")


def _keep_existing(reason: str) -> None:
    """ჩავარდნისას არსებული ფაილი ხელუხლებელი რჩება.

    კრიტიკულია: fetch.py-ს ურჩევნია ოდნავ ძველი Copernicus-ის მონაცემი,
    ვიდრე საერთოდ არაფერი. ცარიელი ფაილის ჩაწერა მონაცემს გაანადგურებდა.
    """
    if OUTPUT_FILE.exists():
        log.warning(f"{reason} — არსებული {OUTPUT_FILE} უცვლელი რჩება")
    else:
        log.warning(f"{reason} — ფაილი ჯერ არ არსებობს")


# ─────────────────────────── ძირითადი ლოგიკა ───────────────────────────


def pick_sea_point(ds, varname: str):
    """ფოთთან უახლოესი *ზღვის* უჯრის კოორდინატები.

    ხმელეთზე მოდელი NaN-ს აბრუნებს. უბრალო `sel(method="nearest")` შეიძლება
    ხმელეთის უჯრაზე მოხვდეს — ფოთის პორტი ზუსტად სანაპიროზეა. ამიტომ
    პირველ დროის ბიჯზე ვეძებთ ყველა არა-NaN უჯრას და ვირჩევთ უახლოესს.
    """
    import numpy as np

    snap = ds[varname].isel(time=0)
    lats = snap["latitude"].values
    lons = snap["longitude"].values
    vals = np.asarray(snap.values, dtype="float64")

    valid = ~np.isnan(vals)
    if not valid.any():
        raise ValueError("bbox-ში ზღვის უჯრა არ მოიძებნა")

    la = lats[:, None] if vals.ndim == 2 else lats
    lo = lons[None, :] if vals.ndim == 2 else lons

    # კოსინუს-კორექცია გრძედზე (42° განედზე ≈ 0.74)
    dlat = (la - POTI_LAT)
    dlon = (lo - POTI_LON) * np.cos(np.deg2rad(POTI_LAT))
    dist = np.sqrt(dlat ** 2 + dlon ** 2)
    dist = np.where(valid, dist, np.inf)

    idx = np.unravel_index(np.argmin(dist), dist.shape)
    p_lat = float(lats[idx[0]])
    p_lon = float(lons[idx[1]])
    km = float(np.min(dist)) * 111.0
    log.info(f"ზღვის უჯრა: {p_lat:.4f}N {p_lon:.4f}E (ფოთიდან ~{km:.1f} კმ)")
    return p_lat, p_lon, km


def build_payload(ds, wanted: dict) -> dict:
    """xarray dataset → პორტალის ფორმატის JSON."""
    import numpy as np
    import pandas as pd

    p_lat, p_lon, km = pick_sea_point(ds, "VHM0")

    point = ds.sel(latitude=p_lat, longitude=p_lon, method="nearest")

    times = pd.to_datetime(point["time"].values)
    now_utc = datetime.now(timezone.utc)
    cutoff = now_utc + timedelta(hours=FORECAST_HOURS)

    series = {ours: np.asarray(point[cm].values, dtype="float64")
              for cm, ours in wanted.items()}

    hourly = []
    for i, t in enumerate(times):
        t_utc = t.tz_localize("UTC") if t.tzinfo is None else t
        if t_utc > cutoff:
            break
        # ერთიან დროის ღერძზე — თბილისის ლოკალური, fetch.py-ის ფორმატით.
        # (2026-08-08-ის ბაგის გაკვეთილი: ღერძი ერთი უნდა იყოს.)
        t_loc = t_utc.astimezone(TBILISI_TZ)
        row = {"time": t_loc.strftime("%Y-%m-%dT%H:%M")}
        for name, arr in series.items():
            row[name] = _r(arr[i]) if i < len(arr) else None
        hourly.append(row)

    return {
        "meta": {
            "source": "Copernicus Marine BLKSEA_ANALYSISFORECAST_WAV_007_003",
            "dataset_id": DATASET_ID,
            "model": "WAM Cycle 6 · 2.5 km · shallow-water",
            "generated": datetime.now(TBILISI_TZ).strftime("%Y-%m-%d %H:%M"),
            "generated_utc": now_utc.isoformat(timespec="seconds"),
            "grid_lat": round(p_lat, 4),
            "grid_lon": round(p_lon, 4),
            "grid_dist_km": round(km, 1),
            "timezone": "Asia/Tbilisi (UTC+4)",
            "hours": len(hourly),
        },
        "hourly": hourly,
    }


def main() -> None:
    user = os.environ.get("COPERNICUSMARINE_SERVICE_USERNAME", "").strip()
    pwd = os.environ.get("COPERNICUSMARINE_SERVICE_PASSWORD", "").strip()
    if not user or not pwd:
        _keep_existing("credentials არ არის დაყენებული")
        return

    try:
        import copernicusmarine as cm
    except ImportError as exc:
        _keep_existing(f"copernicusmarine არ არის ხელმისაწვდომი ({exc})")
        return

    now_utc = datetime.now(timezone.utc)

    try:
        # lazy გახსნა — ფაილი არ ჩამოიტვირთება, მხოლოდ მეტამონაცემები
        ds = cm.open_dataset(
            dataset_id=DATASET_ID,
            username=user,
            password=pwd,
            minimum_latitude=POTI_LAT - BBOX,
            maximum_latitude=POTI_LAT + BBOX,
            minimum_longitude=POTI_LON - BBOX,
            maximum_longitude=POTI_LON + BBOX,
            start_datetime=(now_utc - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S"),
            end_datetime=(now_utc + timedelta(hours=FORECAST_HOURS + 2)).strftime("%Y-%m-%dT%H:%M:%S"),
        )
    except Exception as exc:
        _keep_existing(f"open_dataset ჩავარდა: {exc}")
        return

    available = set(ds.data_vars)
    wanted = {cm_name: ours for cm_name, ours in VAR_MAP.items() if cm_name in available}
    log.info(f"ცვლადები: {len(wanted)}/{len(VAR_MAP)} ხელმისაწვდომი")

    missing = [v for v in VAR_MAP if v not in available]
    if missing:
        log.info(f"არ არის: {', '.join(missing)}")

    if "VHM0" not in wanted:
        _keep_existing("VHM0 (ტალღის სიმაღლე) dataset-ში არ მოიძებნა")
        return

    try:
        payload = build_payload(ds, wanted)
    except Exception as exc:
        _keep_existing(f"დამუშავება ჩავარდა: {exc}")
        return

    if not payload["hourly"]:
        _keep_existing("ცარიელი შედეგი")
        return

    # საღი აზრის შემოწმება — აშკარად აბსურდული მნიშვნელობა არ ჩაიწეროს
    heights = [h["wave_height"] for h in payload["hourly"] if h.get("wave_height") is not None]
    if not heights:
        _keep_existing("ტალღის სიმაღლე მთლიანად NaN-ია")
        return
    if max(heights) > 15.0:
        _keep_existing(f"არარეალური ტალღა ({max(heights)} მ) — უარვყოფთ")
        return

    # ფიზიკური თანმიმდევრობა: Hmax ყოველთვის Hs-ზე დიდია (ტიპურად 1.5–2.0×).
    # თუ ეს ირღვევა, სავარაუდოდ ცვლადი არასწორადაა მიბმული — გავაფრთხილოთ,
    # მაგრამ ფაილი მაინც ჩავწეროთ (Hs თავისთავად სწორია).
    pairs = [(h["wave_height"], h["wave_max_height"]) for h in payload["hourly"]
             if h.get("wave_height") and h.get("wave_max_height")]
    if pairs:
        ratios = [mx / hs for hs, mx in pairs if hs > 0.05]
        if ratios:
            avg = sum(ratios) / len(ratios)
            if avg < 1.2:
                log.warning(f"⚠ Hmax/Hs = {avg:.2f} — მოსალოდნელია 1.5–2.0. "
                            f"შესაძლოა ცვლადი არასწორადაა მიბმული.")
            else:
                log.info(f"Hmax/Hs = {avg:.2f} ✓")

    _write(payload)
    log.info(f"ტალღა: {min(heights)}–{max(heights)} მ ({len(heights)} საათი)")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log.error(f"მოულოდნელი შეცდომა: {exc}")
    # ყოველთვის 0 — ეს pipeline არასოდეს არ უნდა ჩააგდოს workflow
    sys.exit(0)
