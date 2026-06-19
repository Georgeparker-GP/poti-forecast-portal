import json, math, os, logging
from datetime import datetime, timedelta, timezone

import requests
import urllib3
urllib3.disable_warnings()
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

def _session():
    s = requests.Session()
    r = Retry(total=3, backoff_factor=2)
    s.mount("https://", HTTPAdapter(max_retries=r))
    return s

requests_session = _session()

# ─────────────────────────────────────────────
LOCATION = {"name": "ფოთის პორტი", "lat": 42.15, "lon": 41.67, "timezone": "Asia/Tbilisi"}

FORECAST_HOURS      = 48
REQUEST_TIMEOUT     = 15
OUTPUT_FILE         = "data.json"
STATUS_CACHE        = "status_cache.json"
STORMGLASS_CACHE    = "stormglass_cache.json"
YR_NO_CACHE         = "yr_cache.json"
STORMGLASS_INTERVAL = 3
YR_NO_INTERVAL      = 1   # yr.no ყოველ საათში განახლდება

THRESHOLDS = {
    "wind_speed":  15.0,
    "wind_gusts":  21.5,
    "wave_height":  1.50,
    "visibility":   1.0,
}

BASE_WEIGHTS = {
    "best_match": 0.22, "gfs": 0.13, "icon_eu": 0.09,
    "yr_no": 0.13, "windy": 0.18, "stormglass": 0.17, "owm": 0.08,
}
WAVE_WEIGHTS = {"marine": 0.45, "windy": 0.25, "stormglass": 0.30}

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

def fetch_open_meteo_atmosphere(model: str):
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,"
                  "temperature_2m,precipitation,visibility,weather_code",
        "wind_speed_unit": "ms",
        "forecast_days": 3,
        "timezone": LOCATION["timezone"],
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
#  2.  კეშირებები
# ═══════════════════════════════════════════════════════════════

def _load_stormglass_cache():
    try:
        with open(STORMGLASS_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(c["_cached_at"])).total_seconds() / 3600
        if age < STORMGLASS_INTERVAL:
            return c["data"]
    except Exception:
        pass
    return None

def _save_stormglass_cache(data):
    try:
        with open(STORMGLASS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"_cached_at": datetime.now().isoformat(), "data": data},
                      f, ensure_ascii=False)
    except Exception:
        pass

def _load_yr_cache():
    try:
        with open(YR_NO_CACHE, encoding="utf-8") as f:
            c = json.load(f)
        age = (datetime.now() - datetime.fromisoformat(c["_cached_at"])).total_seconds() / 3600
        if age < YR_NO_INTERVAL:
            return c["data"]
    except Exception:
        pass
    return None

def _save_yr_cache(data):
    try:
        with open(YR_NO_CACHE, "w", encoding="utf-8") as f:
            json.dump({"_cached_at": datetime.now().isoformat(), "data": data},
                      f, ensure_ascii=False)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════
#  3.  პარსინგი
# ═══════════════════════════════════════════════════════════════

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
    result = []
    timeseries = raw.get("properties", {}).get("timeseries", [])
    for entry in timeseries[:hours]:
        time_str = entry.get("time", "")[:16]
        instant  = entry.get("data", {}).get("instant", {}).get("details", {})
        next1h   = entry.get("data", {}).get("next_1_hours", {}).get("details", {})
        wind_speed = round(float(instant.get("wind_speed", 0) or 0), 2)
        wind_dir   = round(float(instant.get("wind_from_direction", 0) or 0), 1)
        precip     = round(float(next1h.get("precipitation_amount", 0) or 0), 2)
        fog = float(instant.get("fog_area_fraction", 0) or 0)
        vis_km = round(max(0.1, 10.0 * (1.0 - fog / 100.0)), 2)
        result.append({
            "time":           time_str,
            "wind_speed":     wind_speed,
            "wind_gusts":     round(wind_speed * 1.25, 2),
            "wind_direction": wind_dir,
            "precipitation":  precip,
            "visibility_km":  vis_km,
        })
    return result[:hours]

# ═══════════════════════════════════════════════════════════════
#  4.  კონსენსუსი
# ═══════════════════════════════════════════════════════════════

