from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kakao_mma_news.delivery_control import (
    delivery_status,
    is_kakao_delivery_paused,
    pause_file_path,
    pause_kakao_delivery,
    resume_kakao_delivery,
)
from kakao_mma_news.kakao import post_to_kakao


class DeliveryControlTests(unittest.TestCase):
    def test_pause_and_resume_use_configured_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "delivery.pause"
            with patch.dict(os.environ, {"KAKAO_DELIVERY_PAUSE_FILE": str(marker)}):
                self.assertEqual(pause_file_path(), marker)
                self.assertFalse(is_kakao_delivery_paused())

                pause_kakao_delivery()
                self.assertTrue(is_kakao_delivery_paused())
                self.assertEqual(delivery_status()["delivery_status"], "paused")

                resume_kakao_delivery()
                self.assertFalse(is_kakao_delivery_paused())
                self.assertEqual(delivery_status()["delivery_status"], "enabled")

    def test_direct_kakao_post_stops_before_touching_the_ui(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            marker = Path(temporary_directory) / "delivery.pause"
            with patch.dict(os.environ, {"KAKAO_DELIVERY_PAUSE_FILE": str(marker)}):
                pause_kakao_delivery()
                self.assertFalse(post_to_kakao(object(), "test message"))


if __name__ == "__main__":
    unittest.main()
