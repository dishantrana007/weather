"""Weather - finds the best hour of the day for your plans, plus a farm and village advisor."""
import time
import requests
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)
CACHE, TTL = {}, 600  # cache API answers for 10 minutes
GEO = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST = "https://api.open-meteo.com/v1/forecast"

WMO = {0: ("Clear sky", "☀️", "clear"), 1: ("Mainly clear", "🌤️", "clear"),
       2: ("Partly cloudy", "⛅", "cloud"), 3: ("Overcast", "☁️", "cloud"),
       45: ("Fog", "🌫️", "fog"), 48: ("Rime fog", "🌫️", "fog"),
       51: ("Light drizzle", "🌦️", "rain"), 53: ("Drizzle", "🌦️", "rain"),
       55: ("Heavy drizzle", "🌧️", "rain"), 61: ("Light rain", "🌧️", "rain"),
       63: ("Rain", "🌧️", "rain"), 65: ("Heavy rain", "🌧️", "rain"),
       71: ("Light snow", "🌨️", "snow"), 73: ("Snow", "❄️", "snow"),
       75: ("Heavy snow", "❄️", "snow"), 80: ("Showers", "🌦️", "rain"),
       81: ("Showers", "🌧️", "rain"), 82: ("Violent showers", "⛈️", "storm"),
       95: ("Thunderstorm", "⛈️", "storm"), 96: ("Thunderstorm, hail", "⛈️", "storm"),
       99: ("Thunderstorm, hail", "⛈️", "storm")}


def describe(code):
    return WMO.get(code, ("Unknown", "🌡️", "cloud"))


# Each activity scores an hour from 0-100. h = temp, rain %, wind km/h, cloud %,
# humidity %, uv index, day (bool). Tweak these numbers to change what "good" means.
ACTIVITIES = {
    "Running": lambda h: 100 - abs(h["t"] - 13) * 3.5 - h["rain"] * .5
    - max(0, h["wind"] - 20) * 2 - max(0, h["uv"] - 6) * 5,
    "Cycling": lambda h: 100 - abs(h["t"] - 18) * 2.5 - h["rain"] * .6
    - max(0, h["wind"] - 15) * 3,
    "Picnic": lambda h: 100 - abs(h["t"] - 24) * 3 - h["rain"] * .8
    - max(0, h["wind"] - 25) * 2 - (0 if h["day"] else 40),
    "Stargazing": lambda h: (100 - h["cloud"]) - h["rain"] * .3
    - (0 if not h["day"] else 70) - max(0, h["hum"] - 85),
    "Drying laundry": lambda h: 100 - h["rain"] - max(0, h["hum"] - 50) * .8
    + min(h["wind"], 20) * .8 - (0 if h["day"] else 25),
    # Farm activities: spraying needs calm, dry, not-too-hot air.
    "Spraying crops": lambda h: 100 - max(0, h["wind"] - 8) * 5 - h["rain"] * 1.2
    - max(0, h["t"] - 32) * 4 - (0 if h["day"] else 15),
    "Drying crops": lambda h: 100 - h["rain"] - max(0, h["hum"] - 45) * .9
    + min(h["wind"], 15) * 1.2 + (h["t"] - 20) * 1.5 - (0 if h["day"] else 40),
}


def clamp(v):
    return max(0, min(100, round(v)))


def outfit(feels, rain, wind):
    if feels <= 0: tip = "Heavy coat, hat and gloves."
    elif feels <= 10: tip = "Warm jacket and a long-sleeve layer."
    elif feels <= 18: tip = "Light jacket or a sweater."
    elif feels <= 26: tip = "T-shirt weather. Bring a thin layer for the evening."
    else: tip = "Light, breathable clothes. Drink water often."
    if rain >= 50: tip += " Take an umbrella."
    if wind >= 30: tip += " A windbreaker will help."
    return tip


