from __future__ import annotations

import unittest

from kakao_mma_news.kakao_ui_guard import attachment_points


class KakaoImageAttachTests(unittest.TestCase):
    def test_attachment_points_follow_room_right_edge(self) -> None:
        file_point, send_point = attachment_points((991, 177, 1371, 839))

        self.assertEqual((1078, 814), file_point)
        self.assertEqual((1336, 815), send_point)

    def test_send_point_stays_inside_resized_rooms(self) -> None:
        for rect in ((10, 20, 390, 682), (50, 60, 650, 820), (0, 0, 900, 1000)):
            with self.subTest(rect=rect):
                _file_point, send_point = attachment_points(rect)
                left, top, right, bottom = rect
                self.assertGreater(send_point[0], left)
                self.assertLess(send_point[0], right)
                self.assertGreater(send_point[1], top)
                self.assertLess(send_point[1], bottom)

    def test_tiny_room_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "too small"):
            attachment_points((0, 0, 100, 100))


if __name__ == "__main__":
    unittest.main()
