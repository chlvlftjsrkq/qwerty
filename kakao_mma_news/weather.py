from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import Config
from .news import KST


WEATHER_CODE_TEXT = {
    0: "맑은 하늘",
    1: "대체로 맑은 날씨",
    2: "구름 조금 있는 날씨",
    3: "흐린 날씨",
    45: "안개",
    48: "서리 안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    95: "천둥번개",
}


def _first_number(values: list[Any] | None) -> float | None:
    if not values:
        return None
    for value in values:
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _dust_grade_pm10(value: float | None) -> str:
    if value is None:
        return ""
    if value <= 30:
        return "좋음"
    if value <= 80:
        return "보통"
    if value <= 150:
        return "나쁨"
    return "매우 나쁨"


def _dust_grade_pm25(value: float | None) -> str:
    if value is None:
        return ""
    if value <= 15:
        return "좋음"
    if value <= 35:
        return "보통"
    if value <= 75:
        return "나쁨"
    return "매우 나쁨"


def _round(value: float | None) -> int | None:
    if value is None:
        return None
    return int(round(value))


def build_weather_summary(config: Config) -> str:
    if not config.weather_enabled:
        return ""

    try:
        import requests
    except ImportError:
        return ""

    today = datetime.now(KST).date().isoformat()
    location = config.weather_location
    try:
        forecast = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": config.weather_latitude,
                "longitude": config.weather_longitude,
                "timezone": "Asia/Seoul",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "forecast_days": 1,
            },
            timeout=config.request_timeout_seconds,
        )
        forecast.raise_for_status()
        daily = forecast.json().get("daily", {})
        weather_code = _round(_first_number(daily.get("weather_code")))
        temp_max = _round(_first_number(daily.get("temperature_2m_max")))
        temp_min = _round(_first_number(daily.get("temperature_2m_min")))
        rain_prob = _round(_first_number(daily.get("precipitation_probability_max")))

        air = requests.get(
            "https://air-quality-api.open-meteo.com/v1/air-quality",
            params={
                "latitude": config.weather_latitude,
                "longitude": config.weather_longitude,
                "timezone": "Asia/Seoul",
                "hourly": "pm10,pm2_5",
                "forecast_days": 1,
            },
            timeout=config.request_timeout_seconds,
        )
        air.raise_for_status()
        hourly = air.json().get("hourly", {})
        pm10 = _round(_first_number(hourly.get("pm10")))
        pm25 = _round(_first_number(hourly.get("pm2_5")))
    except Exception:
        return f"🌤️ 오늘 {location} 날씨 정보는 일시적으로 확인하지 못했습니다. 외출 전 최신 예보를 한 번 더 확인해 주세요."

    weather_text = WEATHER_CODE_TEXT.get(weather_code or -1, "날씨")
    temp_text = ""
    if temp_min is not None and temp_max is not None:
        temp_text = f"최저 {temp_min}도, 최고 {temp_max}도"
    elif temp_max is not None:
        temp_text = f"최고 {temp_max}도"

    rain_text = f"강수 확률은 최대 {rain_prob}%입니다" if rain_prob is not None else "강수 확률 정보는 제한적입니다"
    dust_parts = []
    if pm10 is not None:
        dust_parts.append(f"미세먼지 PM10은 {pm10}㎍/㎥, {_dust_grade_pm10(pm10)}")
    if pm25 is not None:
        dust_parts.append(f"초미세먼지 PM2.5는 {pm25}㎍/㎥, {_dust_grade_pm25(pm25)}")
    dust_text = "이고 ".join(dust_parts) + " 수준입니다" if dust_parts else "미세먼지 정보는 제한적입니다"

    if temp_text:
        return f"🌤️ 오늘 {location}은 {weather_text}이며 {temp_text}로 예상됩니다. {rain_text}. {dust_text}. 물과 겉옷을 챙기고 외출 전 최신 예보를 확인해 주세요."
    return f"🌤️ 오늘 {location}은 {weather_text}로 예상됩니다. {rain_text}. {dust_text}. 외출 전 최신 예보를 확인해 주세요."
