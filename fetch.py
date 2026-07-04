"""
ფოთის პორტი — ამინდის კონსენსუს-ბექენდი
==========================================
გაშვება:  python fetch.py
შედეგი:   data.json

წყაროები:
  1. Open-Meteo / best_match  (უფასო)
  2. Open-Meteo / GFS         (უფასო)
  3. Open-Meteo / ICON-EU     (უფასო)
  4. Open-Meteo / ECMWF       (უფასო, ნამდვილი ECMWF IFS HRES 9კმ — open-data 2025 ოქტომბრიდან)
  5. Open-Meteo Marine        (უფასო)
  6. yr.no / MET Norway       (უფასო, გასაღები არ სჭირდება)
  7. Stormglass.io            (უფასო გასაღებით, 10req/დღე)
  8. OpenWeatherMap           (უფასო გასაღებით)

  [გათავისუფლებული] Windy.com — Point Forecast API ლიცენზირების მიზეზით
  ECMWF-ს არასდროს გასცემს, და უფასო tier შეგნებულად "დანოისებულ" მონაცემს
  აბრუნებს (production-ისთვის არ ვარგა). იხ. fetch_windy()-ის თავზე კომენტარი.

გასაღებები (Windows: set, Mac/Linux: export):
  STORMGLASS_API_KEY   ← stormglass.io
  OWM_API_KEY          ← openweathermap.org
  TELEGRAM_BOT_TOKEN   ← @BotFather-დან
  TELEGRAM_CHAT_ID     ← შენი chat ID
"""

import json, math, os, logging, time, sys
from datetime import datetime, timedelta, timezone

