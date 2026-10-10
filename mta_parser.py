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
    # დაემატა 2026-08-26: აქამდე რიცხვით ველებს ვიღებდით მხოლოდ,
    # რის გამოც ნალექი სრულიად გამორცხებოდა — მაშინ როცა სწორედ ის არის
    # არაერთი ცრუ-პოზიტივი/ცრუ-ნეგატივის დასადგენად.
    # მნიშვნელობა ტექსტურია ("No precipitation" / "უნალექოდ"),
    # ამიტომ შედარება ორობითია, არა რიცხვითი.
    "precipitation":                "precip",
    "cloud cover":                  "cloud",
    "rip current":                  "rip_current",
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
    """'155-225' → (155, 225) დიაპაზონი; '8' → 8.0; '20,2' → 20.2; ცარიელი → None."""
    if not val or not val.strip():
        return None
    val = val.strip()
    # ⚠ MTA ათწილადს ხშირად მძიმით წერს ("20,2", "1015,5"). ადრე ასეთი
    #   მნიშვნელობა ტექსტად რჩებოდა და mta_log.json-ში სტრიქონად იწერებოდა.
    num = val.replace(",", ".")
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)$", num)
    if m:
        return (float(m.group(1)), float(m.group(2)))
    m = re.match(r"^(\d+(?:\.\d+)?)$", num)
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
    # list-იც და tuple-იც: JSON-იდან წაკითხვისას tuple list-ად იქცევა,
    # რის გამოც ეს ფუნქცია None-ს აბრუნებდა და ტალღის შედარება ჩუმად ქრებოდა.
    if isinstance(wave_cm, (list, tuple)):
        if len(wave_cm) < 2:
            return round(float(wave_cm[0]) / 100, 2) if wave_cm else None
        return (round(float(wave_cm[0]) / 100, 2), round(float(wave_cm[1]) / 100, 2))
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

# კომპასის ტექსტი → გრადუსი. საჭიროა, რადგან საშტორმო
# ბიულეტენებში მიმართულება ასოებითაა ("E", "NE"), არა რიცხვით —
# პორტალთან შედარებისთვის კი გრადუსები გვჭირდება.
COMPASS_DEG = {
    "N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5,
    "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
    "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5,
    "W": 270.0, "WNW": 292.5, "NW": 315.0, "NNW": 337.5,
}


# ─── ხილვადობა და მოვლენის ტიპი საშტორმო ტექსტიდან ───
# დაემატა 2026-10-10. აქამდე საშტორმოს პარსერი მხოლოდ ქარსა და ზღვას
# ეძებდა. ნისლის გაფრთხილება (14.09, 10.10) ამ ველებს არ შეიცავს და
# ჩანაწერი ცარიელი (`poti: {}`) იწერებოდა — შიგთავსი სრულად იკარგებოდა.
#
# რეალური ფორმატი (14/7539):
#   In the area of Poti-Kulevi In the next 2-3 hours will be remained fog.
#   time to time heavi fog. visibility 0.1-0.5 miles.
#   A.W. Poti - visibility 200 meter.
#   kulevi- 10 miles.
# გაუქმებაში: "a.w. Poti-Kulevi visibility -10 miles."

MILE_KM = 1.852
_NUM = r"(\d+(?:[.,]\d+)?)"
_VIS_RE = re.compile(
    r"visibility\s*[-–:]?\s*" + _NUM + r"(?:\s*[-–]\s*" + _NUM + r")?\s*"
    r"(miles?|meters?|metres?|m\b|km\b)", re.I)


def _vis_to_km(val: str, unit: str) -> float:
    v = float(val.replace(",", "."))
    u = unit.lower()
    if u.startswith("mile"):
        return round(v * MILE_KM, 3)
    if u.startswith("km"):
        return v
    return round(v / 1000.0, 3)


