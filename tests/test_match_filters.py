"""Tests for match filter combination and download logger classification."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
        "youtube_fetch_po_token": None,
        "youtube_po_token": [],
        "youtube_player_params": None,
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

    opts = dc.build_ydl_options(
        args,
        player_client=None,
        logger=logger,
        hook=lambda _: None,
        additional_filters=[extra_filter],
    )

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


def test_build_ydl_options_includes_youtube_specific_args(tmp_path):
    args = make_args(
        output=str(tmp_path),
        youtube_fetch_po_token="always",
        youtube_po_token=["web.gvs+TOKEN"],
        youtube_player_params="8AEB",
    )
    logger = dc.DownloadLogger()

    opts = dc.build_ydl_options(
        args,
        player_client="web_safari",
        logger=logger,
        hook=lambda _: None,
        additional_filters=None,
    )

    extractor_args = opts.get("extractor_args")
    assert extractor_args is not None
    youtube_args = extractor_args.get("youtube")
    assert youtube_args is not None
    assert youtube_args["player_client"] == ["web_safari"]
    assert youtube_args["fetch_pot"] == ["always"]
    assert youtube_args["po_token"] == ["web.gvs+TOKEN"]
    assert youtube_args["player_params"] == ["8AEB"]

@pytest.mark.parametrize(
    "message, expected",
    [
        ("Video unavailable", "unavailable"),
        ("Sign in to confirm your age", "unavailable"),
        ("This video is private", "unavailable"),
        ("Some other random failure", "other"),
        ("Channel XYZ does not have a Shorts tab", "ignored"),
    ],
)
def test_download_logger_classification(message: str, expected: str) -> None:
    logger = dc.DownloadLogger()
    logger.error(message)
    if expected == "unavailable":
        assert logger.video_unavailable_errors == 1
        assert logger.other_errors == 0
    elif expected == "ignored":
        assert logger.video_unavailable_errors == 0
        assert logger.other_errors == 0
    else:
        assert logger.video_unavailable_errors == 0
        assert logger.other_errors == 1

    # Exceptions should be classified the same way.
    logger.record_exception(RuntimeError(message))
    if expected == "unavailable":
        assert logger.video_unavailable_errors == 2
        assert logger.other_errors == 0
    elif expected == "ignored":
        assert logger.video_unavailable_errors == 0
        assert logger.other_errors == 0
    else:
        assert logger.video_unavailable_errors == 0
        assert logger.other_errors == 2


def test_download_logger_retryable_errors() -> None:
    logger = dc.DownloadLogger()
    logger.set_video("abc123def45")
    logger.error("HTTP Error 403: Forbidden")
    assert logger.retryable_error_ids == {"abc123def45"}
    assert logger.other_errors == 0

    logger.set_video("abc123def45")
    logger.record_exception(RuntimeError("HTTP Error 403: Forbidden"))
    assert logger.retryable_error_ids == {"abc123def45"}
    assert logger.other_errors == 0

    logger.set_video("po_token_video")
    logger.error("PO Token Required for playback")
    assert logger.retryable_error_ids == {"abc123def45", "po_token_video"}
    assert logger.other_errors == 0
