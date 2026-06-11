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

# --- კონფიგურაცია ---
LOCATION = {"name": "ფოთის პორტი", "lat": 42.15, "lon": 41.67, "timezone": "Asia/Tbilisi"}
FORECAST_HOURS = 48
OUTPUT_FILE = "data.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# --- დამხმარე ფუნქციები ---
def _safe(lst, i, scale=1.0, default=0.0):
    try:
        v = lst[i]
        return round(v * scale, 3) if v is not None else default
    except (IndexError, TypeError):
        return default

def _r(v, n=2):
    return round(float(v), n)

def _wavg(pool, i, field, total_w):
    vals = [src[i].get(field, 0) * w for src, w in pool if i < len(src) and src[i].get(field) is not None]
    return sum(vals) / total_w if total_w > 0 else 0.0

# --- მონაცემების წამოღება ---
def fetch_open_meteo_atmosphere():
    params = {
        "latitude": LOCATION["lat"], "longitude": LOCATION["lon"],
        "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation,visibility,weather_code",
        "wind_speed_unit": "ms", "forecast_days": 3, "timezone": LOCATION["timezone"],
    }
    try:
        r = requests_session.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=15, verify=False)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"API Error: {e}")
        return None

def parse_data(raw):
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
            "status": "operational",
            "alerts": []
        }
        for i in range(min(FORECAST_HOURS, len(h["time"])))
    ]

def main():
    raw = fetch_open_meteo_atmosphere()
    if not raw: return
    forecast = parse_data(raw)
    
    # მონაცემების სტრუქტურა
    output = {
        "meta": {
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "sources_used": ["Open-Meteo"]
        },
        "current": forecast[0],
        "summary_24h": {"max_wave_height": 0, "max_wind_gusts": max((h["wind_gusts"] for h in forecast), default=0)},
        "forecast": forecast
    }
    
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    log.info("data.json განახლებულია!")

if __name__ == "__main__":
    main()
