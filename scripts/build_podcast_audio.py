from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


NUMBER_WORDS = {
    "1": "첫 번째",
    "2": "두 번째",
    "3": "세 번째",
    "4": "네 번째",
    "5": "다섯 번째",
    "6": "여섯 번째",
    "7": "일곱 번째",
    "8": "여덟 번째",
    "9": "아홉 번째",
    "10": "열 번째",
}

EMOJI_PATTERN = re.compile(
    "["
    "\U0001f300-\U0001f5ff"
    "\U0001f600-\U0001f64f"
    "\U0001f680-\U0001f6ff"
    "\U0001f700-\U0001f77f"
    "\U0001f780-\U0001f7ff"
    "\U0001f800-\U0001f8ff"
    "\U0001f900-\U0001f9ff"
    "\U0001fa00-\U0001fa6f"
    "\U0001fa70-\U0001faff"
    "\u2600-\u27bf"
    "\ufe0f\u20e3"
    "]+"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a podcast MP3 from a daily summary Markdown file.")
    parser.add_argument("--date", required=True, help="Episode date in YYYY-MM-DD.")
    parser.add_argument("--summary", default="", help="Summary Markdown path. Defaults to runs/summary-YYYY-MM-DD.md.")
    parser.add_argument("--podcast-dir", default="podcast", help="Podcast output directory.")
    parser.add_argument("--site-base-url", default=os.getenv("PODCAST_BASE_URL", ""), help="Public podcast page URL.")
    parser.add_argument("--provider", default=os.getenv("TTS_PROVIDER", "edge"), choices=["edge"], help="TTS provider.")
    parser.add_argument("--voice", default=os.getenv("TTS_VOICE", "ko-KR-SunHiNeural"), help="edge-tts voice.")
    parser.add_argument("--rate", default=os.getenv("TTS_RATE", "+0%"), help="edge-tts rate, e.g. +0%.")
    parser.add_argument("--pitch", default=os.getenv("TTS_PITCH", "+0Hz"), help="edge-tts pitch, e.g. +0Hz.")
    parser.add_argument(
        "--target-minutes",
        type=float,
        default=float(os.getenv("TTS_TARGET_MINUTES", "5")),
        help="Approximate maximum narration length. The script is compact and may be shorter when there are few articles.",
    )
    return parser.parse_args()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def remove_ellipsis(value: str) -> str:
    return normalize_space(re.sub(r"(\.{2,}|…+)", " ", value))


def clean_for_speech(line: str) -> str:
    line = re.sub(r"https?://\S+", "", line)
    line = remove_ellipsis(line)
    line = line.replace("🎯", "")
    line = EMOJI_PATTERN.sub("", line)
    line = line.replace("·", ", ")
    line = line.replace("AI", "에이아이")
    line = line.replace("MCP", "엠씨피")
    line = line.replace("URL", "유알엘")
    return normalize_space(line)


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    sentences = re.findall(r"[^.!?。]+[.!?。]?", text)
    return [normalize_space(sentence) for sentence in sentences if normalize_space(sentence)]


def trim_text(text: str, limit: int) -> str:
    text = remove_ellipsis(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(" .,")


def ensure_clear_ending(text: str) -> str:
    text = remove_ellipsis(text).rstrip(" ,")
    if not text:
        return ""
    if text[-1] in ".!?。":
        return text
    if re.search(r"(다|요|죠|니다|습니다|됩니다|합니다|했습니다)$", text):
        return text + "."
    return text + "입니다."


def ensure_title_ending(text: str) -> str:
    text = remove_ellipsis(text).rstrip(" ,")
    if not text:
        return ""
    if text[-1] in ".!?。":
        return text
    return text + "."


def extract_weather(summary: str) -> str:
    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if line.startswith("🌤️"):
            return ensure_clear_ending(clean_for_speech(line))
    return ""


def extract_articles(summary: str) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    current: dict[str, Any] | None = None

    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line or line == "---" or line.startswith("Source:"):
            continue
        if line.startswith("요약 모델 호출 실패:"):
            continue

        heading_match = re.match(r"^#\s*(🔟|[0-9]️⃣|[0-9]+)\s*(.+)$", line)
        if heading_match:
            if current:
                articles.append(
                    {
                        "number": current["number"],
                        "title": current["title"],
                        "body": " ".join(current["body"]),
                    }
                )
            raw_number = heading_match.group(1)
            digit = "10" if raw_number == "🔟" else re.sub(r"\D", "", raw_number) or "1"
            title = clean_for_speech(heading_match.group(2))
            current = {"number": digit, "title": title, "body": []}
            continue

        if not current:
            continue
        if line.startswith("Opinion:") or line.startswith("오늘 한 줄 요약"):
            continue
        if line.startswith("#"):
            continue

        cleaned = clean_for_speech(line)
        if cleaned:
            current["body"].append(cleaned)

    if current:
        articles.append(
            {
                "number": current["number"],
                "title": current["title"],
                "body": " ".join(current["body"]),
            }
        )
    return articles


def build_compact_lines(articles: list[dict[str, str]], max_chars: int) -> list[str]:
    lines: list[str] = []
    for article in articles:
        number = article["number"]
        title = trim_text(article["title"], 110)
        sentences = split_sentences(article["body"])
        body = " ".join(sentences[:2]) if sentences else article["body"]
        body = trim_text(body, 260)
        if not title or not body:
            continue
        candidate = (
            f"{NUMBER_WORDS.get(number, number + '번째')} 소식입니다. "
            f"{ensure_title_ending(title)} 주요 내용은 {ensure_clear_ending(body)}"
        )
        projected = len("\n".join([*lines, candidate]))
        if lines and projected > max_chars:
            break
        lines.append(candidate)
    return lines


def markdown_to_speech(summary: str, target_date: str, target_minutes: float = 5.0) -> str:
    weather = extract_weather(summary)
    articles = extract_articles(summary)
    max_chars = max(700, int(target_minutes * 900))
    intro = f"{target_date} 병무청 뉴스 음성 브리핑입니다."
    if not articles:
        lines = [intro]
        if weather:
            lines.append(weather)
        lines.append("네이버 뉴스 기준으로 공유할 만한 병무청 관련 주요 기사가 많지 않았습니다.")
        return "\n".join(lines)

    opening = f"오늘은 주요 기사 {len(articles)}건을 제목과 핵심 내용 중심으로 전해드리겠습니다."
    lines = build_compact_lines(articles, max_chars=max_chars - len(intro) - len(opening) - 40)
    if len(lines) < len(articles):
        lines.append("나머지 기사는 중복이거나 관련성이 낮아 음성 요약에서는 줄였습니다.")
    closing = "자세한 신청 조건과 일정은 원문 기사와 병무청 공식 안내를 함께 확인하시기 바랍니다."
    output = [intro]
    if weather:
        output.append(weather)
    output.extend([opening, *lines, closing])
    return "\n".join(output)


async def synthesize_edge(text: str, output_path: Path, voice: str, rate: str, pitch: str) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError("edge-tts가 설치되어 있지 않습니다. requirements.txt를 설치하세요.") from exc

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    await communicate.save(str(output_path))


def read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"episodes": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"episodes": []}
    if not isinstance(data, dict) or not isinstance(data.get("episodes"), list):
        return {"episodes": []}
    return data


def update_manifest(path: Path, episode: dict[str, str]) -> None:
    data = read_manifest(path)
    episodes = [
        item
        for item in data.get("episodes", [])
        if isinstance(item, dict) and item.get("date") != episode["date"]
    ]
    episodes.append(episode)
    episodes.sort(key=lambda item: item.get("date", ""), reverse=True)
    data["episodes"] = episodes
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def page_url(base_url: str, target_date: str) -> str:
    if not base_url:
        return ""
    return base_url.rstrip("/") + f"/?date={target_date}"


async def build() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    datetime.strptime(args.date, "%Y-%m-%d")
    summary_path = Path(args.summary) if args.summary else Path("runs") / f"summary-{args.date}.md"
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary file not found: {summary_path}")

    podcast_dir = Path(args.podcast_dir)
    audio_dir = podcast_dir / "audio"
    script_dir = podcast_dir / "scripts"
    audio_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    summary = summary_path.read_text(encoding="utf-8")
    speech_text = markdown_to_speech(summary, args.date, args.target_minutes)
    script_path = script_dir / f"{args.date}.txt"
    script_path.write_text(speech_text, encoding="utf-8")

    audio_path = audio_dir / f"{args.date}.mp3"
    if args.provider == "edge":
        await synthesize_edge(speech_text, audio_path, args.voice, args.rate, args.pitch)

    manifest_path = podcast_dir / "manifest.json"
    episode = {
        "date": args.date,
        "title": f"{args.date} 병무청 뉴스 브리핑",
        "description": "네이버 뉴스 기준 병무청 관련 음성 요약",
        "audio": f"audio/{args.date}.mp3",
        "script": f"scripts/{args.date}.txt",
        "summary": f"../summaries/summary-{args.date}.md",
    }
    update_manifest(manifest_path, episode)

    result = {
        "date": args.date,
        "audio": str(audio_path),
        "script": str(script_path),
        "manifest": str(manifest_path),
        "page_url": page_url(args.site_base_url, args.date),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(build())


if __name__ == "__main__":
    raise SystemExit(main())
