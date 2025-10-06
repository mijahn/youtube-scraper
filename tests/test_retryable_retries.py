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
        "youtube_fetch_po_token": None,
        "youtube_po_token": [],
        "youtube_player_params": None,
        "no_shorts": False,
        "max": None,
    }
    defaults.update(overrides)
    args = SimpleNamespace(**defaults)
    dc.apply_authentication_defaults(args, environ={})
    return args


def test_download_source_retries_next_client_on_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args()

    monkeypatch.setattr(dc, "DEFAULT_PLAYER_CLIENTS", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "PLAYER_CLIENT_CHOICES", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: set())

    calls = []

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
        download_archive=None,
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
    assert calls[0]["client"] == dc.DEFAULT_PLAYER_CLIENTS[0]
    assert calls[1]["client"] == dc.DEFAULT_PLAYER_CLIENTS[1]
    assert calls[1]["target_video_ids"] == {"retry-id"}


def test_download_source_cycles_on_other_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args()

    monkeypatch.setattr(dc, "DEFAULT_PLAYER_CLIENTS", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "PLAYER_CLIENT_CHOICES", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: set())

    calls = []

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
        download_archive=None,
    ):
        calls.append({"client": client, "urls": tuple(urls), "seen": set(downloaded_ids)})
        if len(calls) == 1:
            downloaded_ids.add("first-id")
            return dc.DownloadAttempt(
                downloaded=1,
                video_unavailable_errors=0,
                other_errors=2,
                retryable_error_ids=set(),
                stopped_due_to_limit=False,
            )
        assert "first-id" in downloaded_ids
        return dc.DownloadAttempt(
            downloaded=0,
            video_unavailable_errors=0,
            other_errors=0,
            retryable_error_ids=set(),
            stopped_due_to_limit=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run_download_attempt)

    dc.download_source(source, args)

    assert len(calls) == 2
    assert calls[0]["client"] == dc.DEFAULT_PLAYER_CLIENTS[0]
    assert calls[1]["client"] == dc.DEFAULT_PLAYER_CLIENTS[1]
    assert calls[0]["seen"] == set()
    assert calls[1]["seen"] == {"first-id"}


def test_download_source_retries_after_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args()

    monkeypatch.setattr(dc, "DEFAULT_PLAYER_CLIENTS", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "PLAYER_CLIENT_CHOICES", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: set())

    calls = []

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
        download_archive=None,
    ):
        calls.append({"client": client, "urls": tuple(urls), "seen": set(downloaded_ids)})
        if len(calls) == 1:
            downloaded_ids.add("first-id")
            return dc.DownloadAttempt(
                downloaded=1,
                video_unavailable_errors=2,
                other_errors=0,
                retryable_error_ids=set(),
                stopped_due_to_limit=False,
            )
        assert "first-id" in downloaded_ids
        return dc.DownloadAttempt(
            downloaded=0,
            video_unavailable_errors=0,
            other_errors=0,
            retryable_error_ids=set(),
            stopped_due_to_limit=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run_download_attempt)

    dc.download_source(source, args)

    assert len(calls) == 2
    assert calls[0]["client"] == dc.DEFAULT_PLAYER_CLIENTS[0]
    assert calls[1]["client"] == dc.DEFAULT_PLAYER_CLIENTS[1]
    assert calls[0]["seen"] == set()
    assert calls[1]["seen"] == {"first-id"}


