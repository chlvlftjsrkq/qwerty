from __future__ import annotations

import unittest

from kakao_mma_news.kakao_ui_guard import file_icon_point


class KakaoImageAttachTests(unittest.TestCase):
    def test_file_icon_point_follows_room_left_edge(self) -> None:
        self.assertEqual((1078, 814), file_icon_point((991, 177, 1371, 839)))

    def test_file_icon_point_stays_inside_resized_rooms(self) -> None:
        for rect in ((10, 20, 390, 682), (50, 60, 650, 820), (0, 0, 900, 1000)):
            with self.subTest(rect=rect):
                point = file_icon_point(rect)
                left, top, right, bottom = rect
                self.assertGreater(point[0], left)
                self.assertLess(point[0], right)
                self.assertGreater(point[1], top)
                self.assertLess(point[1], bottom)

    def test_tiny_room_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "too small"):
            file_icon_point((0, 0, 100, 100))


if __name__ == "__main__":
    unittest.main()
