"""
MTA (საზღვაო ტრანსპორტის სააგენტო) ბიულეტენების პარსერი — ფოთი.
Email: potisinoptika@mta.gov.ge, PDF attachment (ტექსტური, ცხრილური).

ოთხი ტიპი (subject-ით იმიჯნება upstream-ში):
  - "ფაქტიური ამინდი ფოთი"      → actual/nowcast  ★ ground-truth
  - "ამინდის პროგნოზი ფოთი"      → forecast (დილა)
  - "საღამოს ამინდის პროგნოზი"   → forecast (საღამო)
  - "საშტორმო გაფრთხილება"       → storm warning

ეს მოდული აპარსებს PDF-ს და აბრუნებს სტრუქტურირებულ dict-ს.
observation-only: პორტალის კონსენსუსში ჯერ არ ერევა (დაკვირვების ფაზა).
"""

import re
import pdfplumber


# ცხრილის ინგლისური label → ჩვენი გასაღები (ინგლისურით ვიჭერთ — სტაბილურია)
FIELD_MAP = {
    "wind (direction degree)":      "wind_dir",
    "wind (average speed m/s)":     "wind_avg",
    "wind (maximum speed m/s)":     "wind_max",
    "ware height (cm)":             "wave_cm",       # sic: MTA "Ware" (typo მათი მხრიდან)
    "sea state (duglas force)":     "sea_state",     # sic: "Duglas"
    "sea level (cm)":               "sea_level",
    "temperature sea (°c)":         "sea_temp",
    "visibility (miles)":           "vis_miles",
    "temperature air (°c)":         "air_temp",
    "humidity (%)":                 "humidity",
    "atmospheric pressure (mb)":    "pressure",
}

# ცხრილში 3 ლოკაცია: ფოთი / ყულევი / ანაკლია. ჩვენ ფოთი გვჭირდება (სვეტი 1).
POTI_COL = 1


def _label_key(cell: str) -> str:
    """ცხრილის პირველი უჯრედი ('ქართული\\nEnglish') → ინგლისური label lowercase."""
    if not cell:
        return ""
    # ბოლო სტრიქონი ინგლისურია
    lines = [l.strip() for l in cell.split("\n") if l.strip()]
    for line in reversed(lines):
        # ინგლისურ ასოს შემცველი სტრიქონი
        if re.search(r"[a-zA-Z]", line):
            return line.lower()
    return lines[-1].lower() if lines else ""


def _num(val: str):
    """'155-225' → (155, 225) დიაპაზონი; '8' → 8.0; ცარიელი → None."""
    if not val or not val.strip():
        return None
    val = val.strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$", val)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.match(r"^(\d+(?:\.\d+)?)$", val)
    if m:
        return float(m.group(1))
    return val  # ტექსტი (მაგ. ამინდის აღწერა)


def parse_actual_weather(pdf_path: str) -> dict:
    """'ფაქტიური ამინდი ფოთი' PDF → dict ფოთის ველებით.

    აბრუნებს:
      {
        "type": "actual",
        "bulletin_no": "14/5950",
        "date": "26 / ივლისი / 2026",
        "time": "16:00",
        "poti": {wind_dir, wind_avg, wind_max, wave_cm, sea_state, ...}
      }
    """
    out = {"type": "actual", "poti": {}}
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables()

    # მეტა — ბიულეტენის ნომერი, თარიღი, საათი
    m = re.search(r"№\s*([\d/]+)", text)
    if m:
        out["bulletin_no"] = m.group(1)
    m = re.search(r"(\d{1,2}\s*/\s*[^\d/]+/\s*\d{4})", text)
    if m:
        out["date"] = m.group(1).strip()
    m = re.search(r"(\d{1,2}:\s*\d{2})", text)
    if m:
        out["time"] = m.group(1).replace(" ", "")

    # ცხრილიდან ფოთის სვეტი
    for table in tables:
        for row in table:
            if not row or len(row) < 2:
                continue
            key = _label_key(row[0])
            if key in FIELD_MAP:
                poti_val = row[POTI_COL] if len(row) > POTI_COL else None
                out["poti"][FIELD_MAP[key]] = _num(poti_val)

    return out


