import re
import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from kakao_mma_news.kakao import split_message
from kakao_mma_news.news import (
    Article,
    _filter_articles,
    briefing_priority_score,
    dedupe_articles,
    matches_required_terms,
    strip_html,
)
from scripts.filter_articles_by_summary_sources import (
    extract_numbered_titles,
    extract_source_urls,
    filter_articles_by_source_urls,
    filter_articles_by_titles,
)
from kakao_mma_news.summarize import (
    _load_json_object,
    _article_topic_key,
    _fit_summary_for_kakao,
    _prepend_weather_summary,
    _render_codex_summary,
    _summary_article_groups,
    codex_article_payload,
    summary_date_label,
    summarize_heuristic,
)
from kakao_mma_news.weather import build_weather_summary
from scripts.build_podcast_audio import format_spoken_date, markdown_to_speech, validate_date_label
from scripts.is_korean_business_day import business_day_status
from scripts.watch_negative_news import (
    Classification,
    NewsItem,
    alert_record,
    article_source_supports_issue,
    build_alert_message,
    build_diagnostic_report,
    classify_heuristic,
    extract_topic_entity,
    has_core_issue_relevance,
    has_institution_reputation_context,
    in_active_window,
    merge_recent_alert_records,
    prune_sent_alerts,
    recent_seen_records_from_urls,
    should_review_with_codex,
    topic_fingerprint,
)


