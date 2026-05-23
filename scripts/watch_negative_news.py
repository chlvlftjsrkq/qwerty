from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT_DIR = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), "KST")

DEFAULT_QUERIES = [
    "병무청 병역기피",
    "병무청 병역법 위반",
    "병무청 허위진단서",
    "병무청 연예인 병역",
    "병무청 사회복무요원 근무태만",
    "사회복무요원 병역법 위반",
    "사회복무요원 병역법",
    "사회복무요원 부실 복무",
    "사회복무요원 무단 결근",
    "병역법 위반 사회복무요원",
    "병무청 수사",
    "병무청 송치",
    "병무청 기소",
    "병역기피 연예인",
    "병역비리",
    "병무청 특별사법경찰",
    "병무청 논란",
]

STRONG_NEGATIVE_TERMS = {
    "병역기피": 6,
    "병역 기피": 6,
    "병역비리": 6,
    "병역 비리": 6,
    "병역법 위반": 6,
    "허위진단서": 6,
    "허위 진단서": 6,
    "재병역판정검사": 5,
    "정신질환 가장": 5,
    "4급 판정": 4,
    "수사": 4,
    "송치": 5,
    "검찰": 4,
    "기소": 5,
    "재판": 4,
    "공판": 4,
    "유죄": 4,
    "징역": 4,
    "집행유예": 4,
    "특별사법경찰": 4,
    "근무태만": 4,
    "부실복무": 4,
    "부실 복무": 4,
    "무단결근": 5,
    "무단 결근": 5,
    "무단이탈": 5,
    "무단 이탈": 5,
    "복무이탈": 5,
    "복무 이탈": 5,
    "부실관리": 4,
    "감사": 3,
    "징계": 4,
    "논란": 3,
    "의혹": 3,
    "비판": 3,
    "고발": 4,
}

CONTEXT_TERMS = {
    "연예인": 2,
    "배우": 2,
    "가수": 2,
    "아이돌": 2,
    "래퍼": 2,
    "방탄": 1,
    "프로야구": 1,
    "축구선수": 2,
    "유튜버": 2,
    "사회복무요원": 2,
    "공익": 1,
}

CORE_ISSUE_RELEVANCE_TERMS = (
    "병무청",
    "병역",
    "병역비리",
    "병역 비리",
    "병역기피",
    "병역 기피",
    "병역법",
    "병역판정",
    "병역 판정",
    "병역법 위반",
    "군면제",
    "군 면제",
    "허위진단서",
    "허위 진단서",
    "사회복무요원",
    "공익",
    "부실복무",
    "부실 복무",
    "무단결근",
    "무단 결근",
    "복무이탈",
    "복무 이탈",
    "특별사법경찰",
    "고의발치",
    "발치",
    "치아",
)

SOFT_EXCLUDE_TERMS = [
    "입영문화제",
    "병역진로설계",
    "업무협약",
    "모집병",
    "현역병 모집",
    "설명회",
    "청춘예찬",
    "채용박람회",
    "방문",
    "간담회",
    "홍보",
]

GENERIC_TOPIC_WORDS = {
    "가수",
    "배우",
    "연예인",
    "아이돌",
    "래퍼",
    "유튜버",
    "방송인",
    "병무청",
    "병역",
    "병역기피",
    "병역비리",
    "기피",
    "비리",
    "병역법",
    "위반",
    "의혹",
    "논란",
    "루머",
    "특혜",
    "해명",
    "무죄",
    "재판",
    "검찰",
    "송치",
    "기소",
    "수사",
    "단독",
    "영상",
    "오늘연예",
    "종결",
}

TOPIC_ENTITY_ALIASES = {
    "유승준": ("유승준", "스티브유", "스티브 유", "steveyoo", "steve yoo", "steve유"),
    "MC몽": ("MC몽", "엠씨몽", "신동현"),
    "송민호": ("송민호", "mino", "위너 송민호", "위너송민호"),
    "라비": ("라비", "김원식"),
    "나플라": ("나플라", "최석배"),
}

ISSUE_FAMILIES = [
    ("병역논란", ("병역기피", "병역 기피", "병역비리", "병역 비리", "병역법 위반", "고의발치", "발치몽")),
    ("허위진단서", ("허위진단서", "허위 진단서", "정신질환 가장", "4급 판정", "재병역판정검사")),
    ("수사재판", ("수사", "송치", "기소", "검찰", "재판", "공판", "유죄", "징역", "집행유예", "고발")),
    ("사회복무요원관리", ("사회복무요원", "공익", "근무태만", "부실관리", "징계")),
    ("기관논란", ("감사", "논란", "의혹", "비판")),
]


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    naver_url: str
    source: str
    published_at: str
    summary: str
    query: str


@dataclass
class Classification:
    send: bool
    severity: str
    category: str
    summary: str
    reason: str
    score: int
    matched_terms: list[str]


def clean_text(value: object) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"(?:\.{2,}|…)+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError):
                return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_urls": {}, "seen_topics": {}, "sent_alerts": [], "last_checked_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen_urls": {}, "seen_topics": {}, "sent_alerts": [], "last_checked_at": ""}
    if not isinstance(data.get("seen_urls"), dict):
        data["seen_urls"] = {}
    if not isinstance(data.get("seen_topics"), dict):
        data["seen_topics"] = {}
    if not isinstance(data.get("sent_alerts"), list):
        data["sent_alerts"] = []
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "naver-news"


def has_core_issue_relevance(text: str) -> bool:
    folded = clean_text(text).casefold()
    return any(term.casefold() in folded for term in CORE_ISSUE_RELEVANCE_TERMS)