def compute_consensus(atmo_best, atmo_gfs, atmo_icon, marine, stormglass, windy, yr_no, owm):
    atmo_pool = []
    for src, key in [
        (atmo_best,  "best_match"), (atmo_gfs,    "gfs"),
        (atmo_icon,  "icon_eu"),    (yr_no,        "yr_no"),
        (windy,      "windy"),      (stormglass,   "stormglass"),
        (owm,        "owm"),
    ]:
        if src:
            atmo_pool.append((src, BASE_WEIGHTS[key]))

    total_w = sum(w for _, w in atmo_pool)
    hours   = min(FORECAST_HOURS, len(atmo_best))
    result  = []

    for i in range(hours):
        wind_speed  = _wavg(atmo_pool, i, "wind_speed",    total_w)
        wind_gusts  = _wavg(atmo_pool, i, "wind_gusts",    total_w)
        precip      = _wavg(atmo_pool, i, "precipitation", total_w)

        vis_pool = [(s, w) for s, w in atmo_pool if s is not windy]
        vis_w    = sum(w for _, w in vis_pool) or total_w
        visibility = _wavg(vis_pool or atmo_pool, i, "visibility_km", vis_w)

        wind_direction = _vector_avg_direction(atmo_pool, i, "wind_direction", total_w)

        wave_src = []
        if marine      and i < len(marine)      and marine[i].get("wave_height", 0)      > 0:
            wave_src.append((marine[i]["wave_height"],     WAVE_WEIGHTS["marine"]))
        if stormglass and i < len(stormglass) and stormglass[i].get("wave_height", 0) > 0:
            wave_src.append((stormglass[i]["wave_height"], WAVE_WEIGHTS["stormglass"]))
        if windy      and i < len(windy)      and windy[i].get("wave_height", 0)      > 0:
            wave_src.append((windy[i]["wave_height"],      WAVE_WEIGHTS["windy"]))

        if wave_src:
            ww     = sum(w for _, w in wave_src)
            wave_h = sum(v * w for v, w in wave_src) / ww
        else:
            wave_h = _estimate_wave_from_wind(wind_speed)

        wave_p = marine[i].get("wave_period",    0) if marine and i < len(marine) else 0
        wave_d = marine[i].get("wave_direction", 0) if marine and i < len(marine) else 0
        swell  = marine[i].get("swell_wave_height", 0) if marine and i < len(marine) else 0
        if stormglass and i < len(stormglass) and stormglass[i].get("swell_height", 0) > 0:
            swell = stormglass[i]["swell_height"]

        water_temp    = stormglass[i].get("water_temp",    0.0) if stormglass and i < len(stormglass) else 0.0
        current_speed = stormglass[i].get("current_speed", 0.0) if stormglass and i < len(stormglass) else 0.0

        air_temp = atmo_best[i].get("air_temp")

        status, alerts = _compute_status(wind_speed, wind_gusts, wave_h, visibility)

        result.append({
            "time":            atmo_best[i]["time"],
            "wind_speed":      _r(wind_speed),
            "wind_gusts":      _r(wind_gusts),
            "wind_direction":  _r(wind_direction, 0),
            "precipitation":   _r(precip),
            "visibility_km":   _r(visibility),
            "wave_height":     _r(wave_h),
            "wave_period":     _r(wave_p),
            "wave_direction":  _r(wave_d),
            "swell_height":    _r(swell),
            "water_temp":      _r(water_temp),
            "current_speed":   _r(current_speed),
            "temperature_2m":  round(air_temp, 1) if air_temp is not None else None,
            "status":          status,
            "alerts":          alerts,
        })

    return result

def _vector_avg_direction(pool, i, field, total_w):
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
        alerts.append(f"ქარის აფეთქება: {gusts} მ/წმ")
        crit = True
    elif wind >= THRESHOLDS["wind_speed"] or gusts >= THRESHOLDS["wind_speed"]:
        alerts.append(f"ქარის სიჩქარე: {wind} მ/წმ")
        warn = True
    if wave >= THRESHOLDS["wave_height"]:
        alerts.append(f"ტალღის სიმაღლე: {wave} მ")
        crit = True
    if vis <= THRESHOLDS["visibility"]:
        alerts.append(f"ხილვადობა: {vis} კმ")
        crit = True
    if crit: return "suspended", alerts
    if warn: return "warning",   alerts
    return "operational", []