import requests
import urllib3
urllib3.disable_warnings()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _session():
    s = requests.Session()
    r = Retry(
        total=3,
        read=3,                        # ReadTimeout-ზეც გაიმეოროს (ადრე ეს არ იყო!)
        connect=3,
        backoff_factor=2,              # 0წმ, 2წმ, 4წმ შუალედებით
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    s.mount("https://", HTTPAdapter(max_retries=r))
    return s

requests_session = _session()

# ─────────────────────────────────────────────
LOCATION   = {"name": "ფოთის პორტი", "lat": 42.15, "lon": 41.67, "timezone": "Asia/Tbilisi"}
TBILISI_TZ = timezone(timedelta(hours=4))   # საქართველოს დროის ზონა (UTC+4, DST არ აქვს)

FORECAST_HOURS      = 48
DAILY_FORECAST_DAYS = 7   # კვირის ხედი — დღიური აგრეგატები, საათობრივი ჩაშლის გარეშე
REQUEST_TIMEOUT     = 30  # 15→30წმ: Open-Meteo timeout-ის წინააღმდეგ
OUTPUT_FILE         = "data.json"
STATUS_CACHE        = "status_cache.json"
SOS_CACHE           = "sos_cache.json"
STORMGLASS_CACHE    = "stormglass_cache.json"
YR_NO_CACHE         = "yr_cache.json"
STORMGLASS_INTERVAL = 3
YR_NO_INTERVAL      = 1   # yr.no ყოველ საათში განახლდება

THRESHOLDS = {
    # cell_selection="sea"-ის შემდეგ ჩვენი კონსენსუსი პიკ-გასტებს ~80-85%-ზე "ხედავს"
    # (MTA 27-28 ივნ: real gusts ~17-18, ჩვენი peak: 14.2 → კოეფ. ~0.82).
    # ამიტომ ზღვრები კალიბრირებულია:  actual_limit × 0.82 ≈ our_threshold
    "wind_speed":  12.0,   # 15.0 × 0.82 ≈ 12.3 — ყვითელი ზონა (ბორანი ≥10 MTA)
    "wind_gusts":  17.5,   # 21.5 × 0.82 ≈ 17.6 — წითელი ზონა (გემ.გასვლა ≥21.5 MTA)
    "wave_height":  1.50,  # უცვლელი — Marine API კარგად ახდენს ტალღის კალიბრაციას
    "visibility":   1.0,   # უცვლელი
}

BASE_WEIGHTS = {
    "best_match": 0.22, "gfs": 0.13, "icon_eu": 0.09, "ecmwf": 0.25,
    "yr_no": 0.13, "stormglass": 0.17, "owm": 0.08,
}
WAVE_WEIGHTS = {"marine": 0.55, "stormglass": 0.45}

STORMGLASS_API_KEY = os.environ.get("STORMGLASS_API_KEY", "")
WINDY_API_KEY      = os.environ.get("WINDY_API_KEY",      "")
OWM_API_KEY        = os.environ.get("OWM_API_KEY",        "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
#  1.  API მოთხოვნები
# ═══════════════════════════════════════════════════════════════

def fetch_open_meteo_daily():
    """7-დღიანი დღიური აგრეგატები (არა საათობრივი) — კვირის ხედისთვის."""
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "wind_speed_10m_max,wind_gusts_10m_max,wind_direction_10m_dominant,weather_code",
        "wind_speed_unit": "ms",
        "forecast_days": DAILY_FORECAST_DAYS,
        "timezone": LOCATION["timezone"],
        "cell_selection": "sea",
    }
    try:
        r = requests_session.get("https://api.open-meteo.com/v1/forecast",
                         params=params, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
        log.info("Open-Meteo Daily ✓")
        return r.json()
    except Exception as e:
        log.warning(f"Open-Meteo Daily ✗ — {e}")
        return None


def fetch_open_meteo_marine_daily():
    """7-დღიანი ტალღის მაქს. სიმაღლე."""
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "daily": "wave_height_max,wave_period_max",
        "forecast_days": DAILY_FORECAST_DAYS,
        "timezone": LOCATION["timezone"],
    }
    try:
        r = requests_session.get("https://marine-api.open-meteo.com/v1/marine",
                         params=params, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
        log.info("Open-Meteo Marine Daily ✓")
        return r.json()
    except Exception as e:
        log.warning(f"Open-Meteo Marine Daily ✗ — {e}")
        return None


def fetch_open_meteo_atmosphere(model: str):
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                  "temperature_2m,precipitation,visibility,weather_code",
        "wind_speed_unit": "ms",
        "forecast_days": 3,           # 72h ქაჩავს, 48h გამოვიყენებთ
        "timezone": LOCATION["timezone"],
        "cell_selection": "sea",      # ნაგულისხმევი 'land' შინაგან/დაცულ წერტილს
                                       # არჩევდა — სანაპირო პორტისთვის ღია წყლის
                                       # grid-cell უფრო რეალურ ექსპოზიციას მისცემს
    }
    if model != "best_match":
        params["models"] = model
    try:
        r = requests_session.get("https://api.open-meteo.com/v1/forecast",
                         params=params, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
        log.info(f"Open-Meteo [{model}] ✓")
        return r.json()
    except Exception as e:
        log.warning(f"Open-Meteo [{model}] ✗ — {e}")
        return None


def fetch_open_meteo_marine():
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "hourly": "wave_height,wave_period,wave_direction,"
                  "wind_wave_height,swell_wave_height",
        "forecast_days": 3,
        "timezone": LOCATION["timezone"],
    }
    try:
        r = requests_session.get("https://marine-api.open-meteo.com/v1/marine",
                         params=params, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
        log.info("Open-Meteo Marine ✓")
        return r.json()
    except Exception as e:
        log.warning(f"Open-Meteo Marine ✗ — {e}")
        return None


def fetch_stormglass():
    if not STORMGLASS_API_KEY:
        log.info("Stormglass გამოტოვებულია (STORMGLASS_API_KEY არ არის)")
        return None
    cached = _load_stormglass_cache()
    if cached:
        log.info("Stormglass ✓ (კეშიდან)")
        return cached
    now   = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    end   = (now + timedelta(hours=FORECAST_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "lat": LOCATION["lat"], "lng": LOCATION["lon"],
        "params": "waveHeight,wavePeriod,waveDirection,swellHeight,"
                  "swellPeriod,windSpeed,windDirection,gust,"
                  "visibility,waterTemperature,currentSpeed",
        "start": start, "end": end,
    }
    try:
        r = requests.get("https://api.stormglass.io/v2/weather/point",
                         params=params,
                         headers={"Authorization": STORMGLASS_API_KEY},
                         timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        _save_stormglass_cache(data)
        log.info("Stormglass ✓ (API-დან)")
        return data
    except Exception as e:
        log.warning(f"Stormglass ✗ — {e}")
        return None


# ⚠️ Windy მთავარი კონსენსუსიდან გათავისუფლებულია (main()-ში არ გამოიძახება) — ორი მიზეზით:
# 1. Windy-ის Point Forecast API ლიცენზირების მიზეზით ECMWF-ს არასდროს გასცემს
#    (დაფიქსირებულია Windy-ის საკუთარ pricing-გვერდზე — "ECMWF model is not
#    included in point forecast due to licensing conditions", ფასიან გეგმაშიც).
#    ანუ "model": "ecmwf" მოთხოვნა ყოველთვის 400 Bad Request-ით ჩავარდება.
# 2. თუ WINDY_API_KEY უფასო "Testing" tier-ისაა — Windy-ის documentation პირდაპირ
#    წერს: "Returns randomly shuffled and slightly modified data — Development
#    purpose only, not intended for production". ანუ თუნდაც სწორი model-ით
#    (gfs/iconEu/gfsWave) გაშვებულიყო, მონაცემი შეგნებულად "დანოისებული" იქნებოდა —
#    არ ვარგა საოპერაციო გადაწყვეტილებისთვის.
# რეალური ECMWF ახლა Open-Meteo-დან მოდის (fetch_open_meteo_atmosphere("ecmwf_ifs")) —
# უფასო, ნამდვილი, 9კმ რეზოლუციით (ECMWF-მა open-data გახსნა 2025 ოქტომბრიდან).
# ფუნქცია ქვემოთ შენარჩუნებულია მხოლოდ საინფორმაციოდ/მომავლისთვის.
def fetch_windy():
    if not WINDY_API_KEY:
        log.info("Windy გამოტოვებულია (WINDY_API_KEY არ არის)")
        return None
    payload = {
        "lat": LOCATION["lat"], "lon": LOCATION["lon"],
        "model": "ecmwf",
        "parameters": ["wind", "windGust", "waves", "precip"],
        "levels": ["surface"],
        "key": WINDY_API_KEY,
    }
    try:
        r = requests.post("https://api.windy.com/api/point-forecast/v2",
                          json=payload, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        log.info("Windy/ECMWF ✓")
        return r.json()
    except Exception as e:
        log.warning(f"Windy ✗ — {e}")
        return None


def fetch_openweathermap():
    if not OWM_API_KEY:
        log.info("OpenWeatherMap გამოტოვებულია (OWM_API_KEY არ არის)")
        return None
    params = {"lat": LOCATION["lat"], "lon": LOCATION["lon"],
              "appid": OWM_API_KEY, "units": "metric", "cnt": 40}
    try:
        r = requests.get("https://api.openweathermap.org/data/2.5/forecast",
                         params=params, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        log.info("OpenWeatherMap ✓")
        return r.json()
    except Exception as e:
        log.warning(f"OpenWeatherMap ✗ — {e}")
        return None


def fetch_yr_no():
    """
    yr.no / MET Norway Locationforecast API — სრულიად უფასო, გასაღები არ სჭირდება.
    Norwegian Meteorological Institute — ECMWF გლობალური მოდელი.
    წესი: User-Agent სავალდებულოა, კეში — ყოველ 1 საათში.
    """
    cached = _load_yr_cache()
    if cached:
        log.info("yr.no ✓ (კეშიდან)")
        return cached

    headers = {
        "User-Agent": (
            "PotiPortalForecast/1.0 "
            "github.com/Georgeparker-GP/poti-forecast-portal"
        )
    }
    params = {
        "lat": round(LOCATION["lat"], 4),
        "lon": round(LOCATION["lon"], 4),
    }
    try:
        r = requests.get(
            "https://api.met.no/weatherapi/locationforecast/2.0/compact",
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        _save_yr_cache(data)
        log.info("yr.no ✓ (API-დან)")
        return data
    except Exception as e:
        log.warning(f"yr.no ✗ — {e}")
        return None


# ═══════════════════════════════════════════════════════════════
#  2.  Stormglass კეში
# ═══════════════════════════════════════════════════════════════

def _load_stormglass_cache():
    try:
        with open(STORMGLASS_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(c["_cached_at"])).total_seconds() / 3600
        if age < STORMGLASS_INTERVAL:
            log.info(f"Stormglass კეში: {age:.1f}სთ ძველი")
            return c["data"]
    except (FileNotFoundError, KeyError, ValueError):
        pass
    return None


def _save_stormglass_cache(data):
    try:
        with open(STORMGLASS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"_cached_at": datetime.now().isoformat(), "data": data},
                      f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"კეშის შენახვა ✗ — {e}")


def _load_yr_cache():
    try:
        with open(YR_NO_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(c["_cached_at"])).total_seconds() / 3600
        if age < YR_NO_INTERVAL:
            log.info(f"yr.no კეში: {age:.1f}სთ ძველი")
            return c["data"]
    except (FileNotFoundError, KeyError, ValueError):
        pass
    return None


def _save_yr_cache(data):
    try:
        with open(YR_NO_CACHE, "w", encoding="utf-8") as f:
            json.dump({"_cached_at": datetime.now().isoformat(), "data": data},
                      f, ensure_ascii=False)
    except Exception as e:
        log.warning(f"yr.no კეშის შენახვა ✗ — {e}")


# ═══════════════════════════════════════════════════════════════
#  3.  პარსინგი
# ═══════════════════════════════════════════════════════════════

def parse_open_meteo_daily(raw, days=DAILY_FORECAST_DAYS):
    if not raw or "daily" not in raw:
        return []
    d = raw["daily"]
    n = min(days, len(d.get("time", [])))
    return [
        {
            "date":      d["time"][i],
            "temp_max":  _safe(d.get("temperature_2m_max", []), i, default=None),
            "temp_min":  _safe(d.get("temperature_2m_min", []), i, default=None),
            "precip_sum": _safe(d.get("precipitation_sum", []), i),
            "wind_max":  _safe(d.get("wind_speed_10m_max", []), i),
            "gust_max":  _safe(d.get("wind_gusts_10m_max", []), i),
            "wind_dir":  _safe(d.get("wind_direction_10m_dominant", []), i, default=None),
        }
        for i in range(n)
    ]


def parse_marine_daily(raw, days=DAILY_FORECAST_DAYS):
    if not raw or "daily" not in raw:
        return []
    d = raw["daily"]
    n = min(days, len(d.get("time", [])))
    return [
        {"date": d["time"][i], "wave_max": _safe(d.get("wave_height_max", []), i)}
        for i in range(n)
    ]


def build_daily_summary(daily_atmo, daily_marine):
    """ანაერთებს ატმოსფერულ + ტალღის დღიურ მონაცემებს, სტატუსს ანგარიშობს დღის
    მაქსიმუმებზე დაყრდნობით (ხილვადობის აგრეგატი Open-Meteo-ს daily-ში არ არსებობს,
    ამიტომ ეს განზომილება დღიურ სტატუსში არ ფასდება)."""
    wave_by_date = {w["date"]: w["wave_max"] for w in daily_marine}
    result = []
    for a in daily_atmo:
        wave_max = wave_by_date.get(a["date"], 0.0)
        status, alerts = _compute_status(a["wind_max"], a["gust_max"], wave_max, 999)
        result.append({**a, "wave_max": wave_max, "status": status, "alerts": alerts})
    return result


def parse_open_meteo_atmosphere(raw, hours=FORECAST_HOURS):
    h = raw["hourly"]
    return [
        {
            "time":           h["time"][i],
            "wind_speed":     _safe(h["wind_speed_10m"], i),
            "wind_gusts":     _safe(h["wind_gusts_10m"], i),
            "wind_direction": _safe(h.get("wind_direction_10m", []), i),
            "precipitation":  _safe(h["precipitation"], i),
            "visibility_km":  _safe(h["visibility"], i, scale=0.001),
            "weather_code":   _safe(h.get("weather_code", []), i, default=0),
            "air_temp":       _safe(h.get("temperature_2m", []), i, default=None),
        }
        for i in range(min(hours, len(h["time"])))
    ]


def parse_open_meteo_marine(raw, hours=FORECAST_HOURS):
    h = raw["hourly"]
    return [
        {
            "time":              h["time"][i],
            "wave_height":       _safe(h["wave_height"], i),
            "wave_period":       _safe(h["wave_period"], i),
            "wave_direction":    _safe(h["wave_direction"], i),
            "wind_wave_height":  _safe(h.get("wind_wave_height", []), i),
            "swell_wave_height": _safe(h.get("swell_wave_height", []), i),
        }
        for i in range(min(hours, len(h["time"])))
    ]


def parse_stormglass(raw, hours=FORECAST_HOURS):
    result = []
    for entry in raw.get("hours", [])[:hours]:
        def sg(key):
            d = entry.get(key, {})
            if not isinstance(d, dict): return 0.0
            for src in ["sg", "noaa", "icon", "dwd", "meto"]:
                if src in d and d[src] is not None:
                    return round(float(d[src]), 3)
            vals = [v for v in d.values() if v is not None]
            return round(float(vals[0]), 3) if vals else 0.0

        result.append({
            "time":           entry.get("time", "")[:16].replace(" ", "T"),
            "wind_speed":     sg("windSpeed"),
            "wind_gusts":     sg("gust"),
            "wind_direction": sg("windDirection"),
            "precipitation":  0.0,
            "visibility_km":  sg("visibility"),
            "wave_height":    sg("waveHeight"),
            "wave_period":    sg("wavePeriod"),
            "wave_direction": sg("waveDirection"),
            "swell_height":   sg("swellHeight"),
            "swell_period":   sg("swellPeriod"),
            "water_temp":     sg("waterTemperature"),
            "current_speed":  sg("currentSpeed"),
        })
    return result


def parse_windy(raw, hours=FORECAST_HOURS):
    ts_list   = raw.get("ts", [])
    u_list    = raw.get("wind_u-surface", [])
    v_list    = raw.get("wind_v-surface", [])
    gust_list = raw.get("gust-surface",  [])
    wave_list = raw.get("waves-surface", [])
    prec_list = raw.get("precip-surface", raw.get("past_precip-surface", []))

    result = []
    for i in range(min(hours, len(ts_list))):
        dt  = datetime.utcfromtimestamp(ts_list[i] / 1000) + timedelta(hours=4)
        u   = u_list[i] if i < len(u_list) else 0.0
        v   = v_list[i] if i < len(v_list) else 0.0
        spd = round(math.sqrt(u**2 + v**2), 2)
        # მეტეოროლოგიური მიმართულება (საიდან ქრის)
        wdir = round((270 - math.degrees(math.atan2(v, u))) % 360, 1)
        result.append({
            "time":           dt.strftime("%Y-%m-%dT%H:00"),
            "wind_speed":     spd,
            "wind_gusts":     round(gust_list[i], 2) if i < len(gust_list) else spd,
            "wind_direction": wdir,
            "precipitation":  round(prec_list[i],  2) if i < len(prec_list) else 0.0,
            "visibility_km":  10.0,
            "wave_height":    round(wave_list[i],  2) if i < len(wave_list) else 0.0,
        })
    return result


def parse_openweathermap(raw, hours=FORECAST_HOURS):
    result = []
    for entry in raw.get("list", [])[:16]:
        dt_str = entry["dt_txt"]
        wind   = entry["wind"]
        rain   = entry.get("rain", {}).get("3h", 0.0) / 3.0
        vis    = entry.get("visibility", 10000) / 1000.0
        for offset in range(3):
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S") + timedelta(hours=offset)
            result.append({
                "time":           dt.strftime("%Y-%m-%dT%H:%M"),
                "wind_speed":     round(wind["speed"], 2),
                "wind_gusts":     round(wind.get("gust", wind["speed"]), 2),
                "wind_direction": round(wind.get("deg", 0), 1),
                "precipitation":  round(rain, 2),
                "visibility_km":  round(vis, 2),
                "weather_code":   entry["weather"][0]["id"],
            })
    return result[:hours]


def parse_yr_no(raw, hours=FORECAST_HOURS):
    """
    yr.no compact პასუხი:
      properties.timeseries[]:
        time                                    — UTC timestamp
        data.instant.details.wind_speed         — მ/წმ
        data.instant.details.wind_from_direction — გრადუსი
        data.instant.details.fog_area_fraction  — % (ხილვადობის proxy)
        data.next_1_hours.details.precipitation_amount — მმ
    
    შენიშვნა: yr.no-ს compact-ში wind gusts არ არის →
    ქარის კონსენსუსში მხოლოდ wind_speed და wind_direction მონაწილეობს.
    """
    result = []
    timeseries = raw.get("properties", {}).get("timeseries", [])

    for entry in timeseries[:hours]:
        time_str = entry.get("time", "")[:16]   # "2026-06-11T10:00"
        instant  = entry.get("data", {}).get("instant", {}).get("details", {})
        next1h   = entry.get("data", {}).get("next_1_hours", {}).get("details", {})

        wind_speed = round(float(instant.get("wind_speed", 0) or 0), 2)
        wind_dir   = round(float(instant.get("wind_from_direction", 0) or 0), 1)
        precip     = round(float(next1h.get("precipitation_amount", 0) or 0), 2)

        # ნისლი → ხილვადობა: fog_area_fraction 0-100%
        fog = float(instant.get("fog_area_fraction", 0) or 0)
        # 0% ნისლი → 10კმ, 100% ნისლი → 0.1კმ (ლოგარითმული)
        vis_km = round(max(0.1, 10.0 * (1.0 - fog / 100.0)), 2)

        result.append({
            "time":           time_str,
            "wind_speed":     wind_speed,
            "wind_gusts":     round(wind_speed * 1.25, 2),  # კონსერვ. შეფასება
            "wind_direction": wind_dir,
            "precipitation":  precip,
            "visibility_km":  vis_km,
        })

    return result[:hours]


# ═══════════════════════════════════════════════════════════════
#  4.  კონსენსუსი
# ═══════════════════════════════════════════════════════════════

def compute_consensus(atmo_best, atmo_gfs, atmo_icon, atmo_ecmwf, marine, stormglass, yr_no, owm):
    # 1. ძირითადი სრული აუზი (ნალექისთვის, ხილვადობისთვის და ფოლბექისთვის)
    atmo_pool = []
    for src, key in [
        (atmo_best,  "best_match"), (atmo_gfs,    "gfs"),
        (atmo_icon,  "icon_eu"),    (yr_no,        "yr_no"),
        (atmo_ecmwf, "ecmwf"),      (stormglass,   "stormglass"),
        (owm,        "owm"),
    ]:
        if src:
            atmo_pool.append((src, BASE_WEIGHTS[key]))

    total_w = sum(w for _, w in atmo_pool)

    # 2. ქარის "ელიტური სამეული" (ნამდვილი ECMWF/Open-Meteo, yr.no/MET Norway, ICON-EU)
    elite_wind_pool = []
    for src, key in [
        (atmo_ecmwf, "ecmwf"), (yr_no, "yr_no"), (atmo_icon, "icon_eu")
    ]:
        if src:
            elite_wind_pool.append((src, BASE_WEIGHTS[key]))

    elite_w = sum(w for _, w in elite_wind_pool)

    # Fail-Safe: თუ ელიტური მოდელები მიუწვდომელია, ვბრუნდებით სრულ აუზზე
    active_wind_pool = elite_wind_pool if elite_wind_pool else atmo_pool
    active_wind_w    = elite_w if elite_wind_pool else total_w

    hours   = min(FORECAST_HOURS, len(atmo_best))
    result  = []

    for i in range(hours):
        # ─── ქარის ლოგიკა (მხოლოდ ელიტური აუზიდან) ───

        # 1. საშუალო სიჩქარე (Weighted average მხოლოდ ელიტებიდან)
        wind_speed = _wavg(active_wind_pool, i, "wind_speed", active_wind_w)

        # 2. ქარის დაქროლვები (GUSTS) — Veto პრინციპი: ვიღებთ აბსოლუტურ მაქსიმუმს!
        active_gusts = [
            src[i].get("wind_gusts", 0) 
            for src, _ in active_wind_pool 
            if i < len(src) and src[i].get("wind_gusts") is not None
        ]
        wind_gusts = max(active_gusts) if active_gusts else wind_speed

        # 3. ქარის მიმართულება (ვექტორული საშუალო მხოლოდ ელიტებიდან)
        wind_direction = _vector_avg_direction(active_wind_pool, i, "wind_direction", active_wind_w)

        # 4. Confidence — წყაროებს შორის ქარის სიჩქარის გაფანტვა (std dev).
        #    დაბალი gap = წყაროები თანხმდებიან = მაღალი ნდობა.
        wind_values = [
            src[i].get("wind_speed")
            for src, _ in active_wind_pool
            if i < len(src) and src[i].get("wind_speed") is not None
        ]
        wind_spread = _stddev(wind_values)

        # ─── დანარჩენი პარამეტრები ───
        precip = _wavg(atmo_pool, i, "precipitation", total_w)
        visibility = _wavg(atmo_pool, i, "visibility_km", total_w)

        # ტალღა (Marine, Stormglass — Windy აქ აღარ მონაწილეობს)
        wave_src = []
        if marine and i < len(marine) and marine[i].get("wave_height", 0) > 0:
            wave_src.append((marine[i]["wave_height"], WAVE_WEIGHTS["marine"]))
        if stormglass and i < len(stormglass) and stormglass[i].get("wave_height", 0) > 0:
            wave_src.append((stormglass[i]["wave_height"], WAVE_WEIGHTS["stormglass"]))

        if wave_src:
            ww = sum(w for _, w in wave_src)
            wave_h = sum(v * w for v, w in wave_src) / ww
        else:
            wave_h = _estimate_wave_from_wind(wind_speed)

        wave_p = marine[i].get("wave_period", 0) if marine and i < len(marine) else 0
        wave_d = marine[i].get("wave_direction", 0) if marine and i < len(marine) else 0

        swell = marine[i].get("swell_wave_height", 0) if marine and i < len(marine) else 0
        if stormglass and i < len(stormglass) and stormglass[i].get("swell_height", 0) > 0:
            swell = stormglass[i]["swell_height"]

        water_temp = stormglass[i].get("water_temp", 0.0) if stormglass and i < len(stormglass) else 0.0
        current_speed = stormglass[i].get("current_speed", 0.0) if stormglass and i < len(stormglass) else 0.0

        air_temp = atmo_best[i].get("air_temp")

        status, alerts = _compute_status(wind_speed, wind_gusts, wave_h, visibility)

        result.append({
            "time": atmo_best[i]["time"],
            "wind_speed": _r(wind_speed),
            "wind_gusts": _r(wind_gusts),
            "wind_direction": _r(wind_direction, 0),
            "precipitation": _r(precip),
            "visibility_km": _r(visibility),
            "wave_height": _r(wave_h),
            "wave_period": _r(wave_p),
            "wave_direction": _r(wave_d),
            "swell_height": _r(swell),
            "water_temp": _r(water_temp),
            "current_speed": _r(current_speed),
            "air_temp": round(air_temp, 1) if air_temp is not None else None,
            "status": status,
            "alerts": alerts,
            "wind_spread": _r(wind_spread, 2),
            "confidence": _confidence_level(wind_spread, len(wind_values)),
            "source_count": len(wind_values),
        })
    return result


CONF_HIGH_MAX   = 1.5   # σ < 1.5 მ/წმ → მაღალი ნდობა
CONF_MEDIUM_MAX = 3.0   # σ 1.5–3.0 → საშუალო; > 3.0 → დაბალი


def _stddev(values):
    """ნიმუშის სტანდარტული გადახრა. <2 მნიშვნელობაზე — 0."""
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return var ** 0.5


def _confidence_level(spread, n_sources):
    """ნდობის დონე ქარის სიჩქარის გაფანტვის მიხედვით."""
    if n_sources < 2:
        return "unknown"     # ერთი წყარო — შედარება შეუძლებელია
    if spread < CONF_HIGH_MAX:
        return "high"
    if spread < CONF_MEDIUM_MAX:
        return "medium"
    return "low"


def _vector_avg_direction(pool, i, field, total_w):
    """ქარის მიმართულების ვექტორული საშუალო — კუთხეების სწორი საშუალო."""
    sin_sum = cos_sum = 0.0
    for src, w in pool:
        if i < len(src) and src[i].get(field) is not None:
            rad = math.radians(src[i][field])
            sin_sum += math.sin(rad) * w
            cos_sum += math.cos(rad) * w
    if sin_sum == 0 and cos_sum == 0:
        return 0.0
    return round((math.degrees(math.atan2(sin_sum, cos_sum)) + 360) % 360, 1)


def _wavg(pool, i, field, total_w):
    if not pool or total_w == 0: return 0.0
    return sum(
        src[i].get(field, 0) * w
        for src, w in pool
        if i < len(src) and src[i].get(field) is not None
    ) / total_w


def _estimate_wave_from_wind(v):
    return round(0.0248 * v**2, 2)


def _compute_status(wind, gusts, wave, vis):
    alerts, crit, warn = [], False, False
    if gusts >= THRESHOLDS["wind_gusts"] or wind >= THRESHOLDS["wind_gusts"]:
        alerts.append(f"ქარის აფეთქება: {gusts} მ/წმ (ლიმიტი: {THRESHOLDS['wind_gusts']})")
        crit = True
    elif wind >= THRESHOLDS["wind_speed"] or gusts >= THRESHOLDS["wind_speed"]:
        alerts.append(f"ქარის სიჩქარე: {wind} მ/წმ (ყვითელი ზონა)")
        warn = True
    if wave >= THRESHOLDS["wave_height"]:
        alerts.append(f"ტალღის სიმაღლე: {wave} მ (ლიმიტი: {THRESHOLDS['wave_height']})")
        crit = True
    if vis <= THRESHOLDS["visibility"]:
        alerts.append(f"ხილვადობა: {vis} კმ (კრიტიკული ნისლი)")
        crit = True
    if crit: return "suspended", alerts
    if warn: return "warning",   alerts
    return "operational", []


# ═══════════════════════════════════════════════════════════════
#  5.  Telegram შეტყობინება
# ═══════════════════════════════════════════════════════════════

STATUS_EMOJI = {"operational": "✅", "warning": "⚠️", "suspended": "🚨"}
STATUS_KA    = {"operational": "სტანდარტული რეჟიმი",
                "warning":     "სიფრთხილე — ყვითელი ზონა",
                "suspended":   "საოპერაციო შეჩერება"}

# რომელ გადასვლებზე ვაგზავნოთ
NOTIFY_TRANSITIONS = {
    ("operational", "warning"),
    ("operational", "suspended"),
    ("warning",     "suspended"),
    ("suspended",   "warning"),
    ("suspended",   "operational"),
    ("warning",     "operational"),
}


def send_failure_alert(message: str):
    """Pipeline-ის ჩავარდნისას ეგზავნება — სტატუსის cache-ზე არ არის დამოკიდებული,
    ყოველთვის იგზავნება, რომ მომხმარებელს არ დარჩეს შეუმჩნეველი 'ჩუმი' ჩავარდნა."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram ალერტი გამოტოვებულია (TOKEN/CHAT_ID არ არის) — " + message)
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if not r.ok:
            log.warning(f"Failure-alert Telegram ✗ — {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.warning(f"Failure-alert Telegram ✗ — {e}")


def send_telegram(output: dict):
    """სტატუსის ცვლილებისას Telegram შეტყობინება."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Telegram გამოტოვებულია (TOKEN/CHAT_ID არ არის)")
        return

    # მიმდინარე საათის სტატუსი, არა 48სთ პროგნოზისა
    # (overall_status="warning" ითვლება, თუ პროგნოზში 1 warning-საათი მაინცაა —
    # ცვლისთვის უფრო რელევანტურია რა ხდება ახლა)
    new_status = output["current"].get("status", "operational")
    old_status = _load_status_cache()

    if old_status == new_status:
        log.info(f"Telegram: სტატუსი უცვლელია ({new_status}) — შეტყობინება არ გაიგზავნა")
        _save_status_cache(new_status)
        return

    if (old_status, new_status) not in NOTIFY_TRANSITIONS:
        _save_status_cache(new_status)
        return

    c   = output["current"]
    s   = output["summary_24h"]
    now = output["meta"]["last_update"]
    em  = STATUS_EMOJI[new_status]
    old_em = STATUS_EMOJI.get(old_status, "")

    # შეტყობინების ტექსტი
    text = (
        f"{em} <b>ფოთის პორტი — სტატუსის ცვლილება</b>\n"
        f"{old_em} {STATUS_KA.get(old_status,'?')} → {em} <b>{STATUS_KA[new_status]}</b>\n"
        f"─────────────────\n"
        f"🕐 {now}\n"
        f"💨 ქარი: <b>{c['wind_speed']} მ/წმ</b> | დაქროლვა: <b>{c['wind_gusts']} მ/წმ</b> | "
        f"მიმართ: <b>{_compass_full(c['wind_direction'])}</b>\n"
        f"🌊 ტალღა: <b>{c['wave_height']} მ</b> | პერიოდი: {c['wave_period']} წმ\n"
        f"👁 ხილვადობა: <b>{c['visibility_km']} კმ</b>\n"
        f"─────────────────\n"
        f"⏱ შეჩერება 48h: <b>{s['suspended_hours']}სთ</b> | "
        f"სიფრთხილე: <b>{s['warning_hours']}სთ</b>\n"
    )

    if c["alerts"]:
        text += "⚡ " + " | ".join(c["alerts"]) + "\n"

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.ok:
            log.info(f"Telegram ✓ — გაიგზავნა: {old_status} → {new_status}")
        else:
            log.warning(f"Telegram ✗ — {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.warning(f"Telegram ✗ — {e}")

    _save_status_cache(new_status)


DIGEST_HOURS          = {2, 5, 11, 14, 17, 23}   # 08:00/20:00 ცვლის რეპორტს ეთმობა
DIGEST_INTERVAL_HOURS  = 3                        # მომდევნო პროგნოზის ფანჯარა


def _precip_label(mm: float) -> str:
    """მმ/სთ მნიშვნელობას ადამიანისთვის გასაგებ აღწერად გარდაქმნის."""
    if mm is None: return "—"
    if mm == 0:       return "მოსალოდნელი არ არის"
    if mm < 0.1:      return f"{mm} მმ — კვალი"
    if mm < 1.0:      return f"{mm} მმ — მსუბუქი წვიმა"
    if mm < 2.5:      return f"{mm} მმ — ზომიერი წვიმა"
    if mm < 7.5:      return f"{mm} მმ — ძლიერი წვიმა"
    if mm < 15.0:     return f"{mm} მმ — ძალიან ძლიერი წვიმა"
    return              f"{mm} მმ — ექსტრემალური წვიმა ⚠️"


def send_digest_telegram(output: dict):
    """ცვლის გადაბარების (08:00/20:00) გარდა, დღე-ღამეში 6-ჯერ — მიმდინარე ვითარება
    + მომდევნო 3 საათის პროგნოზი. სტატუსის ცვლილებაზე დამოკიდებული არ არის."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("Digest Telegram გამოტოვებულია (TOKEN/CHAT_ID არ არის)")
        return

    now_hour = datetime.now(TBILISI_TZ).hour
    if now_hour not in DIGEST_HOURS:
        return

    fc  = output.get("forecast", [])
    idx = _current_hour_index(fc)
    c   = output["current"]
    em  = STATUS_EMOJI.get(c.get("status"), "ℹ️")
    now_str = output["meta"]["last_update"]

    text = (
        f"{em} <b>ფოთის პორტი — მიმდინარე მეტეო-მონაცემები</b>\n"
        f"🕐 {now_str}\n"
        f"─────────────────\n"
        f"💨 ქარი: <b>{c['wind_speed']} მ/წმ</b> | დაქროლვა: <b>{c['wind_gusts']} მ/წმ</b> | "
        f"მიმართ: <b>{_compass_full(c['wind_direction'])}</b>\n"
        f"🌊 ტალღა: <b>{c['wave_height']} მ</b>\n"
        f"🌡 ჰაერი: <b>{c['air_temp']}°C</b>\n"
        f"🌧 ნალექი: <b>{_precip_label(c['precipitation'])}</b>\n"
        f"👁 ხილვადობა: <b>{c['visibility_km']} კმ</b>\n"
        f"სტატუსი: <b>{STATUS_KA.get(c.get('status'), c.get('status'))}</b>\n"
    )
    if c.get("alerts"):
        text += "⚡ " + " | ".join(c["alerts"]) + "\n"

    next_hours = fc[idx + 1 : idx + 1 + DIGEST_INTERVAL_HOURS]
    if next_hours:
        text += "─────────────────\n<b>მოსალოდნელი მომდევნო 3 საათი:</b>\n"
        for h in next_hours:
            t_label = h["time"][11:16] if len(h.get("time", "")) >= 16 else h.get("time", "")
            hem = STATUS_EMOJI.get(h.get("status"), "ℹ️")
            temp_str = f"{h['air_temp']}°C, " if h.get("air_temp") is not None else ""
            rain_str = _precip_label(h.get("precipitation", 0))
            text += (
                f"{hem} {t_label} — {temp_str}ქარი {h['wind_speed']} მ/წმ, "
                f"ტალღა {h['wave_height']} მ\n"
                f"     🌧 {rain_str}\n"
            )

    text += f"─────────────────\n{PORTAL_URL}"
    _send_telegram_text(text, label="Digest")


SHIFT_HANDOVER_HOURS = {8, 20}   # სასურველი დრო: დილის 8 და საღამოს 8
SHIFT_FORECAST_HOURS = 12        # ცვლის ხანგრძლივობა
SHIFT_SEGMENT_HOURS  = 4         # დეტალური ჩაშლა 4-საათიან მონაკვეთებად (3 × 4 = 12სთ)
SHIFT_CATCHUP_HOURS  = 13        # თუ ამაზე მეტი გავიდა წინა რეპორტიდან — გავაგზავნოთ
                                  # მიუხედავად საათისა (trigger-ის უზუსტობის წინააღმდეგ)
SHIFT_CACHE = "shift_cache.json"
PORTAL_URL = "https://georgeparker-gp.github.io/poti-forecast-portal/"

STATUS_SEVERITY = {"operational": 0, "warning": 1, "suspended": 2}


def _load_shift_cache():
    try:
        with open(SHIFT_CACHE, encoding="utf-8") as f:
            return json.load(f).get("last_sent")
    except (FileNotFoundError, KeyError, ValueError):
        return None


def _save_shift_cache(when_iso: str):
    try:
        with open(SHIFT_CACHE, "w", encoding="utf-8") as f:
            json.dump({"last_sent": when_iso}, f)
    except Exception as e:
        log.warning(f"shift_cache შენახვა ✗ — {e}")


def send_shift_handover_telegram(output: dict):
    """ცვლის გადაბარების რეპორტი — იდეალურად 08:00/20:00, მაგრამ trigger-ის
    (GitHub Actions/cron-job.org) დროის უზუსტობის გამო მკაცრად ამ საათებზე არ
    ვართ მიჯაჭვული: თუ ბოლო რეპორტიდან >= SHIFT_CATCHUP_HOURS გავიდა, მაინც
    გავაგზავნით — ცვლას "საერთოდ არ მოსვლა" გაცილებით უარესია, ვიდრე
    "ოდნავ არასწორ დროზე მოსვლა"."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    now = datetime.now(TBILISI_TZ)
    now_hour = now.hour

    last_sent_str = _load_shift_cache()
    hours_since = None
    if last_sent_str:
        try:
            hours_since = (now - datetime.fromisoformat(last_sent_str)).total_seconds() / 3600
        except Exception:
            hours_since = None

    on_schedule = now_hour in SHIFT_HANDOVER_HOURS
    catch_up    = hours_since is not None and hours_since >= SHIFT_CATCHUP_HOURS
    never_sent  = hours_since is None

    if not (on_schedule or catch_up or never_sent):
        return
    # არ გავაორმაგოთ, თუ წინა გაგზავნა 1 საათზე ნაკლები ხნის წინ მოხდა
    # (მაგ. on_schedule ისევ true-ა იმავე საათში მეორე გაშვებაზე)
    if hours_since is not None and hours_since < 1:
        return

    shift_label = "დილის ცვლა" if 2 <= now_hour < 14 else "საღამოს ცვლა"

    fc  = output.get("forecast", [])
    idx = _current_hour_index(fc)
    now_str = output["meta"]["last_update"]

    next_hours = fc[idx + 1 : idx + 1 + SHIFT_FORECAST_HOURS]
    if not next_hours:
        return

    # პერიოდი ყოველთვის "მიეჭიდება" ცვლის ოფიციალურ საათს (8 ან 20),
    # მიუხედავად trigger-ის ზუსტი დროისა (catch-up, ხელით გაშვება და ა.შ.)
    anchor_hour = 8 if shift_label == "დილის ცვლა" else 20
    period_start = f"{anchor_hour:02d}:00"
    period_end   = f"{(anchor_hour + SHIFT_FORECAST_HOURS) % 24:02d}:00"

    max_gust   = max((h["wind_gusts"]    for h in next_hours if h.get("wind_gusts")    is not None), default=0)
    max_wave   = max((h["wave_height"]   for h in next_hours if h.get("wave_height")   is not None), default=0)
    max_temp   = max((h["air_temp"]      for h in next_hours if h.get("air_temp")      is not None), default=None)
    min_vis    = min((h["visibility_km"] for h in next_hours if h.get("visibility_km") is not None), default=None)
    total_rain = round(sum(h["precipitation"] for h in next_hours), 1)
    rain_hours = sum(1 for h in next_hours if (h["precipitation"] or 0) > 0)
    rain_line  = ("მოსალოდნელი არ არის" if rain_hours == 0
                  else f"მოსალოდნელია — ცვლის განმავლობაში ~{total_rain} მმ")

    worst       = max(next_hours, key=lambda h: STATUS_SEVERITY.get(h.get("status"), 0))
    worst_status = worst.get("status")
    status_summary = (
        "სტანდარტული რეჟიმი — ცვლის განმავლობაში სრულად" if worst_status == "operational"
        else STATUS_KA.get(worst_status, worst_status)
    )

    text = (
        f"🔔 <b>ფოთის პორტი — ცვლის ამინდის პროგნოზი (12 საათი)</b>\n"
        f"🕐 განახლებულია: {now_str}\n"
        f"─────────────────\n"
        f"📊 პერიოდი: {period_start} — {period_end} ({shift_label})\n"
        f"{STATUS_EMOJI.get(worst_status,'⚠️')} სტატუსი: <b>{status_summary}</b>\n"
        f"─────────────────\n"
        f"💨 ქარის მაქს. დაქროლვა: <b>{max_gust} მ/წმ</b>\n"
        f"🌊 ტალღის მაქს. სიმაღლე: <b>{max_wave} მ</b>\n"
        f"🌡 ჰაერის მაქს. ტემპ: <b>{f'{max_temp}°C' if max_temp is not None else '--'}</b>\n"
        f"🌧 ნალექი: <b>{rain_line}</b>\n"
        f"👁 ხილვადობის მინიმუმი: <b>{f'{min_vis} კმ' if min_vis is not None else '--'}</b>\n"
        f"─────────────────\n"
        f"<b>ცვლის მსვლელობა:</b>\n"
    )

    for i in range(0, len(next_hours), SHIFT_SEGMENT_HOURS):
        seg = next_hours[i : i + SHIFT_SEGMENT_HOURS]
        if not seg:
            continue
        seg_start_h = int(seg[0]["time"][11:13])
        seg_end_h   = (seg_start_h + len(seg)) % 24
        seg_worst   = max(seg, key=lambda h: STATUS_SEVERITY.get(h.get("status"), 0))
        sem         = STATUS_EMOJI.get(seg_worst.get("status"), "✅")
        seg_gust    = max(h["wind_gusts"]  for h in seg)
        seg_wave    = max(h["wave_height"] for h in seg)
        seg_rain    = round(sum(h["precipitation"] for h in seg), 1)
        text += (
            f"{sem} {seg_start_h:02d}:00–{seg_end_h:02d}:00 — დაქროლვა ≤{seg_gust} მ/წმ, "
            f"ტალღა ≤{seg_wave} მ, ნალექი {seg_rain} მმ\n"
        )

    text += f"─────────────────\nდეტალური მონაცემებისთვის გადადით პორტალზე:\n{PORTAL_URL}"

    _send_telegram_text(text, label="Shift-handover")
    _save_shift_cache(now.isoformat())


def _send_telegram_text(text: str, label: str = "Telegram"):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.ok:
            log.info(f"{label} Telegram ✓ — გაიგზავნა")
        else:
            log.warning(f"{label} Telegram ✗ — {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.warning(f"{label} Telegram ✗ — {e}")


def _load_status_cache() -> str:
    try:
        with open(STATUS_CACHE, encoding="utf-8") as f:
            return json.load(f).get("status", "operational")
    except (FileNotFoundError, KeyError, ValueError):
        return "operational"


def _save_status_cache(status: str):
    try:
        with open(STATUS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"status": status, "updated": datetime.now(TBILISI_TZ).isoformat()}, f)
    except Exception as e:
        log.warning(f"status_cache შენახვა ✗ — {e}")


def _load_sos_cache() -> str:
    """ბოლო საათი, რომლისთვისაც SOS უკვე გაგზავნილია — დუბლირებული ალერტის თავიდან აცილება."""
    try:
        with open(SOS_CACHE, encoding="utf-8") as f:
            return json.load(f).get("warned_for", "")
    except (FileNotFoundError, KeyError, ValueError):
        return ""


def _save_sos_cache(warned_for: str):
    try:
        with open(SOS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"warned_for": warned_for, "sent": datetime.now(TBILISI_TZ).isoformat()}, f)
    except Exception as e:
        log.warning(f"sos_cache შენახვა ✗ — {e}")


def send_sos_alert(output: dict):
    """SOS — თუ მომდევნო საათში (≈1 საათში) მოსალოდნელია წითელი ზონა (suspended),
    და ეს ჯერ არ მომხდარა — დაუყოვნებლივ ალერტი, ცვლის/3სთ-გრაფიკისგან დამოუკიდებლად.
    ერთხელ იგზავნება კონკრეტული საათისთვის (cache-ით დუბლირების თავიდან აცილებით)."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    fc  = output.get("forecast", [])
    idx = _current_hour_index(fc)
    if idx + 1 >= len(fc):
        return

    current_h = fc[idx]
    next_h    = fc[idx + 1]

    if next_h.get("status") != "suspended":
        return
    if current_h.get("status") == "suspended":
        return   # უკვე წითელ ზონაშია — ეს არ არის ახალი მოახლოება, ცალკე ლოგიკას ვუტოვებთ

    target_time = next_h.get("time", "")
    if _load_sos_cache() == target_time:
        return   # ამ კონკრეტულ საათზე SOS უკვე გაგზავნილია

    now_str = output["meta"]["last_update"]
    t_label = target_time[11:16] if len(target_time) >= 16 else target_time

    text = (
        f"🆘 <b>SOS — მოსალოდნელია კრიტიკული გაუარესება!</b>\n"
        f"🕐 ამჟამად: {now_str}\n"
        f"⏰ მოახლოვდება: <b>{t_label}</b> (≈1 საათში)\n"
        f"─────────────────\n"
        f"🚨 მოსალოდნელი სტატუსი: <b>{STATUS_KA['suspended']}</b>\n"
        f"💨 ქარი: <b>{next_h['wind_speed']} მ/წმ</b> | დაქროლვა: <b>{next_h['wind_gusts']} მ/წმ</b> | "
        f"მიმართ: <b>{_compass_full(next_h['wind_direction'])}</b>\n"
        f"🌊 ტალღა: <b>{next_h['wave_height']} მ</b>\n"
        f"👁 ხილვადობა: <b>{next_h['visibility_km']} კმ</b>\n"
    )
    if next_h.get("alerts"):
        text += "⚡ " + " | ".join(next_h["alerts"]) + "\n"
    text += "─────────────────\nსასურველია დროულად მოემზადოთ საოპერაციო შეჩერებისთვის."

    _send_telegram_text(text, label="SOS")
    _save_sos_cache(target_time)


def _deg_to_compass(deg: float) -> str:
    dirs = ["N","NE","E","SE","S","SW","W","NW"]
    return dirs[int((deg + 22.5) / 45) % 8]


GEO_COMPASS = {
    "N":  "ჩრდ.",      "NE": "ჩრდ.-აღმ.",
    "E":  "აღმ.",       "SE": "სამხ.-აღმ.",
    "S":  "სამხ.",      "SW": "სამხ.-დას.",
    "W":  "დას.",       "NW": "ჩრდ.-დას.",
}


def _compass_full(deg: float) -> str:
    """ლათინური მიმართულება + ქართული შესაბამისობა, მაგ: 'SW (სამხ.-დას.)'"""
    lat = _deg_to_compass(deg)
    return f"{lat} ({GEO_COMPASS.get(lat, lat)})"


# ═══════════════════════════════════════════════════════════════
#  6.  JSON გამოსვლა
# ═══════════════════════════════════════════════════════════════

def _current_hour_index(consensus):
    """Open-Meteo-ს hourly მასივი იწყება today 00:00-დან (ლოკალური დროით), არა
    გაშვების მომენტიდან — ამიტომ ვპოულობთ ჩანაწერს, რომლის დროც ყველაზე ახლოსაა
    რეალურ 'ახლა'-სთან (იგივე ლოგიკა, რასაც frontend-ის findNowIndex იყენებს)."""
    if not consensus:
        return 0
    now_str = datetime.now(TBILISI_TZ).strftime("%Y-%m-%dT%H:00")
    idx = 0
    for i, h in enumerate(consensus):
        if h["time"] <= now_str:
            idx = i
        else:
            break
    return idx


def build_output(consensus, sources_used, daily=None):
    now  = consensus[_current_hour_index(consensus)] if consensus else {}
    susp = sum(1 for h in consensus if h["status"] == "suspended")
    warn = sum(1 for h in consensus if h["status"] == "warning")
    return {
        "meta": {
            "location":       LOCATION["name"],
            "lat":            LOCATION["lat"],
            "lon":            LOCATION["lon"],
            "last_update":    datetime.now(TBILISI_TZ).strftime("%Y-%m-%d %H:%M"),
            "next_update":    (datetime.now(TBILISI_TZ) + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            "sources_used":   sources_used,
            "forecast_hours": len(consensus),
        },
        "current": {k: now.get(k, v) for k, v in {
            "time": "", "wind_speed": 0, "wind_gusts": 0,
            "wind_direction": 0, "wave_height": 0, "precipitation": 0,
            "visibility_km": 10, "wave_period": 0, "wave_direction": 0,
            "swell_height": 0, "water_temp": 0, "current_speed": 0, "air_temp": None,
            "status": "operational", "alerts": [],
            "wind_spread": 0, "confidence": "unknown", "source_count": 0,
        }.items()},
        "summary_24h": {
            "max_wave_height":   _r(max((h["wave_height"] for h in consensus), default=0)),
            "max_wind_gusts":    _r(max((h["wind_gusts"]  for h in consensus), default=0)),
            "suspended_hours":   susp,
            "warning_hours":     warn,
            "operational_hours": len(consensus) - susp - warn,
            "overall_status":    "suspended" if susp else "warning" if warn else "operational",
        },
        "forecast": consensus,
        "daily":    daily or [],
    }


# ═══════════════════════════════════════════════════════════════
#  7.  მთავარი
# ═══════════════════════════════════════════════════════════════

def _minutes_since_last_update():
    """data.json-ის meta.last_update-დან გასული წუთები. თუ ფაილი არ არსებობს/არასწორია — None."""
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
        last_str = existing.get("meta", {}).get("last_update")
        if not last_str:
            return None
        last_dt = datetime.strptime(last_str, "%Y-%m-%d %H:%M").replace(tzinfo=TBILISI_TZ)
        delta = datetime.now(TBILISI_TZ) - last_dt
        return delta.total_seconds() / 60
    except Exception:
        return None


def main():
    log.info(f"══ {LOCATION['name']} — კონსენსუს ბექენდი (48h) ══")

    # GitHub Actions-ის cron scheduler ხანდახან საათობით აგვიანებს/'ხტის' გაშვებებს —
    # ამიტომ workflow ხშირად (15 წუთში ერთხელ) ეშვება, მაგრამ ნამდვილი fetch
    # მხოლოდ მაშინ ხდება, თუ წინა წარმატებული განახლებიდან ნამდვილად ~საათი გავიდა.
    # ეს რჩება დაცული Stormglass-ის daily quota-სა და Open-Meteo-ს ზედმეტი დატვირთვისგან.
    force_refresh = os.environ.get("FORCE_REFRESH", "").lower() == "true"
    minutes_since = _minutes_since_last_update()
    if not force_refresh and minutes_since is not None and minutes_since < 55:
        log.info(f"ბოლო განახლება {minutes_since:.0f} წუთის წინ მოხდა — ნაადრევია, ამ ციკლს გამოვტოვებ.")
        return
    if force_refresh and minutes_since is not None and minutes_since < 55:
        log.info(f"ხელით გაშვება (workflow_dispatch) — throttle-ს გამოვტოვებ ({minutes_since:.0f} წუთის წინ).")

    sources_used = []

    try:
        # ═══ FAULT-TOLERANT SOURCE GATHERING ═══
        # ყოველი წყარო დამოუკიდებლად იჭერება — ერთის ჩავარდნა სხვებს არ ჩააგდებს.
        # Open-Meteo-ს მთლიანი მიუწვდომლობა (domain down/IP block) არ ჩააგდებს
        # pipeline-ს — yr.no, Stormglass, OWM საკმარისია კონსენსუსისთვის.

        # ── Open-Meteo ატმოსფეროული (ოთხი მოდელი ერთი domain-იდან) ──
        raw_best   = fetch_open_meteo_atmosphere("best_match")
        atmo_best  = parse_open_meteo_atmosphere(raw_best)  if raw_best  else None
        if atmo_best:  sources_used.append("Open-Meteo/best_match")

        raw_gfs    = fetch_open_meteo_atmosphere("gfs_seamless")
        atmo_gfs   = parse_open_meteo_atmosphere(raw_gfs)   if raw_gfs   else None
        if atmo_gfs:   sources_used.append("Open-Meteo/GFS")

        raw_icon   = fetch_open_meteo_atmosphere("icon_eu")
        atmo_icon  = parse_open_meteo_atmosphere(raw_icon)  if raw_icon  else None
        if atmo_icon:  sources_used.append("Open-Meteo/ICON-EU")

        raw_ecmwf  = fetch_open_meteo_atmosphere("ecmwf_ifs")
        atmo_ecmwf = parse_open_meteo_atmosphere(raw_ecmwf) if raw_ecmwf else None
        if atmo_ecmwf: sources_used.append("Open-Meteo/ECMWF")

        raw_marine = fetch_open_meteo_marine()
        marine     = parse_open_meteo_marine(raw_marine) if raw_marine else None
        if marine:     sources_used.append("Open-Meteo/Marine")

        # ── დამოუკიდებელი წყაროები (სხვა domain-ები — ვარდება ცალ-ცალკე) ──
        raw_yr     = fetch_yr_no()
        yr_no      = parse_yr_no(raw_yr) if raw_yr else None
        if yr_no:      sources_used.append("yr.no/MET Norway")

        raw_sg     = fetch_stormglass()
        stormglass = parse_stormglass(raw_sg) if raw_sg else None
        if stormglass: sources_used.append("Stormglass.io")

        raw_owm    = fetch_openweathermap()
        owm        = parse_openweathermap(raw_owm) if raw_owm else None
        if owm:        sources_used.append("OpenWeatherMap")

        # ── კონსენსუსი — მხოლოდ გადარჩენილი წყაროებით ──
        # atmo_best ამ ეტაპზე შეიძლება None-ი იყოს (Open-Meteo down) —
        # compute_consensus მის გარეშეც ფუნქციონირებს, თუ სხვა წყარო მაინც არსებობს.
        atmo_sources = [x for x in [atmo_best, atmo_gfs, atmo_icon, atmo_ecmwf, yr_no] if x]
        if not atmo_sources:
            # სრულიად ყველა ატმოსფეროული წყარო ჩავარდა — ეს ნამდვილად კრიტიკულია
            log.error("კრიტიკული: ყველა ატმოსფეროული წყარო (Open-Meteo + yr.no) მიუწვდომელია.")
            log.error("sources_used=%s — pipeline ჩერდება.", sources_used)
            sys.exit(1)

        # compute_consensus-ს atmo_best-ად ვაძლევთ პირველ ხელმისაწვდომ ატმოსფეროულ წყაროს
        effective_best  = atmo_best or atmo_ecmwf or atmo_gfs or atmo_icon or yr_no
        effective_ecmwf = atmo_ecmwf if atmo_ecmwf else None
        effective_gfs   = atmo_gfs   if atmo_gfs   else None
        effective_icon  = atmo_icon  if atmo_icon  else None

        om_down = all(x is None for x in [atmo_best, atmo_gfs, atmo_icon, atmo_ecmwf])
        if om_down:
            log.warning("⚠️  Open-Meteo მთლიანად მიუწვდომელია — კონსენსუსი yr.no + Stormglass + OWM-ით")

        log.info(f"კონსენსუსი: {len(sources_used)} წყარო — {sources_used}")
        consensus = compute_consensus(effective_best, effective_gfs, effective_icon, effective_ecmwf,
                                      marine, stormglass, yr_no, owm)

        raw_daily        = fetch_open_meteo_daily()
        daily_atmo       = parse_open_meteo_daily(raw_daily) if raw_daily else []
        raw_marine_daily = fetch_open_meteo_marine_daily()
        daily_marine     = parse_marine_daily(raw_marine_daily) if raw_marine_daily else []
        daily            = build_daily_summary(daily_atmo, daily_marine) if daily_atmo else []
        if daily:
            log.info(f"კვირის ხედი: {len(daily)} დღე ✓")
        else:
            log.warning("კვირის ხედი ✗ — daily მონაცემები მიუწვდომელია (forecast/daily ველი ცარიელია)")

        output = build_output(consensus, sources_used, daily)

        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        # Telegram შეტყობინება (სტატუსის ცვლილებაზე)
        send_telegram(output)
        send_sos_alert(output)
        send_digest_telegram(output)
        send_shift_handover_telegram(output)

        s = output["summary_24h"]
        log.info("══ შედეგი ══")
        log.info(f"  წყაროები:    {', '.join(sources_used)}")
        log.info(f"  ტალღა მაქს:  {s['max_wave_height']} მ")
        log.info(f"  დაქროლვა მაქს: {s['max_wind_gusts']} მ/წმ")
        log.info(f"  შეჩერება:    {s['suspended_hours']}h / {len(consensus)}h")
        log.info(f"  სტატუსი:     {s['overall_status'].upper()}")
        log.info(f"  ✓ {OUTPUT_FILE}")

    except SystemExit:
        raise
    except Exception as e:
        log.exception("მოულოდნელი შეცდომა fetch.py-ში")
        send_failure_alert(
            f"🚨 <b>ფოთის პორტი — განახლება ჩავარდა (გამონაკლისი)</b>\n"
            f"<code>{type(e).__name__}: {e}</code>\n"
            f"მონაცემები გაჩერებულია ბოლო წარმატებულ მნიშვნელობაზე."
        )
        sys.exit(1)


# ─────────────────────────────────
def _safe(lst, i, scale=1.0, default=0.0):
    try:
        v = lst[i]
        return round(v * scale, 3) if v is not None else default
    except (IndexError, TypeError):
        return default

def _r(v, n=2):
    return round(v, n)
# ─────────────────────────────────

if __name__ == "__main__":
    main()
