from __future__ import annotations

import argparse
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


ROOT_DIR = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9), "KST")

DEFAULT_QUERIES = [
    "병무청 병역기피",
    "병무청 병역법 위반",
    "병무청 허위진단서",
    "병무청 연예인 병역",
    "병무청 사회복무요원 근무태만",
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
        try:
            dt = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_urls": {}, "last_checked_at": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"seen_urls": {}, "last_checked_at": ""}
    if not isinstance(data.get("seen_urls"), dict):
        data["seen_urls"] = {}
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "naver-news"


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
        return True
    try:
        published = datetime.fromisoformat(item.published_at).astimezone(KST)
    except ValueError:
        return True
    return published >= now - timedelta(hours=lookback_hours)


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


def heuristic_alert_summary(item: NewsItem, category: str, matched_terms: list[str]) -> str:
    source = item.source or "네이버 뉴스"
    snippet = trim_to_natural_sentence(item.summary or item.title, 135)
    if snippet and snippet != item.title:
        return f"{source}에서 '{item.title}' 관련 보도를 냈습니다. 기사에서는 {snippet} 등의 내용을 다루고 있습니다."
    terms = compact_terms(matched_terms, 3)
    if terms:
        return f"{source}에서 '{item.title}' 보도를 냈습니다. {terms} 표현이 함께 확인돼 {category}로 분류했습니다."
    return f"{source}에서 '{item.title}' 보도를 냈습니다. 병역 관련 부정 이슈로 번질 가능성이 있어 확인 대상으로 분류했습니다."


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


def build_alert_message(item: NewsItem, classification: Classification) -> str:
    url = item.url or item.naver_url
    lead = "병역 관련 부정 이슈로 번질 수 있는 보도가 확인됐습니다."
    if classification.severity == "높음":
        lead = "병역 관련 여론 이슈로 확산될 수 있는 보도가 확인됐습니다."
    elif classification.severity == "낮음":
        lead = "병역 관련 모니터링 후보 보도가 확인됐습니다."
    return "\n".join(
        [
            "🚨 병무청 관련 이슈 알림",
            "",
            lead,
            "",
            f"위험도: {classification.severity}",
            f"유형: {classification.category}",
            f"발행: {format_published_label(item)}",
            "",
            "📰 기사 제목",
            item.title,
            "",
            "핵심 내용",
            classification.summary,
            "",
            "확인 포인트",
            classification.reason,
            "",
            "원문",
            url,
        ]
    )


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Naver news for negative MMA-related issues.")
    parser.add_argument("--room", default=os.getenv("TARGET_CHATROOM", "test"), help="KakaoTalk room title")
    parser.add_argument("--state", default=os.getenv("NEGATIVE_WATCH_STATE", ".scheduler/negative-news-seen.json"))
    parser.add_argument("--output-dir", default=os.getenv("NEGATIVE_WATCH_OUTPUT_DIR", "runs/negative-watch"))
    parser.add_argument("--queries", default=os.getenv("NEGATIVE_WATCH_QUERIES", ""))
    parser.add_argument("--display", type=int, default=int(os.getenv("NEGATIVE_WATCH_DISPLAY", "50")))
    parser.add_argument("--pages", type=int, default=int(os.getenv("NEGATIVE_WATCH_PAGES", "2")))
    parser.add_argument("--lookback-hours", type=int, default=int(os.getenv("NEGATIVE_WATCH_LOOKBACK_HOURS", "168")))
    parser.add_argument("--max-alerts", type=int, default=int(os.getenv("NEGATIVE_WATCH_MAX_ALERTS", "1")))
    parser.add_argument("--dry-run", action="store_true", help="Do not send KakaoTalk messages or update state")
    parser.add_argument("--verify", action="store_true", help="Verify posted KakaoTalk alert")
    parser.add_argument("--mcp-command", default=os.getenv("KAKAOTALK_MCP_COMMAND", ""))
    parser.add_argument("--codex-command", default=os.getenv("CODEX_COMMAND", "codex.cmd"))
    parser.add_argument("--codex-model", default=os.getenv("CODEX_MODEL", ""))
    parser.add_argument("--codex-timeout-seconds", type=float, default=float(os.getenv("CODEX_TIMEOUT_SECONDS", "120")))
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
    state = load_state(state_path)
    seen_urls: dict[str, str] = state.get("seen_urls", {})

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
        if (item.url or item.naver_url or item.title) not in seen_urls
    ]

    heuristic_pairs: list[tuple[NewsItem, Classification]] = []
    inspected_keys: list[str] = []
    for item in new_items:
        classification = classify_heuristic(item)
        if classification.score <= 0:
            continue
        inspected_keys.append(item.url or item.naver_url or item.title)
        heuristic_pairs.append((item, classification))

    heuristic_pairs.sort(key=lambda pair: (pair[1].score, pair[0].published_at), reverse=True)

    classified: list[tuple[NewsItem, Classification]] = []
    for index, (item, classification) in enumerate(heuristic_pairs):
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
            classified.append((item, classification))

    classified.sort(key=lambda pair: (pair[1].score, pair[0].published_at), reverse=True)
    alerts = classified[: max(0, args.max_alerts)]
    messages = [build_alert_message(item, classification) for item, classification in alerts]

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
                "alert_count": len(alerts),
                "alerts": [
                    {"article": asdict(item), "classification": asdict(classification)}
                    for item, classification in alerts
                ],
                "sample_candidates": [asdict(item) for item in new_items[:20]],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    posted = 0
    if not args.dry_run:
        for message in messages:
            post_to_kakao(message, room=args.room, mcp_command=args.mcp_command, verify=args.verify)
            posted += 1

        for key in inspected_keys:
            seen_urls[key] = now.isoformat()
        for item, _classification in alerts:
            seen_urls[item.url or item.naver_url or item.title] = now.isoformat()
        state["seen_urls"] = dict(list(seen_urls.items())[-2000:])
        state["last_checked_at"] = now.isoformat()
        save_state(state_path, state)

        alerts_log = output_dir / f"alerts-{now.strftime('%Y-%m-%d')}.jsonl"
        with alerts_log.open("a", encoding="utf-8") as log_file:
            for item, classification in alerts:
                log_file.write(
                    json.dumps(
                        {
                            "posted_at": now.isoformat(),
                            "room": args.room,
                            "article": asdict(item),
                            "classification": asdict(classification),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    print(
        json.dumps(
            {
                "room": args.room,
                "dry_run": args.dry_run,
                "fetched_count": len(fetched),
                "deduped_recent_count": len(items),
                "new_count": len(new_items),
                "candidate_path": str(candidates_path),
                "alert_count": len(alerts),
                "posted": posted,
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
