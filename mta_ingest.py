"""
MTA ბიულეტენების ingest — observation-only.

გამოძახება (workflow-დან):  python mta_ingest.py

აკეთებს:
  1. კითხულობს ყველა PDF-ს mta_bulletins/ ფოლდერიდან
  2. ცნობს ტიპს (actual / storm_warning / forecast) filename/შიგთავსით
  3. აპარსებს mta_parser-ით
  4. ამატებს mta_log.json-ში (dedup ბიულეტენის ნომრით)
  5. actual/storm nowcast-ს ადარებს იმ საათის data.json-ის კონსენსუსს (თუ არსებობს)

⚠️ კონსენსუსში / data.json-ში არ ერევა. ცალკე ფაილია (mta_log.json).
   ჩავარდნა პორტალს არ აზიანებს.
"""

import json, os, glob, sys, logging, re
from datetime import datetime, timezone, timedelta

from mta_parser import parse_mta_pdf, wave_cm_to_m

TBILISI_TZ   = timezone(timedelta(hours=4))
BULLETIN_DIR = "mta_bulletins"
LOG_FILE     = "mta_log.json"
DATA_FILE    = "data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("mta_ingest")


def _subject_from_name(fname: str) -> str:
    """filename-იდან subject-ის მიახლოება (Power Automate ფაილს ბიულეტენის
    სახელით ინახავს; მაგ. 'ფაქტიური_ამინდი_ფოთი_...pdf')."""
    base = os.path.basename(fname).lower()
    if "ფაქტიური" in base or "actual" in base:   return "ფაქტიური ამინდი ფოთი"
    if "საშტორმო" in base or "storm" in base:     return "საშტორმო გაფრთხილება"
    if "პროგნოზი" in base or "forecast" in base or base.startswith("wf"): return "ამინდის პროგნოზი ფოთი"
    return ""   # router შიგთავსით ცდის


def _load_log() -> dict:
    try:
        with open(LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"entries": []}


def _save_log(dblog: dict):
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(dblog, f, ensure_ascii=False, indent=2)


def _seen_ids(dblog: dict) -> set:
    return {e.get("bulletin_no") for e in dblog.get("entries", []) if e.get("bulletin_no")}


# ქართული თვეები — ბიულეტენის თარიღის გასარჩევად ("26 / აგვისტო / 2026")
GEO_MONTHS = {
    "იანვარი": 1, "თებერვალი": 2, "მარტი": 3, "აპრილი": 4,
    "მაისი": 5, "ივნისი": 6, "ივლისი": 7, "აგვისტო": 8,
    "სექტემბერი": 9, "ოქტომბერი": 10, "ნოემბერი": 11, "დეკემბერი": 12,
}


def _load_portal_now():
    """მიმდინარე data.json-ის current ბლოკი — სათადარიგო ვარიანტი."""
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("current", {}), d.get("meta", {}).get("last_update")
    except Exception:
        return {}, None


def _portal_at(parsed: dict):
    """პორტალის ჩანაწერი ᲑᲘᲣᲚᲔᲢᲔᲜᲘᲡ ᲡᲐᲐᲗᲖᲔ, არა მიმდინარეზე.

    კრიტიკული: MTA ზომავს 04:00-ზე, ingest კი ~05:07-ზე ეშვება. `current`
    ბლოკის გამოყენება ერთსაათიან შეუსაბამობას იწვევდა — შტორმის დროს,
    როცა პირობები სწრაფად იცვლება, ეს შედარებას უაზროდ აქცევს.

    აბრუნებს (portal_dict, matched_time, exact). exact=False ნიშნავს,
    რომ ზუსტი საათი ვერ მოიძებნა და current-ს დავუბრუნდით.
    """
    date_s = (parsed.get("date") or "").strip()
    time_s = (parsed.get("time") or "").strip()

    target = None
    try:
        parts = [x.strip() for x in date_s.split("/")]
        if len(parts) == 3:
            day = int(parts[0])
            # ორი ფორმატი: "26 / აგვისტო / 2026" (ფაქტიური/პროგნოზი)
            # და "26/08/2026" (საშტორმოს გაუქმება).
            mon = GEO_MONTHS.get(parts[1])
            if mon is None and parts[1].isdigit():
                mon = int(parts[1])
            year = int(parts[2].replace("წ.", "").strip())
            hh = int(time_s.split(":")[0])
            if mon and 1 <= mon <= 12:
                target = f"{year:04d}-{mon:02d}-{day:02d}T{hh:02d}:00"
    except Exception:
        target = None

    if target:
        try:
            with open(DATA_FILE, encoding="utf-8") as f:
                d = json.load(f)
            for h in d.get("forecast", []):
                if (h.get("time") or "")[:16] == target:
                    return h, target, True
            log.info(f"პორტალში {target} ვერ მოიძებნა — current-ს ვიყენებ")
        except Exception as exc:
            log.info(f"forecast-ის წაკითხვა ჩავარდა ({exc})")

    cur, cur_t = _load_portal_now()
    return cur, cur_t, False