def fetch_article_meta_text(url: str, timeout: float) -> str:
    if not url:
        return ""
    response = requests.get(
        url,
        timeout=timeout,
        headers={
            "User-Agent": "qwerty-negative-news-watch/0.1",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    parts: list[str] = []
    if soup.title:
        parts.append(soup.title.get_text(" ", strip=True))
    selectors = [
        ("property", "og:title"),
        ("property", "og:description"),
        ("name", "title"),
        ("name", "description"),
        ("name", "twitter:title"),
        ("name", "twitter:description"),
    ]
    for key, value in selectors:
        tag = soup.find("meta", attrs={key: value})
        content = clean_text(tag.get("content", "")) if tag else ""
        if content:
            parts.append(content)
    return clean_text(" ".join(parts))


def article_source_supports_issue(item: NewsItem, timeout: float) -> bool:
    # Naver snippets can occasionally combine one page title with another news
    # blurb. Trust summary text only after the source page itself supports the
    # military-service issue context.
    if has_core_issue_relevance(item.title):
        return True
    try:
        meta_text = fetch_article_meta_text(item.url or item.naver_url, timeout)
    except Exception:
        return False
    return has_core_issue_relevance(meta_text)


def fetch_naver_news(
    query: str,
    *,
    display: int,
    pages: int,
    timeout: float,
    client_id: str,
    client_secret: str,
    proxy_base_url: str,
) -> list[NewsItem]:
    if client_id and client_secret:
        endpoint = "https://openapi.naver.com/v1/search/news.json"
        headers = {
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
            "User-Agent": "qwerty-negative-news-watch/0.1",
        }
        param_name = "query"
    else:
        endpoint = proxy_base_url.rstrip("/") + "/v1/naver-news/search"
        headers = {"User-Agent": "qwerty-negative-news-watch/0.1"}
        param_name = "q"

    items: list[NewsItem] = []
    display = max(1, min(display, 100))
    pages = max(1, min(pages, 10))
    for page in range(pages):
        start = page * display + 1
        if start + display - 1 > 1000:
            break
        response = requests.get(
            endpoint,
            headers=headers,
            params={param_name: query, "display": display, "start": start, "sort": "date"},
            timeout=timeout,
        )
        response.raise_for_status()
        raw_items = response.json().get("items", [])
        for raw in raw_items:
            original = raw.get("original_link") or raw.get("originallink") or ""
            naver_url = raw.get("link") or ""
            url = canonical_url(original or naver_url)
            published_at = parse_datetime(
                raw.get("pub_date_iso")
                or raw.get("pubDate")
                or raw.get("pub_date")
                or raw.get("pubDateIso")
            )
            items.append(
                NewsItem(
                    title=clean_text(raw.get("title")),
                    url=url,
                    naver_url=canonical_url(naver_url),
                    source=clean_text(raw.get("source")) or source_from_url(url),
                    published_at=published_at.isoformat() if published_at else "",
                    summary=clean_text(raw.get("description")),
                    query=query,
                )
            )
    return items


def dedupe_items(items: list[NewsItem]) -> list[NewsItem]:
    seen: set[str] = set()
    result: list[NewsItem] = []
    for item in items:
        key = item.url or item.naver_url or item.title
        title_key = item.title.casefold()
        if key in seen or title_key in seen:
            continue
        seen.add(key)
        seen.add(title_key)
        result.append(item)
    return result


def within_lookback(item: NewsItem, lookback_hours: int, now: datetime) -> bool:
    if not item.published_at:
        return False
    try:
        published = datetime.fromisoformat(item.published_at).astimezone(KST)
    except ValueError:
        return False
    return published >= now - timedelta(hours=lookback_hours)


def parse_iso_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def prune_seen_topics(seen_topics: dict[str, str], now: datetime, ttl_hours: int) -> dict[str, str]:
    if ttl_hours <= 0:
        return seen_topics
    cutoff = now - timedelta(hours=ttl_hours)
    pruned: dict[str, str] = {}
    for key, value in seen_topics.items():
        seen_at = parse_iso_datetime(value)
        if seen_at is None or seen_at >= cutoff:
            pruned[key] = value
    return pruned


def alert_record(
    item: NewsItem,
    classification: Classification,
    topic_key: str,
    sent_at: datetime,
    *,
    related_articles: list[NewsItem] | None = None,
    message: str = "",
) -> dict[str, Any]:
    return {
        "sent_at": sent_at.isoformat(),
        "topic_key": topic_key,
        "article": asdict(item),
        "related_articles": [asdict(related) for related in related_articles or []],
        "classification": asdict(classification),
        "message": message,
    }


def prune_sent_alerts(records: list[Any], now: datetime, ttl_hours: int) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=ttl_hours) if ttl_hours > 0 else None
    pruned: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        sent_at = parse_iso_datetime(record.get("sent_at"))
        if cutoff is not None and (sent_at is None or sent_at < cutoff):
            continue
        pruned.append(record)
    return pruned


def recent_seen_records_from_urls(
    items: list[NewsItem],
    seen_urls: dict[str, str],
    classifications: dict[str, Classification],
    now: datetime,
    ttl_hours: int,
) -> list[dict[str, Any]]:
    cutoff = now - timedelta(hours=ttl_hours) if ttl_hours > 0 else None
    records: list[dict[str, Any]] = []
    for item in items:
        key = item_key(item)
        if key not in seen_urls:
            continue
        seen_at = parse_iso_datetime(seen_urls.get(key))
        if seen_at is None:
            continue
        if cutoff is not None and seen_at < cutoff:
            continue
        classification = classifications.get(key)
        if classification is None:
            classification = classify_heuristic(item)
            classifications[key] = classification
        if classification.score <= 0:
            continue
        topic_key = topic_fingerprint(item, classification)
        records.append(alert_record(item, classification, topic_key, seen_at))
    return records


def merge_recent_alert_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in sorted(
        records,
        key=lambda item: parse_iso_datetime(item.get("sent_at")) or datetime.min.replace(tzinfo=KST),
        reverse=True,
    ):
        article = record.get("article")
        article_key = ""
        if isinstance(article, dict):
            article_key = str(article.get("url") or article.get("naver_url") or article.get("title") or "")
        key = str(record.get("topic_key") or article_key)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(record)
    return merged


def sent_at_for_topic(records: list[dict[str, Any]], topic_key: str) -> str:
    if not topic_key:
        return ""
    for record in records:
        if record.get("topic_key") == topic_key:
            return str(record.get("sent_at") or "")
    return ""


def in_active_window(now: datetime, start_hour: int, end_hour: int) -> bool:
    start = start_hour % 24
    end = end_hour % 24
    if start == end:
        return True
    if start < end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end


def normalize_topic_token(value: str) -> str:
    token = re.sub(r"[\[\]{}()（）<>〈〉'\"“”‘’.,!?·ㆍ:;|/\\]", " ", value)
    token = re.sub(r"\s+", "", token).casefold()
    return token


def strip_leading_issue_label(title: str) -> str:
    return re.sub(
        r"^[\"'“”‘’]\s*"
        r"(?:병역\s*기피|병역\s*비리|병역법\s*위반|부실\s*복무|무단\s*결근|복무\s*이탈|의혹|논란|해명)"
        r"\s*[\"'“”‘’]\s*",
        "",
        title,
    ).strip()


def known_topic_entity(text: str) -> str:
    normalized_text = normalize_topic_token(text)
    for entity, aliases in TOPIC_ENTITY_ALIASES.items():
        if any(normalize_topic_token(alias) in normalized_text for alias in aliases):
            return entity
    return ""


def issue_family(text: str, matched_terms: list[str]) -> str:
    compact = normalize_topic_token(text)
    matched_compact = {normalize_topic_token(term) for term in matched_terms}
    for family, terms in ISSUE_FAMILIES:
        if any(normalize_topic_token(term) in compact for term in terms):
            return family
        if any(normalize_topic_token(term) in matched_compact for term in terms):
            return family
    return "병역이슈"


def extract_topic_entity(item: NewsItem) -> str:
    text = clean_text(f"{item.title} {item.summary}")
    known = known_topic_entity(text)
    if known:
        return known
    title = re.sub(r"^\[[^\]]+\]\s*", "", item.title).strip()
    token_sources = [strip_leading_issue_label(title), title, text]
    candidates: list[str] = []
    for source in token_sources:
        for token in re.findall(r"[A-Za-z]{1,8}[가-힣]{1,8}|[가-힣]{2,5}", source):
            normalized = normalize_topic_token(token)
            if not normalized or normalized in {normalize_topic_token(word) for word in GENERIC_TOPIC_WORDS}:
                continue
            if re.fullmatch(r"\d+", normalized):
                continue
            candidates.append(token)
        if candidates:
            break

    if not candidates:
        return ""

    def score_token(token: str) -> tuple[int, int]:
        normalized = normalize_topic_token(token)
        score = 0
        if re.search(r"[A-Za-z]", token) and re.search(r"[가-힣]", token):
            score += 8
        if title.startswith(token):
            score += 4
        if normalized in normalize_topic_token(title[:40]):
            score += 2
        score += normalize_topic_token(text).count(normalized)
        return score, len(token)

    # Prefer distinctive named subjects such as "MC몽" even when another person
    # appears first in a reaction article.
    return sorted(candidates, key=score_token, reverse=True)[0]


def topic_fingerprint(item: NewsItem, classification: Classification) -> str:
    text = f"{item.title} {item.summary}"
    entity = normalize_topic_token(extract_topic_entity(item))
    family = issue_family(text, classification.matched_terms)
    if entity:
        base = f"{entity}:{family}"
    else:
        tokens = [
            normalize_topic_token(token)
            for token in re.findall(r"[A-Za-z]{1,8}[가-힣]{1,8}|[가-힣]{2,6}", text)
        ]
        filtered = [
            token
            for token in tokens
            if token and token not in {normalize_topic_token(word) for word in GENERIC_TOPIC_WORDS}
        ][:4]
        base = f"{family}:{':'.join(filtered) or normalize_topic_token(item.title)[:40]}"
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]
    return f"{family}:{digest}"


def classify_heuristic(item: NewsItem) -> Classification:
    haystack = f"{item.title} {item.summary}".casefold()
    score = 0
    matched: list[str] = []
    for term, weight in STRONG_NEGATIVE_TERMS.items():
        if term.casefold() in haystack:
            score += weight
            matched.append(term)
    for term, weight in CONTEXT_TERMS.items():
        if term.casefold() in haystack:
            score += weight
            matched.append(term)
    if "병무청" in haystack:
        score += 2
    if "병역" in haystack:
        score += 2

    soft_excluded = any(term.casefold() in haystack for term in SOFT_EXCLUDE_TERMS)
    send = score >= 6 and not (soft_excluded and score < 9)
    if score >= 12:
        severity = "높음"
    elif score >= 8:
        severity = "보통"
    else:
        severity = "낮음"

    if any(term in haystack for term in ["연예인", "배우", "가수", "아이돌", "래퍼", "유튜버"]):
        category = "연예인·공인 병역 이슈"
    elif any(term in haystack for term in ["사회복무요원", "공익", "근무태만"]):
        category = "사회복무요원 관리 이슈"
    elif any(term in haystack for term in ["허위진단서", "병역법 위반", "병역기피", "병역 비리", "병역비리"]):
        category = "병역법 위반·병역기피 의혹"
    elif any(term in haystack for term in ["수사", "송치", "기소", "재판", "검찰"]):
        category = "수사·재판 관련 이슈"
    else:
        category = "병무청 평판 리스크"

    summary = heuristic_alert_summary(item, category, matched)
    reason = alert_reason_sentence(matched)
    return Classification(
        send=send,
        severity=severity,
        category=category,
        summary=summary,
        reason=reason,
        score=score,
        matched_terms=matched,
    )


