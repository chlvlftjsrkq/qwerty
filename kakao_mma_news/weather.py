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


def _temperature_feel(temp_max: int | None) -> str:
    if temp_max is None:
        return ""
    if temp_max <= 3:
        return "매우 춥고"
    if temp_max <= 10:
        return "쌀쌀하고"
    if temp_max <= 19:
        return "선선하고"
    if temp_max <= 27:
        return "포근하고"
    if temp_max <= 31:
        return "따뜻하고"
    return "무더울 수 있고"


def _temperature_clause(temp_min: int | None, temp_max: int | None) -> str:
    feel = _temperature_feel(temp_max)
    if temp_max is None:
        return ""
    return f"기온이 {temp_max}도까지 오를 정도로 {feel}"


def _weather_clause(weather_text: str, rain_prob: int | None) -> str:
    if rain_prob is not None and rain_prob >= 40:
        return f"{weather_text}이고 강수 확률은 최대 {rain_prob}%예요"
    return f"{weather_text}예요"


def _dust_clause(pm10: int | None, pm25: int | None) -> str:
    dust_parts = []
    if pm10 is not None:
        dust_parts.append(f"미세먼지 지수는 {pm10} ({_dust_grade_pm10(pm10)})")
    if pm25 is not None:
        dust_parts.append(f"초미세먼지는 {pm25} ({_dust_grade_pm25(pm25)})")
    if not dust_parts:
        return "미세먼지 정보는 제한적이라"
    return f"{', '.join(dust_parts)} 수준이라"


def _short_dust_clause(pm10: int | None, pm25: int | None) -> str:
    dust_parts = []
    if pm10 is not None:
        dust_parts.append(f"미세먼지 {pm10}({_dust_grade_pm10(pm10)})")
    if pm25 is not None:
        dust_parts.append(f"초미세먼지 {pm25}({_dust_grade_pm25(pm25)})")
    return ", ".join(dust_parts)


def _worst_dust_grade(pm10: int | None, pm25: int | None) -> str:
    grades = []
    if pm10 is not None:
        grades.append(_dust_grade_pm10(pm10))
    if pm25 is not None:
        grades.append(_dust_grade_pm25(pm25))
    if "매우 나쁨" in grades:
        return "매우 나쁨"
    if "나쁨" in grades:
        return "나쁨"
    if "보통" in grades:
        return "보통"
    if "좋음" in grades:
        return "좋음"
    return ""


def _outing_phrase(temp_max: int | None, rain_prob: int | None, dust_grade: str) -> str:
    if rain_prob is not None and rain_prob >= 60:
        return "외출할 때 우산이 필요한 날이에요"
    if dust_grade in {"나쁨", "매우 나쁨"}:
        return "장시간 외출은 조금 부담될 수 있는 날이에요"
    if temp_max is not None and temp_max >= 32:
        return "한낮 외출은 조금 더울 수 있는 날이에요"
    return "가벼운 외출에 딱 좋은 날이에요"


def _advice(temp_min: int | None, temp_max: int | None, rain_prob: int | None, dust_grade: str) -> str:
    if rain_prob is not None and rain_prob >= 60:
        return "접이식 우산과 물을 챙기고, 이동 전 최신 예보를 한 번 더 확인해 주세요."
    if dust_grade in {"나쁨", "매우 나쁨"}:
        return "KF 마스크를 챙기고, 실외 활동은 컨디션에 맞춰 조절해 주세요."
    if temp_max is not None and temp_max >= 28:
        return "수분 충분히 챙기고, 필요하면 얇은 마스크만 챙겨 주세요."
    if temp_min is not None and temp_min <= 10:
        return "아침저녁으로 얇은 겉옷을 챙기고, 외출 전 최신 예보를 확인해 주세요."
    return "가볍게 겉옷을 챙기고, 외출 전 최신 예보를 확인해 주세요."


def _short_advice(temp_max: int | None, rain_prob: int | None, dust_grade: str) -> str:
    if rain_prob is not None and rain_prob >= 60:
        return "우산을 챙겨 주세요."
    if dust_grade in {"나쁨", "매우 나쁨"}:
        return "장시간 외출은 조절해 주세요."
    if temp_max is not None and temp_max >= 28:
        return "수분을 챙겨 주세요."
    return "외출 전 예보를 확인해 주세요."


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
    temp_clause = _temperature_clause(temp_min, temp_max)
    weather_clause = _weather_clause(weather_text, rain_prob)
    dust_grade = _worst_dust_grade(pm10, pm25)
    dust_clause = _short_dust_clause(pm10, pm25)
    advice = _short_advice(temp_max, rain_prob, dust_grade)

    rain_clause = f", 강수확률 {rain_prob}%" if rain_prob is not None and rain_prob >= 40 else ""
    dust_sentence = f" {dust_clause}입니다." if dust_clause else ""
    if temp_clause:
        return f"🌤️ 오늘 {location}은 최고 {temp_max}도, {weather_text}{rain_clause}입니다.{dust_sentence} {advice}"
    return f"🌤️ 오늘 {location}은 {weather_text}{rain_clause}입니다.{dust_sentence} {advice}"
