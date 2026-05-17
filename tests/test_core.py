import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from kakao_mma_news.kakao import split_message
from kakao_mma_news.news import Article, dedupe_articles, matches_required_terms, strip_html
from kakao_mma_news.summarize import (
    _load_json_object,
    _prepend_weather_summary,
    _render_codex_summary,
    summarize_heuristic,
)
from kakao_mma_news.weather import build_weather_summary
from scripts.build_podcast_audio import markdown_to_speech


class CoreTests(unittest.TestCase):
    def test_strip_html(self):
        self.assertEqual(strip_html("<b>병무청</b>&nbsp;뉴스<br>요약"), "병무청 뉴스 요약")

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
            '```json\n{"items":[{"title":"전북지방병무청 경진대회","summary":"업무 인수인계 개선을 위한 행사다.","opinion":"행정 품질 개선 효과를 확인할 필요가 있다.","source":"example.com","url":"https://example.com/news"}],"excluded_note":"","one_line":"지방병무청 업무 개선 소식이 중심이었다."}\n```'
        )
        summary = _render_codex_summary(article.published_date_kst, data, [article], "병무청")
        self.assertIn("🪖 2026-05-15 병무청 뉴스 브리핑", summary)
        self.assertIn("# 1️⃣ 전북지방병무청 경진대회", summary)
        self.assertIn("Opinion: 행정 품질 개선 효과를 확인할 필요가 있다.", summary)
        self.assertIn("Source: example.com / https://example.com/news", summary)
        self.assertNotIn("네이버 뉴스 기준으로 확인한", summary)

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

        self.assertIn("🌤️ 오늘 세종은 기온이 28도까지 오를 정도로 따뜻하고", summary)
        self.assertIn("미세먼지 지수는 61 (보통)", summary)
        self.assertIn("초미세먼지는 26 (보통)", summary)
        self.assertIn("가벼운 외출에 딱 좋은 날이에요", summary)
        self.assertIn("수분 충분히 챙기고", summary)

    def test_podcast_speech_cleans_markdown(self):
        speech = markdown_to_speech(
            "🌤️ 오늘 서울은 맑고 최고 25도입니다.\n"
            "# 1️⃣ 병무청 공공데이터 기사\n"
            "병무청이 공공데이터 행사를 열었다. 🎯\n"
            "Opinion: 공식 안내 확인이 필요하다.\n"
            "Source: example.com / https://example.com/news\n",
            "2026-05-16",
            include_weather=True,
        )
        self.assertIn("오늘 서울은 맑고 최고 25도입니다.", speech)
        self.assertIn("첫 번째 소식입니다. 병무청 공공데이터 기사.", speech)
        self.assertIn("주요 내용은 병무청이 공공데이터 행사를 열었다", speech)
        self.assertNotIn("공식 안내 확인이 필요하다", speech)
        self.assertNotIn("Source:", speech)
        self.assertNotIn("️⃣", speech)
        self.assertNotIn("...", speech)
        self.assertNotIn("…", speech)


if __name__ == "__main__":
    unittest.main()
