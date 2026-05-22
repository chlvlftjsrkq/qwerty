from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from .config import Config
from .news import Article, normalize_space
from .weather import build_weather_summary

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
CODEX_INPUT_ARTICLE_LIMIT = 24
SUMMARY_ITEM_LIMIT = 10
KAKAO_MESSAGE_SOFT_LIMIT = 2900


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


def codex_article_payload(articles: list[Article], limit: int = CODEX_INPUT_ARTICLE_LIMIT) -> list[dict[str, str]]:
    payload = []
    for idx, article in enumerate(articles[:limit], start=1):
        published = (
            article.published_at.astimezone().isoformat(timespec="minutes")
            if article.published_at
            else ""
        )
        payload.append(
            {
                "no": str(idx),
                "title": article.title,
                "source": article.source[:80],
                "published_at": published,
                "summary": article.summary[:600],
                "content": article.content[:500],
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

    agency_name = config.agency_name
    policy_perspective = "병무행정" if agency_name == "병무청" else f"{agency_name} 정책"
    system_prompt = (
        f"너는 한국 {agency_name} 관련 뉴스를 매일 카카오톡 단톡방에 공유하는 편집자다. "
        "제공된 기사 목록에 있는 사실만 사용하고 추측하지 않는다. "
        f"{agency_name}과 직접 관련성이 낮은 기사는 제외하거나 한 줄로만 언급한다. "
        "문장은 간결한 한국어로 작성한다. 광고성 표현과 과장은 금지한다. "
        f"Opinion은 기사 사실을 넘어 단정하지 말고, {policy_perspective} 관점의 확인 포인트로만 쓴다."
    )
    user_prompt = {
        "target_date": target_date.isoformat(),
        "format": (
            f"🪖 YYYY-MM-DD {agency_name} 뉴스 브리핑\n"
            f"오늘의 {agency_name} 뉴스 톡 📡\n"
            "1️⃣ 기사 제목\n"
            "기사 요약 1~2문장. 줄임표 없이 경어체로 끝맺음.\n"
            f"Opinion: {policy_perspective} 관점의 의미나 확인 포인트 1~2문장.\n"
            "Source: 원문 링크\n\n"
            "2️⃣ 기사 제목\n"
            "기사 요약 1~2문장.\n\n"
            "오늘 한 줄 요약 🎯\n"
            "전체 흐름을 한 문장으로 요약."
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


def _clean_text(value: object, limit: int = 0) -> str:
    if value is None:
        return ""
    text = normalize_space(str(value))
    text = _remove_ellipsis(text)
    if limit and len(text) > limit:
        return text[:limit].rstrip(" .,")
    return text


def _clean_title(value: object) -> str:
    title = _clean_text(value)
    title = re.sub(r"\]\s*([^\s\]\),.;:!?])", r"] \1", title)
    title = re.sub(r"([가-힣]{1,12}지방)\s+병무청", r"\1병무청", title)
    title = re.sub(r"병무청\s+장", "병무청장", title)
    return normalize_space(title)


def _clean_excluded_note(value: object) -> str:
    note = _clean_text(value, 220)
    if "말줄임표" in note:
        note = re.sub(r"말줄임표[^,，]*[,，]\s*", "", note)
        note = re.sub(r"말줄임표[^ ]*\s*", "", note)
    return normalize_space(note)


def _article_topic_key(title: str, summary: str) -> str:
    compact = re.sub(r"\s+", "", f"{title} {summary}")
    topic_rules = [
        (("현역병", "모집"), "현역병모집"),
        (("삼도동원훈련장",), "삼도동원훈련장"),
        (("동원훈련장", "홍소영"), "병무청장동원훈련장"),
        (("국외여행", "허가"), "국외여행허가"),
        (("입영문화제",), "입영문화제"),
        (("사회복무요원", "취업"), "사회복무요원취업"),
        (("공공데이터", "경진대회"), "공공데이터경진대회"),
        (("병역기피",), "병역기피"),
        (("재복무",), "재복무"),
    ]
    for terms, key in topic_rules:
        if all(term in compact for term in terms):
            return key
    return ""


def _remove_ellipsis(value: str) -> str:
    return normalize_space(re.sub(r"(\.{2,}|…+)", " ", value))


def _prepend_weather_summary(summary: str, weather_summary: str) -> str:
    weather_summary = _clean_text(weather_summary)
    if not weather_summary:
        return summary
    lines = summary.splitlines()
    if any(line.startswith("🌤️") for line in lines[:5]):
        return summary
    if not lines:
        return weather_summary
    return "\n".join([lines[0], weather_summary, "", *lines[1:]])


def _without_lines_starting(summary: str, prefixes: tuple[str, ...]) -> str:
    return "\n".join(
        line for line in summary.splitlines() if not line.startswith(prefixes)
    )


def _source_url_only(summary: str) -> str:
    lines = []
    for line in summary.splitlines():
        if line.startswith("Source:") and " / " in line:
            lines.append("Source: " + line.rsplit(" / ", 1)[1].strip())
        else:
            lines.append(line)
    return "\n".join(lines)


def _fit_summary_for_kakao(summary: str, max_chars: int = KAKAO_MESSAGE_SOFT_LIMIT) -> str:
    if len(summary) <= max_chars:
        return summary

    compact = _without_lines_starting(summary, ("Opinion:",))
    if len(compact) <= max_chars:
        return compact

    compact = _source_url_only(compact)
    if len(compact) <= max_chars:
        return compact

    compact = _without_lines_starting(compact, ("Source:",))
    if len(compact) <= max_chars:
        return compact

    compact = _without_lines_starting(compact, ("💡",))
    if len(compact) <= max_chars:
        return compact

    notice = "\n\n요약이 길어 일부 세부 내용을 줄였습니다."
    return compact[: max_chars - len(notice)].rstrip() + notice


def _with_weather(config: Config, summary: str) -> str:
    summary = _prepend_weather_summary(summary, build_weather_summary(config))
    return _fit_summary_for_kakao(_normalize_message_format(summary, config.agency_name))


def _is_footer_guidance(line: str, agency_name: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return (
        line.startswith("💡")
        and (
            "관련일정과제도" in compact
            or "실제신청전" in compact
            or "공식안내" in compact
            or "검색어를넓히거나" in compact
        )
    )


def _normalize_message_format(summary: str, agency_name: str) -> str:
    normalized: list[str] = []
    previous_blank = False
    for raw_line in summary.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped == "---":
            continue
        if _is_footer_guidance(stripped, agency_name):
            continue
        line = re.sub(r"^\s*#+\s+((?:\d+️⃣)|🔟|\d+\.)\s+", r"\1 ", line)
        line = re.sub(r"^\s*#+\s+(\d+)\s+", r"\1 ", line)
        blank = not line.strip()
        if blank and previous_blank:
            continue
        normalized.append(line)
        previous_blank = blank
    return "\n".join(normalized).strip()


def _load_json_object(raw_text: str) -> dict[str, Any]:
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or start >= end:
            raise
        data = json.loads(text[start : end + 1])

    if not isinstance(data, dict):
        raise ValueError("Codex CLI 응답이 JSON 객체가 아닙니다.")
    return data


def _render_codex_summary(
    target_date: date,
    data: dict[str, Any],
    articles: list[Article],
    agency_name: str,
    weather_summary: str = "",
) -> str:
    policy_perspective = "병무행정" if agency_name == "병무청" else f"{agency_name} 정책"
    lines = [
        f"🪖 {target_date.isoformat()} {agency_name} 뉴스 브리핑",
    ]
    if weather_summary:
        lines.extend([weather_summary, ""])
    lines.extend([
        f"오늘의 {agency_name} 뉴스 톡 📡",
    ])

    rendered_items = 0
    raw_items = data.get("items", [])
    model_items: list[dict[str, Any]] = [
        item for item in raw_items if isinstance(item, dict)
    ] if isinstance(raw_items, list) else []
    model_by_url = {
        _clean_text(item.get("url"), 500): item
        for item in model_items
        if _clean_text(item.get("url"), 500)
    }
    model_by_title = {
        _clean_title(item.get("title")): item
        for item in model_items
        if _clean_title(item.get("title"))
    }

    for article in articles[:SUMMARY_ITEM_LIMIT]:
        model_item = model_by_url.get(article.url) or model_by_title.get(_clean_title(article.title)) or {}
        title = _clean_title(article.title)
        summary = _clean_text(model_item.get("summary"), 130) or _fallback_article_summary(article)
        opinion = _clean_text(model_item.get("opinion"), 100) or _article_opinion(article, agency_name)
        url = article.url or _clean_text(model_item.get("url"), 500)
        if not title or not summary:
            continue

        rendered_items += 1
        number = (
            NUMBER_EMOJI[rendered_items - 1]
            if rendered_items <= len(NUMBER_EMOJI)
            else f"{rendered_items}."
        )
        lines.extend(
            [
                f"{number} {title}",
                summary,
                f"Opinion: {opinion}",
                f"Source: {url or article.source or '네이버 뉴스'}",
                "",
            ]
        )

    if rendered_items == 0:
        lines.extend(["확인된 주요 뉴스가 없습니다.", ""])

    excluded_note = _clean_excluded_note(data.get("excluded_note"))
    if rendered_items >= min(len(articles), SUMMARY_ITEM_LIMIT):
        excluded_note = ""
    if "중복" in excluded_note:
        excluded_note = ""
    if excluded_note:
        lines.extend([excluded_note, ""])

    one_line = _clean_text(data.get("one_line"), 220)
    if not one_line:
        one_line = _one_line_summary(articles, agency_name) if articles else f"오늘은 공유할 만한 {agency_name} 직접 관련 뉴스가 확인되지 않았습니다."

    lines.extend(
        [
            "오늘 한 줄 요약 🎯",
            one_line,
        ]
    )
    return _fit_summary_for_kakao("\n".join(lines))


def summarize_with_codex(config: Config, target_date: date, articles: list[Article]) -> str:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    agency_name = config.agency_name
    policy_perspective = "병무행정" if agency_name == "병무청" else f"{agency_name} 정책"
    payload = {
        "target_date": target_date.isoformat(),
        "agency_name": agency_name,
        "total_articles": len(articles),
        "articles": codex_article_payload(articles),
    }
    input_file = tempfile.NamedTemporaryFile(
        prefix=f"codex-input-{target_date.isoformat()}-",
        suffix=".json",
        dir=output_dir,
        mode="w",
        encoding="utf-8",
        delete=False,
    )
    json.dump(payload, input_file, ensure_ascii=False)
    input_path = Path(input_file.name)
    input_file.close()

    prompt = " ".join(
        [
            "Task: Summarize the Korean news articles now.",
            "Your entire final answer must be exactly one valid JSON object.",
            "Do not acknowledge, do not explain, do not write Markdown, and do not wrap it in a code block.",
            "Write all JSON string values in concise Korean.",
            "Use polite conversational Korean. End every sentence clearly with forms such as 합니다, 했습니다, 입니다, 주세요, or 됩니다.",
            "Never use ellipses, Unicode ellipsis, repeated trailing dots, or title-shortening marks.",
            "Do not mention input, JSON, article summary, provided text, or source data.",
            "Do not use Markdown heading markers such as # before item numbers.",
            "Do not use horizontal divider lines such as ---.",
            "Do not add a closing guidance line beginning with 💡.",
            f"Read the input JSON file at this path and use only facts from that file: {input_path.resolve()}",
            "Do not ask the user to paste articles; the file already exists in the workspace.",
            "Do not infer unsupported facts.",
            f"Exclude or briefly down-rank articles that are weakly related to {agency_name}.",
            "Preserve the input article order in the items array. Do not reorder by your own judgment.",
            "Include the first 10 usable input articles whenever 10 usable articles exist.",
            "If several articles cover the same event, still summarize each usable input article unless it is clearly irrelevant.",
            f"For opinion, write only a cautious {policy_perspective} 관점의 확인 포인트.",
            "For each item title, preserve the full source title from the input. Do not shorten it in your output.",
            "For each item summary, write one short polite spoken Korean sentence under 80 Korean characters.",
            "Do not mention ellipses, title-shortening marks, JSON, or formatting rules in excluded_note.",
            f'Required JSON schema: {{"items":[{{"title":"기사 제목 전체","summary":"기사 요약 1~2문장","opinion":"{policy_perspective} 관점의 확인 포인트 1문장","source":"매체명","url":"원문 URL"}}],"excluded_note":"관련성이 낮거나 중복이라 제외한 기사 설명. 없으면 빈 문자열","one_line":"전체 흐름 한 문장 요약"}}',
            "items는 입력 기사 순서 그대로 가능하면 10개, 최대 10개까지 포함하고, source와 url은 입력 기사에 있는 값만 사용한다.",
        ]
    )
    output_file = tempfile.NamedTemporaryFile(
        prefix=f"codex-summary-{target_date.isoformat()}-",
        suffix=".md",
        dir=output_dir,
        delete=False,
    )
    output_path = Path(output_file.name)
    output_file.close()

    command = [
        config.codex_command,
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "-o",
        str(output_path),
    ]
    if config.codex_model:
        command.extend(["--model", config.codex_model])
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
            timeout=config.codex_timeout_seconds,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            details = "\n".join(
                part.strip()
                for part in [result.stdout[-2000:], result.stderr[-2000:]]
                if part.strip()
            )
            raise RuntimeError(f"Codex CLI 요약 실패(exit {result.returncode}): {details}")

        raw_summary = output_path.read_text(encoding="utf-8").strip()
        if not raw_summary:
            raise RuntimeError("Codex CLI가 빈 요약을 반환했습니다.")
        try:
            summary_data = _load_json_object(raw_summary)
        except Exception as exc:
            debug_path = output_dir / f"codex-raw-{target_date.isoformat()}.txt"
            debug_path.write_text(raw_summary, encoding="utf-8")
            raise RuntimeError(f"Codex CLI JSON 파싱 실패(raw: {debug_path}): {exc}") from exc
        return _render_codex_summary(target_date, summary_data, articles, agency_name)
    finally:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        try:
            input_path.unlink()
        except FileNotFoundError:
            pass


def _trim_sentence(value: str, limit: int = 180) -> str:
    text = _remove_ellipsis(normalize_space(value))
    if len(text) <= limit:
        return text
    return text[:limit].rstrip(" .,")


def _fallback_article_summary(article: Article, limit: int = 85) -> str:
    text = _trim_sentence(article.summary or article.title, limit)
    if not text:
        return f"{_clean_title(article.title)} 관련 보도입니다."
    if re.search(r"(다|요|죠|니다|습니다|했습니다|됩니다)[.!?]?$", text):
        return text
    return f"{text} 내용을 다뤘습니다."


def _article_opinion(article: Article, agency_name: str = "병무청") -> str:
    if agency_name != "병무청":
        return f"{agency_name} 관련 보도는 시장·조직·정책 환경에 따라 해석이 달라질 수 있어요. 기사 내용만으로 단정하지 말고 원문과 공식 발표를 함께 확인하는 게 좋습니다."
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


def _one_line_summary(articles: list[Article], agency_name: str = "병무청") -> str:
    if agency_name != "병무청":
        return f"{agency_name} 관련 주요 보도가 네이버 뉴스 기준으로 확인된 하루였습니다."
    titles = " ".join(article.title for article in articles[:5])
    if "경진대회" in titles or "공공데이터" in titles:
        return "공공데이터·AI 활용, 복무 인력 관리, 지방병무청 현장 소식이 함께 포착된 하루였습니다."
    if "사회복무요원" in titles:
        return "사회복무요원 운영과 지방병무청 현장 안내가 병무청 뉴스의 중심이었습니다."
    return "병무청 관련 제도·행사·현장 안내가 네이버 뉴스 기준으로 이어진 하루였습니다."


def summarize_heuristic(config: Config, target_date: date, articles: list[Article]) -> str:
    agency_name = config.agency_name
    header = f"🪖 {target_date.isoformat()} {agency_name} 뉴스 브리핑"
    if not articles:
        lines = [
            header,
            f"오늘의 {agency_name} 뉴스 톡 📡",
            "확인된 주요 뉴스가 없습니다.",
            "",
            "오늘 한 줄 요약 🎯",
            f"오늘은 공유할 만한 {agency_name} 직접 관련 뉴스가 확인되지 않았습니다.",
        ]
        return "\n".join(lines)

    top = articles[:SUMMARY_ITEM_LIMIT]
    lines = [
        header,
        f"오늘의 {agency_name} 뉴스 톡 📡",
    ]

    for idx, article in enumerate(top, start=1):
        number = NUMBER_EMOJI[idx - 1] if idx <= len(NUMBER_EMOJI) else f"{idx}."
        summary = _trim_sentence(article.summary or article.title, 150)
        lines.extend(
            [
                f"{number} {_clean_title(article.title)}",
                f"{summary} 🎯",
                f"Opinion: {_article_opinion(article, agency_name)}",
                f"Source: {article.url or article.source or '네이버 뉴스'}",
                "",
            ]
        )

    if len(articles) > len(top):
        lines.append(f"그 외 관련 기사 {len(articles) - len(top)}건은 중복·관련도 기준으로 제외했습니다.")
        lines.append("")

    lines.extend(
        [
            "오늘 한 줄 요약 🎯",
            _one_line_summary(articles, agency_name),
        ]
    )
    return _fit_summary_for_kakao("\n".join(lines))


def build_summary(config: Config, target_date: date, articles: list[Article]) -> str:
    provider = config.summary_provider
    if provider == "codex":
        try:
            return _with_weather(config, summarize_with_codex(config, target_date, articles))
        except Exception as exc:
            fallback = summarize_heuristic(config, target_date, articles)
            return _with_weather(config, f"{fallback}\n\n요약 모델 호출 실패: {exc}")
    if provider == "openai":
        return _with_weather(config, summarize_with_openai(config, target_date, articles))
    if provider in {"heuristic", "none", "fallback"}:
        return _with_weather(config, summarize_heuristic(config, target_date, articles))
    if provider not in {"auto", ""}:
        raise RuntimeError(f"지원하지 않는 SUMMARY_PROVIDER입니다: {provider}")
    if config.openai_api_key:
        try:
            return _with_weather(config, summarize_with_openai(config, target_date, articles))
        except Exception as exc:
            fallback = summarize_heuristic(config, target_date, articles)
            return _with_weather(config, f"{fallback}\n\n요약 모델 호출 실패: {exc}")
    return _with_weather(config, summarize_heuristic(config, target_date, articles))