MILE_KM = 1.852
# MTA ხილვადობას 10 მილზე მეტს არ აღნიშნავს — ეს მაქსიმალური
# სარეპორტო მნიშვნელობაა. სუფთა ამინდში რეალური ხილვადობა
# 30+ კმ-ია, ანუ დიაპაზონი ნაწილებრივია. თუ ამას უგულებელვად
# შევადარებთ, ყოველ მზიან დღეს ცრუ განსხვავებას დავაფიქსირებთ.
VIS_CAP_MILES = 10.0

_DRY = ("no precipitation", "უნალექოდ", "none", "nil")
_WET = ("rain", "shower", "drizzle", "snow", "storm", "thunder",
        "წვიმ", "ჟინჟ", "თოვლ", "ჩქექ")


def _mta_is_dry(text):
    """MTA-ს ნალექის ტექსტი → True (მშრალი) / False (ნალექი) / None.

    თანამიმდევრობა აქ მნიშვნელოვანია: "უნალექოდ" შეიცავს სტრიქონს
    "ნალექ", ამიტომ სველი მარკერი ჿველაზე დროით მოწმდება.
    """
    if text is None:
        return None
    t = str(text).strip().lower()
    if not t:
        return None
    if any(k in t for k in _DRY):
        return True
    if any(k in t for k in _WET):
        return False
    return None


def _circ_delta(a, b):
    """ვექტორული მიმართულებების სხვაობა — მაქსიმუმ 180°.

    350° და 10° ერთმანეთისგან 20°-ითაა დაშორებული, არა 340°-ით.
    """
    try:
        d = abs(float(a) - float(b)) % 360
        return round(min(d, 360 - d), 1)
    except Exception:
        return None


