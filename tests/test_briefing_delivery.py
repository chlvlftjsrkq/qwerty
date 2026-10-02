import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kakao_mma_news.briefing_delivery import (
    bundle_path, check_bundle_files, last_json_object, load_state, prepare_briefing,
    send_saved_briefing, status_catalog, validate_delivery_result,
)
from kakao_mma_news.delivery_control import kakao_delivery_lock


class BriefingDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.outbox = self.root / "outbox"
        self.source = self.root / "runner"
        self.source.mkdir()
        self.summary = self.source / "summary.md"
        self.summary.write_text("병무청 뉴스 브리핑\n오늘의 주요 소식입니다.", encoding="utf-8")
        self.image = self.source / "image.png"
        self.image.write_bytes(b"image contents")
        self.audio = self.source / "audio.mp3"
        self.audio.write_bytes(b"audio contents")
        self.podcast = self.source / "podcast.txt"
        self.podcast.write_text("음성요약 듣기\nhttps://example.com/podcast", encoding="utf-8")
        pause = patch("kakao_mma_news.briefing_delivery.is_kakao_delivery_paused", return_value=False)
        pause.start()
        self.addCleanup(pause.stop)

    def prepare(self):
        result = prepare_briefing(episode_id="2026-10-01-병무청", date_label="2026-10-01", agency="병무청", room="test", summary=self.summary, image=self.image, audio=self.audio, podcast_message=self.podcast, outbox=self.outbox)
        return bundle_path(result["bundle_id"], self.outbox)

    def success(self, command, directory, step_id):
        if step_id == "image":
            return {"dialog_closed": True}
        if step_id == "notice":
            return {"notice_registered": True, "comment_registered": True}
        return {"chunks": 1, "sent": [{"success": True}]}

    def test_saved_files_survive_runner_cleanup(self):
        path = self.prepare()
        for source in self.source.iterdir():
            source.unlink()
        check_bundle_files(path, load_state(path))
        self.assertEqual("ready", load_state(path)["status"])
        self.assertEqual(1, len(status_catalog(self.outbox)["pending"]))

    def test_failed_image_is_retried_before_text_without_generation(self):
        path = self.prepare()
        calls = []
        def fail_image(command, directory, step_id):
            calls.append(step_id)
            raise RuntimeError("attachment failed")
        result = send_saved_briefing(path, self.root, sender=fail_image)
        self.assertEqual(["image"], calls)
        self.assertEqual("failed", result["status"])
        commands = []
        def send(command, directory, step_id):
            calls.append(step_id)
            commands.append(command)
            return self.success(command, directory, step_id)
        result = send_saved_briefing(path, self.root, sender=send)
        self.assertEqual(["image", "image", "summary-01", "notice", "podcast"], calls)
        self.assertEqual("completed", result["status"])
        self.assertTrue(all(Path(command[1]).name in {"post_kakao_image_attach.py", "post_summary_mcp.py", "publish_kakao_notice.py"} for command in commands))
        self.assertEqual([], status_catalog(self.outbox)["pending"])

    def test_partial_text_resumes_at_unsent_chunk(self):
        self.summary.write_text("완성된 소식입니다. " * 800, encoding="utf-8")
        path = self.prepare()
        original = load_state(path)
        chunks = [step for step in original["steps"] if step["stage"] == "summary"]
        self.assertGreater(len(chunks), 1)
        for step in chunks:
            self.assertLessEqual(len((path.parent / step["file"]).read_text(encoding="utf-8")), 3000)
        def fail_second(command, directory, step_id):
            if step_id == "summary-02":
                raise RuntimeError("text failed")
            return self.success(command, directory, step_id)
        send_saved_briefing(path, self.root, sender=fail_second)
        calls = []
        def retry(command, directory, step_id):
            calls.append(step_id)
            return self.success(command, directory, step_id)
        result = send_saved_briefing(path, self.root, sender=retry)
        self.assertEqual("summary-02", calls[0])
        self.assertNotIn("image", calls)
        self.assertNotIn("summary-01", calls)
        self.assertEqual("completed", result["status"])

    def test_paused_or_skipped_is_not_recorded_as_sent(self):
        path = self.prepare()
        with patch("kakao_mma_news.briefing_delivery.is_kakao_delivery_paused", return_value=True):
            result = send_saved_briefing(path, self.root, sender=self.success)
        self.assertEqual("paused", result["status"])
        self.assertTrue(all(step["status"] == "pending" for step in result["steps"]))
        result = send_saved_briefing(path, self.root, sender=lambda *args: {"skipped": True, "delivery_paused": True})
        self.assertEqual("failed", result["status"])
        self.assertFalse(any(step["status"] == "sent" for step in result["steps"]))

    def test_notice_retry_reposts_correct_briefing_but_not_image_or_audio(self):
        path = self.prepare()
        def fail_notice(command, directory, step_id):
            if step_id == "notice":
                raise RuntimeError("notice failed")
            return self.success(command, directory, step_id)
        result = send_saved_briefing(path, self.root, sender=fail_notice)
        self.assertEqual("failed", result["status"])
        self.assertEqual("sent", result["steps"][-1]["status"])
        calls = []
        def retry(command, directory, step_id):
            calls.append(step_id)
            return self.success(command, directory, step_id)
        result = send_saved_briefing(path, self.root, sender=retry)
        self.assertEqual(["notice-repost-summary-01", "notice"], calls)
        self.assertEqual("completed", result["status"])

    def test_changed_saved_file_blocks_delivery(self):
        path = self.prepare()
        (path.parent / "summary.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "changed"):
            send_saved_briefing(path, self.root, sender=self.success)
        self.assertFalse(status_catalog(self.outbox)["pending"][0]["valid"])

    def test_missing_generated_file_does_not_create_incomplete_bundle(self):
        self.audio.unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()
        self.assertEqual([], list(self.outbox.iterdir()))

    def test_completed_bundle_is_not_sent_again(self):
        path = self.prepare()
        send_saved_briefing(path, self.root, sender=self.success)
        def unexpected(*args):
            self.fail("Completed briefing should not be sent again")
        self.assertEqual("completed", send_saved_briefing(path, self.root, sender=unexpected)["status"])

    def test_new_completion_supersedes_older_failure_for_same_room_and_day(self):
        old_path = self.prepare()
        new_path = self.prepare()
        send_saved_briefing(new_path, self.root, sender=self.success)
        self.assertEqual("superseded", load_state(old_path)["status"])
        self.assertEqual([], status_catalog(self.outbox)["pending"])

    def test_success_exit_alone_does_not_count_as_delivery(self):
        for result in ({}, {"chunks": 1, "sent": []}, {"chunks": 1, "sent": [{"success": False}]}):
            with self.assertRaises(RuntimeError):
                validate_delivery_result("summary", result)
        self.assertEqual({"sent": []}, last_json_object('progress\n{"phase":"guard"}\n' + json.dumps({"sent": []})))
        with self.assertRaises(ValueError):
            bundle_path("../outside", self.outbox)

    def test_child_sender_reuses_parent_ui_lock(self):
        environment = os.environ.copy()
        environment["QWERTY_KAKAO_LOCK_PARENT"] = str(os.getpid())
        with kakao_delivery_lock():
            result = subprocess.run(
                [sys.executable, "-c", "from kakao_mma_news.delivery_control import kakao_delivery_lock\nwith kakao_delivery_lock():\n print('child acquired')"],
                env=environment, capture_output=True, text=True, timeout=5,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("child acquired", result.stdout)


if __name__ == "__main__":
    unittest.main()
