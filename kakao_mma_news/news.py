from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus, urlencode, urlparse, urlunparse, parse_qsl
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import Config


try:
    KST = ZoneInfo("Asia/Seoul")
except ZoneInfoNotFoundError:
    KST = timezone(timedelta(hours=9), "KST")

MMA_BRIEFING_NEGATIVE_TERMS = {
    "병역법 위반": 120,
    "병역기피": 120,
    "병역 기피": 120,
    "병역비리": 120,
    "병역 비리": 120,
    "허위진단서": 100,
    "허위 진단서": 100,
    "부실복무": 95,
    "부실 복무": 95,
    "무단결근": 95,
    "무단 결근": 95,
    "무단이탈": 90,
    "무단 이탈": 90,
    "복무이탈": 90,
    "복무 이탈": 90,
    "근무태만": 80,
    "징역": 75,
    "기소": 70,
    "검찰": 65,
    "재판": 65,
    "공판": 65,
    "수사": 60,
    "송치": 60,
    "논란": 45,
    "의혹": 45,
    "특혜": 35,
    "루머": 25,
}

MMA_BRIEFING_PUBLIC_TERMS = (
    "연예인",
    "가수",
    "아이돌",
    "배우",
    "래퍼",
    "방송인",
    "공인",
    "축구선수",
    "프로야구",
    "유튜버",
)

MMA_BRIEFING_LOCAL_TERMS = (
    "지방병무청",
    "병무지청",
    "서울지방",
    "부산지방",
    "경인지방",
    "인천병무",
    "대전충남",
    "충북지방",
    "광주전남",
    "대구경북",
    "전북지방",
    "경남지방",
    "제주지방",
)

MMA_BRIEFING_ROUTINE_TERMS = (
    "기고",
    "간담회",
    "방문",
    "홍보",
    "업무협약",
    "협약",
    "청렴",
    "모집",
    "설명회",
    "체험",
    "캠페인",
    "기념 촬영",
)


@dataclass(frozen=True)
class Article:
    title: str
    url: str
    source: str
    published_at: datetime | None
    summary: str
    origin: str
    content: str = ""

    @property
    def published_date_kst(self) -> date | None:
        if not self.published_at:
            return None
        return self.published_at.astimezone(KST).date()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return normalize_space(text)


