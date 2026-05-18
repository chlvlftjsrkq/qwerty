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

LETTER_READINGS = {
    "A": "에이",
    "B": "비",
    "C": "씨",
    "D": "디",
    "E": "이",
    "F": "에프",
    "G": "지",
    "H": "에이치",
    "I": "아이",
    "J": "제이",
    "K": "케이",
    "L": "엘",
    "M": "엠",
    "N": "엔",
    "O": "오",
    "P": "피",
    "Q": "큐",
    "R": "알",
    "S": "에스",
    "T": "티",
    "U": "유",
    "V": "브이",
    "W": "더블유",
    "X": "엑스",
    "Y": "와이",
    "Z": "제트",
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
    parser.add_argument(
        "--episode-id",
        default="",
        help="Unique episode id used for audio/script filenames. Defaults to --date.",
    )
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
    parser.add_argument(
        "--include-weather",
        action="store_true",
        default=os.getenv("TTS_INCLUDE_WEATHER", "").strip().lower() in {"1", "true", "yes", "y", "on"},
        help="Include the weather line in the spoken podcast script. Disabled by default.",
    )
    return parser.parse_args()


def normalize_space(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    text = re.sub(r"\s+([,.;:!?。])", r"\1", text)
    text = re.sub(r"([(（])\s+", r"\1", text)
    return text


def remove_ellipsis(value: str) -> str:
    return normalize_space(re.sub(r"(\.{2,}|…+)", " ", value))


def clean_for_speech(line: str) -> str:
    line = re.sub(r"https?://\S+", "", line)
    line = remove_ellipsis(line)
    line = re.sub(r"\[([^\]]+)\]\s*", r"\1, ", line)
    line = line.replace("대구·경북지방병무청", "대구경북지방병무청")
    line = line.replace("🎯", "")
    line = EMOJI_PATTERN.sub("", line)
    line = line.replace("·", ", ")
    line = line.replace("&", " 앤 ")
    line = re.sub(r"[◆◇■□▪▫●○]", " ", line)
    return normalize_space(line)


def format_spoken_date(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    return f"{parsed.year} 년 {parsed.month} 월 {parsed.day} 일"


def _spell_letters(value: str) -> str:
    return " ".join(LETTER_READINGS.get(letter, letter) for letter in value)


def normalize_symbols_for_tts(text: str) -> str:
    text = re.sub(
        r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b",
        lambda match: f"{int(match.group(1))} 년 {int(match.group(2))} 월 {int(match.group(3))} 일",
        text,
    )
    text = re.sub(r"([A-Z]{2,})", lambda match: _spell_letters(match.group(1)), text)
    text = re.sub(r"(?<=\d)([A-Z])", lambda match: " " + _spell_letters(match.group(1)), text)
    text = re.sub(r"\bn\s*%", "엔 퍼센트", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)\s*%", r"\1 퍼센트", text)
    text = re.sub(r"(\d+)\s*명", r"\1 명", text)
    text = re.sub(r"(\d+)\s*건", r"\1 건", text)
    text = re.sub(r"(\d+)\s*개", r"\1 개", text)
    text = re.sub(r"(\d+)\s*만원", r"\1 만원", text)
    text = re.sub(r"(\d+)\s*회", r"\1 회", text)
    text = re.sub(r"(\d+)\s*차", r"\1 차", text)
    text = re.sub(r"(\d+)\s*개월", r"\1 개월", text)
    text = re.sub(r"(\d+)\s*세", r"\1 세", text)
    text = re.sub(r"(\d+)\s*시", r"\1 시", text)
    text = re.sub(r"(\d+)\s*분", r"\1 분", text)
    text = re.sub(r"(\d+)\s*초", r"\1 초", text)
    text = re.sub(r"(\d+)\s*기", r"\1 기", text)
    text = re.sub(r"(\d+)\s*년", r"\1 년", text)
    text = re.sub(r"(\d+)\s*월", r"\1 월", text)
    text = re.sub(r"(\d+)\s*일", r"\1 일", text)
    text = re.sub(r"(\d+)\s*도", r"\1 도", text)
    text = re.sub(r"(\d+\s*개월)서", r"\1에서", text)
    text = text.replace("허가기간", "허가 기간")
    text = text.replace("국외여행허가", "국외여행 허가")
    text = text.replace("안전수칙교육", "안전 수칙 교육")
    text = text.replace("병무청 장", "병무청장")
    text = text.replace("육, 해, 공군", "육군, 해군, 공군")
    text = text.replace("月", "월")
    text = re.sub(r"제\s+(\d+)\s*노조", r"제\1 노조", text)
    text = re.sub(r"([가-힣]{1,12}지방)\s+병무청", r"\1병무청", text)
    text = re.sub(r"(?<=[가-힣])(\d)", r" \1", text)
    text = re.sub(r"제\s+(\d+)\s*노조", r"제\1 노조", text)
    return normalize_space(text)


def polish_korean_sentence(text: str) -> str:
    text = normalize_symbols_for_tts(remove_ellipsis(text).rstrip(" ,.!?。"))
    replacements = [
        (r"하였다$", "하였습니다"),
        (r"했다$", "했습니다"),
        (r"한다$", "합니다"),
        (r"됐다$", "됐습니다"),
        (r"되었다$", "되었습니다"),
        (r"이다$", "입니다"),
        (r"있다$", "있습니다"),
        (r"없다$", "없습니다"),
        (r"열었다$", "열었습니다"),
        (r"밝혔다$", "밝혔습니다"),
        (r"전했다$", "전했습니다"),
        (r"나왔다$", "나왔습니다"),
        (r"시작된다$", "시작됩니다"),
        (r"진행된다$", "진행됩니다"),
        (r"운영된다$", "운영됩니다"),
        (r"추진된다$", "추진됩니다"),
        (r"확대된다$", "확대됩니다"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    if not text:
        return ""
    if text.endswith("위해"):
        return text + " 마련된 내용입니다."
    if text.endswith("위한"):
        return text + " 내용입니다."
    if re.search(r"(다|요|죠|니다|습니다|됩니다|합니다|했습니다)$", text):
        return text + "."
    return text + "입니다."


def polish_korean_text(text: str) -> str:
    sentences = split_sentences(text)
    if not sentences:
        return polish_korean_sentence(text)
    return " ".join(
        sentence
        for sentence in (polish_korean_sentence(item) for item in sentences)
        if sentence
    )


def split_sentences(text: str) -> list[str]:
    text = normalize_space(text)
    if not text:
        return []
    sentences = re.findall(r"[^.!?。]+[.!?。]?", text)
    return [normalize_space(sentence) for sentence in sentences if normalize_space(sentence)]


def trim_text(text: str, limit: int) -> str:
    text = normalize_symbols_for_tts(remove_ellipsis(text))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(" .,")


def ensure_clear_ending(text: str) -> str:
    text = normalize_symbols_for_tts(remove_ellipsis(text).rstrip(" ,"))
    if not text:
        return ""
    if text[-1] in ".!?。":
        return text
    if re.search(r"(다|요|죠|니다|습니다|됩니다|합니다|했습니다)$", text):
        return text + "."
    return text + "입니다."


def ensure_title_ending(text: str) -> str:
    text = normalize_symbols_for_tts(remove_ellipsis(text).rstrip(" ,"))
    if not text:
        return ""
    if text[-1] in ".!?。":
        return text
    return text + "."


def spoken_title(title: str) -> str:
    title = normalize_symbols_for_tts(remove_ellipsis(title).rstrip(" ,.!?。"))
    title = re.sub(r"\((\d+)\)", r"\1 번째", title)
    title = re.sub(r"[\[\]\"'“”‘’]", "", title)
    if not title:
        return ""
    return ensure_title_ending(title)


def extract_weather(summary: str) -> str:
    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if line.startswith("🌤️"):
            return ensure_clear_ending(clean_for_speech(line))
    return ""


def extract_agency_name(summary: str) -> str:
    for raw_line in summary.splitlines():
        line = raw_line.strip()
        match = re.match(r"^🪖\s+\d{4}-\d{2}-\d{2}\s+(.+?)\s+뉴스\s+브리핑$", line)
        if match:
            return clean_for_speech(match.group(1)) or "기관"
    return "기관"


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

        if line.startswith("오늘 한 줄 요약"):
            break
        if not current:
            continue
        if line.startswith("Opinion:"):
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
            f"{spoken_title(title)} {polish_korean_text(body)}"
        )
        projected = len("\n".join([*lines, candidate]))
        if lines and projected > max_chars:
            break
        lines.append(candidate)
    return lines


def markdown_to_speech(
    summary: str,
    target_date: str,
    target_minutes: float = 5.0,
    include_weather: bool = False,
) -> str:
    weather = extract_weather(summary) if include_weather else ""
    agency_name = extract_agency_name(summary)
    articles = extract_articles(summary)
    max_chars = max(700, int(target_minutes * 900))
    intro = f"{format_spoken_date(target_date)} {agency_name} 뉴스 음성 브리핑입니다."
    if not articles:
        lines = [intro]
        if weather:
            lines.append(weather)
        lines.append(f"네이버 뉴스 기준으로 공유할 만한 {agency_name} 관련 주요 기사가 확인되지 않았습니다.")
        return "\n".join(lines)

    opening = f"오늘은 주요 기사 {len(articles)} 건을 제목과 핵심 내용 중심으로 전해드리겠습니다."
    lines = build_compact_lines(articles, max_chars=max_chars - len(intro) - len(opening) - 40)
    if len(lines) < len(articles):
        lines.append("나머지 기사는 중복이거나 관련성이 낮아 음성 요약에서는 줄였습니다.")
    closing = f"자세한 내용과 개인별 적용 조건은 원문 기사와 {agency_name} 공식 안내를 함께 확인하시기 바랍니다."
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


def page_url(base_url: str, episode_id: str) -> str:
    if not base_url:
        return ""
    return base_url.rstrip("/") + f"/?date={episode_id}"


async def build() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args = parse_args()
    datetime.strptime(args.date, "%Y-%m-%d")
    episode_id = args.episode_id.strip() or args.date
    summary_path = Path(args.summary) if args.summary else Path("runs") / f"summary-{args.date}.md"
    if not summary_path.exists():
        raise FileNotFoundError(f"Summary file not found: {summary_path}")

    podcast_dir = Path(args.podcast_dir)
    audio_dir = podcast_dir / "audio"
    script_dir = podcast_dir / "scripts"
    audio_dir.mkdir(parents=True, exist_ok=True)
    script_dir.mkdir(parents=True, exist_ok=True)

    summary = summary_path.read_text(encoding="utf-8")
    agency_name = extract_agency_name(summary)
    speech_text = markdown_to_speech(summary, args.date, args.target_minutes, args.include_weather)
    script_path = script_dir / f"{episode_id}.txt"
    script_path.write_text(speech_text, encoding="utf-8")

    audio_path = audio_dir / f"{episode_id}.mp3"
    if args.provider == "edge":
        await synthesize_edge(speech_text, audio_path, args.voice, args.rate, args.pitch)

    manifest_path = podcast_dir / "manifest.json"
    episode = {
        "date": episode_id,
        "title": f"{args.date} {agency_name} 뉴스 브리핑",
        "description": f"네이버 뉴스 기준 {agency_name} 관련 음성 요약",
        "audio": f"audio/{episode_id}.mp3",
        "script": f"scripts/{episode_id}.txt",
        "summary": f"../summaries/summary-{episode_id}.md",
    }
    update_manifest(manifest_path, episode)

    result = {
        "date": args.date,
        "episode_id": episode_id,
        "audio": str(audio_path),
        "script": str(script_path),
        "manifest": str(manifest_path),
        "page_url": page_url(args.site_base_url, episode_id),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(build())


if __name__ == "__main__":
    raise SystemExit(main())
