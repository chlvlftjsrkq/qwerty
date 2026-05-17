from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_float(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def parse_list(value: str | None, default: list[str]) -> list[str]:
    if value is None or value.strip() == "":
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def load_env_file(path: str | Path | None) -> None:
    if not path:
        return
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Config:
    target_chatroom: str
    kakao_enabled: bool
    kakao_app_path: str
    kakao_window_titles: list[str]
    kakao_search_hotkey: list[str]
    kakao_search_click_x: int | None
    kakao_search_click_y: int | None
    kakao_message_click_x: int | None
    kakao_message_click_y: int | None
    kakao_send_enter: bool
    kakao_max_chunk_chars: int
    kakao_wait_seconds: float
    kakao_step_delay_seconds: float

    openai_api_key: str
    openai_model: str
    summary_provider: str
    agency_name: str
    codex_command: str
    codex_model: str
    codex_timeout_seconds: float

    weather_enabled: bool
    weather_location: str
    weather_latitude: float
    weather_longitude: float

    naver_client_id: str
    naver_client_secret: str
    naver_news_enabled: bool
    naver_news_pages: int
    kskill_proxy_base_url: str

    query_terms: list[str]
    required_terms: list[str]
    google_news_enabled: bool
    policy_rss_enabled: bool
    policy_rss_urls: list[str]
    max_items: int
    lookback_days: int
    fetch_article_text: bool
    request_timeout_seconds: float

    output_dir: Path


def _optional_coord(name: str) -> int | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def load_config(env_file: str | Path | None = ".env") -> Config:
    load_env_file(env_file)
    default_terms = [
        "병무청",
        "병역",
        "입영",
        "사회복무요원",
        "예비군",
        "병역판정검사",
        "현역병",
    ]
    return Config(
        target_chatroom=os.getenv("TARGET_CHATROOM", ""),
        kakao_enabled=parse_bool(os.getenv("KAKAO_ENABLED"), False),
        kakao_app_path=os.getenv(
            "KAKAO_APP_PATH",
            r"C:\Program Files (x86)\Kakao\KakaoTalk\KakaoTalk.exe",
        ),
        kakao_window_titles=parse_list(
            os.getenv("KAKAO_WINDOW_TITLES"), ["KakaoTalk", "카카오톡"]
        ),
        kakao_search_hotkey=parse_list(os.getenv("KAKAO_SEARCH_HOTKEY"), ["ctrl", "f"]),
        kakao_search_click_x=_optional_coord("KAKAO_SEARCH_CLICK_X"),
        kakao_search_click_y=_optional_coord("KAKAO_SEARCH_CLICK_Y"),
        kakao_message_click_x=_optional_coord("KAKAO_MESSAGE_CLICK_X"),
        kakao_message_click_y=_optional_coord("KAKAO_MESSAGE_CLICK_Y"),
        kakao_send_enter=parse_bool(os.getenv("KAKAO_SEND_ENTER"), True),
        kakao_max_chunk_chars=parse_int(os.getenv("KAKAO_MAX_CHUNK_CHARS"), 3500),
        kakao_wait_seconds=parse_float(os.getenv("KAKAO_WAIT_SECONDS"), 8.0),
        kakao_step_delay_seconds=parse_float(os.getenv("KAKAO_STEP_DELAY_SECONDS"), 0.7),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
        summary_provider=os.getenv("SUMMARY_PROVIDER", "auto").strip().lower() or "auto",
        agency_name=os.getenv("AGENCY_NAME", "병무청").strip() or "병무청",
        codex_command=os.getenv("CODEX_COMMAND", "codex.cmd"),
        codex_model=os.getenv("CODEX_MODEL", ""),
        codex_timeout_seconds=parse_float(os.getenv("CODEX_TIMEOUT_SECONDS"), 300.0),
        weather_enabled=parse_bool(os.getenv("WEATHER_ENABLED"), True),
        weather_location=os.getenv("WEATHER_LOCATION", "서울"),
        weather_latitude=parse_float(os.getenv("WEATHER_LATITUDE"), 37.5665),
        weather_longitude=parse_float(os.getenv("WEATHER_LONGITUDE"), 126.9780),
        naver_client_id=os.getenv("NAVER_CLIENT_ID", ""),
        naver_client_secret=os.getenv("NAVER_CLIENT_SECRET", ""),
        naver_news_enabled=parse_bool(os.getenv("NAVER_NEWS_ENABLED"), True),
        naver_news_pages=max(1, parse_int(os.getenv("NAVER_NEWS_PAGES"), 5)),
        kskill_proxy_base_url=os.getenv(
            "KSKILL_PROXY_BASE_URL", "https://k-skill-proxy.nomadamas.org"
        ).rstrip("/"),
        query_terms=parse_list(os.getenv("NEWS_QUERY_TERMS"), default_terms),
        required_terms=parse_list(os.getenv("NEWS_REQUIRED_TERMS"), ["병무청"]),
        google_news_enabled=parse_bool(os.getenv("GOOGLE_NEWS_ENABLED"), False),
        policy_rss_enabled=parse_bool(os.getenv("POLICY_RSS_ENABLED"), False),
        policy_rss_urls=parse_list(
            os.getenv("POLICY_RSS_URLS"),
            ["https://www.korea.kr/rss/pressrelease.xml"],
        ),
        max_items=parse_int(os.getenv("NEWS_MAX_ITEMS"), 40),
        lookback_days=parse_int(os.getenv("NEWS_LOOKBACK_DAYS"), 2),
        fetch_article_text=parse_bool(os.getenv("FETCH_ARTICLE_TEXT"), False),
        request_timeout_seconds=parse_float(os.getenv("REQUEST_TIMEOUT_SECONDS"), 15.0),
        output_dir=Path(os.getenv("OUTPUT_DIR", "runs")),
    )
