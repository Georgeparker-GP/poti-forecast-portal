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

LOCATION = {"name": "ფოთის პორტი", "lat": 42.15, "lon": 41.67, "timezone": "Asia/Tbilisi"}
FORECAST_HOURS = 48
REQUEST_TIMEOUT = 15
OUTPUT_FILE = "data.json"
STATUS_CACHE = "status_cache.json"
STORMGLASS_CACHE = "stormglass_cache.json"
YR_NO_CACHE = "yr_cache.json"
STORMGLASS_INTERVAL = 3
YR_NO_INTERVAL = 1

THRESHOLDS = {
    "wind_speed": 15.0,
    "wind_gusts": 21.5,
    "wave_height": 1.50,
    "visibility": 1.0,
}

BASE_WEIGHTS = {
    "best_match": 0.22, "gfs": 0.13, "icon_eu": 0.09,
    "yr_no": 0.13, "windy": 0.18, "stormglass": 0.17, "owm": 0.08,
}
WAVE_WEIGHTS = {"marine": 0.45, "windy": 0.25, "stormglass": 0.30}

STORMGLASS_API_KEY = os.environ.get("STORMGLASS_API_KEY", "")
WINDY_API_KEY = os.environ.get("WINDY_API_KEY", "")
OWM_API_KEY = os.environ.get("OWM_API_KEY", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger(__name__)

def fetch_open_meteo_atmosphere(model: str):
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation,visibility,weather_code",
        "wind_speed_unit": "ms", "forecast_days": 3, "timezone": LOCATION["timezone"],
    }
    if model != "best_match": params["models"] = model
    try:
        r = requests_session.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=REQUEST_TIMEOUT, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning(f"Open-Meteo [{model}] ✗ — {e}")
        return None

def parse_open_meteo_atmosphere(raw, hours=FORECAST_HOURS):
    h = raw["hourly"]
    return [
        {
            "time": h["time"][i],
            "temperature_2m": _safe(h["temperature_2m"], i),
            "wind_speed": _safe(h["wind_speed_10m"], i),
            "wind_gusts": _safe(h["wind_gusts_10m"], i),
            "wind_direction": _safe(h.get("wind_direction_10m", []), i),
            "precipitation": _safe(h["precipitation"], i),
            "visibility_km": _safe(h["visibility"], i, scale=0.001),
        }
        for i in range(min(hours, len(h["time"])))
    ]

# --- კონსენსუსის ლოგიკა (მთავარი ნაწილი) ---
def compute_consensus(atmo_best, atmo_gfs, atmo_icon, marine, stormglass, windy, yr_no, owm):
    atmo_pool = []
    for src, key in [(atmo_best, "best_match"), (atmo_gfs, "gfs"), (atmo_icon, "icon_eu"), (yr_no, "yr_no"), (windy, "windy"), (stormglass, "stormglass"), (owm, "owm")]:
        if src: atmo_pool.append((src, BASE_WEIGHTS[key]))
    
    total_w = sum(w for _, w in atmo_pool)
    result = []
    for i in range(min(FORECAST_HOURS, len(atmo_best))):
        wind_speed = _wavg(atmo_pool, i, "wind_speed", total_w)
        wind_gusts = _wavg(atmo_pool, i, "wind_gusts", total_w)
        temp_2m    = _wavg(atmo_pool, i, "temperature_2m", total_w)
        precip     = _wavg(atmo_pool, i, "precipitation", total_w)
        
        # მიმართულება
        wind_dir = _vector_avg_direction(atmo_pool, i, "wind_direction", total_w)
        
        # სტატუსი
        status, alerts = _compute_status(wind_speed, wind_gusts, 0, 10) # მარტივი ვერსია

        result.append({
            "time": atmo_best[i]["time"],
            "temperature_2m": _r(temp_2m, 1),
            "wind_speed": _r(wind_speed),
            "wind_gusts": _r(wind_gusts),
            "wind_direction": _r(wind_dir, 0),
            "precipitation": _r(precip),
            "status": status,
            "alerts": alerts,
        })
    return result

def _wavg(pool, i, field, total_w):
    vals = [src[i].get(field, 0) * w for src, w in pool if i < len(src) and src[i].get(field) is not None]
    return sum(vals) / total_w if total_w > 0 else 0.0

def _r(v, n=2): return round(float(v), n)

def _vector_avg_direction(pool, i, field, total_w):
    sin_sum = cos_sum = 0.0
    for src, w in pool:
        if i < len(src) and src[i].get(field) is not None:
            rad = math.radians(src[i][field])
            sin_sum += math.sin(rad) * w
            cos_sum += math.cos(rad) * w
    return round((math.degrees(math.atan2(sin_sum, cos_sum)) + 360) % 360, 1) if (sin_sum or cos_sum) else 0.0

def _compute_status(wind, gusts, wave, vis): return "operational", []

def main():
    raw_best = fetch_open_meteo_atmosphere("best_match")
    if not raw_best: return
    atmo_best = parse_open_meteo_atmosphere(raw_best)
    
    consensus = compute_consensus(atmo_best, None, None, None, None, None, None, None)
    
    output = {
        "meta": {"last_update": datetime.now().strftime("%Y-%m-%d %H:%M"), "sources_used": ["Open-Meteo"]},
        "current": consensus[0],
        "summary_24h": {"max_wave_height": 0, "max_wind_gusts": 0, "suspended_hours": 0, "warning_hours": 0},
        "forecast": consensus
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