def _compare_to_portal(parsed: dict, portal: dict) -> dict:
    """actual/storm nowcast-ის ფოთის ქარი/ტალღა vs პორტალი. მხოლოდ ჩაწერისთვის."""
    cmp = {}
    poti = parsed.get("poti", {})
    # ქარი (actual: wind_max; storm: wind_range hi)
    mta_gust = None
    if poti.get("wind_max") is not None:
        mta_gust = poti["wind_max"]
    elif isinstance(poti.get("wind_range"), (list, tuple)):
        mta_gust = poti["wind_range"][1]
    if mta_gust is not None and portal.get("wind_gusts") is not None:
        cmp["wind_mta"]    = mta_gust
        cmp["wind_portal"] = portal["wind_gusts"]
        cmp["wind_delta"]  = round(mta_gust - portal["wind_gusts"], 2)

    # საშუალო ქარიც — MTA-ს wind_avg vs პორტალის wind_speed.
    # აქამდე მხოლოდ პიკი ედარებოდა; საშუალო ცალკე სიდიდეა და
    # ტალღის წარმოქმნისთვის სწორედ ის არის განმსაზღვრელი.
    mta_avg = poti.get("wind_avg")
    if mta_avg is not None and portal.get("wind_speed") is not None:
        cmp["wind_avg_mta"]    = mta_avg
        cmp["wind_avg_portal"] = portal["wind_speed"]
        cmp["wind_avg_delta"]  = round(mta_avg - portal["wind_speed"], 2)
    # ტალღა — MTA სმ→მ, დიაპაზონის შუა
    wave_m = wave_cm_to_m(poti.get("wave_cm"))
    if isinstance(wave_m, (list, tuple)):
        mta_wave = round(sum(wave_m) / 2, 2)
    elif isinstance(wave_m, (int, float)):
        mta_wave = wave_m
    else:
        mta_wave = None
    if mta_wave is not None and portal.get("wave_height") is not None:
        cmp["wave_mta"]    = mta_wave
        cmp["wave_portal"] = portal["wave_height"]
        cmp["wave_delta"]  = round(mta_wave - portal["wave_height"], 2)

    # ── მიმართულება (წრფივი) ──
    if poti.get("wind_dir") is not None and portal.get("wind_direction") is not None:
        d = _circ_delta(poti["wind_dir"], portal["wind_direction"])
        if d is not None:
            cmp["dir_mta"]    = poti["wind_dir"]
            cmp["dir_portal"] = portal["wind_direction"]
            cmp["dir_delta"]  = d

    # ── ხილვადობა: მილი → კმ, წაღების გათვალისწინებით ──
    vm = poti.get("vis_miles")
    if isinstance(vm, (int, float)) and portal.get("visibility_km") is not None:
        vis_km = round(float(vm) * MILE_KM, 2)
        capped = float(vm) >= VIS_CAP_MILES
        cmp["vis_mta_km"]   = vis_km
        cmp["vis_portal_km"] = portal["visibility_km"]
        cmp["vis_capped"]   = capped
        if capped:
            # MTA ამბობს "≥ 18.5 კმ". ღირსებულია მხოლოდ იმ შემთხვევაში,
            # თუ პორტალი ამაზე დაბლავს — ეს ნამდვილი შეუსაბამობაა.
            cmp["vis_flag"] = ("portal_below" if portal["visibility_km"] < vis_km
                               else "ok_capped")
        else:
            cmp["vis_delta"] = round(vis_km - portal["visibility_km"], 2)

    # ── ნალექი: ორობითი შედარება ──
    dry = _mta_is_dry(poti.get("precip"))
    pp = portal.get("precipitation")
    if dry is not None and pp is not None:
        portal_wet = float(pp) >= 0.1
        cmp["precip_mta"]    = "dry" if dry else "wet"
        cmp["precip_portal"] = round(float(pp), 2)
        if dry and portal_wet:
            cmp["precip_verdict"] = "false_positive"     # პორტალი აწვიმებდა
        elif (not dry) and (not portal_wet):
            cmp["precip_verdict"] = "false_negative"     # პორტალმა ვერ დაახდინა
        else:
            cmp["precip_verdict"] = "match"

    # ── ტემპერატურები — დიაგნოსტიკური ორიენტირი ──
    # ოპერაციულად უმნიშვნელოა, მაგრამ დიაგნოსტიკურად ძვირფასიანია:
    # თუ ტემპერატურა ზუსტად ემთხვევა და ქარი — არა, მაშინ მოდელი
    # სწორ ადგილს უყურებს და პრობლემა კონკრეტულ პარამეტრშია,
    # არა ბადის წერტილში.
    for mta_k, portal_k, out_k in (("air_temp", "air_temp", "air_temp"),
                                   ("sea_temp", "water_temp", "sea_temp")):
        mv, pv = poti.get(mta_k), portal.get(portal_k)
        if isinstance(mv, (int, float)) and isinstance(pv, (int, float)):
            cmp[f"{out_k}_mta"]   = round(float(mv), 1)
            cmp[f"{out_k}_portal"] = round(float(pv), 1)
            cmp[f"{out_k}_delta"] = round(float(mv) - float(pv), 1)

    return cmp


# ═══════════════════════════════════════════════════════════════
#  Forecast ↔ პორტალი — 6-საათიანი ბლოკებით
# ═══════════════════════════════════════════════════════════════
#
# MTA-ს პროგნოზი დიაპაზონებს იძლევა, არა წერტილოვან მნიშვნელობებს,
# ამიტომ actual-ის ლოგიკა (ერთი საათი ↔ ერთი საათი) აქ არ გამოდგება.
#
# ცხრილის სტრუქტურა: "დღე" და "ღამე" თითო 12 საათია, ხოლო მნიშვნელობებში
# "/" ორ 6-საათიან ქვე-ბლოკს ჰყოფს:
#     day  "125-220/90-160"  →  10:00-16:00 = 125-220 სმ,  16:00-22:00 = 90-160
#     night "70-135/50-100"  →  22:00-04:00 = 70-135,      04:00-10:00 = 50-100
#
# შედარება: პორტალიდან იმავე ფანჯარაზე min/max ვიღებთ და დიაპაზონებს
# ვადარებთ — გადაფარვა, max-ის სხვაობა (ოპერაციულად ეს წყვეტს) და
# შუაწერტილის სხვაობა (სტატისტიკისთვის).

FORECAST_BLOCKS = [
    ("day",   0, 10, 16),   # (პერიოდი, ქვე-ბლოკის ინდექსი, დაწყება, დასრულება)
    ("day",   1, 16, 22),
    ("night", 0, 22, 4),
    ("night", 1, 4, 10),
]