def remove_korean_particle_spacing(text: str) -> str:
    particles = [
        "으로부터",
        "으로서",
        "으로써",
        "까지",
        "부터",
        "보다",
        "처럼",
        "에게",
        "에서",
        "으로",
        "라고",
        "하고",
        "이며",
        "이고",
        "이나",
        "거나",
        "의",
        "은",
        "는",
        "이",
        "가",
        "을",
        "를",
        "에",
        "와",
        "과",
        "도",
        "만",
        "로",
    ]
    for particle in particles:
        text = re.sub(rf"(?<=[가-힣A-Za-z0-9])\s+{particle}(?=[\s.,!?)]|$)", particle, text)
    return text


def compact_terms(terms: list[str], limit: int = 4) -> str:
    unique: list[str] = []
    for term in terms:
        if term not in unique:
            unique.append(term)
    selected = unique[:limit]
    if not selected:
        return ""
    if len(selected) == 1:
        return selected[0]
    return ", ".join(selected[:-1]) + ", " + selected[-1]


def trim_to_natural_sentence(text: str, limit: int = 150) -> str:
    cleaned = remove_korean_particle_spacing(clean_text(text))
    if len(cleaned) <= limit and re.search(r"[.!?다요죠니다습니다]$", cleaned):
        return cleaned

    sentence_match = re.match(r"^(.{20,}?[.!?])\s", cleaned + " ")
    if sentence_match and len(sentence_match.group(1)) <= limit:
        return sentence_match.group(1).strip()

    shortened = cleaned[:limit].rstrip(" ,.;:!?")
    shortened = re.sub(r"\s+\S{0,8}$", "", shortened).rstrip(" ,.;:!?")
    return shortened


def looks_like_clipped_korean_fragment(text: str) -> bool:
    stripped = clean_text(text).lstrip(" \"'“”‘’([{")
    if not stripped:
        return True
    bad_starts = (
        "등은",
        "등이",
        "등을",
        "등도",
        "등과",
        "등에",
        "이라며",
        "라며",
        "며 ",
        "고 ",
        "또 ",
        "또한 ",
        "한편 ",
        "하지만 ",
        "그러나 ",
        "이와 함께 ",
    )
    return stripped.startswith(bad_starts)


def fallback_alert_summary(item: NewsItem, category: str, matched_terms: list[str]) -> str:
    terms = compact_terms(matched_terms, 3)
    if terms:
        return f"대표 기사에서 {terms} 관련 표현이 확인됐습니다. {category} 이슈로 번질 수 있어 내용을 확인할 필요가 있습니다."
    return "대표 기사에서 병역 관련 부정 이슈로 번질 수 있는 내용이 확인됐습니다. 사실관계와 후속 보도를 함께 확인할 필요가 있습니다."


def salvage_after_leading_fragment(text: str) -> str:
    pieces = re.split(r"(?<=[.!?])\s+", clean_text(text), maxsplit=1)
    if len(pieces) < 2:
        return ""
    candidate = pieces[1].strip()
    if looks_like_clipped_korean_fragment(candidate) or len(candidate) < 18:
        return ""
    return candidate


def polish_alert_summary(summary: str, item: NewsItem, category: str, matched_terms: list[str]) -> str:
    text = remove_korean_particle_spacing(clean_text(summary))
    text = re.sub(r"^(?:대표\s+)?기사(?:에서는|는)\s*", "", text).strip()
    text = re.sub(r"^에서는\s*", "", text).strip()
    text = re.sub(r"\s*등의 내용을 다루고 있습니다\.?$", ".", text).strip()
    text = re.sub(r"\s*관련 보도를 냈습니다\.\s*기사(?:에서는|는)\s*", " ", text).strip()
    if looks_like_clipped_korean_fragment(text):
        salvaged = salvage_after_leading_fragment(text)
        if salvaged:
            text = salvaged
        else:
            return fallback_alert_summary(item, category, matched_terms)
    if len(text) < 18:
        return fallback_alert_summary(item, category, matched_terms)
    if not re.search(r"[.!?]$", text):
        text = f"{text}."
    return text


def heuristic_alert_summary(item: NewsItem, category: str, matched_terms: list[str]) -> str:
    snippet = trim_to_natural_sentence(item.summary or item.title, 135)
    if snippet and snippet != item.title and not looks_like_clipped_korean_fragment(snippet):
        return polish_alert_summary(snippet, item, category, matched_terms)
    return fallback_alert_summary(item, category, matched_terms)


def alert_reason_sentence(matched_terms: list[str]) -> str:
    terms = compact_terms(matched_terms, 5)
    if not terms:
        return "기사 제목과 요약이 병무청 관련 부정 이슈 감지 기준에 걸려 확인 대상으로 잡았습니다."
    return f"기사 제목과 요약에서 {terms} 표현이 함께 확인돼 모니터링 대상으로 잡았습니다."


def load_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped, flags=re.IGNORECASE).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        stripped = stripped[start : end + 1]
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("Codex output is not a JSON object.")
    return data


def resolve_codex_command(value: str) -> str | None:
    if value and Path(value).exists():
        return value
    if value:
        found = shutil.which(value)
        if found:
            return found
    for candidate in ("codex.cmd", "codex"):
        found = shutil.which(candidate)
        if found:
            return found
    default_windows = Path(os.environ.get("APPDATA", "")) / "npm" / "codex.cmd"
    if default_windows.exists():
        return str(default_windows)
    return None


def compact_alert_record_for_ai(record: dict[str, Any]) -> dict[str, Any]:
    article = record.get("article") if isinstance(record.get("article"), dict) else {}
    classification = record.get("classification") if isinstance(record.get("classification"), dict) else {}
    related = record.get("related_articles") if isinstance(record.get("related_articles"), list) else []
    related_titles = []
    for related_item in related[:5]:
        if isinstance(related_item, dict):
            title = clean_text(related_item.get("title"))
            if title:
                related_titles.append(title)
    return {
        "sent_at": record.get("sent_at", ""),
        "topic_key": record.get("topic_key", ""),
        "title": clean_text(article.get("title")),
        "summary": clean_text(article.get("summary")),
        "url": article.get("url") or article.get("naver_url") or "",
        "published_at": article.get("published_at", ""),
        "category": clean_text(classification.get("category")),
        "classification_summary": clean_text(classification.get("summary")),
        "matched_terms": classification.get("matched_terms") if isinstance(classification.get("matched_terms"), list) else [],
        "message": clean_text(record.get("message"))[:1200],
        "related_titles": related_titles,
    }