def wave_cm_to_m(wave_cm):
    """(155.0, 225.0) სმ → (1.55, 2.25) მ; ერთი რიცხვი → მეტრი; None → None."""
    if wave_cm is None:
        return None
    if isinstance(wave_cm, tuple):
        return (round(wave_cm[0] / 100, 2), round(wave_cm[1] / 100, 2))
    if isinstance(wave_cm, (int, float)):
        return round(wave_cm / 100, 2)
    return None


if __name__ == "__main__":
    import sys, json
    path = sys.argv[1] if len(sys.argv) > 1 else \
        "/mnt/user-data/uploads/1785128406815_ფაქტიური_ამინდი_ფოთი___14_5950__26_07_2026_წ_____16_სთ_.pdf"
    result = parse_actual_weather(path)
    # ტალღა მეტრებში დავამატოთ მოხერხებულობისთვის
    if result["poti"].get("wave_cm"):
        result["poti"]["wave_m"] = wave_cm_to_m(result["poti"]["wave_cm"])
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════
#  Storm warning პარსერი (თავისუფალი ტექსტი)
# ═══════════════════════════════════════════════════════════════

def parse_storm_warning(pdf_path: str) -> dict:
    """'საშტორმო გაფრთხილება' — თავისუფალი ტექსტი, ინგლისური ბლოკიდან ვიღებთ.

    სტრუქტურა:
      In the area of Poti-Kulevi will be ... wind 8-13 m/sec ... gust until to 17 m/sec.
      Sea 4 (w.h. 125-250 cm.).
      a.w. (Poti) Wind- W 6-7 m/sec.        ← nowcast (actual)
      Sea waves 3 (w.h. 89-116 cm).         ← nowcast sea
    """
    out = {"type": "storm_warning", "poti": {}}
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""

    m = re.search(r"№\s*([\d/]+)", text)
    if m: out["bulletin_no"] = m.group(1)
    m = re.search(r"(\d{2}\.\d{2}\.\d{2}\s*წ\.\s*\d{1,2}:\d{2})", text)
    if m: out["issued"] = m.group(1).strip()

    # ── forecast ნაწილი (მთელი აკვატორია) ──
    fc = {}
    m = re.search(r"wind\s+(\d+)-(\d+)\s*m/sec.*?gust\s+until\s+to\s+(\d+)\s*m/sec", text, re.I)
    if m:
        fc["wind_range"] = (float(m.group(1)), float(m.group(2)))
        fc["gust_max"] = float(m.group(3))
    m = re.search(r"Sea\s+(\d+)\s*\(w\.h\.\s*(\d+)-(\d+)\s*cm", text, re.I)
    if m:
        fc["sea_state"] = float(m.group(1))
        fc["wave_cm"] = (float(m.group(2)), float(m.group(3)))
    out["forecast"] = fc

    # ── nowcast ნაწილი: a.w. (Poti) ──
    m = re.search(r"a\.w\.\s*\(Poti\)\s*Wind-?\s*([NSEW]{1,2})\s*(\d+)-(\d+)\s*m/sec", text, re.I)
    if m:
        out["poti"]["wind_dir_txt"] = m.group(1).upper()
        out["poti"]["wind_range"] = (float(m.group(2)), float(m.group(3)))
    # Sea waves N (w.h. XX-YY cm) — პირველი "Sea waves" ფოთისაა
    m = re.search(r"Sea\s+waves\s+(\d+)\s*\(w\.h\.\s*(\d+)-(\d+)\s*cm", text, re.I)
    if m:
        out["poti"]["sea_state"] = float(m.group(1))
        out["poti"]["wave_cm"] = (float(m.group(2)), float(m.group(3)))

    return out


# ═══════════════════════════════════════════════════════════════
#  Forecast პარსერი (ცხრილი, day/night)
# ═══════════════════════════════════════════════════════════════

FC_FIELD_MAP = {
    "wind (speed m/s)":         "wind_speed",   # value შემდეგ 'note' რიგშია
    "wave height (cm)":         "wave_cm",
    "sea state (duglas force)": "sea_state",
    "visibility (miles)":       "vis_miles",
}


