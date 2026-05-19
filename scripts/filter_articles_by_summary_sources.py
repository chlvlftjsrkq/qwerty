from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


URL_RE = re.compile(r"https?://[^\s)>\]]+")


def canonical_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip().rstrip(".,;"))
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}
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

    source_urls = extract_source_urls(summary_path.read_text(encoding="utf-8"))
    selected = filter_articles_by_source_urls(articles, source_urls)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "summary_sources": len(source_urls),
                "selected_articles": len(selected),
                "output": str(output_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
