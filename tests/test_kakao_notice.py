from __future__ import annotations

import unittest
from unittest.mock import call, patch

from PIL import Image, ImageDraw

from scripts.publish_kakao_notice import (
    Box,
    find_menu_separator_rows,
    find_notice_menu_center,
    find_latest_notice_action_band,
    find_latest_outgoing_bubble,
    normalize_control_text,
    notice_marker,
    prepare_notice_text,
    write_notice_comment_input,
)


class KakaoNoticeTests(unittest.TestCase):
    def test_prepare_notice_text_requires_one_complete_kakao_message(self) -> None:
        self.assertEqual("브리핑 본문", prepare_notice_text("  브리핑 본문\n", 3000))
        with self.assertRaisesRegex(RuntimeError, "split into"):
            prepare_notice_text("첫 문단입니다.\n\n" + ("긴 내용 " * 900), 300)

    def test_notice_marker_uses_first_nonempty_line(self) -> None:
        self.assertEqual("🪖 2026-09-08 병무청 뉴스 브리핑", notice_marker("\n🪖 2026-09-08 병무청 뉴스 브리핑\n본문"))

    def test_control_text_normalizes_windows_line_endings(self) -> None:
        self.assertEqual("첫 줄\n둘째 줄", normalize_control_text("  첫 줄\r\n둘째 줄\r\n"))

    @patch("scripts.publish_kakao_notice.time.sleep")
    @patch("scripts.publish_kakao_notice.wait_for_value", return_value=True)
    @patch("scripts.publish_kakao_notice.window_box", return_value=Box(10, 20, 210, 80))
    @patch("scripts.publish_kakao_notice.pyperclip")
    @patch("scripts.publish_kakao_notice.pyautogui")
    @patch("scripts.publish_kakao_notice.win32gui")
    def test_notice_comment_uses_focused_clipboard_paste(
        self,
        win32gui,
        pyautogui,
        pyperclip,
        _window_box,
        _wait_for_value,
        _sleep,
    ) -> None:
        pyperclip.paste.return_value = "기존 클립보드"

        write_notice_comment_input(101, 202, "음성요약\nhttps://example.test", 8.0)

        win32gui.SetForegroundWindow.assert_called_once_with(101)
        pyautogui.click.assert_called_once_with(110, 50)
        self.assertEqual(
            [call("ctrl", "a"), call("ctrl", "v")],
            pyautogui.hotkey.call_args_list,
        )
        pyautogui.press.assert_called_once_with("backspace")
        self.assertEqual(
            [call("음성요약\nhttps://example.test"), call("기존 클립보드")],
            pyperclip.copy.call_args_list,
        )

    def test_latest_outgoing_bubble_survives_window_resize(self) -> None:
        for width, height in ((380, 662), (520, 760)):
            with self.subTest(width=width, height=height):
                image = Image.new("RGB", (width, height), (186, 206, 224))
                draw = ImageDraw.Draw(image)
                chat = Box(1, 89, width - 1, height - 118)
                draw.rounded_rectangle((width - 250, 180, width - 25, 225), radius=8, fill=(254, 229, 0))
                expected = Box(width - 310, chat.bottom - 65, width - 25, chat.bottom - 20)
                draw.rounded_rectangle(
                    (expected.left, expected.top, expected.right - 1, expected.bottom - 1),
                    radius=8,
                    fill=(254, 229, 0),
                )

                found = find_latest_outgoing_bubble(image, chat)
                self.assertIsNotNone(found)
                self.assertGreaterEqual(found.bottom, expected.bottom - 3)
                self.assertGreaterEqual(found.right, width - 30)

    def test_latest_notice_action_prefers_right_aligned_bottom_card(self) -> None:
        image = Image.new("RGB", (520, 760), (186, 206, 224))
        draw = ImageDraw.Draw(image)
        chat = Box(1, 89, 519, 642)
        draw.rectangle((15, 210, 230, 270), fill=(255, 255, 255))
        draw.rectangle((283, 360, 492, 420), fill=(255, 255, 255))
        draw.rectangle((283, 524, 492, 584), fill=(255, 255, 255))

        found = find_latest_notice_action_band(image, chat)
        self.assertIsNotNone(found)
        self.assertGreaterEqual(found.top, 520)
        self.assertGreaterEqual(found.right, 490)

    def test_notice_menu_center_uses_separator_structure(self) -> None:
        for height, separators in ((351, (33, 164, 199, 282, 317)), (386, (33, 164, 199, 282, 317, 352))):
            with self.subTest(height=height):
                image = Image.new("RGB", (112, height), (255, 255, 255))
                draw = ImageDraw.Draw(image)
                for row in separators:
                    draw.line((13, row, 98, row), fill=(242, 242, 242), width=1)

                self.assertEqual(list(separators), find_menu_separator_rows(image))
                self.assertEqual((56, 182), find_notice_menu_center(image))


if __name__ == "__main__":
    unittest.main()