class CoreTests(unittest.TestCase):
    def test_filter_articles_by_summary_sources(self):
        articles = [
            {"title": "excluded", "url": "https://example.com/excluded", "source": "example.com"},
            {"title": "second", "url": "https://example.com/two?utm_source=x", "source": "example.com"},
            {"title": "first", "url": "https://example.com/one", "source": "example.com"},
        ]
        summary = "\n".join(
            [
                "# 1 first",
                "Source: https://example.com/one",
                "# 2 second",
                "Source: example.com / https://example.com/two",
            ]
        )

        source_urls = extract_source_urls(summary)
        selected = filter_articles_by_source_urls(articles, source_urls)

        self.assertEqual([item["title"] for item in selected], ["first", "second"])

    def test_filter_articles_by_numbered_titles_when_sources_are_trimmed(self):
        articles = [
            {"title": "제외 기사", "url": "https://example.com/excluded", "source": "example.com"},
            {"title": "예비군 훈련 현장 점검", "url": "https://example.com/one", "source": "example.com"},
            {"title": "박근혜 “보수결집의 시간”… 국민의힘, 정권심판론 강조", "url": "https://example.com/two", "source": "example.com"},
        ]
        summary = "\n".join(
            [
                "🪖 2026-05-28 병무청 뉴스 브리핑",
                "1️⃣ 예비군 훈련 현장 점검",
                "요약입니다.",
                "2️⃣ 박근혜 “보수결집의 시간” 국민의힘, 정권심판론 강조",
                "요약입니다.",
            ]
        )

        titles = extract_numbered_titles(summary)
        selected = filter_articles_by_titles(articles, titles)

        self.assertEqual(
            [item["url"] for item in selected],
            ["https://example.com/one", "https://example.com/two"],
        )

    def test_strip_html(self):
        self.assertEqual(strip_html("<b>병무청</b>&nbsp;뉴스<br>요약"), "병무청 뉴스 요약")
        self.assertEqual(
            strip_html("[김종석의 리포트]검찰, 대구경북지방 <b>병무청</b> , 병무청 장 방문"),
            "[김종석의 리포트] 검찰, 대구경북지방병무청, 병무청장 방문",
        )

    def test_dedupe_by_title(self):
        articles = [
            Article("같은 제목", "https://example.com/a?utm_source=x", "A", datetime.now(timezone.utc), "", "test"),
            Article("같은 제목", "https://example.com/b", "B", datetime.now(timezone.utc), "", "test"),
        ]
        self.assertEqual(len(dedupe_articles(articles)), 1)

    def test_split_message(self):
        chunks = split_message("a\nb\nc", 3)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(len(chunk) <= 3 for chunk in chunks))

    def test_matches_required_terms(self):
        article = Article(
            "서울지방병무청 행사",
            "https://example.com",
            "A",
            datetime.now(timezone.utc),
            "",
            "test",
        )
        self.assertTrue(matches_required_terms(article, ["병무청"]))
        self.assertFalse(matches_required_terms(article, ["예비군"]))

    def test_filter_articles_accepts_target_date_range(self):
        config = SimpleNamespace(
            required_terms=["병무청"],
            query_terms=["병무청"],
            fetch_article_text=False,
            summary_provider="codex",
        )
        articles = [
            Article(
                f"병무청 범위 기사 {day}",
                f"https://example.com/{day}",
                "example.com",
                datetime(2026, 5, day, 3, 0, tzinfo=timezone.utc),
                "병무청 관련 기사입니다.",
                "test",
            )
            for day in (21, 22, 23, 24)
        ]

        filtered = _filter_articles(articles, config, date(2026, 5, 24), start_date=date(2026, 5, 22))

        self.assertEqual([article.published_date_kst for article in filtered], [date(2026, 5, 24), date(2026, 5, 23), date(2026, 5, 22)])

    def test_range_date_labels_for_briefing_and_podcast(self):
        self.assertEqual(summary_date_label(date(2026, 5, 25), date(2026, 5, 22)), "2026-05-22~2026-05-25")
        spoken = format_spoken_date("2026-05-22~2026-05-25")
        validate_date_label("2026-05-22~2026-05-25")
        validate_date_label("2026-05-22-to-2026-05-25")
        with self.assertRaises(ValueError):
            validate_date_label("2026-05-22~bad")
        self.assertIn("2026 년 5 월 22 일부터", spoken)
        self.assertIn("2026 년 5 월 25 일까지", spoken)

    def test_korean_business_day_guard(self):
        self.assertFalse(business_day_status(date(2026, 5, 23))["business_day"])
        self.assertFalse(business_day_status(date(2026, 5, 25))["business_day"])
        self.assertTrue(business_day_status(date(2026, 5, 26))["business_day"])

    def test_markdown_summary_format(self):
        article = Article(
            "서울지방병무청 행사",
            "https://example.com",
            "example.com",
            datetime(2026, 5, 15, tzinfo=timezone.utc),
            "서울지방병무청이 병역진로설계 행사를 열었다.",
            "test",
        )
        config = SimpleNamespace(agency_name="병무청")
        summary = summarize_heuristic(config, article.published_date_kst, [article])
        self.assertIn("오늘의 병무청 뉴스 톡", summary)
        self.assertIn("1️⃣ 서울지방병무청 행사", summary)
        self.assertNotIn("# 1️⃣", summary)
        self.assertIn("Opinion:", summary)
        self.assertIn("오늘 한 줄 요약", summary)
        self.assertNotIn("네이버 뉴스 기준으로 확인한", summary)

    def test_codex_json_render_format(self):
        article = Article(
            "전북지방병무청 경진대회",
            "https://example.com/news",
            "example.com",
            datetime(2026, 5, 15, tzinfo=timezone.utc),
            "전북지방병무청이 업무 인수인계 개선 경진대회를 열었다.",
            "test",
        )
        data = _load_json_object(
            '```json\n{"items":[{"title":"전북지방 병무청 , 경진대회","summary":"업무 인수인계 개선을 위한 행사다.","opinion":"행정 품질 개선 효과를 확인할 필요가 있다.","source":"example.com","url":"https://example.com/news"}],"excluded_note":"말줄임표가 포함되었거나 중복된 항목은 제외했습니다.","one_line":"지방병무청 업무 개선 소식이 중심이었다."}\n```'
        )
        summary = _render_codex_summary(article.published_date_kst, data, [article], "병무청")
        self.assertIn("🪖 2026-05-15 병무청 뉴스 브리핑", summary)
        self.assertIn("1️⃣ 전북지방병무청, 경진대회", summary)
        self.assertIn("Opinion: 행정 품질 개선 효과를 확인할 필요가 있다.", summary)
        self.assertIn("Source: https://example.com/news", summary)
        self.assertNotIn("Source: example.com / https://example.com/news", summary)
        self.assertNotIn("네이버 뉴스 기준으로 확인한", summary)
        self.assertNotIn("말줄임표", summary)

    def test_codex_render_dedupes_same_news_topic(self):
        article = Article(
            "현역병 모집",
            "https://example.com/a",
            "example.com",
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            "현역병 모집 안내",
            "test",
        )
        data = {
            "items": [
                {
                    "title": "2026년 8월 입영 각 군 현역병 모집 접수",
                    "summary": "대구경북지방병무청이 현역병 모집 접수를 알렸습니다.",
                    "opinion": "지원 기간 안내 확인이 필요합니다.",
                    "source": "a",
                    "url": "https://example.com/a",
                },
                {
                    "title": "대경 병무청, 8월 입영 현역병 모집 접수",
                    "summary": "대구경북지방병무청이 현역병 모집을 안내했습니다.",
                    "opinion": "접수 채널 확인이 필요합니다.",
                    "source": "b",
                    "url": "https://example.com/b",
                },
                {
                    "title": "홍소영 병무청장, 삼도동원훈련장 방문",
                    "summary": "홍소영 병무청장이 삼도동원훈련장을 방문했습니다.",
                    "opinion": "현장 의견 반영을 확인해야 합니다.",
                    "source": "c",
                    "url": "https://example.com/c",
                },
            ],
            "excluded_note": "",
            "one_line": "현역병 모집과 현장 점검 소식입니다.",
        }
        summary = _render_codex_summary(article.published_date_kst, data, [article], "병무청")
        self.assertIn("2026년 8월 입영 각 군 현역병 모집 접수", summary)
        self.assertNotIn("대경 병무청, 8월 입영 현역병 모집 접수", summary)
        self.assertIn("삼도동원훈련장 방문", summary)

    def test_codex_payload_and_render_can_use_ten_items(self):
        articles = [
            Article(
                f"삼성전자 테스트 기사 {idx}",
                f"https://example.com/{idx}",
                "example.com",
                datetime(2026, 5, 17, tzinfo=timezone.utc),
                f"삼성전자 테스트 요약 {idx}",
                "test",
            )
            for idx in range(1, 25)
        ]
        payload = codex_article_payload(articles)
        self.assertEqual(len(payload), 24)
        data = {
            "items": [
                {
                    "title": f"삼성전자 주요 기사 {idx}",
                    "summary": f"삼성전자 관련 핵심 내용 {idx}입니다.",
                    "opinion": "확인 포인트입니다.",
                    "source": "example.com",
                    "url": f"https://example.com/{idx}",
                }
                for idx in range(1, 12)
            ],
            "excluded_note": "",
            "one_line": "삼성전자 관련 주요 흐름입니다.",
        }
        summary = _render_codex_summary(articles[0].published_date_kst, data, articles, "삼성전자")
        self.assertEqual(sum(1 for line in summary.splitlines() if re.match(r"^(?:[1-9]️⃣|🔟) ", line)), 10)
        self.assertIn("🔟 삼성전자 주요 기사 10", summary)
        self.assertNotIn("삼성전자 주요 기사 11", summary)

    def test_codex_provider_does_not_fallback_without_ai_duplicate_check(self):
        article = Article(
            "부영그룹 병무청 협약",
            "https://example.com/booyoung",
            "example.com",
            datetime(2026, 6, 3, tzinfo=timezone.utc),
            "부영그룹과 병무청이 병역명문가 레저시설 우대 협약을 맺었습니다.",
            "test",
        )
        config = SimpleNamespace(summary_provider="codex")

        with patch("kakao_mma_news.summarize.summarize_with_codex", side_effect=RuntimeError("timeout")):
            with self.assertRaises(RuntimeError) as raised:
                from kakao_mma_news.summarize import build_summary

                build_summary(config, article.published_date_kst, [article])

        self.assertIn("Codex CLI의 의미 기반 중복 판단이 필수", str(raised.exception))

    def test_summary_fit_drops_opinion_first(self):
        long_summary = "\n".join(
            ["🪖 2026-05-17 삼성전자 뉴스 브리핑"]
            + [
                "\n".join(
                    [
                        f"# {idx}. 삼성전자 아주 긴 제목 {idx}",
                        "삼성전자 관련 설명입니다. " * 10,
                        "Opinion: 이 문장은 길이를 줄일 때 먼저 빠져야 합니다.",
                        f"Source: example.com / https://example.com/{idx}",
                    ]
                )
                for idx in range(1, 11)
            ]
        )
        fitted = _fit_summary_for_kakao(long_summary, 1200)
        self.assertLessEqual(len(fitted), 1200)
        self.assertNotIn("Opinion:", fitted)

    def test_default_summary_fit_keeps_source_links_for_kakao_split(self):
        summary = "\n".join(
            ["🪖 2026-05-28 병무청 뉴스 브리핑"]
            + [
                "\n".join(
                    [
                        f"{idx}. 병무청 기사 {idx}",
                        "병무청 관련 설명입니다. " * 8,
                        f"Source: https://example.com/{idx}",
                    ]
                )
                for idx in range(1, 11)
            ]
        )
        fitted = _fit_summary_for_kakao(summary)

        self.assertIn("Source: https://example.com/1", fitted)
        self.assertIn("Source: https://example.com/10", fitted)

    def test_article_topic_key_for_known_duplicates(self):
        self.assertEqual(
            _article_topic_key("대경 병무청, 8월 입영 현역병 모집 접수", "현역병 모집 안내"),
            "현역병모집",
        )
        self.assertEqual(
            _article_topic_key("인도인접 현장", "홍소영 병무청장이 삼도동원훈련장을 방문했습니다."),
            "삼도동원훈련장",
        )

    def test_summary_groups_same_public_figure_issue(self):
        articles = [
            Article(
                f"유승준 병역 루머 해명 보도 {idx}",
                f"https://example.com/ysj-{idx}",
                "example.com",
                datetime(2026, 5, 22, idx, 0, tzinfo=timezone.utc),
                "유승준이 병역 관련 루머를 해명했습니다.",
                "naver_news",
            )
            for idx in range(1, 5)
        ] + [
            Article(
                "송민호 복무 책임자 병역법 위반 공모 혐의 부인",
                "https://example.com/mino",
                "example.com",
                datetime(2026, 5, 22, 5, 0, tzinfo=timezone.utc),
                "송민호 사회복무요원 복무 관련 재판 소식입니다.",
                "naver_news",
            ),
            Article(
                "병무청, 현역병 입영 안내",
                "https://example.com/mma",
                "example.com",
                datetime(2026, 5, 22, 6, 0, tzinfo=timezone.utc),
                "병무청이 현역병 입영 일정을 안내했습니다.",
                "naver_news",
            ),
        ]

        groups = _summary_article_groups(articles)
        self.assertEqual(len(groups), 3)
        self.assertEqual(groups[0]["article"].url, "https://example.com/ysj-1")
        self.assertEqual(len(groups[0]["related"]), 3)

        summary = _render_codex_summary(
            date(2026, 5, 22),
            {"items": [], "excluded_note": "", "one_line": "병역 이슈가 이어졌습니다."},
            articles,
            "병무청",
        )
        self.assertIn("1️⃣ 유승준 병역 루머 해명 보도 1", summary)
        self.assertIn("관련 보도: 3건 추가 묶음", summary)
        self.assertNotIn("유승준 병역 루머 해명 보도 2", summary)
        self.assertIn("2️⃣ 송민호 복무 책임자 병역법 위반 공모 혐의 부인", summary)
        self.assertIn("3️⃣ 병무청, 현역병 입영 안내", summary)

    def test_weather_summary_inserted_after_header(self):
        summary = _prepend_weather_summary(
            "🪖 2026-05-15 병무청 뉴스 브리핑\n본문",
            "🌤️ 오늘 서울은 맑고 최고 25도입니다.",
        )
        self.assertIn(
            "🪖 2026-05-15 병무청 뉴스 브리핑\n🌤️ 오늘 서울은 맑고 최고 25도입니다.\n\n본문",
            summary,
        )

    def test_weather_summary_uses_conversational_outing_style(self):
        class FakeResponse:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                return None

            def json(self):
                return self.payload

        class FakeRequests:
            @staticmethod
            def get(url, **kwargs):
                if "air-quality" in url:
                    return FakeResponse({"hourly": {"pm10": [61], "pm2_5": [26]}})
                return FakeResponse(
                    {
                        "daily": {
                            "weather_code": [2],
                            "temperature_2m_max": [28],
                            "temperature_2m_min": [16],
                            "precipitation_probability_max": [0],
                        }
                    }
                )

        config = SimpleNamespace(
            weather_enabled=True,
            weather_location="세종",
            weather_latitude=36.48,
            weather_longitude=127.289,
            request_timeout_seconds=3,
        )
        with patch.dict("sys.modules", {"requests": FakeRequests}):
            summary = build_weather_summary(config)

        self.assertIn("🌤️ 오늘 세종은 최고 28도", summary)
        self.assertIn("미세먼지 61(보통)", summary)
        self.assertIn("초미세먼지 26(보통)", summary)
        self.assertIn("수분을 챙겨 주세요", summary)

    def test_podcast_speech_cleans_markdown(self):
        speech = markdown_to_speech(
            "🌤️ 오늘 서울은 맑고 최고 25도입니다.\n"
            "# 1️⃣ 병무청 AI·5G 공공데이터 2026-05-16 기사\n"
            "병무청이 공공데이터 행사를 열었다. AI 활용 계획은 5건이다. 🎯\n"
            "관련 보도: 3건 추가 묶음\n"
            "Opinion: 공식 안내 확인이 필요하다.\n"
            "Source: example.com / https://example.com/news\n"
            "---\n"
            "같은 이슈를 다룬 관련 보도 2건은 대표 기사에 묶었습니다.\n"
            "그 외 관련 기사 5건은 관련도 기준으로 제외했습니다.\n"
            "오늘 한 줄 요약 🎯\n"
            "이 문장은 음성 기사 내용에 들어가면 안 된다.\n",
            "2026-05-16",
            include_weather=True,
        )
        self.assertIn("오늘 서울은 맑고 최고 25 도입니다.", speech)
        self.assertIn("2026 년 5 월 16 일 기관 뉴스 음성 브리핑입니다.", speech)
        self.assertIn("오늘은 주요 기사 1 건을 제목과 핵심 내용 중심으로 전해드리겠습니다.", speech)
        self.assertIn(
            "첫 번째 소식입니다. 병무청 에이 아이, 5 지 공공데이터 2026 년 5 월 16 일 기사 관련 보도입니다.",
            speech,
        )
        self.assertIn("병무청이 공공데이터 행사를 열었습니다.", speech)
        self.assertNotIn("제목은", speech)
        self.assertNotIn("주요 내용입니다", speech)
        self.assertIn("에이 아이 활용 계획은 5 건입니다.", speech)
        self.assertNotIn("공식 안내 확인이 필요하다", speech)
        self.assertNotIn("들어가면 안 된다", speech)
        self.assertNotIn("Source:", speech)
        self.assertNotIn("️⃣", speech)
        self.assertNotIn("추가 묶음", speech)
        self.assertNotIn("대표 기사에 묶었습니다", speech)
        self.assertNotIn("관련도 기준으로 제외", speech)
        self.assertNotIn("...", speech)
        self.assertNotIn("…", speech)

    def test_podcast_speech_polishes_sample_title_and_units(self):
        speech = markdown_to_speech(
            "🪖 2026-04-21 병무청 뉴스 브리핑\n"
            "# 1️⃣ [김종석의 리포트]검찰, 송민호에 징역 1년 6개월 구형\n"
            "검찰이 송민호 씨에게 징역 1년 6개월을 구형했습니다.\n"
            "# 2️⃣ 대구경북지방 병무청 , 25세 이상 병역의무자, 국외여행허가 받아야\n"
            "단기 국외여행 허가기간이 최대 6개월서 1개월 이내로 단축됩니다. 오후 2시 안내입니다.\n"
            "# 3️⃣ 홍소영 병무청 장, 해병대 신병 1329기 입영문화제 이슈&톡\n"
            "홍소영 병무청 장이 현장을 방문했습니다.\n"
            "# 4️⃣ 대구·경북지방병무청 입영문화제\n"
            "대구·경북지방병무청이 행사를 열었습니다. 5개 자치구가 참여했습니다.\n",
            "2026-04-21",
        )
        self.assertIn("김종석의 리포트, 검찰", speech)
        self.assertIn("1 년 6 개월", speech)
        self.assertIn("대구경북지방병무청", speech)
        self.assertIn("25 세", speech)
        self.assertIn("국외여행 허가", speech)
        self.assertIn("허가 기간", speech)
        self.assertIn("6 개월에서 1 개월", speech)
        self.assertIn("오후 2 시", speech)
        self.assertIn("병무청장", speech)
        self.assertIn("1329 기", speech)
        self.assertIn("이슈 앤 톡", speech)
        self.assertIn("대구경북지방병무청이 행사를 열었습니다.", speech)
        self.assertNotIn("대구, 경북지방병무청", speech)
        self.assertIn("5 개 자치구", speech)
        self.assertNotIn("제목은", speech)
        self.assertNotIn("주요 내용입니다", speech)
        self.assertNotIn("주요 내용은 대구경북지방병무청은", speech)
        self.assertIn("자세한 내용과 개인별 적용 조건", speech)

    def test_podcast_speech_polishes_company_symbols(self):
        speech = markdown_to_speech(
            "🪖 2026-05-17 삼성전자 뉴스 브리핑\n"
            "# 1️⃣ 삼전 노조 지휘부 수당 ‘月 500만원’ 논란\n"
            "DX 부문 제1노조가 영업이익 n% 성과급을 요구했습니다.\n",
            "2026-05-17",
        )
        self.assertIn("월 500 만원", speech)
        self.assertIn("디 엑스 부문 제1 노조", speech)
        self.assertIn("영업이익 엔 퍼센트 성과급", speech)

    def test_podcast_no_article_message_is_exact(self):
        speech = markdown_to_speech(
            "🪖 2026-03-31 병무청 뉴스 브리핑\n확인된 주요 뉴스가 없습니다.",
            "2026-03-31",
        )
        self.assertIn("주요 기사가 확인되지 않았습니다.", speech)
        self.assertNotIn("많지 않았습니다", speech)

    def test_negative_watch_groups_same_person_issue(self):
        first = NewsItem(
            title="MC몽, '발치몽' 이미지 억울함 토로 \"대법원까지 무죄\"",
            url="https://example.com/a",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T12:00:00+09:00",
            summary="가수 MC몽이 병역비리 의혹과 병역법 위반 무죄 판결을 해명했다.",
            query="병역비리",
        )
        second = NewsItem(
            title="[오늘연예] MC몽, 병역 비리 의혹 재차 해명",
            url="https://example.com/b",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T12:05:00+09:00",
            summary="MC몽은 라이브 방송에서 과거 병역 기피 논란에 관해 입장을 밝혔다.",
            query="병역기피 연예인",
        )
        self.assertEqual(
            topic_fingerprint(first, classify_heuristic(first)),
            topic_fingerprint(second, classify_heuristic(second)),
        )

        reaction = NewsItem(
            title="김민종, MC몽 병역 비리 관련 주장 반박",
            url="https://example.com/c",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T12:10:00+09:00",
            summary="김민종은 MC몽이 언급한 내용을 반박했고, 기사에서는 병역 비리 논란도 함께 다뤘다.",
            query="병역비리",
        )
        self.assertEqual(
            topic_fingerprint(first, classify_heuristic(first)),
            topic_fingerprint(reaction, classify_heuristic(reaction)),
        )

    def test_negative_watch_ignores_leading_issue_label_for_topic_entity(self):
        first = NewsItem(
            title='유승준, 딸 앞서 병역 루머 해명 "퇴근 후 연예활동 보장?"',
            url="https://example.com/steve-a",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T08:49:00+09:00",
            summary="병역 기피 논란으로 입국이 제한된 가수 유승준이 과거 특혜 의혹을 부인했다.",
            query="병무청 병역기피",
        )
        second = NewsItem(
            title="' 병역 기피 ' 유승준, 오랜 루머에 입 열었다 \"이제 종결\" [MHN:픽]",
            url="https://example.com/steve-b",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T12:31:00+09:00",
            summary="유승준은 병무청에 직접 확인했다며 병역 의무와 공익 특혜 의혹을 해명했다.",
            query="병무청 병역기피",
        )
        self.assertEqual("유승준", extract_topic_entity(second))
        self.assertEqual(
            topic_fingerprint(first, classify_heuristic(first)),
            topic_fingerprint(second, classify_heuristic(second)),
        )

    def test_negative_watch_prefers_known_entity_over_title_prefix(self):
        first = NewsItem(
            title='딸과 함께 해명 나선 \' 병역기피 \' 유승준 "공무원 해고설은 루머"',
            url="https://example.com/steve-prefix-a",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T18:15:00+09:00",
            summary="병역 기피 논란으로 입국이 제한된 가수 스티브 유가 병역 특혜와 공무원 해고설을 해명했다.",
            query="병무청 병역기피",
        )
        second = NewsItem(
            title='항소심 앞둔 유승준, 공무원 해고· 병역 특혜 논란 해명 "모두 사실무근"',
            url="https://example.com/steve-prefix-b",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T20:15:00+09:00",
            summary="가수 유승준이 과거 병역 기피 혐의를 둘러싼 각종 루머를 해명했다.",
            query="병무청 병역기피",
        )
        third = NewsItem(
            title="법무부, 유승준 ‘입국 금지’ 법적 근거 마련한다",
            url="https://example.com/steve-prefix-c",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T20:45:00+09:00",
            summary="병역 기피 논란의 유승준 입국 금지 관련 법적 근거를 마련한다.",
            query="병무청 병역기피",
        )

        self.assertEqual("유승준", extract_topic_entity(first))
        self.assertEqual("유승준", extract_topic_entity(second))
        self.assertEqual("유승준", extract_topic_entity(third))
        self.assertEqual(
            topic_fingerprint(first, classify_heuristic(first)),
            topic_fingerprint(second, classify_heuristic(second)),
        )
        self.assertEqual(
            topic_fingerprint(first, classify_heuristic(first)),
            topic_fingerprint(third, classify_heuristic(third)),
        )

    def test_negative_watch_prunes_and_merges_sent_alert_records(self):
        now = datetime.fromisoformat("2026-05-22T20:00:00+09:00")
        recent = {
            "sent_at": "2026-05-22T19:30:00+09:00",
            "topic_key": "topic-a",
            "article": {"url": "https://example.com/a", "title": "recent"},
        }
        old = {
            "sent_at": "2026-05-22T07:30:00+09:00",
            "topic_key": "topic-old",
            "article": {"url": "https://example.com/old", "title": "old"},
        }
        duplicate_topic = {
            "sent_at": "2026-05-22T19:00:00+09:00",
            "topic_key": "topic-a",
            "article": {"url": "https://example.com/a-copy", "title": "copy"},
        }

        pruned = prune_sent_alerts([recent, old], now, 12)
        self.assertEqual([record["topic_key"] for record in pruned], ["topic-a"])

        merged = merge_recent_alert_records([duplicate_topic, recent])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["sent_at"], "2026-05-22T19:30:00+09:00")

    def test_negative_watch_rebuilds_recent_alert_records_from_seen_urls(self):
        now = datetime.fromisoformat("2026-05-22T20:00:00+09:00")
        item = NewsItem(
            title="same issue",
            url="https://example.com/a",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T19:00:00+09:00",
            summary="same issue summary",
            query="query",
        )
        classification = Classification(
            send=True,
            severity="high",
            category="issue",
            summary="summary",
            reason="reason",
            score=5,
            matched_terms=["issue"],
        )

        records = recent_seen_records_from_urls(
            [item],
            {"https://example.com/a": "2026-05-22T19:10:00+09:00"},
            {"https://example.com/a": classification},
            now,
            12,
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["article"]["title"], "same issue")
        self.assertEqual(records[0]["topic_key"], topic_fingerprint(item, classification))

    def test_negative_watch_diagnostic_report_includes_duplicate_reason(self):
        item = NewsItem(
            title="same issue title",
            url="https://example.com/a",
            naver_url="",
            source="example.com",
            published_at="2026-05-22T19:00:00+09:00",
            summary="same issue summary",
            query="query",
        )
        classification = Classification(
            send=True,
            severity="high",
            category="issue",
            summary="summary",
            reason="reason",
            score=5,
            matched_terms=["issue"],
        )
        message = build_diagnostic_report(
            room="test",
            now=datetime.fromisoformat("2026-05-22T22:15:00+09:00"),
            fetched_count=10,
            recent_count=8,
            new_count=2,
            raw_candidate_count=1,
            source_relevance_reject_count=0,
            topic_duplicate_matches=[],
            semantic_duplicate_matches=[
                {
                    "title": "same issue title",
                    "topic_key": "topic-new",
                    "matched_topic_key": "topic-old",
                    "reason": "최근 발송한 같은 인물의 같은 병역 논란입니다.",
                }
            ],
            ai_duplicate_checks=1,
            recent_alert_record_count=3,
            recent_alert_records=[
                alert_record(
                    item,
                    classification,
                    "topic-old",
                    datetime.fromisoformat("2026-05-22T20:00:00+09:00"),
                )
            ],
            new_items=[item],
            alerts=[],
            errors=[],
        )

        self.assertIn("부정 이슈 탐지 테스트 리포트", message)
        self.assertIn("AI 중복 판단", message)
        self.assertIn("최근 발송한 같은 인물의 같은 병역 논란입니다.", message)
        self.assertIn("최근 12시간 실제 알림 비교 대상", message)
        self.assertIn("최근 12시간 실제 알림 이력", message)
        self.assertIn("실제 알림 이력에는 포함하지 않습니다", message)
        self.assertIn("신규 검색 기사", message)
        self.assertIn("https://example.com/a", message)

    def test_negative_watch_detects_institution_reputation_issue(self):
        item = NewsItem(
            title="강명구 세월호 어묵국 국회 메뉴 비판",
            url="https://example.com/reputation",
            naver_url="",
            source="example.com",
            published_at="2026-05-24T15:25:00+09:00",
            summary="병무청 기관의 점심 메뉴가 세월호 참사일 어묵국이었다는 논란이 제기됐고 사퇴 요구가 나왔습니다.",
            query="병무청 논란",
        )

        classification = classify_heuristic(item)

        self.assertTrue(has_institution_reputation_context(f"{item.title} {item.summary}"))
        self.assertTrue(classification.send)
        self.assertGreaterEqual(classification.score, 6)

    def test_negative_watch_uses_codex_review_for_broad_mma_context(self):
        item = NewsItem(
            title="중립 제목",
            url="https://example.com/review",
            naver_url="",
            source="example.com",
            published_at="2026-05-24T15:25:00+09:00",
            summary="점심 메뉴 논란이 제기됐습니다.",
            query="병무청 논란",
        )
        classification = Classification(
            send=False,
            severity="낮음",
            category="병무청 평판 리스크",
            summary="",
            reason="",
            score=0,
            matched_terms=[],
        )

        self.assertTrue(should_review_with_codex(item, classification))

    def test_negative_watch_active_window(self):
        self.assertTrue(in_active_window(datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc), 8, 22))
        self.assertTrue(in_active_window(datetime(2026, 5, 19, 21, 59, tzinfo=timezone.utc), 8, 22))
        self.assertTrue(in_active_window(datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc), 8, 22))
        self.assertFalse(in_active_window(datetime(2026, 5, 19, 22, 1, tzinfo=timezone.utc), 8, 22))
        self.assertFalse(in_active_window(datetime(2026, 5, 19, 22, 15, tzinfo=timezone.utc), 8, 22))
        self.assertFalse(in_active_window(datetime(2026, 5, 19, 7, 59, tzinfo=timezone.utc), 8, 22))

    def test_mma_briefing_priority_orders_issue_before_national_before_local(self):
        config = SimpleNamespace(
            agency_name="병무청",
            query_terms=["병무청", "사회복무요원", "병역법 위반"],
        )
        issue = Article(
            title="송민호, 병역법 위반 의혹 재판",
            url="https://example.com/issue",
            source="example.com",
            published_at=datetime(2026, 5, 21, 9, 0, tzinfo=timezone.utc),
            summary="가수 송민호가 사회복무요원 부실 복무와 무단 결근 의혹을 받고 있다.",
            origin="naver_news",
        )
        national = Article(
            title="병무청, 병역판정검사 제도 개선",
            url="https://example.com/national",
            source="example.com",
            published_at=datetime(2026, 5, 21, 10, 0, tzinfo=timezone.utc),
            summary="병무청이 현역병 입영과 병역판정검사 안내를 발표했다.",
            origin="naver_news",
        )
        local = Article(
            title="서울지방병무청, 청렴 캠페인 개최",
            url="https://example.com/local",
            source="example.com",
            published_at=datetime(2026, 5, 21, 11, 0, tzinfo=timezone.utc),
            summary="서울지방병무청이 청렴 홍보 행사를 열었다.",
            origin="naver_news",
        )
        self.assertGreater(
            briefing_priority_score(issue, config),
            briefing_priority_score(national, config),
        )
        self.assertGreater(
            briefing_priority_score(national, config),
            briefing_priority_score(local, config),
        )

    def test_codex_summary_render_uses_model_selected_items_only(self):
        articles = [
            Article(
                title=f"{idx}번 병무청 관련 기사",
                url=f"https://example.com/{idx}",
                source="example.com",
                published_at=datetime(2026, 5, 22, idx % 24, 0, tzinfo=timezone.utc),
                summary=f"{idx}번 기사 요약입니다.",
                origin="naver_news",
            )
            for idx in range(1, 13)
        ]
        summary = _render_codex_summary(
            date(2026, 5, 22),
            {
                "items": [
                    {
                        "title": "2번 병무청 관련 기사",
                        "summary": "모델이 선택한 2번 요약입니다.",
                        "opinion": "확인 포인트입니다.",
                        "url": "https://example.com/2",
                    }
                ],
                "excluded_note": "중복 기사는 제외했습니다.",
                "one_line": "전체 흐름 요약입니다.",
            },
            articles,
            "병무청",
        )
        self.assertIn("1️⃣ 2번 병무청 관련 기사", summary)
        self.assertNotIn("1번 병무청 관련 기사", summary)
        self.assertNotIn("10번 병무청 관련 기사", summary)
        self.assertNotIn("중복 기사는 제외했습니다", summary)

    def test_negative_watch_rejects_mismatched_naver_snippet(self):
        item = NewsItem(
            title="이정부 통일백서 남북 두 국가 명시 논란 일파만파",
            url="https://example.com/politics",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T16:30:00+09:00",
            summary="전날 MC몽은 자신을 둘러싼 병역 비리와 불법 도박 의혹을 부인했다.",
            query="병역비리",
        )
        self.assertFalse(has_core_issue_relevance(item.title))
        with patch("scripts.watch_negative_news.fetch_article_meta_text", return_value="통일백서 남북 두 국가 논란 정치 기사"):
            self.assertFalse(article_source_supports_issue(item, 1))

    def test_negative_watch_accepts_source_meta_with_core_issue(self):
        item = NewsItem(
            title="MC몽 실명 폭로 후 계정 정지",
            url="https://example.com/entertainment",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T16:30:00+09:00",
            summary="MC몽이 병역 기피 의혹을 해명했다.",
            query="병역비리",
        )
        with patch("scripts.watch_negative_news.fetch_article_meta_text", return_value="MC몽 병역 기피 의혹 해명 기사"):
            self.assertTrue(article_source_supports_issue(item, 1))

    def test_negative_watch_alert_includes_related_links(self):
        representative = NewsItem(
            title="MC몽, 병역 비리 의혹 재차 해명",
            url="https://example.com/main",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T12:00:00+09:00",
            summary="MC몽이 병역비리 의혹을 해명했다.",
            query="병역비리",
        )
        related = [
            NewsItem(
                title=f"MC몽 병역 논란 관련 기사 {idx}",
                url=f"https://example.com/related-{idx}",
                naver_url="",
                source="example.com",
                published_at="2026-05-19T12:0{idx}:00+09:00",
                summary="같은 이슈입니다.",
                query="병역비리",
            )
            for idx in range(1, 7)
        ]
        message = build_alert_message(
            representative,
            classify_heuristic(representative),
            related[:5],
            related_hours=12,
        )
        self.assertIn("📰 대표 기사", message)
        self.assertIn("대표 원문\nhttps://example.com/main", message)
        self.assertIn("같은 이슈로 최근 12시간 안에 추가 보도 5건", message)
        self.assertIn("관련 기사 링크 최대 5건", message)
        self.assertIn("https://example.com/related-5", message)
        self.assertNotIn("https://example.com/related-6", message)

    def test_negative_watch_summary_does_not_prefix_source_domain(self):
        representative = NewsItem(
            title="MC몽 병역 비리 의혹 재차 해명",
            url="https://example.com/main",
            naver_url="",
            source="example.com",
            published_at="2026-05-19T12:00:00+09:00",
            summary="MC몽은 라이브 방송에서 병역 비리 의혹을 부인했다.",
            query="병역비리",
        )
        message = build_alert_message(
            representative,
            classify_heuristic(representative),
            [],
            related_hours=12,
        )
        self.assertIn("핵심 내용", message)
        self.assertIn("MC몽은 라이브 방송에서 병역 비리 의혹을 부인했다.", message)
        self.assertNotIn("example.com에서", message)


if __name__ == "__main__":
    unittest.main()
