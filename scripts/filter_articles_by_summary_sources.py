from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


URL_RE = re.compile(r"https?://[^\s)>\]]+")
NUMBERED_TITLE_RE = re.compile(r"^(?:#\s*)?(?:[1-9]️⃣|🔟|\d+(?:[.)])?)\s+(.+?)\s*$")
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "input",
    "outurl",
    "ref",
    "referer",
    "sc",
}


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip().rstrip(".,;"))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    ]
    normalized = urlunparse(parsed._replace(query=urlencode(query), fragment=""))
    return normalized.rstrip("/")


def extract_source_urls(summary_text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in summary_text.splitlines():
        if not line.strip().lower().startswith("source:"):
            continue
        for raw_url in URL_RE.findall(line):
            url = canonical_url(raw_url)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def title_lookup_key(value: str) -> str:
    normalized = normalize_title(value).casefold()
    return re.sub(r"[\s\"'“”‘’.,·…\-\[\]()（）{}<>:;!?]", "", normalized)


def extract_numbered_titles(summary_text: str) -> list[str]:
    titles: list[str] = []
    seen: set[str] = set()
    for line in summary_text.splitlines():
        match = NUMBERED_TITLE_RE.match(line.strip())
        if not match:
            continue
        title = normalize_title(match.group(1))
        if title and title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def extract_summary_items(summary_text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in summary_text.splitlines():
        stripped = line.strip()
        match = NUMBERED_TITLE_RE.match(stripped)
        if match:
            if current is not None:
                items.append(current)
            current = {"title": normalize_title(match.group(1)), "url": ""}
            continue
        if current is None or not stripped.lower().startswith("source:"):
            continue
        urls = URL_RE.findall(stripped)
        if urls:
            current["url"] = canonical_url(urls[0])
    if current is not None:
        items.append(current)
    return items


def filter_articles_by_source_urls(articles: list[dict[str, Any]], source_urls: list[str]) -> list[dict[str, Any]]:
    if not source_urls:
        return []

    by_url: dict[str, dict[str, Any]] = {}
    for article in articles:
        url = canonical_url(str(article.get("url") or ""))
        if url and url not in by_url:
            by_url[url] = article

    selected: list[dict[str, Any]] = []
    for source_url in source_urls:
        article = by_url.get(source_url)
        if article is not None:
            selected.append(article)
    return selected


def filter_articles_by_titles(articles: list[dict[str, Any]], titles: list[str]) -> list[dict[str, Any]]:
    if not titles:
        return []

    by_title: dict[str, dict[str, Any]] = {}
    by_key: dict[str, dict[str, Any]] = {}
    for article in articles:
        title = normalize_title(str(article.get("title") or ""))
        if title and title not in by_title:
            by_title[title] = article
        key = title_lookup_key(title)
        if key and key not in by_key:
            by_key[key] = article

    selected: list[dict[str, Any]] = []
    for title in titles:
        article = by_title.get(title)
        if article is None:
            article = by_key.get(title_lookup_key(title))
        if article is not None:
            selected.append(article)
    return selected


def url_lookup_key(value: str) -> str:
    normalized = canonical_url(value)
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    host = parsed.netloc.casefold()
    if host.startswith("www."):
        host = host[4:]
    return urlunparse(("", host, parsed.path.rstrip("/"), "", parsed.query, ""))


def select_articles_for_summary(
    articles: list[dict[str, Any]],
    summary_text: str,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    items = extract_summary_items(summary_text)
    stats = {
        "summary_items": len(items),
        "matched_by_url": 0,
        "matched_by_title": 0,
        "synthesized": 0,
    }
    if not items:
        return [], stats

    by_url: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    by_title_key: dict[str, dict[str, Any]] = {}
    for article in articles:
        url_key = url_lookup_key(str(article.get("url") or ""))
        if url_key and url_key not in by_url:
            by_url[url_key] = article
        title = normalize_title(str(article.get("title") or ""))
        if title and title not in by_title:
            by_title[title] = article
        title_key = title_lookup_key(title)
        if title_key and title_key not in by_title_key:
            by_title_key[title_key] = article

    selected: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        title = item["title"]
        source_url = item["url"]
        article = by_url.get(url_lookup_key(source_url)) if source_url else None
        if article is not None:
            stats["matched_by_url"] += 1
        else:
            article = by_title.get(title) or by_title_key.get(title_lookup_key(title))
            if article is not None:
                stats["matched_by_title"] += 1

        if article is None:
            source = urlparse(source_url).netloc.casefold()
            if source.startswith("www."):
                source = source[4:]
            selected_article: dict[str, Any] = {
                "title": title,
                "url": source_url,
                "source": source or "네이버 뉴스",
                "published_at": "",
                "summary": "",
                "origin": "summary_source",
            }
            stats["synthesized"] += 1
        else:
            selected_article = dict(article)
            selected_article["original_title"] = str(article.get("title") or "")
            selected_article["title"] = title

        selected_article["summary_index"] = index
        selected.append(selected_article)

    return selected, stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Filter collected article JSON to the Source URLs used in a summary.")
    parser.add_argument("--articles", required=True, help="Input runs/articles-YYYY-MM-DD.json")
    parser.add_argument("--summary", required=True, help="Generated summary Markdown")
    parser.add_argument("--output", required=True, help="Filtered article JSON path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    articles_path = Path(args.articles)
    summary_path = Path(args.summary)
    output_path = Path(args.output)

    articles = json.loads(articles_path.read_text(encoding="utf-8"))
    if not isinstance(articles, list):
        raise RuntimeError(f"Article JSON must contain a list: {articles_path}")

    summary_text = summary_path.read_text(encoding="utf-8")
    source_urls = extract_source_urls(summary_text)
    selected, selection_stats = select_articles_for_summary(articles, summary_text)
    title_matches = selection_stats["matched_by_title"]
    if not selected:
        numbered_titles = extract_numbered_titles(summary_text)
        selected = filter_articles_by_source_urls(articles, source_urls)
        if not selected:
            selected = filter_articles_by_titles(articles, numbered_titles)
            title_matches = len(selected)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "summary_sources": len(source_urls),
                "summary_titles": selection_stats["summary_items"],
                "selected_articles": len(selected),
                "matched_by_url": selection_stats["matched_by_url"],
                "matched_by_title": title_matches,
                "synthesized": selection_stats["synthesized"],
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
