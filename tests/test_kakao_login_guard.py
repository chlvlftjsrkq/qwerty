from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import kakao_login_guard
import post_summary_mcp


class KakaoLoginGuardTests(unittest.TestCase):
    def test_try_open_room_reuses_an_existing_chat_window(self) -> None:
        controller = Mock()
        controller.find_chat_window.return_value = 12345

        with patch.object(kakao_login_guard, "controller", controller):
            result = kakao_login_guard.try_open_room("AI 병무청 데일리 모닝톡")

        self.assertTrue(result["success"])
        self.assertTrue(result["already_open"])
        self.assertEqual(result["hwnd"], 12345)
        controller.search_and_open_room.assert_not_called()

    def test_try_open_room_searches_when_no_chat_window_is_open(self) -> None:
        controller = Mock()
        controller.find_chat_window.return_value = None
        controller.search_and_open_room.return_value = {"success": True, "hwnd": 67890}

        with patch.object(kakao_login_guard, "controller", controller):
            result = kakao_login_guard.try_open_room("AI 병무청 데일리 모닝톡")

        self.assertEqual(result, {"success": True, "hwnd": 67890})
        controller.search_and_open_room.assert_called_once_with("AI 병무청 데일리 모닝톡")

    def test_ready_check_can_detect_without_opening_the_room(self) -> None:
        with (
            patch.object(kakao_login_guard, "launch_kakao_if_needed"),
            patch.object(kakao_login_guard, "find_login_window", return_value=None),
            patch.object(kakao_login_guard, "find_open_room", return_value=None),
            patch.object(kakao_login_guard, "ensure_chat_tab", return_value=True),
            patch.object(kakao_login_guard, "try_open_room") as try_open_room,
        ):
            result = kakao_login_guard.ensure_kakao_ready(
                room="AI 병무청 데일리 모닝톡",
                open_room=False,
            )

        self.assertTrue(result["chat_tab_ready"])
        self.assertFalse(result["room_open_requested"])
        self.assertIsNone(result["room_result"])
        try_open_room.assert_not_called()


class OpenRoomDeliveryTests(unittest.TestCase):
    def test_open_room_fast_path_sends_without_starting_mcp_session(self) -> None:
        args = SimpleNamespace(room="test", verify=False)
        controller = Mock()
        controller.send_message_to_room.return_value = {
            "success": True,
            "message": "sent",
        }

        with (
            patch("kakao_mcp.controller", controller),
            patch.object(post_summary_mcp, "is_kakao_delivery_paused", return_value=False),
            patch("builtins.print"),
        ):
            result = post_summary_mcp.post_chunks_to_open_room(
                args,
                Path("summary.md"),
                ["first", "second"],
            )

        self.assertEqual(result, 0)
        self.assertEqual(
            controller.send_message_to_room.call_args_list,
            [
                call("test", "(1/2)\nfirst"),
                call("test", "(2/2)\nsecond"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
