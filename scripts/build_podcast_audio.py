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
    return parser.parse_args()


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def clean_for_speech(line: str) -> str:
    line = re.sub(r"https?://\S+", "", line)
    line = line.replace("🎯", "")
    line = EMOJI_PATTERN.sub("", line)
    line = line.replace("·", ", ")
    line = line.replace("AI", "에이아이")
    line = line.replace("MCP", "엠씨피")
    line = line.replace("URL", "유알엘")
    return normalize_space(line)


def markdown_to_speech(summary: str, target_date: str) -> str:
    lines: list[str] = []
    title_seen = False

    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        if line.startswith("Source:"):
            continue
        if line.startswith("요약 모델 호출 실패:"):
            continue

        heading_match = re.match(r"^#\s*(🔟|[0-9]️⃣|[0-9]+)\s*(.+)$", line)
        if heading_match:
            raw_number = heading_match.group(1)
            digit = "10" if raw_number == "🔟" else re.sub(r"\D", "", raw_number) or "1"
            title = clean_for_speech(heading_match.group(2))
            lines.append(f"{NUMBER_WORDS.get(digit, digit + '번째')} 소식입니다. {title}.")
            continue

        if line.startswith("#"):
            line = line.lstrip("#").strip()

        if line.startswith("Opinion:"):
            line = "확인 포인트:" + line[len("Opinion:") :]

        cleaned = clean_for_speech(line)
        if not cleaned:
            continue
        if cleaned.startswith(target_date) and "병무청 뉴스 브리핑" in cleaned:
            title_seen = True
        lines.append(cleaned)

    intro = f"{target_date} 병무청 뉴스 음성 브리핑입니다."
    if title_seen:
        return "\n".join([intro, *lines])
    return "\n".join([intro, *lines])


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
    speech_text = markdown_to_speech(summary, args.date)
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
