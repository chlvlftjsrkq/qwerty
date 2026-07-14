from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .config import load_config
from .kakao import post_to_kakao
from .news import KST, collect_articles, save_articles
from .summarize import build_summary


def default_target_date() -> str:
    return (datetime.now(KST).date() - timedelta(days=1)).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="기관별 전날 뉴스 요약을 생성하고 PC 카카오톡에 게시합니다.")
    parser.add_argument("--env-file", default=".env", help="환경설정 파일 경로")
    parser.add_argument("--date", default=default_target_date(), help="요약 대상 날짜 YYYY-MM-DD, 기본값은 KST 기준 전날")
    parser.add_argument("--start-date", default="", help="Optional first date for a multi-day briefing range, YYYY-MM-DD")
    parser.add_argument("--output", default="", help="요약 Markdown 저장 경로")
    parser.add_argument("--articles-output", default="", help="Optional article JSON output path")
    parser.add_argument("--post", action="store_true", help="PC 카카오톡 단톡방에 게시")
    parser.add_argument("--dry-run", action="store_true", help="게시하지 않고 수집/요약만 수행")
    parser.add_argument("--fetch-pages", action="store_true", help="기사 본문 일부를 추가 수집")
    return parser.parse_args()


def date_label(start_date, target_date) -> str:
    if start_date == target_date:
        return target_date.isoformat()
    return f"{start_date.isoformat()}~{target_date.isoformat()}"


def file_date_label(start_date, target_date) -> str:
    return date_label(start_date, target_date).replace("~", "-to-")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    config = load_config(args.env_file)
    if args.fetch_pages:
        config = config.__class__(**{**config.__dict__, "fetch_article_text": True})

    target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date() if args.start_date else target_date
    if start_date > target_date:
        raise SystemExit("--start-date must be earlier than or equal to --date")
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    articles = collect_articles(config, target_date, start_date=start_date)
    articles_path = Path(args.articles_output) if args.articles_output else output_dir / f"articles-{file_date_label(start_date, target_date)}.json"
    save_articles(articles_path, articles)

    summary = build_summary(config, target_date, articles, start_date=start_date)
    output_path = Path(args.output) if args.output else output_dir / f"summary-{file_date_label(start_date, target_date)}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(summary, encoding="utf-8")

    print(summary)
    print(f"\n저장: {output_path}")
    print(f"기사 목록: {articles_path}")

    should_post = args.post or config.kakao_enabled
    if args.dry_run:
        should_post = False
    if should_post:
        posted = post_to_kakao(config, summary)
        if posted:
            print("카카오톡 게시 완료")
        else:
            print("카카오톡 자동발송이 일시정지되어 게시를 건너뛰었습니다.")
    return 0
