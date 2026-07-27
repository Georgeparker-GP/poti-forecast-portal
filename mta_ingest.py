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

import json, os, glob, sys, logging
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


def _load_portal_now():
    """მიმდინარე data.json-ის current ბლოკი — MTA-სთან შესადარებლად."""
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d.get("current", {}), d.get("meta", {}).get("last_update")
    except Exception:
        return {}, None


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
    return cmp


def main():
    if not os.path.isdir(BULLETIN_DIR):
        log.info(f"{BULLETIN_DIR}/ არ არსებობს — გამოსატანი არაფერია.")
        return

    dblog = _load_log()
    seen  = _seen_ids(dblog)
    portal, portal_time = _load_portal_now()

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
        # actual/storm nowcast → პორტალთან შედარება
        if parsed.get("type") in ("actual", "storm_warning") and portal:
            entry["vs_portal"] = _compare_to_portal(parsed, portal)
            entry["portal_time"] = portal_time

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
