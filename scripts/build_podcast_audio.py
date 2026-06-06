from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
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

ARTICLE_INTRO_TEMPLATES = [
    "{ordinal} 소식입니다.",
    "다음 소식입니다.",
    "이어서 전해드립니다.",
    "{ordinal}로 살펴볼 소식입니다.",
    "다음으로 전해드릴 내용입니다.",
    "{ordinal} 소식도 확인됐습니다.",
    "또 다른 소식입니다.",
    "{ordinal} 주요 내용입니다.",
    "아울러 전해드립니다.",
    "마지막 소식입니다.",
]

DETAIL_FALLBACK_SENTENCES = [
    "세부 대상과 적용 기준은 원문과 공식 안내에서 함께 확인할 필요가 있습니다.",
    "후속 조치와 현장 적용 방식도 함께 살펴볼 부분입니다.",
    "관련 일정과 대상 범위는 실제 안내 기준으로 다시 확인해야 합니다.",
    "정책 현장에 미칠 영향도 함께 지켜볼 사안입니다.",
    "이용 대상과 절차는 원문 기준으로 확인하는 것이 좋습니다.",
]

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
    parser = argparse.ArgumentParser(description="Build a podcast MP3 from an agency news briefing Markdown file.")
    parser.add_argument("--date", required=True, help="Episode date label in YYYY-MM-DD or YYYY-MM-DD~YYYY-MM-DD.")
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
    parser.add_argument(
        "--script-provider",
        default=os.getenv("PODCAST_SCRIPT_PROVIDER", "codex").strip().lower() or "codex",
        choices=["codex", "heuristic", "auto"],
        help="Podcast narration script generator. codex uses a logged-in Codex CLI and falls back to heuristic on failure.",
    )
    parser.add_argument("--codex-command", default=os.getenv("CODEX_COMMAND", "codex.cmd"), help="Codex CLI command.")
    parser.add_argument("--codex-model", default=os.getenv("CODEX_MODEL", ""), help="Optional Codex CLI model.")
    parser.add_argument(
        "--script-timeout-seconds",
        type=float,
        default=float(os.getenv("PODCAST_SCRIPT_TIMEOUT_SECONDS", os.getenv("CODEX_TIMEOUT_SECONDS", "300"))),
        help="Timeout for LLM podcast script generation.",
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
    for separator in ("~", "-to-"):
        if separator in value:
            start, end = value.split(separator, 1)
            start_spoken = format_spoken_date(start.strip())
            end_spoken = format_spoken_date(end.strip())
            return f"{start_spoken}부터 {end_spoken}까지"
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return value
    return f"{parsed.year} 년 {parsed.month} 월 {parsed.day} 일"


def validate_date_label(value: str) -> None:
    parts = [value]
    for separator in ("~", "-to-"):
        if separator in value:
            parts = [part.strip() for part in value.split(separator, 1)]
            break
    for part in parts:
        datetime.strptime(part, "%Y-%m-%d")


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
    text = re.sub(r"(?<=[가-힣])\s+하고\s+", "하고 ", text)
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


def clean_title_for_context(title: str) -> str:
    title = normalize_symbols_for_tts(remove_ellipsis(title).rstrip(" ,.!?。"))
    title = re.sub(r"\((\d+)\)", r"\1 번째", title)
    title = re.sub(r"[\[\]\"'“”‘’]", "", title)
    title = title.replace("지차제", "지자체")
    title = title.replace("엠 오 유", "업무협약")
    title = re.sub(r"\s+(과|와|은|는|이|가|을|를|에|로)\b", r"\1", title)
    return normalize_space(title)


def _has_final_consonant(text: str) -> bool:
    for char in reversed(text.strip()):
        if "가" <= char <= "힣":
            return (ord(char) - ord("가")) % 28 != 0
    return True


def _particle(text: str, with_final: str, without_final: str) -> str:
    return with_final if _has_final_consonant(text) else without_final


def _subject_with_particle(subject: str) -> str:
    return subject + _particle(subject, "이", "가")


def _object_with_particle(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if value.endswith(("을", "를")):
        return value
    return value + _particle(value, "을", "를")


def _topic_with_particle(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    if value.endswith(("은", "는")):
        return value
    return value + _particle(value, "은", "는")


def _polish_title_subject(subject: str) -> str:
    subject = normalize_space(subject)
    subject = subject.replace("병역기피 ", "병역기피 논란의 ")
    subject = re.sub(r"([가-힣]{1,12})\s+병무청$", r"\1병무청", subject)
    if re.search(r"(유승준|송민호|MC몽)$", subject):
        subject += " 씨"
    return subject


BODY_CONTEXT_TERMS = (
    "예비군 훈련 중 사망 사건",
    "예비군 행정업무",
    "공중보건의",
    "공보의",
    "사회복무요원",
    "현역병",
    "병역면탈",
    "병역기피",
    "병역 기피",
    "입영",
    "동원훈련",
    "병역명문가",
    "나라사랑통장",
    "복무관리",
    "현충원",
)


def _lead_sentence_from_body(body: str) -> str:
    sentences = [sentence for sentence in split_sentences(body) if sentence]
    if not sentences:
        return ""
    return normalize_space(normalize_symbols_for_tts(clean_for_speech(sentences[0]))).rstrip(" .!?。")


def _sentence_ngrams(text: str, size: int = 3) -> set[str]:
    compact = re.sub(r"[^0-9A-Za-z가-힣]", "", normalize_symbols_for_tts(clean_for_speech(text)))
    if len(compact) < size:
        return {compact} if compact else set()
    return {compact[index : index + size] for index in range(len(compact) - size + 1)}


def _sentences_too_similar(left: str, right: str) -> bool:
    left_grams = _sentence_ngrams(left)
    right_grams = _sentence_ngrams(right)
    if not left_grams or not right_grams:
        return False
    overlap = len(left_grams & right_grams) / max(1, min(len(left_grams), len(right_grams)))
    shared_terms = sum(
        1
        for term in BODY_CONTEXT_TERMS
        if term in left and term in right
    )
    shared_verbs = sum(
        1
        for term in ("전담", "지시", "체결", "접수", "참배", "해명", "할인", "모집", "운영")
        if term in left and term in right
    )
    return overlap >= 0.50 or (shared_terms >= 1 and shared_verbs >= 1)


def _contextual_title_from_body(title: str, body: str) -> str:
    lead = _lead_sentence_from_body(body)
    if len(lead) < 18:
        return ""

    title_text = normalize_space(title)
    has_new_context = any(term in lead and term not in title_text for term in BODY_CONTEXT_TERMS)
    title_looks_vague = bool(re.search(r"[\"“”'‘’]|정부|출범|논란|총력전|메시지|시간", title_text))
    if not has_new_context and not title_looks_vague:
        return ""

    if title_looks_vague and not has_new_context:
        return polish_korean_text(f"{title_text}과 관련해 {lead}.")

    replacements = (
        ("지시했습니다", "지시했다는 내용입니다."),
        ("진행했습니다", "진행했다는 내용입니다."),
        ("예고했습니다", "예고했다는 내용입니다."),
        ("체결했습니다", "체결했다는 내용입니다."),
        ("운영했습니다", "운영했다는 내용입니다."),
        ("전했습니다", "전했다는 내용입니다."),
        ("검토했습니다", "검토했다는 내용입니다."),
        ("밝혔습니다", "밝혔다는 내용입니다."),
        ("했습니다", "했다는 내용입니다."),
        ("됐습니다", "됐다는 내용입니다."),
        ("되었습니다", "됐다는 내용입니다."),
        ("나왔습니다", "나왔다는 내용입니다."),
        ("확인됐습니다", "확인됐다는 내용입니다."),
        ("다뤄졌습니다", "다뤄졌다는 내용입니다."),
        ("받았습니다", "받았다는 내용입니다."),
        ("열었습니다", "열었다는 내용입니다."),
    )
    for suffix, replacement in replacements:
        if lead.endswith(suffix):
            return polish_korean_text(lead[: -len(suffix)] + replacement)

    if lead.endswith(("입니다", "습니다")):
        return polish_korean_text(lead + ".")
    if re.search(r"(다|요)$", lead):
        return polish_korean_text(lead + ".")
    return polish_korean_text(f"{lead} 내용입니다.")


def spoken_title_sentence(title: str, body: str = "") -> str:
    title = clean_title_for_context(title)
    if not title:
        return ""
    context = normalize_symbols_for_tts(clean_for_speech(body))
    haystack = f"{title} {context}"

    if "예비군 행정업무" in haystack and "전담" in haystack:
        return "병무청은 예비군 행정업무 전담을 통해 첨단강군과 자주국방 추진을 뒷받침하겠다고 밝혔습니다."

    if "예비군 훈련 중 사망 사건" in haystack and "진상 규명" in haystack:
        return "예비군 훈련 중 사망 사건은 신속한 진상 규명 지시로 이어졌습니다."

    if "한의사 대체 진료" in haystack or ("공보의" in haystack and "의료 공백" in haystack):
        return "의과 공보의가 부족한 일부 지역에서 한의사로 의료 공백을 대체하는 움직임이 나왔습니다."

    if "유승준" in haystack and ("한국 입국" in haystack or "한국행" in haystack):
        return "병역기피 논란의 유승준 씨가 한국 입국 문제에 대한 입장을 다시 밝혔다는 내용입니다."

    if "병역명문가" in haystack and "부영그룹" in haystack and "할인" in haystack:
        return "병역명문가에게 부영그룹 숙박시설 할인 혜택이 제공된다는 내용입니다."

    if "하나은행" in haystack and "나라사랑통장" in haystack and "연령 제한" in haystack:
        return "하나은행이 나라사랑통장 가입 연령 제한을 없애기로 했다는 내용입니다."

    if "사회복무요원" in haystack and "재학생입영원" in haystack and "추가 접수" in haystack:
        return "대전충남병무청이 사회복무요원 재학생입영원 추가 접수를 진행한다는 내용입니다."

    if "병역명문가" in haystack and ("의료지원 협약" in haystack or "업무협약" in haystack):
        return "병역명문가 의료지원과 예우 확대를 위한 업무협약이 체결됐다는 내용입니다."

    if "병역이행자 예우 협약" in haystack:
        return "지역 병역이행자 예우 확대를 위한 협약이 체결됐다는 내용입니다."

    if "대전현충원" in haystack or "국립대전현충원" in haystack:
        return "대전충남병무청이 현충일을 앞두고 국립대전현충원을 참배했다는 소식입니다."

    contextual_title = _contextual_title_from_body(title, context)
    if contextual_title:
        return contextual_title

    if "속도 낸다" in title:
        base = title.replace("속도 낸다", "").strip()
        base = base.replace(" 구축하고 ", " 구축과 ")
        return polish_korean_text(f"{base}에 속도를 내겠다는 내용입니다.")

    if "남은 4 년은 8 년처럼" in title:
        return polish_korean_text(f"{title}이라는 메시지가 나왔습니다.")

    explain_match = re.match(r"^([가-힣A-Za-z0-9]{2,12})\s+(.+)\s+해명$", title)
    if explain_match:
        name = explain_match.group(1)
        claim = explain_match.group(2).rstrip("라")
        return polish_korean_text(f"{name} 씨가 {claim}라고 해명했다는 내용입니다.")

    if "," in title:
        subject, rest = [part.strip() for part in title.split(",", 1)]
        subject = _polish_title_subject(subject)
        subject_phrase = _subject_with_particle(subject)

        if rest.endswith("없앤다"):
            target = rest[: -len("없앤다")].strip()
            return polish_korean_text(f"{subject_phrase} {_object_with_particle(target)} 없앤다는 내용입니다.")

        if "포기" in rest:
            rest = rest.replace("24 년만", "24 년 만")
            rest = rest.replace("한국행 포기", "한국행을 포기")
            rest = rest.replace(" 사과했지만 비난만 남아", "하고 사과했지만 비난만 남았다고 밝혔습니다")
            if rest.endswith("밝혔습니다"):
                return polish_korean_text(f"{subject_phrase} {rest}")
            return polish_korean_text(f"{subject_phrase} {_topic_with_particle(rest)} 입장을 밝혔다는 내용입니다.")

        if rest.endswith("체결"):
            target = rest[: -len("체결")].strip()
            return polish_korean_text(f"{subject_phrase} {_object_with_particle(target)} 체결했다는 내용입니다.")

        if rest.endswith("추가 접수"):
            return polish_korean_text(f"{subject_phrase} {_object_with_particle(rest)} 진행한다는 내용입니다.")

        if rest.endswith("참배"):
            target = rest[: -len("참배")].strip()
            if target:
                return polish_korean_text(f"{subject_phrase} {_object_with_particle(target)} 참배했다는 소식입니다.")
            return polish_korean_text(f"{subject_phrase} 참배했다는 소식입니다.")

        if "혜택" in rest:
            return polish_korean_text(f"{subject}와 관련해 {rest}이 제공된다는 내용입니다.")

    if title.endswith("낸다"):
        base = title[: -len("낸다")].strip()
        return polish_korean_text(f"{base}내겠다는 내용입니다.")

    if title.endswith(("접수", "모집", "안내")):
        return polish_korean_text(f"{_object_with_particle(title)} 진행한다는 내용입니다.")

    return polish_korean_text(f"{title} 내용이 전해졌습니다.")


def spoken_title(title: str, body: str = "") -> str:
    return spoken_title_sentence(title, body)


def article_intro_sentence(number: str, is_last: bool = False) -> str:
    try:
        index = max(0, int(number) - 1)
    except ValueError:
        index = 0
    template = ARTICLE_INTRO_TEMPLATES[-1] if is_last and index >= 2 else ARTICLE_INTRO_TEMPLATES[index % len(ARTICLE_INTRO_TEMPLATES)]
    ordinal = NUMBER_WORDS.get(number, f"{number}번째")
    return polish_korean_text(template.format(ordinal=ordinal))


def detail_fallback_sentence(title: str, body: str, number: str) -> str:
    haystack = f"{title} {body}"
    if "예비군" in haystack and ("행정" in haystack or "전담" in haystack):
        return "예비군 행정 체계 개편이 핵심입니다."
    if "사망" in haystack or "사고" in haystack:
        return "훈련 안전관리와 진상 규명이 핵심 쟁점입니다."
    if "공보의" in haystack or "공중보건의" in haystack:
        return "지역 의료 공백 대응과 병역 자원 변화가 함께 걸린 사안입니다."
    if "유승준" in haystack or "병역기피" in haystack or "병역 기피" in haystack:
        return "병역기피 논란과 관련 제도 판단이 다시 다뤄졌습니다."
    if "할인" in haystack or "혜택" in haystack:
        return "혜택 대상과 이용 조건 확인이 중요한 내용입니다."
    if "연령 제한" in haystack or "나라사랑통장" in haystack:
        return "금융 혜택 이용 대상 확대가 핵심입니다."
    if "접수" in haystack or "모집" in haystack:
        return "신청 대상과 접수 일정 확인이 필요합니다."
    if "협약" in haystack or "업무협약" in haystack:
        return "병역이행자 예우 확대가 주요 내용입니다."
    if "참배" in haystack or "현충원" in haystack:
        return "호국보훈 메시지를 전한 일정입니다."
    try:
        index = max(0, int(number) - 1)
    except ValueError:
        index = 0
    return DETAIL_FALLBACK_SENTENCES[index % len(DETAIL_FALLBACK_SENTENCES)]


def article_body_for_speech(title: str, body: str, number: str, lead_sentence: str = "") -> str:
    sentences = [sentence for sentence in split_sentences(body) if sentence]
    generic_lead = "내용이 전해졌습니다" in lead_sentence
    if lead_sentence and sentences and not generic_lead and _sentences_too_similar(lead_sentence, sentences[0]):
        sentences = sentences[1:]
    selected = sentences[:3]
    while len(selected) < 2:
        fallback = detail_fallback_sentence(title, " ".join(selected) or body, number)
        normalized_existing = {normalize_space(item) for item in selected}
        if normalize_space(fallback) in normalized_existing:
            fallback = DETAIL_FALLBACK_SENTENCES[len(selected) % len(DETAIL_FALLBACK_SENTENCES)]
        selected.append(fallback)
    return " ".join(polish_korean_sentence(sentence) for sentence in selected[:3] if sentence)


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


def is_briefing_meta_line(line: str) -> bool:
    return (
        line.startswith("관련 보도:")
        or line.startswith("같은 이슈")
        or line.startswith("그 외 관련 기사")
        or line.startswith("요약이 길어")
    )


def extract_articles(summary: str) -> list[dict[str, str]]:
    articles: list[dict[str, str]] = []
    current: dict[str, Any] | None = None

    for raw_line in summary.splitlines():
        line = raw_line.strip()
        if not line or line == "---" or line.startswith("Source:"):
            continue
        if line.startswith("요약 모델 호출 실패:"):
            continue

        heading_match = re.match(r"^(?:#\s*)?(🔟|[0-9]️⃣|[0-9]+)\s*(.+)$", line)
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
        if is_briefing_meta_line(line):
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
    for index, article in enumerate(articles):
        number = article["number"]
        title = clean_title_for_context(trim_text(article["title"], 110))
        sentences = split_sentences(article["body"])
        raw_body = " ".join(sentences[:3]) if sentences else article["body"]
        title_context = spoken_title(title, raw_body)
        body = article_body_for_speech(title, raw_body, number, lead_sentence=title_context)
        body = trim_text(body, 420)
        if not title or not body:
            continue
        intro_context = article_intro_sentence(number, is_last=index == len(articles) - 1)
        body_context = polish_korean_text(body)
        candidate = f"{intro_context} {title_context} {body_context}"
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


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    match = re.fullmatch(r"```(?:text|markdown|md)?\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def _clean_llm_script(text: str) -> str:
    text = _strip_code_fence(text).replace("\r\n", "\n").replace("\r", "\n")
    lines = [normalize_space(line) for line in text.splitlines()]
    cleaned = "\n".join(line for line in lines if line)
    cleaned = remove_ellipsis(cleaned)
    cleaned = re.sub(r"(?m)^[-#]{2,}\s*$", "", cleaned)
    return normalize_space(cleaned).replace(". ", ".\n")


def _validate_llm_script(text: str, agency_name: str) -> None:
    if len(text) < 120:
        raise RuntimeError("LLM 음성 스크립트가 너무 짧습니다.")
    forbidden = ("제목은", "주요 내용입니다", "관련 보도입니다", "Source:", "http://", "https://")
    found = [item for item in forbidden if item in text]
    if found:
        raise RuntimeError(f"LLM 음성 스크립트에 금지 표현이 포함됐습니다: {', '.join(found)}")
    if agency_name and agency_name not in text[:120]:
        raise RuntimeError("LLM 음성 스크립트 도입부에서 기관명을 찾지 못했습니다.")


def podcast_script_payload(
    summary: str,
    target_date: str,
    target_minutes: float,
    include_weather: bool,
) -> dict[str, Any]:
    agency_name = extract_agency_name(summary)
    return {
        "target_date_label": target_date,
        "spoken_date": format_spoken_date(target_date),
        "agency_name": agency_name,
        "target_minutes": target_minutes,
        "weather": extract_weather(summary) if include_weather else "",
        "articles": [
            {
                "number": article["number"],
                "title": clean_title_for_context(trim_text(article["title"], 140)),
                "body": trim_text(article["body"], 700),
            }
            for article in extract_articles(summary)
        ],
    }


def podcast_script_with_codex(
    summary: str,
    target_date: str,
    target_minutes: float,
    include_weather: bool,
    output_dir: Path,
    codex_command: str,
    codex_model: str = "",
    timeout_seconds: float = 300,
) -> str:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = podcast_script_payload(summary, target_date, target_minutes, include_weather)
    if not payload["articles"]:
        return markdown_to_speech(summary, target_date, target_minutes, include_weather)

    input_file = tempfile.NamedTemporaryFile(
        prefix=f"podcast-script-input-{target_date.replace('~', '-to-')}-",
        suffix=".json",
        dir=output_dir,
        mode="w",
        encoding="utf-8",
        delete=False,
    )
    json.dump(payload, input_file, ensure_ascii=False)
    input_path = Path(input_file.name)
    input_file.close()

    output_file = tempfile.NamedTemporaryFile(
        prefix=f"podcast-script-output-{target_date.replace('~', '-to-')}-",
        suffix=".txt",
        dir=output_dir,
        delete=False,
    )
    output_path = Path(output_file.name)
    output_file.close()

    prompt = " ".join(
        [
            "Task: Write a natural Korean podcast narration script for TTS.",
            "Your final answer must be plain Korean text only. Do not write Markdown, JSON, bullet points, headings, or code fences.",
            f"Read the input JSON file at this path and use only facts from that file: {input_path.resolve()}",
            "The script is for a Korean announcer. Use polite formal Korean with clear sentence endings such as 합니다, 했습니다, 입니다, 전했습니다, 확인됐습니다.",
            "Do not use ellipses, Unicode ellipsis, repeated dots, source labels, URLs, or emoji.",
            "Do not say 제목은, 주요 내용입니다, 관련 보도입니다, or 같은 이슈를 묶었습니다.",
            "Open with the spoken date, agency name, and that this is a news audio briefing.",
            "Mention the article count once near the beginning.",
            "For each article, write a smooth short segment.",
            "Each segment must start with a varied transition such as 첫 번째 소식입니다, 다음 소식입니다, 이어서 전해드립니다, or 마지막 소식입니다.",
            "After the transition, write at least three connected sentences for that article.",
            "The first sentence must synthesize the article title and article body into one natural news lead. Do not merely read or restate the raw title.",
            "The second sentence must add a different detail, background, affected group, schedule, condition, or procedural point. It must not restate the first sentence with different word order.",
            "The third sentence must explain the significance, caution, or practical check point. It must not repeat the first or second sentence.",
            "When title and body contain the same fact, merge them into one sentence instead of saying it twice.",
            "Do not begin many sentences with 이번. Use natural flow instead of template-like repetition.",
            "Avoid repeating the same noun phrase in adjacent sentences unless it is unavoidable for clarity.",
            "Vary endings. Do not make every first sentence end with 내용입니다.",
            "Keep the total script close to the target_minutes value in the input, but prioritize natural flow over exact length.",
            "Close with a short reminder to check original articles and the official agency notice for details.",
        ]
    )
    command = [
        codex_command,
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

    env = os.environ.copy()
    env.setdefault("NO_COLOR", "1")
    try:
        result = subprocess.run(
            command,
            input="",
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            details = "\n".join(
                item.strip()
                for item in (result.stdout[-1200:], result.stderr[-1200:])
                if item.strip()
            )
            raise RuntimeError(f"Codex CLI 음성 스크립트 생성 실패(exit {result.returncode}): {details}")
        script = _clean_llm_script(output_path.read_text(encoding="utf-8"))
        _validate_llm_script(script, str(payload["agency_name"]))
        return script
    finally:
        for path in (input_path, output_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def build_podcast_speech(
    summary: str,
    target_date: str,
    target_minutes: float,
    include_weather: bool,
    script_provider: str,
    output_dir: Path,
    codex_command: str,
    codex_model: str = "",
    timeout_seconds: float = 300,
) -> str:
    provider = (script_provider or "heuristic").lower()
    if provider in {"codex", "auto"}:
        try:
            return podcast_script_with_codex(
                summary,
                target_date,
                target_minutes,
                include_weather,
                output_dir=output_dir,
                codex_command=codex_command,
                codex_model=codex_model,
                timeout_seconds=timeout_seconds,
            )
        except Exception as exc:
            print(f"Podcast script LLM fallback: {exc}", file=sys.stderr)
            if provider == "codex":
                print("Using heuristic podcast script so audio generation can continue.", file=sys.stderr)
    return markdown_to_speech(summary, target_date, target_minutes, include_weather)


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
    validate_date_label(args.date)
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
    speech_text = build_podcast_speech(
        summary,
        args.date,
        args.target_minutes,
        args.include_weather,
        script_provider=args.script_provider,
        output_dir=summary_path.parent,
        codex_command=args.codex_command,
        codex_model=args.codex_model,
        timeout_seconds=args.script_timeout_seconds,
    )
    script_path = script_dir / f"{episode_id}.txt"
    script_path.write_text(speech_text, encoding="utf-8")

    audio_path = audio_dir / f"{episode_id}.mp3"
    if audio_path.exists():
        audio_path.unlink()
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
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
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
