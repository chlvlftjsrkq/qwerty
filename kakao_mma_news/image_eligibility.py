from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from io import BytesIO
from typing import Callable
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from PIL import Image

from .news import Article


USER_AGENT = "agency-news-talkbriefing/0.1 (+image-preflight)"
ROUNDUP_TITLE_PATTERNS = (
    re.compile(r"이\s*시각\s*(?:주요\s*)?헤드라인", re.IGNORECASE),
    re.compile(r"(?:오늘|오전|오후|저녁)의?\s*(?:주요\s*)?뉴스", re.IGNORECASE),
    re.compile(r"(?:뉴스|이슈)\s*(?:브리핑|클리핑|모음)", re.IGNORECASE),
    re.compile(r"(?:주요\s*)?헤드라인\s*(?:뉴스)?", re.IGNORECASE),
    re.compile(r"한\s*눈에\s*(?:보는|읽는)?\s*(?:뉴스|이슈)", re.IGNORECASE),
)


@dataclass(frozen=True)
class ImageProbe:
    image_url: str = ""
    width: int = 0
    height: int = 0
    reason: str = ""

    @property
    def eligible(self) -> bool:
        return bool(self.image_url and self.width and self.height and not self.reason)


def roundup_reason(article: Article) -> str:
    title = re.sub(r"\s+", " ", article.title or "").strip()
    for pattern in ROUNDUP_TITLE_PATTERNS:
        if pattern.search(title):
            return f"roundup title: {pattern.pattern}"
    return ""


def _meta_image_url(page_url: str, html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    selectors = (
        ("property", "og:image"),
        ("property", "og:image:url"),
        ("name", "twitter:image"),
        ("name", "image"),
    )
    for key, value in selectors:
        tag = soup.find("meta", attrs={key: value})
        content = str(tag.get("content", "")).strip() if tag else ""
        if content:
            return urljoin(page_url, content)
    return ""


def probe_article_image(page_url: str, timeout: float = 6.0) -> ImageProbe:
    if not page_url:
        return ImageProbe(reason="missing article URL")

    import requests

    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        page_response = requests.get(page_url, timeout=timeout, headers=headers)
        page_response.raise_for_status()
        image_url = _meta_image_url(page_url, page_response.text)
        if not image_url:
            return ImageProbe(reason="missing representative image metadata")

        image_headers = {
            "User-Agent": USER_AGENT,
            "Referer": f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/",
        }
        image_response = requests.get(image_url, timeout=timeout, headers=image_headers)
        image_response.raise_for_status()
        with Image.open(BytesIO(image_response.content)) as image:
            width, height = image.size
        if width * height < 90_000 or max(width, height) < 320 or min(width, height) < 160:
            return ImageProbe(
                image_url=image_url,
                width=width,
                height=height,
                reason="representative image is too small",
            )
        return ImageProbe(image_url=image_url, width=width, height=height)
    except Exception as exc:
        return ImageProbe(reason=f"{type(exc).__name__}: {exc}")


def filter_image_eligible_articles(
    articles: list[Article],
    *,
    timeout: float = 6.0,
    max_workers: int = 12,
    probe: Callable[[str, float], ImageProbe] = probe_article_image,
) -> tuple[list[Article], dict[str, int]]:
    candidates: list[tuple[int, Article]] = []
    stats = {
        "input": len(articles),
        "roundup_excluded": 0,
        "image_excluded": 0,
        "eligible": 0,
    }
    for index, article in enumerate(articles):
        if roundup_reason(article):
            stats["roundup_excluded"] += 1
            continue
        candidates.append((index, article))

    results: dict[int, ImageProbe] = {}
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        futures = {
            executor.submit(probe, article.url, timeout): index
            for index, article in candidates
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = ImageProbe(reason=f"{type(exc).__name__}: {exc}")

    eligible: list[Article] = []
    for index, article in candidates:
        result = results.get(index, ImageProbe(reason="image probe did not complete"))
        if not result.eligible:
            stats["image_excluded"] += 1
            continue
        eligible.append(replace(article, image_url=result.image_url))

    stats["eligible"] = len(eligible)
    return eligible, stats