def duplicate_with_codex(
    item: NewsItem,
    classification: Classification,
    recent_records: list[dict[str, Any]],
    *,
    codex_command: str,
    codex_model: str,
    timeout_seconds: float,
    output_dir: Path,
) -> tuple[bool, str, str]:
    if not recent_records:
        return False, "", ""
    resolved = resolve_codex_command(codex_command)
    if not resolved:
        return False, "", ""

    payload = {
        "candidate": {
            "article": asdict(item),
            "classification": asdict(classification),
            "topic_key": topic_fingerprint(item, classification),
        },
        "comparison_alerts": [compact_alert_record_for_ai(record) for record in recent_records[:30]],
        # Keep the old key as a compatibility hint for older prompt traces.
        "recent_sent_alerts": [compact_alert_record_for_ai(record) for record in recent_records[:30]],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="negative-watch-duplicate-input-",
        suffix=".json",
        dir=output_dir,
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as input_file:
        json.dump(payload, input_file, ensure_ascii=False)
        input_path = Path(input_file.name)

    with tempfile.NamedTemporaryFile(
        prefix="negative-watch-duplicate-output-",
        suffix=".json",
        dir=output_dir,
        delete=False,
    ) as output_file:
        output_path = Path(output_file.name)

    prompt = " ".join(
        [
            "Task: Compare a candidate Korean negative-news alert with alerts sent during the last 12 hours.",
            "Read only this JSON file:",
            str(input_path.resolve()),
            "Decide whether the candidate is substantially the same issue as any recent sent alert.",
            "The comparison_alerts list can include alerts already selected earlier in this same run. Treat them as prior alerts for duplicate suppression.",
            "Treat it as duplicate when it is the same person, organization, legal dispute, allegation, or military-service controversy even if the news source, title wording, or publication time differs.",
            "Do not mark duplicate merely because both articles mention military service; the concrete issue must overlap.",
            "Return exactly one valid JSON object, no Markdown.",
            'Schema: {"duplicate":true,"matched_topic_key":"copy the topic_key of the closest recent alert, or empty string","reason":"one concise Korean sentence"}',
        ]
    )
    command = [
        resolved,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "-o",
        str(output_path),
    ]
    if codex_model:
        command.extend(["--model", codex_model])
    command.append(prompt)

    try:
        result = subprocess.run(
            command,
            input="",
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            return False, "", ""
        raw = output_path.read_text(encoding="utf-8").strip()
        data = load_json_object(raw)
        return (
            bool(data.get("duplicate")),
            clean_text(data.get("matched_topic_key")),
            clean_text(data.get("reason")),
        )
    except Exception:
        return False, "", ""
    finally:
        for path in (input_path, output_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def selected_candidate_record(
    item: NewsItem,
    classification: Classification,
    topic_key: str,
    selected_at: datetime,
) -> dict[str, Any]:
    return alert_record(
        item,
        classification,
        topic_key,
        selected_at,
        message="이번 검색 실행에서 이미 먼저 선택된 후보입니다.",
    )


def refine_with_codex(
    item: NewsItem,
    classification: Classification,
    *,
    codex_command: str,
    codex_model: str,
    timeout_seconds: float,
    output_dir: Path,
) -> Classification:
    resolved = resolve_codex_command(codex_command)
    if not resolved:
        return classification

    payload = {"article": asdict(item), "heuristic": asdict(classification)}
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="negative-watch-input-",
        suffix=".json",
        dir=output_dir,
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as input_file:
        json.dump(payload, input_file, ensure_ascii=False)
        input_path = Path(input_file.name)

    with tempfile.NamedTemporaryFile(
        prefix="negative-watch-output-",
        suffix=".json",
        dir=output_dir,
        delete=False,
    ) as output_file:
        output_path = Path(output_file.name)

    prompt = " ".join(
        [
            "Task: Decide whether this Korean news article should be sent as a real-time alert.",
            "The alert is for external negative or reputation-risk news related to MMA 병무청 or Korean military service.",
            "Examples to send: celebrity military service evasion, 병역법 위반, 허위진단서, 병역비리, prosecution, police referral, trials, public criticism of 병무청, social service personnel management scandals.",
            "Examples to suppress: routine recruitment notices, ceremonies, policy PR, MoU, ordinary guidance, neutral agency events.",
            "Read only this JSON file:",
            str(input_path.resolve()),
            "Return exactly one valid JSON object, no Markdown.",
            'Schema: {"send":true,"severity":"높음|보통|낮음","category":"분류","summary":"카카오톡에 넣을 자연스러운 경어체 요약 1~2문장","reason":"왜 확인 대상으로 잡았는지 자연스러운 한 문장"}',
            "Write like a concise Korean newsroom monitor alert, not a system log.",
            "Do not copy a clipped Naver API description verbatim. Rewrite it as a complete natural sentence.",
            "The summary must start with the main actor or event, not with a clipped fragment such as 등은, 이라며, 라며, 또, 또한, 한편.",
            "Do not use boilerplate such as 대표 기사에서는, 기사에서는, or 등의 내용을 다루고 있습니다.",
            "Do not use labels such as 감지어 in reason. Explain the monitoring reason in plain Korean.",
            "Do not use ellipses or unsupported facts.",
        ]
    )
    command = [
        resolved,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "-o",
        str(output_path),
    ]
    if codex_model:
        command.extend(["--model", codex_model])
    command.append(prompt)

    try:
        result = subprocess.run(
            command,
            input="",
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if result.returncode != 0:
            return classification
        raw = output_path.read_text(encoding="utf-8").strip()
        data = load_json_object(raw)
        return Classification(
            send=bool(data.get("send", classification.send)),
            severity=clean_text(data.get("severity")) or classification.severity,
            category=clean_text(data.get("category")) or classification.category,
            summary=clean_text(data.get("summary")) or classification.summary,
            reason=clean_text(data.get("reason")) or classification.reason,
            score=classification.score,
            matched_terms=classification.matched_terms,
        )
    except Exception:
        return classification
    finally:
        for path in (input_path, output_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def format_published_label(item: NewsItem) -> str:
    if not item.published_at:
        return "발행 시각 확인 안 됨"
    try:
        dt = datetime.fromisoformat(item.published_at).astimezone(KST)
    except ValueError:
        return "발행 시각 확인 안 됨"
    return f"{dt.year}년 {dt.month}월 {dt.day}일 {dt.hour}시 {dt.minute:02d}분"


def item_key(item: NewsItem) -> str:
    return item.url or item.naver_url or item.title


def sort_key_published(item: NewsItem) -> datetime:
    parsed = parse_iso_datetime(item.published_at)
    return parsed or datetime.min.replace(tzinfo=KST)


def within_hours(item: NewsItem, hours: int, now: datetime) -> bool:
    if hours <= 0:
        return True
    published = parse_iso_datetime(item.published_at)
    if not published:
        return True
    return published >= now - timedelta(hours=hours)


def related_articles_for_topic(
    topic_key: str,
    representative: NewsItem,
    items: list[NewsItem],
    classifications: dict[str, Classification],
    now: datetime,
    related_hours: int,
    related_limit: int,
) -> list[NewsItem]:
    representative_key = item_key(representative)
    related: list[NewsItem] = []
    seen: set[str] = {representative_key}
    for item in items:
        key = item_key(item)
        if key in seen or not within_hours(item, related_hours, now):
            continue
        classification = classifications.get(key)
        if classification is None:
            classification = classify_heuristic(item)
            classifications[key] = classification
        if classification.score <= 0:
            continue
        if topic_fingerprint(item, classification) != topic_key:
            continue
        seen.add(key)
        related.append(item)

    related.sort(key=sort_key_published, reverse=True)
    return related[: max(0, related_limit)]


def related_link_lines(items: list[NewsItem]) -> list[str]:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.extend([f"{index}. {item.title}", item.url or item.naver_url])
    return lines


def image_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\malgunbd.ttf" if bold else r"C:\Windows\Fonts\malgun.ttf",
        r"C:\Windows\Fonts\NanumGothicBold.ttf" if bold else r"C:\Windows\Fonts\NanumGothic.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    if not text:
        return 0
    box = draw.textbbox((0, 0), text, font=font)
    return int(box[2] - box[0])


def image_wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int = 4,
) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    lines: list[str] = []
    current = ""
    tokens = re.findall(r"\S+\s*", text)
    for token in tokens:
        candidate = current + token
        if draw_text_width(draw, candidate.rstrip(), font) <= max_width:
            current = candidate
            continue
        if current.strip():
            lines.append(current.strip())
            current = ""
            if len(lines) >= max_lines:
                break
        chunk = ""
        for char in token.strip():
            if draw_text_width(draw, chunk + char, font) <= max_width:
                chunk += char
            else:
                if chunk:
                    lines.append(chunk)
                    if len(lines) >= max_lines:
                        break
                chunk = char
        if len(lines) >= max_lines:
            break
        current = chunk + (" " if token.endswith(" ") else "")
    if len(lines) < max_lines and current.strip():
        lines.append(current.strip())
    if len(lines) == max_lines and draw_text_width(draw, lines[-1], font) > max_width - 20:
        while lines[-1] and draw_text_width(draw, lines[-1] + "...", font) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "..."
    return lines[:max_lines]


def safe_filename_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:10]


def article_has_dental_context(item: NewsItem, classification: Classification) -> bool:
    haystack = " ".join(
        [
            item.title,
            item.summary,
            classification.summary,
            classification.reason,
            " ".join(classification.matched_terms),
        ]
    ).lower()
    terms = ["발치", "치아", "치과", "이빨", "어금니", "tooth", "teeth", "dental"]
    return any(term in haystack for term in terms)


def draw_soft_glow(base: Image.Image, center: tuple[int, int], radius: int, color: tuple[int, int, int]) -> None:
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, 120))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius // 2))
    base.alpha_composite(overlay)


def draw_microphone(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float, color: tuple[int, int, int]) -> None:
    w = int(54 * scale)
    h = int(92 * scale)
    draw.rounded_rectangle((x, y, x + w, y + h), radius=int(24 * scale), fill=color)
    draw.line((x + w // 2, y + h, x + w // 2, y + h + int(48 * scale)), fill=color, width=int(8 * scale))
    draw.arc((x - int(22 * scale), y + int(30 * scale), x + w + int(22 * scale), y + h + int(22 * scale)), 0, 180, fill=color, width=int(7 * scale))
    draw.line((x - int(10 * scale), y + h + int(48 * scale), x + w + int(10 * scale), y + h + int(48 * scale)), fill=color, width=int(8 * scale))


def draw_gavel(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float, color: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((x, y, x + int(120 * scale), y + int(38 * scale)), radius=int(10 * scale), fill=color)
    draw.rounded_rectangle((x + int(18 * scale), y - int(14 * scale), x + int(102 * scale), y + int(52 * scale)), radius=int(8 * scale), outline=color, width=int(7 * scale))
    draw.line((x + int(78 * scale), y + int(36 * scale), x + int(170 * scale), y + int(125 * scale)), fill=color, width=int(14 * scale))
    draw.rounded_rectangle((x + int(150 * scale), y + int(112 * scale), x + int(230 * scale), y + int(142 * scale)), radius=int(11 * scale), fill=color)


def draw_molar(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float, color: tuple[int, int, int], accent: tuple[int, int, int]) -> None:
    points = [
        (x + int(15 * scale), y + int(15 * scale)),
        (x + int(50 * scale), y),
        (x + int(86 * scale), y + int(12 * scale)),
        (x + int(112 * scale), y + int(48 * scale)),
        (x + int(96 * scale), y + int(124 * scale)),
        (x + int(70 * scale), y + int(176 * scale)),
        (x + int(58 * scale), y + int(112 * scale)),
        (x + int(42 * scale), y + int(176 * scale)),
        (x + int(16 * scale), y + int(126 * scale)),
        (x, y + int(52 * scale)),
    ]
    draw.polygon(points, fill=color)
    draw.line((x + int(24 * scale), y + int(48 * scale), x + int(91 * scale), y + int(48 * scale)), fill=accent, width=int(7 * scale))
    draw.line((x + int(40 * scale), y + int(76 * scale), x + int(78 * scale), y + int(76 * scale)), fill=accent, width=int(5 * scale))


def draw_phone_panel(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, accent: tuple[int, int, int]) -> None:
    draw.rounded_rectangle((x, y, x + w, y + h), radius=44, fill=(19, 28, 44), outline=(85, 101, 128), width=3)
    draw.rounded_rectangle((x + 22, y + 22, x + w - 22, y + h - 22), radius=30, fill=(8, 16, 28))
    draw.rounded_rectangle((x + 44, y + 46, x + 126, y + 82), radius=12, fill=accent)
    draw.text((x + 60, y + 52), "LIVE", fill=(255, 255, 255), font=image_font(22, bold=True))
    head = (x + w // 2, y + 170)
    draw.ellipse((head[0] - 42, head[1] - 42, head[0] + 42, head[1] + 42), fill=(4, 7, 12))
    draw.rounded_rectangle((head[0] - 76, head[1] + 34, head[0] + 76, head[1] + 190), radius=54, fill=(4, 7, 12))
    for i, color in enumerate([(54, 78, 112), (74, 98, 135), accent]):
        yy = y + h - 150 + i * 42
        draw.rounded_rectangle((x + 52, yy, x + w - 52, yy + 22), radius=11, fill=color)
    draw.ellipse((x + w - 74, y + h - 91, x + w - 34, y + h - 51), fill=accent)


def draw_document_stack(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, accent: tuple[int, int, int]) -> None:
    for offset, fill in [(26, (211, 217, 229)), (13, (231, 236, 245)), (0, (248, 250, 252))]:
        draw.rounded_rectangle((x + offset, y - offset, x + w + offset, y + h - offset), radius=18, fill=fill)
    draw.rounded_rectangle((x + 34, y + 34, x + 126, y + 74), radius=8, fill=accent)
    draw.text((x + 51, y + 41), "NEWS", fill=(255, 255, 255), font=image_font(22, bold=True))
    line_color = (97, 110, 130)
    for index in range(5):
        yy = y + 108 + index * 36
        draw.rounded_rectangle((x + 40, yy, x + w - 44, yy + 13), radius=6, fill=line_color)
    draw.rounded_rectangle((x + 40, y + h - 92, x + w - 160, y + h - 54), radius=10, outline=accent, width=4)


def generate_alert_image(
    item: NewsItem,
    classification: Classification,
    related_items: list[NewsItem],
    *,
    output_path: Path,
    size: str,
) -> Path:
    articles = []
    for article in [item, *related_items]:
        url = article.url or article.naver_url
        if not url:
            continue
        articles.append(
            {
                "title": article.title,
                "source": article.source or source_from_url(url),
                "url": url,
                "summary": article.summary,
            }
        )
    if not articles:
        articles.append(
            {
                "title": item.title or "병무청 관련 이슈",
                "source": item.source or "naver-news",
                "url": item.url or item.naver_url,
                "summary": item.summary,
            }
        )

    articles_path = output_path.with_suffix(".articles.json")
    articles_path.parent.mkdir(parents=True, exist_ok=True)
    articles_path.write_text(json.dumps(articles[:10], ensure_ascii=False, indent=2), encoding="utf-8")

    published = parse_iso_datetime(item.published_at) or datetime.now(KST)
    command = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "build_news_image_sheet.py"),
        "--articles",
        str(articles_path),
        "--output",
        str(output_path),
        "--date",
        published.strftime("%Y-%m-%d"),
        "--agency",
        "병무청 이슈",
        "--title",
        item.title,
        "--limit",
        str(min(10, len(articles))),
    ]
    result = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        details = "\n".join(
            part.strip()
            for part in [result.stdout[-2000:], result.stderr[-2000:]]
            if part.strip()
        )
        raise RuntimeError(f"Article image sheet generation failed with exit {result.returncode}: {details}")
    if not output_path.exists():
        raise RuntimeError(f"Article image sheet was not created: {output_path}")
    return output_path

    try:
        width_text, height_text = size.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except ValueError:
        width, height = 1024, 1024
    width = max(768, min(width, 1600))
    height = max(768, min(height, 1600))

    seed = int(hashlib.sha1((item.title + item.url).encode("utf-8", errors="ignore")).hexdigest()[:8], 16)
    palettes = [
        ((8, 15, 28), (190, 38, 52), (230, 238, 248)),
        ((12, 19, 34), (222, 72, 50), (244, 239, 229)),
        ((9, 24, 35), (214, 47, 92), (234, 244, 247)),
    ]
    bg, accent, paper = palettes[seed % len(palettes)]
    image = Image.new("RGBA", (width, height), bg + (255,))
    draw_soft_glow(image, (int(width * 0.22), int(height * 0.24)), int(width * 0.28), accent)
    draw_soft_glow(image, (int(width * 0.76), int(height * 0.70)), int(width * 0.24), (38, 95, 150))
    draw = ImageDraw.Draw(image)

    for offset in range(-height, width, 72):
        draw.line((offset, 0, offset + height, height), fill=(255, 255, 255, 16), width=2)

    draw.rounded_rectangle((54, 52, width - 54, height - 52), radius=46, outline=(255, 255, 255, 42), width=2)
    draw.rounded_rectangle((74, 72, 240, 124), radius=18, fill=accent)
    draw.text((102, 84), "ISSUE WATCH", fill=(255, 255, 255), font=image_font(26, bold=True))
    draw.rounded_rectangle((width - 284, 75, width - 76, 123), radius=16, fill=(28, 42, 63), outline=(255, 255, 255, 78))
    draw.text((width - 254, 85), "MILITARY SERVICE", fill=(235, 241, 249), font=image_font(22, bold=True))

    draw_phone_panel(draw, 84, 176, 290, 430, accent)
    draw_document_stack(draw, width - 390, 178, 300, 300, accent)
    draw_gavel(draw, width - 338, 530, 0.9, (223, 232, 243))
    draw_microphone(draw, 450, 238, 1.0, (229, 235, 244))

    if article_has_dental_context(item, classification):
        draw.rounded_rectangle((84, height - 302, 296, height - 114), radius=26, fill=(231, 240, 248), outline=accent, width=5)
        draw.text((116, height - 278), "X-RAY", fill=(25, 37, 56), font=image_font(26, bold=True))
        draw_molar(draw, 131, height - 240, 0.98, (23, 37, 60), accent)

    title_font = image_font(42, bold=True)
    body_font = image_font(25)
    meta_font = image_font(26, bold=True)
    title_box = (350, 610, width - 76, height - 126)
    draw.rounded_rectangle(title_box, radius=28, fill=(255, 255, 255, 232))
    draw.text((title_box[0] + 34, title_box[1] + 26), "REPRESENTATIVE ARTICLE", fill=accent, font=meta_font)
    title_lines = image_wrap_text(draw, item.title, title_font, title_box[2] - title_box[0] - 68, max_lines=3)
    y = title_box[1] + 72
    for line in title_lines:
        draw.text((title_box[0] + 34, y), line, fill=(12, 22, 37), font=title_font)
        y += 52

    summary = classification.summary or item.summary
    summary_lines = image_wrap_text(draw, summary, body_font, title_box[2] - title_box[0] - 68, max_lines=2)
    y += 12
    for line in summary_lines:
        if y + 32 > title_box[3] - 20:
            break
        draw.text((title_box[0] + 36, y), line, fill=(59, 72, 92), font=body_font)
        y += 32

    source = item.source or source_from_url(item.url)
    related_label = f"RELATED {len(related_items)}"
    draw.rounded_rectangle((84, height - 92, width - 84, height - 52), radius=18, fill=(28, 42, 63), outline=(255, 255, 255, 54))
    draw.text((112, height - 84), source.upper()[:28], fill=(236, 242, 249), font=image_font(23, bold=True))
    draw.text((width - 230, height - 84), related_label, fill=(236, 242, 249), font=image_font(23, bold=True))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output_path, quality=94)
    return output_path


def build_alert_message(
    item: NewsItem,
    classification: Classification,
    related_items: list[NewsItem] | None = None,
    related_hours: int = 12,
) -> str:
    url = item.url or item.naver_url
    related_items = related_items or []
    lead = "병역 관련 부정 이슈로 번질 수 있는 보도가 확인됐습니다."
    if classification.severity == "높음":
        lead = "병역 관련 여론 이슈로 확산될 수 있는 보도가 확인됐습니다."
    elif classification.severity == "낮음":
        lead = "병역 관련 모니터링 후보 보도가 확인됐습니다."
    summary = polish_alert_summary(classification.summary, item, classification.category, classification.matched_terms)
    if related_items:
        summary = (
            f"{summary}\n"
            f"같은 이슈로 최근 {related_hours}시간 안에 추가 보도 {len(related_items)}건이 함께 확인됐습니다."
        )
    lines = [
        "🚨 병무청 관련 이슈 알림",
        "",
        lead,
        "",
        f"위험도: {classification.severity}",
        f"유형: {classification.category}",
        f"발행: {format_published_label(item)}",
        "",
        "📰 대표 기사",
        item.title,
        "",
        "핵심 내용",
        summary,
        "",
        "확인 포인트",
        classification.reason,
        "",
        "대표 원문",
        url,
    ]
    if related_items:
        lines.extend(["", f"관련 기사 링크 최대 {len(related_items)}건", *related_link_lines(related_items)])
    return "\n".join(lines)


def trim_for_report(value: str, limit: int = 82) -> str:
    text = clean_text(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def build_diagnostic_report(
    *,
    room: str,
    now: datetime,
    fetched_count: int,
    recent_count: int,
    new_count: int,
    raw_candidate_count: int,
    source_relevance_reject_count: int,
    topic_duplicate_matches: list[dict[str, str]],
    semantic_duplicate_matches: list[dict[str, str]],
    ai_duplicate_checks: int,
    recent_alert_record_count: int,
    alerts: list[tuple[NewsItem, Classification, str]],
    errors: list[str],
) -> str:
    checked_label = f"{now.year}년 {now.month}월 {now.day}일 {now.hour}시 {now.minute:02d}분"
    lines = [
        "🧪 부정 이슈 탐지 테스트 리포트",
        "",
        f"검색 시각: {checked_label}",
        f"대상 채팅방: {room}",
        "",
        "검색 결과",
        f"- 전체 수집: {fetched_count}건",
        f"- 최근 범위 기사: {recent_count}건",
        f"- 신규 기사: {new_count}건",
        f"- 부정 이슈 후보: {raw_candidate_count}건",
        f"- 원문 확인 제외: {source_relevance_reject_count}건",
        f"- 최근 12시간 발송 이력: {recent_alert_record_count}건",
        "",
        "중복 판단",
    ]

    duplicate_lines: list[str] = []
    for match in semantic_duplicate_matches[:3]:
        duplicate_lines.extend(
            [
                f"- AI 중복 판단: {trim_for_report(match.get('title', ''))}",
                f"  근거: {trim_for_report(match.get('reason', '') or '최근 12시간 발송 이력과 같은 이슈로 판단했습니다.', 120)}",
            ]
        )
    for match in topic_duplicate_matches[:3]:
        duplicate_lines.extend(
            [
                f"- 규칙 중복 판단: {trim_for_report(match.get('title', ''))}",
                f"  근거: {trim_for_report(match.get('reason', ''), 120)}",
            ]
        )

    if duplicate_lines:
        lines.extend(duplicate_lines)
    elif raw_candidate_count == 0:
        lines.append("- 판단 대상 없음: 신규 부정 이슈 후보가 없었습니다.")
    elif alerts:
        lines.append("- 중복 아님: 최근 12시간 발송 이력과 다른 후보가 있어 알림 대상으로 남겼습니다.")
    else:
        lines.append("- 중복 아님: 후보는 있었지만 최종 발송 조건을 통과하지 못했습니다.")

    lines.extend(["", f"AI 중복 비교 실행: {ai_duplicate_checks}회", f"실제 알림 발송: {len(alerts)}건"])
    if alerts:
        lines.extend(["", "발송 대상"])
        for index, (item, classification, _topic_key) in enumerate(alerts[:3], start=1):
            lines.append(f"{index}. {trim_for_report(item.title)}")
            lines.append(f"   판단: {trim_for_report(classification.reason, 120)}")
    if errors:
        lines.extend(["", "수집 오류"])
        for error in errors[:3]:
            lines.append(f"- {trim_for_report(error, 120)}")
    return "\n".join(lines)


def resolve_mcp_command(value: str) -> str:
    if value:
        return value
    found = shutil.which("kakaotalk-mcp") or shutil.which("kakaotalk-mcp.exe")
    if found:
        return found
    scripts_dir = Path(sys.executable).resolve().parent / "Scripts"
    candidate = scripts_dir / "kakaotalk-mcp.exe"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("kakaotalk-mcp executable was not found.")


def post_to_kakao(message: str, *, room: str, mcp_command: str, verify: bool) -> None:
    (ROOT_DIR / "runs").mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix="negative-news-alert-",
        suffix=".md",
        dir=ROOT_DIR / "runs",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as message_file:
        message_file.write(message)
        message_path = Path(message_file.name)

    command = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "post_summary_mcp.py"),
        "--room",
        room,
        "--summary",
        str(message_path),
        "--mcp-command",
        resolve_mcp_command(mcp_command),
    ]
    if verify:
        command.append("--verify")
    try:
        result = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            details = "\n".join(
                part.strip()
                for part in [result.stdout[-2000:], result.stderr[-2000:]]
                if part.strip()
            )
            raise RuntimeError(f"Kakao post failed with exit {result.returncode}: {details}")
    finally:
        try:
            message_path.unlink()
        except FileNotFoundError:
            pass


def post_image_to_kakao(
    image_path: Path,
    *,
    room: str,
    open_wait: float,
    send_wait: float,
) -> None:
    if not image_path.exists():
        raise FileNotFoundError(f"Kakao alert image was not found: {image_path}")

    command = [
        sys.executable,
        str(ROOT_DIR / "scripts" / "post_kakao_image_attach.py"),
        "--room",
        room,
        "--image",
        str(image_path),
        "--open-wait",
        str(open_wait),
        "--send-wait",
        str(send_wait),
    ]
    result = subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        details = "\n".join(
            part.strip()
            for part in [result.stdout[-2000:], result.stderr[-2000:]]
            if part.strip()
        )
        raise RuntimeError(f"Kakao image post failed with exit {result.returncode}: {details}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Naver news for negative MMA-related issues.")
    parser.add_argument("--room", default=os.getenv("TARGET_CHATROOM", "AI 병무청 데일리 모닝톡"), help="KakaoTalk room title")
    parser.add_argument("--state", default=os.getenv("NEGATIVE_WATCH_STATE", ".scheduler/negative-news-seen.json"))
    parser.add_argument("--output-dir", default=os.getenv("NEGATIVE_WATCH_OUTPUT_DIR", "runs/negative-watch"))
    parser.add_argument("--alert-image", default=os.getenv("NEGATIVE_WATCH_ALERT_IMAGE", ""))
    parser.add_argument("--generate-alert-image", action="store_true", default=os.getenv("NEGATIVE_WATCH_GENERATE_IMAGE", "").strip().lower() in {"1", "true", "yes", "y", "on"})
    parser.add_argument("--image-size", default=os.getenv("NEGATIVE_WATCH_IMAGE_SIZE", "1024x1024"))
    parser.add_argument("--image-open-wait", type=float, default=float(os.getenv("NEGATIVE_WATCH_IMAGE_OPEN_WAIT", "1.5")))
    parser.add_argument("--image-send-wait", type=float, default=float(os.getenv("NEGATIVE_WATCH_IMAGE_SEND_WAIT", "8.0")))
    parser.add_argument("--queries", default=os.getenv("NEGATIVE_WATCH_QUERIES", ""))
    parser.add_argument("--display", type=int, default=int(os.getenv("NEGATIVE_WATCH_DISPLAY", "50")))
    parser.add_argument("--pages", type=int, default=int(os.getenv("NEGATIVE_WATCH_PAGES", "2")))
    parser.add_argument("--lookback-hours", type=int, default=int(os.getenv("NEGATIVE_WATCH_LOOKBACK_HOURS", "24")))
    parser.add_argument("--topic-ttl-hours", type=int, default=int(os.getenv("NEGATIVE_WATCH_TOPIC_TTL_HOURS", "12")))
    parser.add_argument("--related-hours", type=int, default=int(os.getenv("NEGATIVE_WATCH_RELATED_HOURS", "12")))
    parser.add_argument("--related-limit", type=int, default=int(os.getenv("NEGATIVE_WATCH_RELATED_LIMIT", "5")))
    parser.add_argument("--active-start-hour", type=int, default=int(os.getenv("NEGATIVE_WATCH_ACTIVE_START_HOUR", "8")))
    parser.add_argument("--active-end-hour", type=int, default=int(os.getenv("NEGATIVE_WATCH_ACTIVE_END_HOUR", "22")))
    parser.add_argument("--ignore-active-window", action="store_true", help="Run even outside the configured active hours")
    parser.add_argument("--max-alerts", type=int, default=int(os.getenv("NEGATIVE_WATCH_MAX_ALERTS", "1")))
    parser.add_argument("--send-diagnostic-report", action="store_true", default=os.getenv("NEGATIVE_WATCH_SEND_DIAGNOSTIC", "").strip().lower() in {"1", "true", "yes", "y", "on"})
    parser.add_argument("--dry-run", action="store_true", help="Do not send KakaoTalk messages or update state")
    parser.add_argument("--verify", action="store_true", help="Verify posted KakaoTalk alert")
    parser.add_argument("--mcp-command", default=os.getenv("KAKAOTALK_MCP_COMMAND", ""))
    parser.add_argument("--codex-command", default=os.getenv("CODEX_COMMAND", "codex.cmd"))
    parser.add_argument("--codex-model", default=os.getenv("CODEX_MODEL", ""))
    parser.add_argument("--codex-timeout-seconds", type=float, default=float(os.getenv("CODEX_TIMEOUT_SECONDS", "120")))
    parser.add_argument("--ai-duplicate-limit", type=int, default=int(os.getenv("NEGATIVE_WATCH_AI_DUPLICATE_LIMIT", "30")))
    parser.add_argument("--ai-refine-limit", type=int, default=int(os.getenv("NEGATIVE_WATCH_AI_REFINE_LIMIT", "3")))
    parser.add_argument("--summary-provider", default=os.getenv("NEGATIVE_WATCH_SUMMARY_PROVIDER", "codex"))
    parser.add_argument("--naver-client-id", default=os.getenv("NAVER_CLIENT_ID", ""))
    parser.add_argument("--naver-client-secret", default=os.getenv("NAVER_CLIENT_SECRET", ""))
    parser.add_argument("--proxy-base-url", default=os.getenv("KSKILL_PROXY_BASE_URL", "https://k-skill-proxy.nomadamas.org"))
    parser.add_argument("--timeout-seconds", type=float, default=float(os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
    return parser.parse_args()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = Path(args.state)
    if not state_path.is_absolute():
        state_path = ROOT_DIR / state_path

    queries = [item.strip() for item in args.queries.split(",") if item.strip()] or DEFAULT_QUERIES
    now = datetime.now(KST)
    if not args.ignore_active_window and not in_active_window(now, args.active_start_hour, args.active_end_hour):
        print(
            json.dumps(
                {
                    "room": args.room,
                    "dry_run": args.dry_run,
                    "skipped": True,
                    "reason": "outside_active_window",
                    "active_start_hour": args.active_start_hour,
                    "active_end_hour": args.active_end_hour,
                    "checked_at": now.isoformat(),
                    "fetched_count": 0,
                    "deduped_recent_count": 0,
                    "new_count": 0,
                    "topic_duplicate_count": 0,
                    "seen_topic_count": 0,
                    "alert_count": 0,
                    "posted": 0,
                    "errors": [],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    state = load_state(state_path)
    seen_urls: dict[str, str] = state.get("seen_urls", {})
    seen_topics: dict[str, str] = prune_seen_topics(
        state.get("seen_topics", {}),
        now,
        args.topic_ttl_hours,
    )
    sent_alerts = prune_sent_alerts(
        state.get("sent_alerts", []),
        now,
        args.topic_ttl_hours,
    )
    classifications: dict[str, Classification] = {}

    fetched: list[NewsItem] = []
    errors: list[str] = []
    for query in queries:
        try:
            fetched.extend(
                fetch_naver_news(
                    query,
                    display=args.display,
                    pages=args.pages,
                    timeout=args.timeout_seconds,
                    client_id=args.naver_client_id,
                    client_secret=args.naver_client_secret,
                    proxy_base_url=args.proxy_base_url,
                )
            )
        except Exception as exc:
            errors.append(f"{query}: {exc}")

    items = [
        item
        for item in dedupe_items(fetched)
        if within_lookback(item, args.lookback_hours, now)
    ]
    new_items = [
        item
        for item in items
        if item_key(item) not in seen_urls
    ]

    # Older state files only had URL-level dedupe. Rebuild issue-level topics
    # from URLs we have already seen and that still appear in the Naver window.
    if not seen_topics:
        for item in items:
            key = item_key(item)
            if key not in seen_urls:
                continue
            seen_at = parse_iso_datetime(seen_urls[key])
            if args.topic_ttl_hours > 0 and seen_at and seen_at < now - timedelta(hours=args.topic_ttl_hours):
                continue
            classification = classify_heuristic(item)
            classifications[key] = classification
            if classification.score <= 0:
                continue
            seen_topics.setdefault(topic_fingerprint(item, classification), seen_urls[key])

    recent_alert_records = merge_recent_alert_records(
        sent_alerts
        + recent_seen_records_from_urls(
            items,
            seen_urls,
            classifications,
            now,
            args.topic_ttl_hours,
        )
    )

    def classify_cached(item: NewsItem) -> Classification:
        key = item_key(item)
        cached = classifications.get(key)
        if cached is not None:
            return cached
        classification = classify_heuristic(item)
        classifications[key] = classification
        return classification

    source_relevance_cache: dict[str, bool] = {}

    def source_supports_cached(item: NewsItem) -> bool:
        key = item_key(item)
        cached = source_relevance_cache.get(key)
        if cached is not None:
            return cached
        supported = article_source_supports_issue(item, args.timeout_seconds)
        source_relevance_cache[key] = supported
        return supported

    raw_pairs: list[tuple[NewsItem, Classification, str]] = []
    inspected_keys: list[str] = []
    source_relevance_reject_count = 0
    for item in new_items:
        classification = classify_cached(item)
        if classification.score <= 0:
            continue
        inspected_keys.append(item_key(item))
        if classification.score >= 5 and not source_supports_cached(item):
            source_relevance_reject_count += 1
            continue
        raw_pairs.append((item, classification, topic_fingerprint(item, classification)))

    raw_pairs.sort(key=lambda pair: (pair[1].score, pair[0].published_at), reverse=True)

    heuristic_pairs: list[tuple[NewsItem, Classification, str]] = []
    selected_candidate_records: list[dict[str, Any]] = []
    run_topics: set[str] = set()
    topic_duplicate_count = 0
    topic_duplicate_matches: list[dict[str, str]] = []
    semantic_duplicate_count = 0
    semantic_duplicate_matches: list[dict[str, str]] = []
    ai_duplicate_checks = 0
    for item, classification, topic_key in raw_pairs:
        comparison_records = recent_alert_records + selected_candidate_records
        checked_with_ai = False
        if (
            args.summary_provider.lower() == "codex"
            and classification.score >= 5
            and ai_duplicate_checks < max(0, args.ai_duplicate_limit)
        ):
            ai_duplicate_checks += 1
            checked_with_ai = True
            is_duplicate, matched_topic_key, duplicate_reason = duplicate_with_codex(
                item,
                classification,
                comparison_records,
                codex_command=args.codex_command,
                codex_model=args.codex_model,
                timeout_seconds=args.codex_timeout_seconds,
                output_dir=output_dir,
            )
            if is_duplicate:
                semantic_duplicate_count += 1
                matched_sent_at = sent_at_for_topic(recent_alert_records, matched_topic_key)
                seen_topics.setdefault(topic_key, matched_sent_at or now.isoformat())
                semantic_duplicate_matches.append(
                    {
                        "title": item.title,
                        "topic_key": topic_key,
                        "matched_topic_key": matched_topic_key,
                        "reason": duplicate_reason,
                    }
                )
                continue
        if not checked_with_ai and (topic_key in seen_topics or topic_key in run_topics):
            topic_duplicate_count += 1
            reason = (
                "Codex 중복 판단 한도 또는 실행 환경 문제로 규칙 기반 토픽 키를 보조 적용했습니다."
                if topic_key in seen_topics
                else "Codex 중복 판단 한도 또는 실행 환경 문제로 이번 실행 내 토픽 키 중복을 보조 적용했습니다."
            )
            topic_duplicate_matches.append(
                {
                    "title": item.title,
                    "topic_key": topic_key,
                    "reason": reason,
                }
            )
            continue
        run_topics.add(topic_key)
        heuristic_pairs.append((item, classification, topic_key))
        selected_candidate_records.append(
            selected_candidate_record(item, classification, topic_key, now)
        )

    classified: list[tuple[NewsItem, Classification, str]] = []
    for index, (item, classification, topic_key) in enumerate(heuristic_pairs):
        if (
            args.summary_provider.lower() == "codex"
            and classification.score >= 5
            and index < max(0, args.ai_refine_limit)
        ):
            classification = refine_with_codex(
                item,
                classification,
                codex_command=args.codex_command,
                codex_model=args.codex_model,
                timeout_seconds=args.codex_timeout_seconds,
                output_dir=output_dir,
            )
        if classification.send:
            classified.append((item, classification, topic_key))

    classified.sort(key=lambda pair: (pair[1].score, pair[0].published_at), reverse=True)
    alerts = classified[: max(0, args.max_alerts)]
    related_by_topic = {}
    for item, _classification, topic_key in alerts:
        related_candidates = related_articles_for_topic(
            topic_key,
            item,
            items,
            classifications,
            now,
            args.related_hours,
            args.related_limit,
        )
        related_by_topic[topic_key] = [
            related for related in related_candidates if source_supports_cached(related)
        ][: args.related_limit]
    messages = [
        build_alert_message(
            item,
            classification,
            related_by_topic.get(topic_key, []),
            args.related_hours,
        )
        for item, classification, topic_key in alerts
    ]

    timestamp = now.strftime("%Y%m%d-%H%M%S")
    candidates_path = output_dir / f"candidates-{timestamp}.json"
    candidates_path.write_text(
        json.dumps(
            {
                "checked_at": now.isoformat(),
                "queries": queries,
                "errors": errors,
                "fetched_count": len(fetched),
                "deduped_recent_count": len(items),
                "new_count": len(new_items),
                "raw_candidate_count": len(raw_pairs),
                "topic_duplicate_count": topic_duplicate_count,
                "topic_duplicate_matches": topic_duplicate_matches,
                "semantic_duplicate_count": semantic_duplicate_count,
                "semantic_duplicate_matches": semantic_duplicate_matches,
                "ai_duplicate_checks": ai_duplicate_checks,
                "ai_duplicate_limit": args.ai_duplicate_limit,
                "source_relevance_reject_count": source_relevance_reject_count,
                "seen_topic_count": len(seen_topics),
                "recent_alert_record_count": len(recent_alert_records),
                "alert_count": len(alerts),
                "alerts": [
                    {
                        "article": asdict(item),
                        "classification": asdict(classification),
                        "topic_key": topic_key,
                        "related_articles": [asdict(related) for related in related_by_topic.get(topic_key, [])],
                    }
                    for item, classification, topic_key in alerts
                ],
                "sample_candidates": [asdict(item) for item in new_items[:20]],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    posted = 0
    alert_image_paths: list[str] = []
    if not args.dry_run:
        static_alert_image = Path(args.alert_image) if args.alert_image else None
        if static_alert_image is not None and not static_alert_image.is_absolute():
            static_alert_image = ROOT_DIR / static_alert_image
        posted_alert_records: list[dict[str, Any]] = []
        for index, ((item, classification, topic_key), message) in enumerate(zip(alerts, messages), start=1):
            related_items = related_by_topic.get(topic_key, [])
            alert_image = static_alert_image
            if args.generate_alert_image:
                alert_image = generate_alert_image(
                    item,
                    classification,
                    related_items,
                    output_path=output_dir / f"negative-alert-image-{timestamp}-{index:02d}.png",
                    size=args.image_size,
                )
                alert_image_paths.append(str(alert_image))
            if alert_image is not None:
                post_image_to_kakao(
                    alert_image,
                    room=args.room,
                    open_wait=args.image_open_wait,
                    send_wait=args.image_send_wait,
                )
            post_to_kakao(message, room=args.room, mcp_command=args.mcp_command, verify=args.verify)
            posted += 1
            posted_alert_records.append(
                alert_record(
                    item,
                    classification,
                    topic_key,
                    now,
                    related_articles=related_items,
                    message=message,
                )
            )

        for key in inspected_keys:
            seen_urls[key] = now.isoformat()
        for item, _classification, topic_key in alerts:
            seen_urls[item_key(item)] = now.isoformat()
            seen_topics[topic_key] = now.isoformat()
        state["seen_urls"] = dict(list(seen_urls.items())[-2000:])
        state["seen_topics"] = dict(list(seen_topics.items())[-2000:])
        state["sent_alerts"] = prune_sent_alerts(sent_alerts + posted_alert_records, now, args.topic_ttl_hours)[-200:]
        state["last_checked_at"] = now.isoformat()
        save_state(state_path, state)

        diagnostic_posted = 0
        if args.send_diagnostic_report:
            diagnostic_message = build_diagnostic_report(
                room=args.room,
                now=now,
                fetched_count=len(fetched),
                recent_count=len(items),
                new_count=len(new_items),
                raw_candidate_count=len(raw_pairs),
                source_relevance_reject_count=source_relevance_reject_count,
                topic_duplicate_matches=topic_duplicate_matches,
                semantic_duplicate_matches=semantic_duplicate_matches,
                ai_duplicate_checks=ai_duplicate_checks,
                recent_alert_record_count=len(recent_alert_records),
                alerts=alerts,
                errors=errors,
            )
            post_to_kakao(diagnostic_message, room=args.room, mcp_command=args.mcp_command, verify=False)
            diagnostic_posted = 1

        alerts_log = output_dir / f"alerts-{now.strftime('%Y-%m-%d')}.jsonl"
        with alerts_log.open("a", encoding="utf-8") as log_file:
            for item, classification, topic_key in alerts:
                log_file.write(
                    json.dumps(
                        {
                            "posted_at": now.isoformat(),
                            "room": args.room,
                            "topic_key": topic_key,
                            "article": asdict(item),
                            "related_articles": [
                                asdict(related) for related in related_by_topic.get(topic_key, [])
                            ],
                            "classification": asdict(classification),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    else:
        diagnostic_posted = 0

    print(
        json.dumps(
            {
                "room": args.room,
                "dry_run": args.dry_run,
                "fetched_count": len(fetched),
                "deduped_recent_count": len(items),
                "new_count": len(new_items),
                "raw_candidate_count": len(raw_pairs),
                "topic_duplicate_count": topic_duplicate_count,
                "topic_duplicate_matches": topic_duplicate_matches,
                "semantic_duplicate_count": semantic_duplicate_count,
                "ai_duplicate_checks": ai_duplicate_checks,
                "seen_topic_count": len(seen_topics),
                "recent_alert_record_count": len(recent_alert_records),
                "candidate_path": str(candidates_path),
                "alert_count": len(alerts),
                "posted": posted,
                "diagnostic_posted": diagnostic_posted,
                "generated_alert_images": alert_image_paths,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