def _parse_range(raw):
    """'6-11' → (6.0, 11.0);  '4' → (4.0, 4.0);  სხვა → None."""
    if raw is None:
        return None
    t = str(raw).strip()
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*-\s*(\d+(?:[.,]\d+)?)\s*$", t)
    if m:
        return (float(m.group(1).replace(",", ".")), float(m.group(2).replace(",", ".")))
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*$", t)
    if m:
        v = float(m.group(1).replace(",", "."))
        return (v, v)
    return None


def _split_subblocks(raw):
    """'125-220/90-160' → [(125,220), (90,160)].

    თუ '/' არ არის, ერთივე ქვე-ბლოკს ერთი დიაპაზონი ხვდება — MTA ასე
    აღნიშნავს, რომ მთელ 12 საათზე ერთი პროგნოზია.
    """
    if raw is None:
        return [None, None]
    parts = [x for x in str(raw).split("/")]
    if len(parts) == 1:
        r = _parse_range(parts[0])
        return [r, r]
    return [_parse_range(parts[0]), _parse_range(parts[1])]


def _issue_dt(parsed):
    """ბიულეტენის გამოცემის მომენტი — 'valid' ველიდან, თუ ვერა — ახლანდელი."""
    valid = parsed.get("valid") or ""
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4}).{0,12}?(\d{1,2})[:.](\d{2})", valid)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)),
                            int(m.group(4)), int(m.group(5)), tzinfo=TBILISI_TZ)
        except Exception:
            pass
    return datetime.now(TBILISI_TZ)


def _next_window(issue, start_h, end_h):
    """გამოცემის შემდეგ პირველი [start_h, end_h) ფანჯარა.

    ღამის ბლოკები დღის საზღვარს კვეთენ (22→04), ამიტომ დასრულება
    შესაძლოა მომდევნო დღეს იყოს.
    """
    base = issue.replace(minute=0, second=0, microsecond=0)
    start = base.replace(hour=start_h)
    if start < issue:
        start += timedelta(days=1)
    span = (end_h - start_h) % 24 or 24
    return start, start + timedelta(hours=span)


def _portal_window_stats(forecast, t0, t1):
    """პორტალის min/max მოცემულ ფანჯარაზე. აბრუნებს dict ან None."""
    vals = {"wind_speed": [], "wind_gusts": [], "wave_height": [], "visibility_km": []}
    hours = 0
    for h in forecast or []:
        t = (h.get("time") or "")[:16]
        if not t:
            continue
        try:
            dt = datetime.strptime(t, "%Y-%m-%dT%H:%M").replace(tzinfo=TBILISI_TZ)
        except Exception:
            continue
        if not (t0 <= dt < t1):
            continue
        hours += 1
        for k in vals:
            v = h.get(k)
            if v is not None:
                vals[k].append(float(v))
    if hours == 0:
        return None
    out = {"hours": hours}
    for k, arr in vals.items():
        if arr:
            out[k] = (round(min(arr), 2), round(max(arr), 2))
    return out


def _cmp_range(mta, portal):
    """ორი დიაპაზონის შედარება → გადაფარვა, max-სხვაობა, შუაწერტილის სხვაობა."""
    if not mta or not portal:
        return None
    lo_m, hi_m = mta
    lo_p, hi_p = portal
    return {
        "mta": [lo_m, hi_m],
        "portal": [lo_p, hi_p],
        "overlap": not (hi_p < lo_m or lo_p > hi_m),
        "max_delta": round(hi_m - hi_p, 2),          # + → MTA უფრო მაღალი
        "mid_delta": round((lo_m + hi_m) / 2 - (lo_p + hi_p) / 2, 2),
    }


def _compare_forecast_to_portal(parsed: dict) -> dict:
    """MTA-ს პროგნოზი ↔ პორტალი, 6-საათიან ბლოკებზე."""
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            forecast = json.load(f).get("forecast", [])
    except Exception:
        return {}

    poti = parsed.get("poti", {})
    issue = _issue_dt(parsed)
    blocks = []

    for period, idx, sh, eh in FORECAST_BLOCKS:
        src = poti.get(period) or {}
        if not src:
            continue
        t0, t1 = _next_window(issue, sh, eh)
        stats = _portal_window_stats(forecast, t0, t1)
        if not stats:
            continue   # პორტალის ჰორიზონტს სცდება

        wind_r = _split_subblocks(src.get("wind_speed"))[idx]
        wave_r = _split_subblocks(src.get("wave_cm"))[idx]
        if wave_r:
            wave_r = (round(wave_r[0] / 100, 2), round(wave_r[1] / 100, 2))

        entry = {
            "block": f"{period}{idx + 1}",
            "window": f"{t0.strftime('%Y-%m-%dT%H:%M')} → {t1.strftime('%H:%M')}",
            "portal_hours": stats["hours"],
        }
        w = _cmp_range(wind_r, stats.get("wind_speed"))
        if w: entry["wind"] = w
        v = _cmp_range(wave_r, stats.get("wave_height"))
        if v: entry["wave"] = v
        if len(entry) > 3:
            blocks.append(entry)

    if not blocks:
        return {}

    # შემაჯამებელი — რამდენ ბლოკში ემთხვევა დიაპაზონები
    def _rate(key):
        got = [b[key]["overlap"] for b in blocks if key in b]
        return f"{sum(got)}/{len(got)}" if got else None

    return {
        "issued": issue.strftime("%Y-%m-%d %H:%M"),
        "blocks": blocks,
        "wind_overlap": _rate("wind"),
        "wave_overlap": _rate("wave"),
    }