def normalize_space(value: str) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([\[\(（])\s+", r"\1", text)
    text = re.sub(r"\s+([\]\)）])", r"\1", text)
    text = re.sub(r"\]\s*([^\s\]\),.;:!?])", r"] \1", text)
    text = re.sub(r"([가-힣]{1,12}지방)\s+병무청", r"\1병무청", text)
    text = re.sub(r"병무청\s+장", "병무청장", text)
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
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            try:
                from dateutil import parser

                dt = parser.parse(value)
            except Exception:
                return None
    else:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc)


def parse_feed_datetime(entry: object) -> datetime | None:
    published = getattr(entry, "published", None) or getattr(entry, "updated", None)
    parsed = parse_datetime(published)
    if parsed:
        return parsed

    parsed_tuple = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if parsed_tuple:
        try:
            return datetime(*parsed_tuple[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None
    return None


def article_identity(article: Article) -> str:
    normalized_title = normalize_space(article.title).lower()
    normalized_url = canonical_url(article.url)
    digest_source = normalized_url or normalized_title
    return hashlib.sha256(digest_source.encode("utf-8")).hexdigest()


def dedupe_articles(articles: Iterable[Article]) -> list[Article]:
    seen: set[str] = set()
    result: list[Article] = []
    for article in articles:
        key = article_identity(article)
        title_key = normalize_space(article.title).lower()
        if key in seen or title_key in seen:
            continue
        seen.add(key)
        seen.add(title_key)
        result.append(article)
    return result


def relevance_score(article: Article, terms: list[str]) -> int:
    haystack = f"{article.title} {article.summary} {article.content}".lower()
    score = 0
    for term in terms:
        if term.lower() in haystack:
            score += 2 if term == "병무청" else 1
    return score


def briefing_priority_score(article: Article, config: Config) -> int:
    if config.agency_name != "병무청":
        return relevance_score(article, config.query_terms)

    haystack = f"{article.title} {article.summary} {article.content}"
    title_text = article.title
    score = 0
    negative_score = 0
    title_negative_score = 0
    for term, weight in MMA_BRIEFING_NEGATIVE_TERMS.items():
        if term in haystack:
            negative_score += weight
        if term in title_text:
            title_negative_score += weight
    score += negative_score
    score += title_negative_score

    has_public_context = any(term in haystack for term in MMA_BRIEFING_PUBLIC_TERMS)
    if negative_score and has_public_context:
        score += 160
    elif has_public_context:
        score += 45

    if "병무청" in haystack:
        score += 70
    if "병무청장" in haystack:
        score += 45
    if "특별사법경찰" in haystack:
        score += 55
    if "사회복무요원" in haystack:
        score += 35
    if "병역판정검사" in haystack or "입영" in haystack or "현역병" in haystack:
        score += 25
    if negative_score and not title_negative_score and not any(term in title_text for term in ("병무청", "병역", "사회복무요원", "공익")):
        score -= 140

    if any(term in haystack for term in MMA_BRIEFING_LOCAL_TERMS):
        score -= 35 if not negative_score else 10
    if any(term in haystack for term in MMA_BRIEFING_ROUTINE_TERMS):
        score -= 25 if not negative_score else 5

    return score


def matches_required_terms(article: Article, terms: list[str]) -> bool:
    if not terms:
        return True
    haystack = f"{article.title} {article.summary} {article.content}".lower()
    return any(term.lower() in haystack for term in terms)


def split_google_news_title(title: str) -> tuple[str, str]:
    if " - " not in title:
        return title, "Google News"
    article_title, source = title.rsplit(" - ", 1)
    return article_title.strip(), source.strip()


def read_feed(url: str, origin: str, timeout: float) -> list[Article]:
    try:
        import feedparser
    except ImportError as exc:
        raise RuntimeError("feedparser가 설치되어 있지 않습니다. requirements.txt를 설치하세요.") from exc

    parsed = feedparser.parse(url, request_headers={"User-Agent": "agency-news-talkbriefing/0.1"})
    if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", []):
        raise RuntimeError(f"RSS 읽기 실패: {getattr(parsed, 'bozo_exception', 'unknown error')}")

    articles: list[Article] = []
    for entry in parsed.entries:
        raw_title = strip_html(getattr(entry, "title", ""))
        title = raw_title
        source = getattr(getattr(entry, "source", None), "title", "") or origin
        if origin == "google_news":
            title, google_source = split_google_news_title(raw_title)
            source = google_source or source
        articles.append(
            Article(
                title=title,
                url=canonical_url(getattr(entry, "link", "")),
                source=strip_html(source),
                published_at=parse_feed_datetime(entry),
                summary=strip_html(getattr(entry, "summary", "")),
                origin=origin,
            )
        )
    return articles


def google_news_url(term: str, lookback_days: int) -> str:
    query = f'"{term}" when:{lookback_days}d'
    return (
        "https://news.google.com/rss/search?"
        + urlencode({"q": query, "hl": "ko", "gl": "KR", "ceid": "KR:ko"})
    )


def collect_google_news(config: Config) -> list[Article]:
    if not config.google_news_enabled:
        return []
    articles: list[Article] = []
    for term in config.query_terms:
        articles.extend(
            read_feed(
                google_news_url(term, config.lookback_days),
                origin="google_news",
                timeout=config.request_timeout_seconds,
            )
        )
    return articles


def collect_policy_rss(config: Config) -> list[Article]:
    if not config.policy_rss_enabled:
        return []
    articles: list[Article] = []
    for url in config.policy_rss_urls:
        articles.extend(read_feed(url, origin="policy_rss", timeout=config.request_timeout_seconds))
    return articles


def _naver_item_to_article(item: dict, origin: str) -> Article:
    url = (
        item.get("original_link")
        or item.get("originallink")
        or item.get("link")
        or ""
    )
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    source = strip_html(item.get("source") or "")
    if source.lower() in {"naver-openapi", "naver_news", "naver-news"}:
        source = ""
    published = (
        item.get("pub_date_iso")
        or item.get("pubDate")
        or item.get("pub_date")
        or item.get("pubDateIso")
    )
    return Article(
        title=strip_html(item.get("title", "")),
        url=canonical_url(url),
        source=source or host or "네이버 뉴스 검색",
        published_at=parse_datetime(published),
        summary=strip_html(item.get("description", "")),
        origin=origin,
    )


def _should_continue_date_paging(articles: list[Article], target_date: date | None) -> bool:
    if not target_date or not articles:
        return False
    parsed_dates = [
        article.published_date_kst
        for article in articles
        if article.published_date_kst is not None
    ]
    if not parsed_dates:
        return True
    return min(parsed_dates) >= target_date


def _collect_naver_news_direct(config: Config, target_date: date | None = None) -> list[Article]:
    import requests

    endpoint = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": config.naver_client_id,
        "X-Naver-Client-Secret": config.naver_client_secret,
        "User-Agent": "agency-news-talkbriefing/0.1",
    }
    articles: list[Article] = []
    for term in config.query_terms:
        for page in range(config.naver_news_pages):
            response = requests.get(
                endpoint,
                headers=headers,
                params={"query": term, "display": 100, "start": page * 100 + 1, "sort": "date"},
                timeout=config.request_timeout_seconds,
            )
            response.raise_for_status()
            page_articles = [
                _naver_item_to_article(item, "naver_news")
                for item in response.json().get("items", [])
            ]
            articles.extend(page_articles)
            if not _should_continue_date_paging(page_articles, target_date):
                break
    return articles


def _collect_naver_news_proxy(config: Config, target_date: date | None = None) -> list[Article]:
    import requests

    endpoint = f"{config.kskill_proxy_base_url}/v1/naver-news/search"
    articles: list[Article] = []
    for term in config.query_terms:
        if len(term.strip()) < 2:
            continue
        for page in range(config.naver_news_pages):
            response = requests.get(
                endpoint,
                params={"q": term, "display": 100, "start": page * 100 + 1, "sort": "date"},
                timeout=config.request_timeout_seconds,
            )
            response.raise_for_status()
            page_articles = [
                _naver_item_to_article(item, "naver_news_proxy")
                for item in response.json().get("items", [])
            ]
            articles.extend(page_articles)
            if not _should_continue_date_paging(page_articles, target_date):
                break
    return articles


def _collect_naver_news_web_date_filter(config: Config, target_date: date) -> list[Article]:
    import requests
    from bs4 import BeautifulSoup

    articles: list[Article] = []
    target = target_date.strftime("%Y%m%d")
    target_dot = target_date.strftime("%Y.%m.%d")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    page_count = max(1, min(config.naver_news_pages, 10))
    for term in config.query_terms:
        if len(term.strip()) < 2:
            continue
        records: dict[str, dict[str, str]] = {}
        for page in range(page_count):
            start = page * 10 + 1
            response = requests.get(
                "https://search.naver.com/search.naver",
                params={
                    "where": "news",
                    "query": term,
                    "sort": "1",
                    "pd": "3",
                    "ds": target_dot,
                    "de": target_dot,
                    "nso": f"so:dd,p:from{target}to{target},a:all",
                    "start": start,
                },
                headers=headers,
                timeout=config.request_timeout_seconds,
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for link in soup.find_all("a", href=True):
                href = canonical_url(link["href"])
                parsed = urlparse(href)
                if parsed.scheme not in {"http", "https"}:
                    continue
                if "search.naver.com" in parsed.netloc or "channelPromotion" in href:
                    continue
                title = normalize_space(link.get_text(" ", strip=True))
                if len(title) < 8 or len(title) > 160:
                    continue
                if title.startswith("뉴스 기사와 댓글") or title.startswith("언론사 선정"):
                    continue

                context = ""
                parent = link.parent
                for _ in range(3):
                    if not parent:
                        break
                    context = normalize_space(parent.get_text(" ", strip=True))
                    if len(context) >= len(title) + 20:
                        break
                    parent = parent.parent

                haystack = f"{title} {context}"
                if config.required_terms and not any(required in haystack for required in config.required_terms):
                    continue
                if not any(query in haystack for query in config.query_terms):
                    continue

                host = parsed.netloc.lower()
                if host.startswith("www."):
                    host = host[4:]
                record = records.setdefault(
                    href,
                    {"title": title, "summary": "", "url": href, "source": host or "네이버 뉴스"},
                )
                if context and context != title and len(context) > len(record["summary"]):
                    record["summary"] = context

        published = datetime.combine(target_date, time(12, 0), tzinfo=KST)
        for record in records.values():
            summary = record["summary"] or record["title"]
            articles.append(
                Article(
                    title=record["title"],
                    url=record["url"],
                    source=record["source"],
                    published_at=published,
                    summary=summary,
                    origin="naver_news_web_date",
                )
            )
    return articles


def collect_naver_news(config: Config, target_date: date | None = None) -> list[Article]:
    if not config.naver_news_enabled:
        return []
    try:
        import requests  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("requests가 설치되어 있지 않습니다. requirements.txt를 설치하세요.") from exc

    if config.naver_client_id and config.naver_client_secret:
        return _collect_naver_news_direct(config, target_date)
    return _collect_naver_news_proxy(config, target_date)


def _filter_articles(articles: Iterable[Article], config: Config, target_date: date) -> list[Article]:
    filtered: list[Article] = []
    for article in dedupe_articles(articles):
        if article.published_date_kst != target_date:
            continue
        if not matches_required_terms(article, config.required_terms):
            continue
        if relevance_score(article, config.query_terms) <= 0:
            continue
        if config.fetch_article_text:
            article = Article(
                title=article.title,
                url=article.url,
                source=article.source,
                published_at=article.published_at,
                summary=article.summary,
                origin=article.origin,
                content=fetch_article_text(article.url, config.request_timeout_seconds),
            )
        filtered.append(article)

    filtered.sort(
        key=lambda item: (
            briefing_priority_score(item, config),
            relevance_score(item, config.query_terms),
            item.published_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    return filtered


def fetch_article_text(url: str, timeout: float) -> str:
    if not url or "news.google.com" in url:
        return ""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; agency-news-talkbriefing/0.1)"},
        )
        response.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    meta = soup.find("meta", attrs={"property": "og:description"}) or soup.find(
        "meta", attrs={"name": "description"}
    )
    meta_text = meta.get("content", "") if meta else ""
    paragraphs = [normalize_space(p.get_text(" ")) for p in soup.find_all("p")]
    joined = " ".join(p for p in paragraphs if len(p) >= 30)
    return normalize_space(f"{meta_text} {joined}")[:4000]


def collect_articles(config: Config, target_date: date) -> list[Article]:
    articles = []
    errors: list[str] = []

    if config.naver_news_enabled:
        try:
            articles.extend(collect_naver_news(config, target_date))
        except Exception as exc:
            errors.append(f"collect_naver_news: {exc}")

    for collector in (collect_policy_rss, collect_google_news):
        try:
            articles.extend(collector(config))
        except Exception as exc:
            errors.append(f"{collector.__name__}: {exc}")

    filtered = _filter_articles(articles, config, target_date)
    if not filtered and config.naver_news_enabled:
        try:
            articles.extend(_collect_naver_news_web_date_filter(config, target_date))
            filtered = _filter_articles(articles, config, target_date)
        except Exception as exc:
            errors.append(f"collect_naver_news_web_date_filter: {exc}")

    if errors and not articles:
        raise RuntimeError("뉴스 수집 실패: " + "; ".join(errors))

    if errors:
        print("일부 뉴스 소스 수집 실패:", "; ".join(errors))
    return filtered[: config.max_items]


def save_articles(path: Path, articles: list[Article]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = []
    for article in articles:
        item = asdict(article)
        item["published_at"] = article.published_at.isoformat() if article.published_at else None
        serializable.append(item)
    path.write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding="utf-8")
