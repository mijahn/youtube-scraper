import sys
import types
import unittest


if "yt_dlp" not in sys.modules:
    sys.modules["yt_dlp"] = types.SimpleNamespace()

from download_channel_videos import DownloadLogger


class DownloadLoggerTests(unittest.TestCase):
    def test_known_video_unavailable_messages(self):
        logger = DownloadLogger()
        messages = [
            "Video unavailable",
            "This video is available to members only",
            "This content isn't available in your country",
            "HTTP Error 403: Forbidden",
            "HTTP Error 410: Gone",
            "Sign in to confirm your age",
        ]

        for message in messages:
            logger.error(message)

        self.assertEqual(len(messages), logger.video_unavailable_errors)
        self.assertEqual(0, logger.other_errors)

    def test_other_errors_tracked_separately(self):
        logger = DownloadLogger()
        logger.error("Unexpected network failure")

        self.assertEqual(0, logger.video_unavailable_errors)
        self.assertEqual(1, logger.other_errors)


if __name__ == "__main__":
    unittest.main()
