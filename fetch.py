import json, requests, os
from datetime import datetime

# 1. წყაროებიდან მონაცემების გამოთხოვა
def get_model_data(model_name):
    # Open-Meteo-ს მოდელები: best_match, gfs_seamless, icon_eu_seamless
    url = f"https://api.open-meteo.com/v1/forecast?latitude=42.15&longitude=41.67&hourly=temperature_2m,wind_speed_10m,wind_gusts_10m,visibility&wind_speed_unit=ms&models={model_name}&timezone=Asia/Tbilisi"
    try:
        r = requests.get(url).json()
        return r["hourly"]
    except: return None

def main():
    # ვიღებთ მონაცემებს 3 სხვადასხვა მოდელიდან
    models = ["best_match", "gfs_seamless", "icon_eu_seamless"]
    data_list = [get_model_data(m) for m in models]
    data_list = [d for d in data_list if d] # ვფილტრავთ გაუმართავებს

    if not data_list: return

    # კონსენსუსის გამოთვლა (საშუალო)
    consensus_forecast = []
    num_hours = len(data_list[0]["time"])
    
    for i in range(num_hours):
        # საშუალო ტემპერატურა
        temp = sum(d["temperature_2m"][i] for d in data_list) / len(data_list)
        # საშუალო ქარი
        wind = sum(d["wind_speed_10m"][i] for d in data_list) / len(data_list)
        
        consensus_forecast.append({
            "time": data_list[0]["time"][i],
            "temperature_2m": round(temp, 1),
            "wind_speed": round(wind, 1),
            "wind_gusts": max(d["wind_gusts_10m"][i] for d in data_list), # გუსტებში მაქსიმუმს ვიღებთ უსაფრთხოებისთვის
            "visibility_km": min(d["visibility"][i] for d in data_list) / 1000 # ხილვადობაში ყველაზე ცუდ სცენარს ვიღებთ
        })

    output = {
        "meta": {"last_update": datetime.now().strftime("%Y-%m-%d %H:%M"), "sources": models},
        "current": consensus_forecast[0],
        "forecast": consensus_forecast
    }
    
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()
