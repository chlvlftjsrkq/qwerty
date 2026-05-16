from __future__ import annotations

import json
from datetime import date

from .config import Config
from .news import Article, normalize_space

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def article_payload(articles: list[Article]) -> list[dict[str, str]]:
    payload = []
    for idx, article in enumerate(articles, start=1):
        published = (
            article.published_at.astimezone().isoformat(timespec="minutes")
            if article.published_at
            else ""
        )
        payload.append(
            {
                "no": str(idx),
                "title": article.title,
                "source": article.source,
                "published_at": published,
                "summary": article.summary[:700],
                "content": article.content[:1500],
                "url": article.url,
            }
        )
    return payload


def summarize_with_openai(config: Config, target_date: date, articles: list[Article]) -> str:
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY가 없습니다.")
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests가 설치되어 있지 않습니다. requirements.txt를 설치하세요.") from exc

    system_prompt = (
        "너는 한국 병무/국방 행정 뉴스를 매일 카카오톡 단톡방에 공유하는 편집자다. "
        "제공된 기사 목록에 있는 사실만 사용하고 추측하지 않는다. "
        "병무청과 직접 관련성이 낮은 기사는 제외하거나 한 줄로만 언급한다. "
        "문장은 간결한 한국어로 작성한다. 광고성 표현과 과장은 금지한다. "
        "Opinion은 기사 사실을 넘어 단정하지 말고, 병무행정 관점의 확인 포인트로만 쓴다."
    )
    user_prompt = {
        "target_date": target_date.isoformat(),
        "format": (
            "🪖 YYYY-MM-DD 병무청 뉴스 브리핑\n"
            "전날 네이버 뉴스 기준으로 확인한 병무청 관련 주요 소식을 정리했어요. "
            "개별 신청·접수 조건은 원문과 병무청 공식 안내를 함께 확인해 주세요.\n\n"
            "---\n\n"
            "오늘의 병무청 뉴스 톡 📡\n"
            "# 1️⃣ 기사 제목\n"
            "기사 요약 2~3문장. 🎯\n"
            "Opinion: 병무행정 관점의 의미나 확인 포인트 1~2문장.\n"
            "Source: 매체 / 링크\n\n"
            "# 2️⃣ 기사 제목\n"
            "...\n\n"
            "---\n\n"
            "오늘 한 줄 요약 🎯\n"
            "전체 흐름을 한 문장으로 요약.\n\n"
            "---\n\n"
            "💡 병역·입영·복무 관련 일정은 개인별 조건에 따라 달라질 수 있어요. "
            "실제 신청 전 공식 안내를 한 번 더 확인해 주세요."
        ),
        "articles": article_payload(articles),
    }
    response = requests.post(
        "https://api.openai.com/v1/responses",
        headers={
            "Authorization": f"Bearer {config.openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.openai_model,
            "input": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_prompt, ensure_ascii=False),
                },
            ],
            "temperature": 0.2,
            "max_output_tokens": 1400,
        },
        timeout=config.request_timeout_seconds,
    )
    response.raise_for_status()
    data = response.json()
    output_text = data.get("output_text")
    if output_text:
        return output_text.strip()

    chunks: list[str] = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                chunks.append(content["text"])
    if not chunks:
        raise RuntimeError("OpenAI 응답에서 텍스트를 찾지 못했습니다.")
    return "\n".join(chunks).strip()