def main():
    if not os.path.isdir(BULLETIN_DIR):
        log.info(f"{BULLETIN_DIR}/ არ არსებობს — გამოსატანი არაფერია.")
        return

    dblog = _load_log()
    seen  = _seen_ids(dblog)

    pdfs = sorted(glob.glob(os.path.join(BULLETIN_DIR, "*.pdf")))
    if not pdfs:
        log.info("ახალი PDF არ არის.")
        return

    added = 0
    processed_files = []
    for pdf in pdfs:
        subject = _subject_from_name(pdf)
        try:
            parsed = parse_mta_pdf(pdf, subject)
        except Exception as e:
            log.warning(f"პარსინგი ჩავარდა [{os.path.basename(pdf)}] — {e}")
            continue

        bno = parsed.get("bulletin_no")
        if bno and bno in seen:
            processed_files.append(pdf)   # უკვე გვაქვს — PDF მაინც წასაშლელია
            continue   # უკვე დამუშავებული

        entry = {
            "ingested_at": datetime.now(TBILISI_TZ).strftime("%Y-%m-%d %H:%M"),
            "source_file": os.path.basename(pdf),
            **parsed,
        }
        # actual/storm nowcast → პორტალთან შედარება.
        # პორტალის ჩანაწერი ᲗᲘᲗᲝᲔᲣᲚ ᲑᲘᲣᲚᲔᲢᲔᲜᲖᲔ ცალკე ირჩევა — მისი
        # საკუთარი საათის მიხედვით, არა ingest-ის მომენტის.
        if parsed.get("type") == "forecast":
            fc_cmp = _compare_forecast_to_portal(parsed)
            if fc_cmp:
                entry["vs_portal_forecast"] = fc_cmp

        # storm_cancel შეიცავს ფაქტიურ ამინდს — დამატებითი ground truth
        # წერტილი ორ რეგულარულ ფაქტიურ ბიულეტენს გარდა.
        if parsed.get("type") in ("actual", "storm_warning", "storm_cancel"):
            portal, portal_time, exact = _portal_at(parsed)
            if portal:
                entry["vs_portal"] = _compare_to_portal(parsed, portal)
                entry["portal_time"] = portal_time
                # exact=False → შედარება ერთსაათიან ცდომილებას შეიცავს.
                # ოქტომბრის სტატისტიკაში ასეთი ჩანაწერები უნდა გამოირიცხოს.
                entry["portal_hour_exact"] = exact

        dblog["entries"].append(entry)
        if bno: seen.add(bno)
        added += 1
        processed_files.append(pdf)
        log.info(f"დამატებულია: {parsed.get('type')} #{bno} ({os.path.basename(pdf)})")

    if added:
        dblog["last_ingest"] = datetime.now(TBILISI_TZ).strftime("%Y-%m-%d %H:%M")
        _save_log(dblog)
        log.info(f"✓ {added} ახალი ჩანაწერი → {LOG_FILE}")
    else:
        log.info("ახალი ჩანაწერი არ დამატებულა (ყველა უკვე დამუშავებული).")

    # (ბ) წარმატებით დამუშავებული PDF-ები იშლება — mta_log.json ინახავს ყველაფერს.
    # პარსინგ-ჩავარდნილი ფაილები რჩება (ხელით შესამოწმებლად).
    for pdf in processed_files:
        try:
            os.remove(pdf)
            log.info(f"წაშლილია დამუშავებული PDF: {os.path.basename(pdf)}")
        except Exception as e:
            log.warning(f"PDF წაშლა ვერ მოხერხდა [{os.path.basename(pdf)}] — {e}")


if __name__ == "__main__":
    main()
