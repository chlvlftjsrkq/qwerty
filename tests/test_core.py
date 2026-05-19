import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from kakao_mma_news.kakao import split_message
from kakao_mma_news.news import Article, dedupe_articles, matches_required_terms, strip_html
from kakao_mma_news.summarize import (
    _load_json_object,
    _article_topic_key,
    _fit_summary_for_kakao,
    _prepend_weather_summary,
    _render_codex_summary,
    codex_article_payload,
    summarize_heuristic,
)
from kakao_mma_news.weather import build_weather_summary
from scripts.build_podcast_audio import markdown_to_speech
from scripts.watch_negative_news import (
    NewsItem,
    article_source_supports_issue,
    build_alert_message,
    classify_heuristic,
    has_core_issue_relevance,
    in_active_window,
    topic_fingerprint,
)


class CoreTests(unittest.TestCase):
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
        self.assertIn("# 1️⃣ 서울지방병무청 행사", summary)
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
        self.assertIn("# 1️⃣ 전북지방병무청, 경진대회", summary)
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
        self.assertEqual(summary.count("\n# "), 10)
        self.assertIn("# 🔟 삼성전자 주요 기사 10", summary)
        self.assertNotIn("삼성전자 주요 기사 11", summary)

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

    def test_article_topic_key_for_known_duplicates(self):
        self.assertEqual(
            _article_topic_key("대경 병무청, 8월 입영 현역병 모집 접수", "현역병 모집 안내"),
            "현역병모집",
        )
        self.assertEqual(
            _article_topic_key("인도인접 현장", "홍소영 병무청장이 삼도동원훈련장을 방문했습니다."),
            "삼도동원훈련장",
        )

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
            "Opinion: 공식 안내 확인이 필요하다.\n"
            "Source: example.com / https://example.com/news\n"
            "---\n"
            "오늘 한 줄 요약 🎯\n"
            "이 문장은 음성 기사 내용에 들어가면 안 된다.\n",
            "2026-05-16",
            include_weather=True,
        )
        self.assertIn("오늘 서울은 맑고 최고 25 도입니다.", speech)
        self.assertIn("2026 년 5 월 16 일 기관 뉴스 음성 브리핑입니다.", speech)
        self.assertIn("오늘은 주요 기사 1 건을 제목과 핵심 내용 중심으로 전해드리겠습니다.", speech)
        self.assertIn(
            "첫 번째 소식입니다. 병무청 에이 아이, 5 지 공공데이터 2026 년 5 월 16 일 기사.",
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

    def test_negative_watch_active_window(self):
        self.assertTrue(in_active_window(datetime(2026, 5, 19, 8, 0, tzinfo=timezone.utc), 8, 22))
        self.assertTrue(in_active_window(datetime(2026, 5, 19, 21, 59, tzinfo=timezone.utc), 8, 22))
        self.assertFalse(in_active_window(datetime(2026, 5, 19, 22, 0, tzinfo=timezone.utc), 8, 22))
        self.assertFalse(in_active_window(datetime(2026, 5, 19, 7, 59, tzinfo=timezone.utc), 8, 22))

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
        self.assertIn("대표 기사에서는", message)
        self.assertNotIn("example.com에서", message)


if __name__ == "__main__":
    unittest.main()