def _fc_num_pair(val: str):
    """'50-125/ 70-160' → {'lo': (50,125), 'hi': (70,160)} (ორი დიაპაზონი 6სთ-ბლოკებზე);
       '6-11' → (6,11); '3/ 3-4' → {'a':3,'b':(3,4)}. აბრუნებს raw-ს + parsed-ს."""
    if not val or not val.strip():
        return None
    return val.strip()  # raw ვინახავთ; ინტერპრეტაცია მოგვიანებით (day-ს 2 ბლოკი აქვს)


def parse_forecast(pdf_path: str) -> dict:
    """'ამინდის პროგნოზი ფოთი' — ცხრილი. ფოთი = სვეტები 1(day),2(night).

    day/night სვეტებში ხშირად '50-125/ 70-160' ორი 6სთ-ბლოკია.
    ნედლ სტრიქონებს ვინახავთ — ინტერპრეტაცია კონსენსუსთან შედარებისას.
    """
    out = {"type": "forecast", "poti": {"day": {}, "night": {}}}
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        tables = page.extract_tables()

    m = re.search(r"№\s*(WF[\d/]+)", text)
    if m: out["bulletin_no"] = m.group(1)
    m = re.search(r"(\d{2}/\d{2}/\s*\d{4}.*?სთ-დან.*?\d{2}/\d{2}/\s*\d{4}.*?სთ-მდე)", text, re.S)
    if m: out["valid"] = re.sub(r"\s+", " ", m.group(1)).strip()

    prev_label = None
    for table in tables:
        for row in table:
            if not row or len(row) < 3:
                continue
            key = _label_key(row[0])
            # ფოთი: სვეტი 1 = day, სვეტი 2 = night
            day_val   = row[1] if len(row) > 1 else None
            night_val = row[2] if len(row) > 2 else None

            # wind speed მნიშვნელობა შემდეგ 'note' რიგშია (ცხრილის თავისებურება)
            if key in FC_FIELD_MAP:
                fld = FC_FIELD_MAP[key]
                if fld == "wind_speed" and not (day_val or "").strip():
                    prev_label = "wind_speed_pending"
                    continue
                out["poti"]["day"][fld]   = _fc_num_pair(day_val)
                out["poti"]["night"][fld] = _fc_num_pair(night_val)
            elif key.startswith("note") and prev_label == "wind_speed_pending":
                out["poti"]["day"]["wind_speed"]   = _fc_num_pair(day_val)
                out["poti"]["night"]["wind_speed"] = _fc_num_pair(night_val)
                prev_label = None
            # gust note (Occasional gusts 12-15)
            elif "note" in key and day_val and "gust" in day_val.lower():
                mm = re.search(r"(\d+)-(\d+)", day_val)
                if mm:
                    g = (float(mm.group(1)), float(mm.group(2)))
                    out["poti"]["day"]["gust"]   = g
                    out["poti"]["night"]["gust"] = g

    return out


def parse_mta_pdf(pdf_path: str, subject: str = "") -> dict:
    """Router — subject-ის მიხედვით სწორ პარსერს ირჩევს."""
    s = (subject or "").lower()
    if "ფაქტიური" in s or "actual" in s:
        return parse_actual_weather(pdf_path)
    if "საშტორმო" in s or "storm" in s:
        return parse_storm_warning(pdf_path)
    if "პროგნოზი" in s or "forecast" in s:
        return parse_forecast(pdf_path)
    # subject უცნობია — ვცდით შიგთავსით
    with pdfplumber.open(pdf_path) as pdf:
        txt = (pdf.pages[0].extract_text() or "").lower()
    if "საშტორმო გაფრთხილება" in txt or "storm warning" in txt:
        return parse_storm_warning(pdf_path)
    if "ფაქტიური ამინდი" in txt or "actual weather" in txt:
        return parse_actual_weather(pdf_path)
    if "პროგნოზი" in txt or "forecast" in txt:
        return parse_forecast(pdf_path)
    return {"type": "unknown"}
