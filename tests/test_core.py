import unittest
from datetime import datetime, timezone

from kakao_mma_news.kakao import split_message
from kakao_mma_news.news import Article, dedupe_articles, matches_required_terms, strip_html
from kakao_mma_news.summarize import _load_json_object, _render_codex_summary, summarize_heuristic


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
        summary = summarize_heuristic(article.published_date_kst, [article])
        self.assertIn("오늘의 병무청 뉴스 톡", summary)
        self.assertIn("# 1️⃣ 서울지방병무청 행사", summary)
        self.assertIn("Opinion:", summary)
        self.assertIn("오늘 한 줄 요약", summary)

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
        summary = _render_codex_summary(article.published_date_kst, data, [article])
        self.assertIn("🪖 2026-05-15 병무청 뉴스 브리핑", summary)
        self.assertIn("# 1️⃣ 전북지방병무청 경진대회", summary)
        self.assertIn("Opinion: 행정 품질 개선 효과를 확인할 필요가 있다.", summary)
        self.assertIn("Source: example.com / https://example.com/news", summary)


if __name__ == "__main__":
    unittest.main()