def test_download_source_prints_summary(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args()

    monkeypatch.setattr(dc, "DEFAULT_PLAYER_CLIENTS", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "PLAYER_CLIENT_CHOICES", ("tv", "web_safari"))
    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: {"vid1", "vid2", "vid3"})

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
        download_archive=None,
    ):
        return dc.DownloadAttempt(
            downloaded=2,
            video_unavailable_errors=0,
            other_errors=0,
            detected_video_ids={"vid1", "vid2", "vid3"},
            downloaded_video_ids={"vid1", "vid2"},
            retryable_error_ids=set(),
            stopped_due_to_limit=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run_download_attempt)

    dc.download_source(source, args)

    captured = capsys.readouterr()
    assert "\033[1;45;97m" in captured.out
    assert "Summary for @Example" in captured.out

    lines = captured.out.splitlines()
    detected_line = next(line for line in lines if "Total videos detected:" in line)
    downloaded_line = next(line for line in lines if "Total videos downloaded:" in line)
    pending_line = next(line for line in lines if "Total videos not downloaded:" in line)

    assert "3" in detected_line
    assert "2" in downloaded_line
    assert "1" in pending_line

def test_download_source_cycles_after_user_selected_client(monkeypatch: pytest.MonkeyPatch) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    primary = dc.DEFAULT_PLAYER_CLIENTS[-1]
    args = make_args(youtube_client=primary)

    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: set())
    monkeypatch.setattr(dc, "PLAYER_CLIENT_CHOICES", tuple(dc.DEFAULT_PLAYER_CLIENTS))

    calls = []

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
        download_archive=None,
    ):
        calls.append(client)
        if len(calls) == 1:
            return dc.DownloadAttempt(
                downloaded=0,
                video_unavailable_errors=0,
                other_errors=1,
                retryable_error_ids=set(),
                stopped_due_to_limit=False,
            )
        return dc.DownloadAttempt(
            downloaded=1,
            video_unavailable_errors=0,
            other_errors=0,
            retryable_error_ids=set(),
            stopped_due_to_limit=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run_download_attempt)

    dc.download_source(source, args)

    fallback_order = [client for client in dc.DEFAULT_PLAYER_CLIENTS if client != primary]
    assert len(calls) >= 2
    assert calls[0] == primary
    assert calls[1] == fallback_order[0]


def test_run_download_attempt_respects_failure_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    args = make_args()

    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            assert urls == ["https://www.youtube.com/watch?v=example"]
            hooks = self.params.get("progress_hooks") or []
            assert hooks, "Expected at least one progress hook"
            hook = hooks[0]
            info = {"id": "video-1", "title": "Example"}
            payload = {
                "status": "error",
                "info_dict": info,
                "error": "HTTP Error 403: Forbidden",
            }
            for _ in range(dc.MAX_FAILURES_PER_CLIENT):
                try:
                    hook(dict(payload))
                except dc.DownloadCancelled:
                    raise

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    attempt = dc.run_download_attempt(
        ["https://www.youtube.com/watch?v=example"],
        args,
        player_client="tv",
        max_total=None,
        downloaded_ids=set(),
        download_archive=None,
    )

    assert attempt.downloaded == 0
    assert attempt.failure_limit_reached is True
    assert attempt.failure_count == dc.MAX_FAILURES_PER_CLIENT
    assert "video-1" in attempt.retryable_error_ids


def test_run_download_attempt_logger_errors_trigger_failure_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = make_args()

    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            assert urls == ["https://www.youtube.com/watch?v=example"]
            logger = self.params.get("logger")
            assert isinstance(logger, dc.DownloadLogger)
            for idx in range(dc.MAX_FAILURES_PER_CLIENT):
                logger.set_video(f"video-{idx}")
                try:
                    logger.error("Requested format is not available")
                except dc.DownloadCancelled:
                    raise
                finally:
                    logger.set_video(None)

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    attempt = dc.run_download_attempt(
        ["https://www.youtube.com/watch?v=example"],
        args,
        player_client="web",
        max_total=None,
        downloaded_ids=set(),
        download_archive=None,
    )

    assert attempt.downloaded == 0
    assert attempt.other_errors == dc.MAX_FAILURES_PER_CLIENT
    assert attempt.failure_limit_reached is True
    assert attempt.failure_count == dc.MAX_FAILURES_PER_CLIENT
    assert not attempt.retryable_error_ids
