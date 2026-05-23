from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9), "KST")

NON_PUBLIC_HOLIDAY_NAMES = ("제헌절",)

FALLBACK_HOLIDAYS: dict[int, dict[str, str]] = {
    2026: {
        "2026-01-01": "신정",
        "2026-02-16": "설날 전날",
        "2026-02-17": "설날",
        "2026-02-18": "설날 다음날",
        "2026-03-01": "삼일절",
        "2026-03-02": "삼일절 대체공휴일",
        "2026-05-05": "어린이날",
        "2026-05-24": "부처님오신날",
        "2026-05-25": "부처님오신날 대체공휴일",
        "2026-06-03": "지방선거일",
        "2026-06-06": "현충일",
        "2026-08-15": "광복절",
        "2026-08-17": "광복절 대체공휴일",
        "2026-09-24": "추석 전날",
        "2026-09-25": "추석",
        "2026-09-26": "추석 다음날",
        "2026-10-03": "개천절",
        "2026-10-05": "개천절 대체공휴일",
        "2026-10-09": "한글날",
        "2026-12-25": "기독탄신일",
    }
}


def parse_target_date(value: str) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(KST).date()


def holiday_name(target: date) -> str:
    try:
        import holidays

        kr_holidays = holidays.KR(years=[target.year], observed=True)
        name = str(kr_holidays.get(target, "") or "")
        if name and not any(excluded in name for excluded in NON_PUBLIC_HOLIDAY_NAMES):
            return name
    except Exception:
        pass
    return FALLBACK_HOLIDAYS.get(target.year, {}).get(target.isoformat(), "")


def business_day_status(target: date) -> dict[str, Any]:
    weekday = target.weekday()
    if weekday >= 5:
        return {
            "date": target.isoformat(),
            "business_day": False,
            "reason": "weekend",
            "holiday_name": "토요일" if weekday == 5 else "일요일",
        }
    name = holiday_name(target)
    if name:
        return {
            "date": target.isoformat(),
            "business_day": False,
            "reason": "holiday",
            "holiday_name": name,
        }
    return {
        "date": target.isoformat(),
        "business_day": True,
        "reason": "business_day",
        "holiday_name": "",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether a KST date is a Korean business day.")
    parser.add_argument("--date", default="", help="Date to check in YYYY-MM-DD. Defaults to today in KST.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--output", default="", help="Optional UTF-8 JSON output path.")
    args = parser.parse_args()

    status = business_day_status(parse_target_date(args.date))
    if args.output:
        from pathlib import Path

        Path(args.output).write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    if args.json:
        print(json.dumps(status, ensure_ascii=False))
    else:
        print(
            f"{status['date']} "
            f"{'business day' if status['business_day'] else 'non-business day'} "
            f"{status['reason']} {status['holiday_name']}".strip()
        )
    return 0 if status["business_day"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