def fetch_weather(lat, lon):
    key = (round(lat, 2), round(lon, 2))
    hit = CACHE.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1]
    r = requests.get(FORECAST, timeout=8, params={
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 7,
        "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m",
        "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,cloud_cover,"
                  "relative_humidity_2m,uv_index,is_day,precipitation,et0_fao_evapotranspiration,soil_temperature_6cm",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"})
    r.raise_for_status()
    CACHE[key] = (time.time(), r.json())
    return CACHE[key][1]


def farm_info(hr, idx, hours):
    i = list(idx)
    rain48 = sum(hr["precipitation"][k] or 0 for k in range(i[0], i[0] + 48))
    et0 = sum(hr["et0_fao_evapotranspiration"][k] or 0 for k in i)
    soil = hr["soil_temperature_6cm"][i[0]]
    tips = []
    if min(h["t"] for h in hours) <= 2:
        tips.append("Frost risk in the next 24 hours. Cover young plants.")
    if rain48 >= 10:
        tips.append("Good rain expected. Skip irrigation.")
    elif et0 > 3:
        tips.append("Dry, thirsty day. Irrigate in the evening.")
    if max(h["wind"] for h in hours[:12]) > 25:
        tips.append("Strong wind today. Avoid spraying.")
    if soil is not None and soil < 10:
        tips.append("Soil is cold. Wait before sowing warm-season crops.")
    if not tips:
        tips.append("No special alerts. Normal field work is fine.")
    return {"soil": soil, "rain48": round(rain48, 1), "et0": round(et0, 1), "tips": tips}


def rain_watch(hr, idx):
    for n, k in enumerate(idx):
        if (hr["precipitation_probability"][k] or 0) >= 50 or (hr["precipitation"][k] or 0) >= 0.3:
            return "Rain likely this hour." if n == 0 else f"Rain likely around {hr['time'][k][11:16]}."
    return "No rain expected in the next 24 hours."


@app.route("/api/search")
def api_search():
    """Returns up to 8 matching places (cities, towns and villages) so the user can pick one."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify([])
    try:
        g = requests.get(GEO, params={"name": q, "count": 8}, timeout=8).json()
    except requests.RequestException:
        return jsonify([])
    return jsonify([{"name": p["name"], "lat": p["latitude"], "lon": p["longitude"],
                     "where": ", ".join(x for x in (p.get("admin3"), p.get("admin2"), p.get("admin1"),
                                                    p.get("country")) if x)}
                    for p in g.get("results", [])])


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/api/weather")
def api_weather():
    try:
        lat, lon = request.args.get("lat"), request.args.get("lon")
        name = request.args.get("name") or "Your location"
        if not (lat and lon):
            city = (request.args.get("city") or "").strip()
            if not city:
                return jsonify(error="Type a city name to search."), 400
            g = requests.get(GEO, params={"name": city, "count": 1}, timeout=8).json()
            if not g.get("results"):
                return jsonify(error=f"No city found for '{city}'. Check the spelling."), 404
            p = g["results"][0]
            lat, lon, name = p["latitude"], p["longitude"], f'{p["name"]}, {p.get("country", "")}'
        d = fetch_weather(float(lat), float(lon))
    except requests.RequestException:
        return jsonify(error="Weather service is unreachable. Try again in a minute."), 502
    except ValueError:
        return jsonify(error="Invalid coordinates."), 400

    cur, hr, daily = d["current"], d["hourly"], d["daily"]
    start = hr["time"].index(cur["time"][:13] + ":00")
    idx = range(start, start + 24)
    hours = [{"t": hr["temperature_2m"][i], "rain": hr["precipitation_probability"][i] or 0,
              "wind": hr["wind_speed_10m"][i], "cloud": hr["cloud_cover"][i],
              "hum": hr["relative_humidity_2m"][i], "uv": hr["uv_index"][i] or 0,
              "day": bool(hr["is_day"][i])} for i in idx]
    desc, icon, mood = describe(cur["weather_code"])
    return jsonify(
        place=name, mood=mood, farm=farm_info(hr, idx, hours), rain_watch=rain_watch(hr, idx),
        sun={"rise": daily["sunrise"][0][11:16], "set": daily["sunset"][0][11:16]},
        now={"temp": cur["temperature_2m"], "feels": cur["apparent_temperature"],
             "desc": desc, "icon": icon, "wind": cur["wind_speed_10m"],
             "humidity": cur["relative_humidity_2m"]},
        labels=[hr["time"][i][11:16] for i in idx],
        activities={n: [clamp(f(h)) for h in hours] for n, f in ACTIVITIES.items()},
        outfit=outfit(cur["apparent_temperature"], max(h["rain"] for h in hours[:12]), cur["wind_speed_10m"]),
        daily=[{"date": daily["time"][i], "icon": describe(daily["weather_code"][i])[1],
                "hi": daily["temperature_2m_max"][i], "lo": daily["temperature_2m_min"][i],
                "rain": daily["precipitation_probability_max"][i] or 0} for i in range(7)])


if __name__ == "__main__":
    app.run(debug=True)