def _parse_vis(fragment: str):
    """პირველი "visibility N[-M] unit" → (km_lo, km_hi, ნედლი ტექსტი) ან None."""
    m = _VIS_RE.search(fragment or "")
    if not m:
        return None
    lo = _vis_to_km(m.group(1), m.group(3))
    hi = _vis_to_km(m.group(2), m.group(3)) if m.group(2) else lo
    return lo, hi, m.group(0).strip()


def _storm_phenomena(text: str) -> list:
    """რა მოვლენაზეა გაფრთხილება. რამდენიმე შეიძლება იყოს ერთდროულად."""
    low = (text or "").lower()
    out = []
    if "fog" in low or "ნისლ" in low:
        out.append("fog")
    elif "mist" in low or "ბურუს" in low:
        out.append("mist")
    if "thunder" in low or "ელჭექ" in low or "ჭექა" in low:
        out.append("thunderstorm")
    if re.search(r"\bwind\b|\bgust", low):
        out.append("wind")
    if re.search(r"\bsea\b|w\.h\.", low):
        out.append("sea")
    return out


def _storm_issued(text: str):
    """"14.09.26წ. 07სთ20წთ." / "15.09.26 წ. 18:35 სთ." → ("14/09/2026", "07:20")."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{2,4})\s*წ?\.?\s*(\d{1,2})\s*(?:სთ\.?|[:.])\s*(\d{2})",
                  text or "")
    if not m:
        return None, None
    y = m.group(3)
    y = ("20" + y) if len(y) == 2 else y
    return f"{m.group(1)}/{m.group(2)}/{y}", f"{int(m.group(4)):02d}:{m.group(5)}"


def _english_block(text: str) -> str:
    """ინგლისური ნაწილი "STORM WARNING"-იდან ხელმოწერის თარიღამდე."""
    m = re.search(r"STORM\s+WARNING(.*?)(?:\n\s*\d{2}\.\d{2}\.\d{2}|$)", text or "", re.S | re.I)
    if not m:
        return ""
    return " ".join(m.group(1).split())[:500]


def parse_storm_cancellation(pdf_path: str) -> dict:
    """'საშტორმო გაფრთხილების გაუქმება' — შეიცავს ფაქტიურ ამინდს.

    ეს დამატებითი ground truth წერტილია — ორ რეგულარულ
    ფაქტიურ ბიულეტენს გარდა (04:20 და 16:20), და ყულევიც ცალკე.

    სტრუქტურა:
      STORM WARNING 14/6882 CANCELATION
      Actual Weather : Poti - E 6-10 m/sec. Sea swell 3 state (W.H. 74-119 cm).
                       Kulevi - NE  2-4 m/sec.
      26.08.2026  09:20

    ⚠ შენიშვნა: ქართულ და ინგლისურ ტექსტში ციფრები ზოგჯერ
    არ ემთხვევა (5-7 vs 6-10). ვიღებთ ინგლისურს — ის უფრო
    სტაბილურად ფორმატირებულია და პარსინგს უკეთესად ეძლევა.
    """
    out = {"type": "storm_cancel", "poti": {}, "kulevi": {}}
    with pdfplumber.open(pdf_path) as pdf:
        text = pdf.pages[0].extract_text() or ""

    m = re.search(r"№\s*([\d/]+)", text)
    if m:
        out["bulletin_no"] = m.group(1)

    # ── აკვატორიის ამოცნობა ──
    # ⚠ დაემატა 2026-09-16. MTA ᲑᲐᲗᲣᲛᲘᲡ ბიულეტენებსაც აგზავნის იმავე
    #   ფოსტაზე, ნომრები კი თანმიმდევრულია (ფოთი 14/7588, ბათუმი 14/7589).
    #   თუ ბათუმის ბიულეტენი ფოთის მონაცემად ჩაიწერება, კალიბრაცია
    #   დაბინძურდება. `area` ველი ამას გამორიცხავს.
    low = text.lower()
    has_poti = ("poti" in low) or ("ფოთ" in text)
    has_batumi = ("batumi" in low) or ("ბათუმ" in text)
    out["area"] = "poti" if has_poti else ("batumi" if has_batumi else "unknown")

    # ── გაუქმებული გაფრთხილების ნომერი ──
    # ᲝᲠᲘ ᲤᲝᲠᲛᲐᲢᲘ გვხვდება:
    #   "STORM WARNING 14/6882 CANCELATION"
    #   "CANCELLATION THE STORM WARNING 14/7589"   ← 2026-09-16-ზე დაფიქსირდა
    # ⚠ 2026-10-10: "№" ნიშანი დაემატა — რეალურ PDF-ში წერია
    #   "STORM WARNING №14/7539 Cancelation" და ძველი ნიმუში ვერ იჭერდა.
    for pat in (r"STORM\s+WARNING\s+№?\s*([\d/]+)\s+CANCEL",
                r"CANCEL\w*\s+(?:THE\s+)?STORM\s+WARNING\s+№?\s*([\d/]+)",
                r"storm\s+warning\s+№?\s*([\d/]+)\s+is\s+cancel",
                r"საშტორმო\s+გაფრთხილება\s+№?\s*([\d/]+)\s*-?\s*ის\s+გაუქმება"):
        m = re.search(pat, text, re.I)
        if m:
            out["cancels"] = m.group(1)
            break

    # ── თარიღი და დრო ──
    # "26.08.2026  09:20"  და  "16.09.2026წ. 14:20სთ."
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s*წ?\.?\s*(\d{1,2})[:.](\d{2})",
                  text)
    if m:
        out["date"] = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
        out["time"] = f"{int(m.group(4)):02d}:{m.group(5)}"

    # ── ფოთი: "Poti - E 6-10 m/sec" ──
    m = re.search(r"Poti\s*[-–:]\s*([NSEW]{1,3})\s*(\d+)\s*-\s*(\d+)\s*m/sec", text, re.I)
    if m:
        d = m.group(1).upper()
        lo, hi = float(m.group(2)), float(m.group(3))
        out["poti"]["wind_dir_txt"] = d
        if d in COMPASS_DEG:
            out["poti"]["wind_dir"] = COMPASS_DEG[d]
        # _compare_to_portal-ის კონტრაქტი: საშუალო და მაქსიმუმი
        out["poti"]["wind_avg"] = lo
        out["poti"]["wind_max"] = hi
    else:
        # ── ალტერნატიული ფორმატი (2026-09-16) ──
        #   "A.W. Wind : NW 3 m/sec."      — ერთი მნიშვნელობა
        #   "A.W. Wind : NW 3-4 m/sec."    — დიაპაზონი
        m = re.search(r"A\.?\s*W\.?\s*Wind\s*[:\-–]\s*([NSEW]{1,3})\s*"
                      r"(\d+)(?:\s*-\s*(\d+))?\s*m/sec", text, re.I)
        if m:
            d = m.group(1).upper()
            lo = float(m.group(2))
            hi = float(m.group(3)) if m.group(3) else lo
            out["poti"]["wind_dir_txt"] = d
            if d in COMPASS_DEG:
                out["poti"]["wind_dir"] = COMPASS_DEG[d]
            out["poti"]["wind_avg"] = lo
            out["poti"]["wind_max"] = hi

    # ── ტალღა: "Sea swell 3 state (W.H. 74-119 cm)" ──
    m = re.search(r"Sea\s+swell\s+(\d+)\s*state\s*\(?\s*W\.?H\.?\s*(\d+)\s*-\s*(\d+)\s*cm",
                  text, re.I)
    if m:
        out["poti"]["sea_state"] = float(m.group(1))
        out["poti"]["wave_cm"] = (float(m.group(2)), float(m.group(3)))
    else:
        # "Sea 3 state. w.h. ( 50-125 cm.)" — პროგნოზის ნაწილი
        m = re.search(r"Sea\s+(\d+)\s*state\.?\s*w\.?h\.?\s*\(?\s*"
                      r"(\d+)\s*-\s*(\d+)\s*cm", text, re.I)
        if m:
            fc_sea = (float(m.group(1)), float(m.group(2)), float(m.group(3)))
        else:
            fc_sea = None
        # "A.W. ... Sea : 3 state." — ფაქტიური ბალიანობა, სიმაღლის გარეშე
        m = re.search(r"Sea\s*[:\-–]\s*(\d+)\s*state", text, re.I)
        if m:
            out["poti"]["sea_state"] = float(m.group(1))
        elif fc_sea:
            out["poti"]["sea_state"] = fc_sea[0]
        if fc_sea:
            out.setdefault("forecast", {})["sea_state"] = fc_sea[0]
            out["forecast"]["wave_cm"] = (fc_sea[1], fc_sea[2])

    # ── ყულევი: "Kulevi - NE  2-4 m/sec" ──
    m = re.search(r"Kulevi\s*[-–:]\s*([NSEW]{1,3})\s*(\d+)\s*-\s*(\d+)\s*m/sec", text, re.I)
    if m:
        d = m.group(1).upper()
        out["kulevi"]["wind_dir_txt"] = d
        if d in COMPASS_DEG:
            out["kulevi"]["wind_dir"] = COMPASS_DEG[d]
        out["kulevi"]["wind_avg"] = float(m.group(2))
        out["kulevi"]["wind_max"] = float(m.group(3))

    # ── ხილვადობა (ნისლის გაუქმება): "a.w. Poti-Kulevi visibility -10 miles" ──
    m = re.search(r"Poti[^\n]*?visibility[^\n]*", text, re.I)
    v = _parse_vis(m.group(0)) if m else None
    if v:
        out["poti"]["vis_km"] = v[0]

    # თარიღი ორნიშნა წლით ("14.09.26წ. 10:00სთ.") ზემოთ არ იჭერდა
    if "date" not in out:
        d, t = _storm_issued(text)
        if d:
            out["date"], out["time"] = d, t
    out["text_eng"] = _english_block(text)
    return out


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

    # ── ალტერნატიული ფორმატი (14/7588):
    #   "Wind NW/W 6-11 m/sec, with ocasionally gusts 12-15 m/sec."
    #   "Sea 3-4/ 4 state (W.H 80-180/ 125-250cm)"
    #   "A.W. (Poti)- SW 4-5 m/sec."
    if "wind_range" not in fc:
        m = re.search(r"Wind\s+[NSEW/]+\s+(\d+)\s*-\s*(\d+)\s*m/sec.{0,40}?gusts?\s+"
                      r"(\d+)(?:\s*-\s*(\d+))?\s*m/sec", text, re.I | re.S)
        if m:
            fc["wind_range"] = (float(m.group(1)), float(m.group(2)))
            fc["gust_max"] = float(m.group(4) or m.group(3))
    if "wave_cm" not in fc:
        m = re.search(r"sea\s+([\d\-/ ]+?)\s*state\s*\(\s*W\.?\s*H\.?\s*"
                      r"([\d\-/ ]+?)\s*cm", text, re.I)
        if m:
            nums = [float(x) for x in re.findall(r"\d+", m.group(2))]
            if len(nums) >= 2:
                fc["wave_cm"] = (min(nums), max(nums))
            st = [float(x) for x in re.findall(r"\d+", m.group(1))]
            if st:
                fc["sea_state"] = max(st)
    if "wind_range" not in out["poti"]:
        m = re.search(r"a\.\s*w\.\s*\(Poti\)\s*[-–:]?\s*([NSEW]{1,3})\s*(\d+)\s*-\s*(\d+)\s*m/sec",
                      text, re.I)
        if m:
            out["poti"]["wind_dir_txt"] = m.group(1).upper()
            out["poti"]["wind_range"] = (float(m.group(2)), float(m.group(3)))

    # ── ნისლი / ხილვადობა (დაემატა 2026-10-10) ──
    # ინგლისური ბლოკი ორ ნაწილად: პროგნოზი "A.W."-მდე, nowcast — მის შემდეგ.
    eng = _english_block(text)
    out["text_eng"] = eng
    out["phenomena"] = _storm_phenomena(eng or text)
    parts = re.split(r"(?i)\ba\.\s*w\.", eng, maxsplit=1)
    fc_part = parts[0]
    aw_part = parts[1] if len(parts) > 1 else ""
    v = _parse_vis(fc_part)
    if v:
        fc["vis_km"] = (v[0], v[1])
        fc["vis_text"] = v[2]
    i = aw_part.lower().find("poti")
    v = _parse_vis(aw_part[i:]) if i >= 0 else None
    if v:
        out["poti"]["vis_km"] = v[0]

    d, t = _storm_issued(text)
    if d:
        out["date"], out["time"] = d, t

    return out


# ═══════════════════════════════════════════════════════════════
#  Forecast პარსერი (ცხრილი, day/night)
# ═══════════════════════════════════════════════════════════════

FC_FIELD_MAP = {
    "wind (speed m/s)":         "wind_speed",   # value შემდეგ 'note' რიგშია
    "wave height (cm)":         "wave_cm",
    "sea state (duglas force)": "sea_state",
    "visibility (miles)":       "vis_miles",
    # დაემატა 2026-08-26. ⚠ პროგნოზის ცხრილში ეს ეტიკეტები
    # ნაგულისხმევია — რეალურ PDF-ზე გადამოწმებას ისახებს.
    # თუ სახელი არ ემთხვევა, ველი უბრალოდ არ ამოიცნობა —
    # შედარება გამოიტოვება, შეცდომა არ მოხდება.
    "precipitation":            "precip",
    "cloud cover":              "cloud",
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
    last_field = None
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
                last_field = fld
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
                last_field = None

            # დანარჩენი note-ები — ახლა აღარ იკარგება (დაემატა 2026-08-26).
            #
            # სწორედ აქ ზიბა სინოპტიკოსის თვისებრივი შეფასება, რომელსაც
            # მოდელი ვერ აწარმოებს: "მოსალოდნელია ელჭექა", ადგილობრივი
            # გაძლიერება და სხვ. ვინახავთ ბოლო ველზე მიმაგრებულად.
            elif "note" in key and last_field:
                for col, period in ((day_val, "day"), (night_val, "night")):
                    t = re.sub(r"\s+", " ", (col or "")).strip()
                    if t:
                        out["poti"][period][f"{last_field}_note"] = t
                last_field = None

    return out


def parse_mta_pdf(pdf_path: str, subject: str = "") -> dict:
    """Router — subject-ის მიხედვით სწორ პარსერს ირჩევს."""
    s = (subject or "").lower()
    if "ფაქტიური" in s or "actual" in s:
        return parse_actual_weather(pdf_path)
    # გაუქმება ცალკე ტიპია — სხვა სტრუქტურა აქვს და ფაქტიურ ამინდს შეიცავს
    if "გაუქმებ" in s or "cancel" in s:
        return parse_storm_cancellation(pdf_path)
    if "საშტორმო" in s or "storm" in s:
        return parse_storm_warning(pdf_path)
    if "პროგნოზი" in s or "forecast" in s:
        return parse_forecast(pdf_path)
    # subject უცნობია — ვცდით შიგთავსით
    with pdfplumber.open(pdf_path) as pdf:
        txt = (pdf.pages[0].extract_text() or "").lower()
    if "cancelation" in txt or "cancellation" in txt or "გაუქმებ" in txt:
        return parse_storm_cancellation(pdf_path)
    if "საშტორმო გაფრთხილება" in txt or "storm warning" in txt:
        return parse_storm_warning(pdf_path)
    if "ფაქტიური ამინდი" in txt or "actual weather" in txt:
        return parse_actual_weather(pdf_path)
    if "პროგნოზი" in txt or "forecast" in txt:
        return parse_forecast(pdf_path)
    return {"type": "unknown"}