def _trim_sentence(value: str, limit: int = 180) -> str:
    text = normalize_space(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _article_opinion(article: Article) -> str:
    haystack = f"{article.title} {article.summary}".lower()
    if "전문연구요원" in haystack or "산업기능요원" in haystack or "병역특례" in haystack:
        return "병역특례 지정·추천 소식은 기업과 지원자 모두에게 영향을 줄 수 있어요. 제도 요건과 실제 신청 가능 여부는 별도 확인이 필요합니다."
    if "사회복무요원" in haystack:
        return "사회복무요원 관련 소식은 복무기관 운영과 개인 복무 경험에 직접 닿아 있어요. 모집·배치·우수사례처럼 성격이 다른 기사들은 구분해서 보는 게 좋습니다."
    if "입영" in haystack or "병역판정검사" in haystack:
        return "입영과 병역판정검사는 대상자별 일정 차이가 커요. 기사 요약만 보고 판단하기보다 병무청 공식 안내에서 본인 조건을 다시 확인해야 합니다."
    if "예비역" in haystack or "예비군" in haystack:
        return "예비역·예비군 관련 안내는 소속과 신분에 따라 적용 범위가 달라질 수 있어요. 실제 제출·신청 기준은 원문 공고를 확인하는 게 안전합니다."
    if "공공데이터" in haystack or ("ai" in haystack and ("질병관리청" in haystack or "방위사업청" in haystack)):
        return "병무행정 데이터가 다른 공공 영역과 연결되는 흐름을 보여주는 소식이에요. 참여 조건과 활용 데이터 범위는 원문에서 확인하는 편이 좋습니다."
    if "경진대회" in haystack:
        return "기관 내부 업무 품질을 높이려는 현장형 소식이에요. 실제 참여 대상과 일정은 해당 지방병무청 안내를 확인하는 편이 좋습니다."
    return "병무청 관련 행정·제도 흐름을 확인할 수 있는 기사예요. 기사에 없는 세부 조건은 추정하지 말고 원문과 공식 안내를 함께 보는 게 좋습니다."


def _one_line_summary(articles: list[Article]) -> str:
    titles = " ".join(article.title for article in articles[:5])
    if "경진대회" in titles or "공공데이터" in titles:
        return "공공데이터·AI 활용, 복무 인력 관리, 지방병무청 현장 소식이 함께 포착된 하루였습니다."
    if "사회복무요원" in titles:
        return "사회복무요원 운영과 지방병무청 현장 안내가 병무청 뉴스의 중심이었습니다."
    return "병무청 관련 제도·행사·현장 안내가 네이버 뉴스 기준으로 이어진 하루였습니다."


def summarize_heuristic(target_date: date, articles: list[Article]) -> str:
    header = f"🪖 {target_date.isoformat()} 병무청 뉴스 브리핑"
    if not articles:
        return (
            f"{header}\n"
            "전날 네이버 뉴스 기준으로 확인한 병무청 관련 주요 소식이 많지 않았어요. "
            "급한 신청·접수 일정은 병무청 공식 안내를 한 번 더 확인해 주세요.\n\n"
            "---\n\n"
            "오늘의 병무청 뉴스 톡 📡\n"
            "확인된 주요 뉴스가 없습니다.\n\n"
            "---\n\n"
            "오늘 한 줄 요약 🎯\n"
            "오늘은 공유할 만한 병무청 직접 관련 뉴스가 확인되지 않았습니다.\n\n"
            "---\n\n"
            "💡 필요하면 검색어를 넓히거나 Google News·정책브리핑 RSS 보조 출처를 켜서 다시 확인할 수 있어요."
        )

    top = articles[:8]
    lines = [
        header,
        f"전날 네이버 뉴스 기준으로 확인한 병무청 관련 주요 소식 {len(articles)}건을 정리했어요. 신청·접수 조건은 원문과 병무청 공식 안내를 함께 확인해 주세요.",
        "",
        "---",
        "",
        "오늘의 병무청 뉴스 톡 📡",
    ]

    for idx, article in enumerate(top, start=1):
        number = NUMBER_EMOJI[idx - 1] if idx <= len(NUMBER_EMOJI) else f"{idx}."
        summary = _trim_sentence(article.summary or article.title)
        lines.extend(
            [
                f"# {number} {article.title}",
                f"{summary} 🎯",
                f"Opinion: {_article_opinion(article)}",
                f"Source: {article.source or '네이버 뉴스'} / {article.url}",
                "",
            ]
        )

    if len(articles) > len(top):
        lines.append(f"그 외 관련 기사 {len(articles) - len(top)}건은 중복·관련도 기준으로 제외했습니다.")
        lines.append("")

    lines.extend(
        [
            "---",
            "",
            "오늘 한 줄 요약 🎯",
            _one_line_summary(articles),
            "",
            "---",
            "",
            "💡 병역·입영·복무 관련 일정은 개인별 조건에 따라 달라질 수 있어요. 실제 신청 전 공식 안내를 한 번 더 확인해 주세요.",
        ]
    )
    return "\n".join(lines)


def build_summary(config: Config, target_date: date, articles: list[Article]) -> str:
    if config.openai_api_key:
        try:
            return summarize_with_openai(config, target_date, articles)
        except Exception as exc:
            fallback = summarize_heuristic(target_date, articles)
            return f"{fallback}\n\n요약 모델 호출 실패: {exc}"
    return summarize_heuristic(target_date, articles)
