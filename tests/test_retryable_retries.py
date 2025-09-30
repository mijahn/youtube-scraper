"""Tests for retryable HTTP 403 handling in download_source."""

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
        "youtube_client": None,
        "no_shorts": False,
        "max": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_download_source_retries_next_client_on_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args()

    calls = []

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
    ):
        calls.append(
            {
                "client": client,
                "urls": tuple(urls),
                "target_video_ids": None if target_video_ids is None else set(target_video_ids),
            }
        )
        if len(calls) == 1:
            assert target_video_ids is None
            return dc.DownloadAttempt(
                downloaded=0,
                video_unavailable_errors=0,
                other_errors=0,
                retryable_error_ids={"retry-id"},
                stopped_due_to_limit=False,
            )

        assert target_video_ids == {"retry-id"}
        downloaded_ids.add("retry-id")
        return dc.DownloadAttempt(
            downloaded=1,
            video_unavailable_errors=0,
            other_errors=0,
            retryable_error_ids=set(),
            stopped_due_to_limit=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run_download_attempt)

    dc.download_source(source, args)

    assert len(calls) == 2
    assert calls[0]["client"] == "web"
    assert calls[1]["client"] == "android"
    assert calls[1]["target_video_ids"] == {"retry-id"}
