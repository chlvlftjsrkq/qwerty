from __future__ import annotations

import json
import os
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

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


def codex_article_payload(articles: list[Article], limit: int = 12) -> list[dict[str, str]]:
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
                "title": article.title[:180],
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


def _clean_text(value: object, limit: int = 0) -> str:
    if value is None:
        return ""
    text = normalize_space(str(value))
    if limit and len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


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
    target_date: date, data: dict[str, Any], articles: list[Article]
) -> str:
    lines = [
        f"🪖 {target_date.isoformat()} 병무청 뉴스 브리핑",
        "전날 네이버 뉴스 기준으로 확인한 병무청 관련 주요 소식을 정리했어요. 개별 신청·접수 조건은 원문과 병무청 공식 안내를 함께 확인해 주세요.",
        "",
        "---",
        "",
        "오늘의 병무청 뉴스 톡 📡",
    ]

    rendered_items = 0
    raw_items = data.get("items", [])
    if isinstance(raw_items, list):
        for item in raw_items[:8]:
            if not isinstance(item, dict):
                continue
            title = _clean_text(item.get("title"), 120)
            summary = _clean_text(item.get("summary"), 450)
            opinion = _clean_text(item.get("opinion"), 260)
            source = _clean_text(item.get("source"), 80) or "네이버 뉴스"
            url = _clean_text(item.get("url"), 500)
            if not title or not summary:
                continue

            rendered_items += 1
            number = (
                NUMBER_EMOJI[rendered_items - 1]
                if rendered_items <= len(NUMBER_EMOJI)
                else f"{rendered_items}."
            )
            fallback_article = (
                articles[rendered_items - 1] if rendered_items <= len(articles) else None
            )
            fallback_opinion = (
                _article_opinion(fallback_article)
                if fallback_article
                else "병무청 관련 행정·제도 흐름을 확인할 수 있는 기사예요. 실제 세부 조건은 원문과 공식 안내에서 확인하는 게 좋습니다."
            )
            lines.extend(
                [
                    f"# {number} {title}",
                    f"{summary} 🎯",
                    f"Opinion: {opinion or fallback_opinion}",
                    f"Source: {source}{' / ' + url if url else ''}",
                    "",
                ]
            )

    if rendered_items == 0:
        lines.extend(["확인된 주요 뉴스가 없습니다.", ""])

    excluded_note = _clean_text(data.get("excluded_note"), 220)
    if excluded_note:
        lines.extend([excluded_note, ""])

    one_line = _clean_text(data.get("one_line"), 220)
    if not one_line:
        one_line = _one_line_summary(articles) if articles else "오늘은 공유할 만한 병무청 직접 관련 뉴스가 확인되지 않았습니다."

    lines.extend(
        [
            "---",
            "",
            "오늘 한 줄 요약 🎯",
            one_line,
            "",
            "---",
            "",
            "💡 병역·입영·복무 관련 일정은 개인별 조건에 따라 달라질 수 있어요. 실제 신청 전 공식 안내를 한 번 더 확인해 주세요.",
        ]
    )
    return "\n".join(lines)


def summarize_with_codex(config: Config, target_date: date, articles: list[Article]) -> str:
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "target_date": target_date.isoformat(),
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
            f"Read the input JSON file at this path and use only facts from that file: {input_path.resolve()}",
            "Do not ask the user to paste articles; the file already exists in the workspace.",
            "Do not infer unsupported facts.",
            "Exclude or briefly down-rank articles that are weakly related to 병무청.",
            "For opinion, write only a cautious 병무행정 관점의 확인 포인트.",
            'Required JSON schema: {"items":[{"title":"기사 제목","summary":"기사 요약 2~3문장","opinion":"병무행정 관점의 확인 포인트 1~2문장","source":"매체명","url":"원문 URL"}],"excluded_note":"관련성이 낮거나 중복이라 제외한 기사 설명. 없으면 빈 문자열","one_line":"전체 흐름 한 문장 요약"}',
            "items는 최대 8개만 포함하고, source와 url은 입력 기사에 있는 값만 사용한다.",
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
        return _render_codex_summary(target_date, summary_data, articles)
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
    provider = config.summary_provider
    if provider == "codex":
        try:
            return summarize_with_codex(config, target_date, articles)
        except Exception as exc:
            fallback = summarize_heuristic(target_date, articles)
            return f"{fallback}\n\n요약 모델 호출 실패: {exc}"
    if provider == "openai":
        return summarize_with_openai(config, target_date, articles)
    if provider in {"heuristic", "none", "fallback"}:
        return summarize_heuristic(target_date, articles)
    if provider not in {"auto", ""}:
        raise RuntimeError(f"지원하지 않는 SUMMARY_PROVIDER입니다: {provider}")
    if config.openai_api_key:
        try:
            return summarize_with_openai(config, target_date, articles)
        except Exception as exc:
            fallback = summarize_heuristic(target_date, articles)
            return f"{fallback}\n\n요약 모델 호출 실패: {exc}"
    return summarize_heuristic(target_date, articles)