# ═══════════════════════════════════════════════════════════════
#  5.  JSON გამოსვლა და დრო
# ═══════════════════════════════════════════════════════════════

def build_output(consensus, sources_used):
    # ვაგენერირებთ ზუსტად საქართველოს დროს (UTC + 4)
    now_geo = datetime.now(timezone.utc) + timedelta(hours=4)

    now  = consensus[0] if consensus else {}
    susp = sum(1 for h in consensus if h["status"] == "suspended")
    warn = sum(1 for h in consensus if h["status"] == "warning")
    return {
        "meta": {
            "location":       LOCATION["name"],
            "lat":            LOCATION["lat"],
            "lon":            LOCATION["lon"],
            "last_update":    now_geo.strftime("%Y-%m-%d %H:%M"),
            "next_update":    (now_geo + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M"),
            "sources_used":   sources_used,
            "forecast_hours": len(consensus),
        },
        "current": {k: now.get(k, v) for k, v in {
            "time": "", "wind_speed": 0, "wind_gusts": 0,
            "wind_direction": 0, "wave_height": 0, "precipitation": 0,
            "visibility_km": 10, "wave_period": 0, "wave_direction": 0,
            "swell_height": 0, "water_temp": 0, "current_speed": 0, "temperature_2m": None,
            "status": "operational", "alerts": [],
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
    }

# ═══════════════════════════════════════════════════════════════
#  6.  მთავარი
# ═══════════════════════════════════════════════════════════════

def main():
    log.info(f"══ {LOCATION['name']} — კონსენსუს ბექენდი (48h) ══")
    sources_used = []

    raw_best  = fetch_open_meteo_atmosphere("best_match")
    if not raw_best:
        log.error("კრიტიკული: best_match მიუწვდომელია!")
        return
    atmo_best = parse_open_meteo_atmosphere(raw_best)
    sources_used.append("Open-Meteo/best_match")

    raw_gfs   = fetch_open_meteo_atmosphere("gfs_seamless")
    atmo_gfs  = parse_open_meteo_atmosphere(raw_gfs)  if raw_gfs  else None
    if atmo_gfs:  sources_used.append("Open-Meteo/GFS")

    raw_icon  = fetch_open_meteo_atmosphere("icon_eu")
    atmo_icon = parse_open_meteo_atmosphere(raw_icon) if raw_icon else None
    if atmo_icon: sources_used.append("Open-Meteo/ICON-EU")

    raw_marine = fetch_open_meteo_marine()
    marine     = parse_open_meteo_marine(raw_marine) if raw_marine else None
    if marine:    sources_used.append("Open-Meteo/Marine")
    else:         log.warning("Marine ✗ — ტალღა ქარიდან გამოანგარიშდება")

    raw_yr    = fetch_yr_no()
    yr_no     = parse_yr_no(raw_yr) if raw_yr else None
    if yr_no:     sources_used.append("yr.no/MET Norway")

    raw_sg     = fetch_stormglass()
    stormglass = parse_stormglass(raw_sg) if raw_sg else None
    if stormglass: sources_used.append("Stormglass.io")

    raw_windy  = fetch_windy()
    windy      = parse_windy(raw_windy) if raw_windy else None
    if windy:     sources_used.append("Windy/ECMWF")

    raw_owm    = fetch_openweathermap()
    owm        = parse_openweathermap(raw_owm) if raw_owm else None
    if owm:       sources_used.append("OpenWeatherMap")

    consensus = compute_consensus(atmo_best, atmo_gfs, atmo_icon,
                                  marine, stormglass, windy, yr_no, owm)

    output = build_output(consensus, sources_used)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    log.info("══ შედეგი ══")
    log.info(f"  ✓ {OUTPUT_FILE} წარმატებით განახლდა.")

def _safe(lst, i, scale=1.0, default=0.0):
    try:
        v = lst[i]
        return round(v * scale, 3) if v is not None else default
    except (IndexError, TypeError):
        return default

def _r(v, n=2):
    return round(v, n)

if __name__ == "__main__":
    main()
