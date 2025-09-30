import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
from download_channel_videos import DownloadLogger


@pytest.mark.parametrize(
    "message",
    [
        "This video is members-only content.",
        "Please sign in to confirm your age.",
        "Upgrade to Premium to continue.",
        "HTTP Error 403: Forbidden",
        "Missing PO token for playback",
    ],
)
def test_auth_required_keywords_are_classified(message):
    logger = DownloadLogger()
    logger.error(message)

    assert logger.auth_required_errors == 1
    assert logger.video_unavailable_errors == 0
    assert logger.other_errors == 0


def test_mixed_messages_affect_counters():
    logger = DownloadLogger()
    logger.error("Video unavailable")
    logger.error("Unexpected failure")
    logger.error("members-only content")

    assert logger.video_unavailable_errors == 1
    assert logger.other_errors == 1
    assert logger.auth_required_errors == 1
