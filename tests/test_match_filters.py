"""Tests for match filter combination and download logger classification."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import download_channel_videos as dc


def make_args(**overrides):
    defaults = {
        "output": "downloads",
        "skip_thumbs": False,
        "skip_subtitles": False,
        "archive": None,
        "rate_limit": None,
        "concurrency": None,
        "since": None,
        "until": None,
        "cookies_from_browser": None,
        "sleep_requests": None,
        "sleep_interval": None,
        "max_sleep_interval": None,
        "allow_restricted": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_build_ydl_options_combines_filters(tmp_path):
    args = make_args(output=str(tmp_path))
    logger = dc.DownloadLogger()
    called = []

    def extra_filter(info_dict):
        called.append(info_dict.get("id"))
        if info_dict.get("id") == "duplicate":
            return "duplicate video"
        return None

    opts = dc.build_ydl_options(args, player_client=None, logger=logger, hook=lambda _: None, additional_filters=[extra_filter])

    match_filter = opts.get("match_filter")
    assert match_filter is not None

    # Extra filter short-circuits duplicates.
    assert match_filter({"id": "duplicate"}) == "duplicate video"

    # Restricted videos are still flagged even when the extra filter does not match first.
    reason = match_filter({"id": "restricted", "availability": "premium_only"})
    assert "premium" in reason.lower()

    # Non-filtered videos pass through without raising.
    assert match_filter({"id": "ok"}) is None

    # Ensure the extra filter was invoked for the checks above.
    assert called.count("duplicate") == 1


@pytest.mark.parametrize(
    "message, expected_unavailable",
    [
        ("Video unavailable", True),
        ("Sign in to confirm your age", True),
        ("This video is private", True),
        ("Some other random failure", False),
    ],
)
def test_download_logger_classification(message: str, expected_unavailable: bool) -> None:
    logger = dc.DownloadLogger()
    logger.error(message)
    if expected_unavailable:
        assert logger.video_unavailable_errors == 1
        assert logger.other_errors == 0
    else:
        assert logger.video_unavailable_errors == 0
        assert logger.other_errors == 1

    # Exceptions should be classified the same way.
    logger.record_exception(RuntimeError(message))
    if expected_unavailable:
        assert logger.video_unavailable_errors == 2
        assert logger.other_errors == 0
    else:
        assert logger.video_unavailable_errors == 0
        assert logger.other_errors == 2
