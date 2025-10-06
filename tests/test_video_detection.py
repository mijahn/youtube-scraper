from __future__ import annotations

import re
from types import SimpleNamespace
from typing import Optional

import pytest

import download_channel_videos as dc


def make_args(**overrides):
    defaults = {
        "output": "downloads",
        "skip_thumbs": False,
        "skip_subtitles": False,
        "archive": "archive.txt",
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


def test_collect_video_ids_from_info_handles_nested_entries() -> None:
    info = {
        "_type": "playlist",
        "entries": [
            {"id": "video-1"},
            {"_type": "playlist", "entries": [{"id": "video-2"}, None]},
            None,
            {"_type": "url", "entries": [{"id": "video-3"}]},
        ],
    }

    dest: set[str] = set()
    dc._collect_video_ids_from_info(info, dest)

    assert dest == {"video-1", "video-2", "video-3"}


def test_collect_all_video_ids_uses_metadata_without_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    args = make_args()
    captured_opts = {}

    class DummyYDL:
        def __init__(self, opts):
            captured_opts.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def extract_info(self, url, download=False):
            assert download is False
            return {"_type": "playlist", "entries": [{"id": "a"}, {"id": "b"}]}

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", DummyYDL)

    ids = dc.collect_all_video_ids(["https://example.com/channel"], args, None)

    assert ids == {"a", "b"}
    assert captured_opts["skip_download"] is True
    assert captured_opts["progress_hooks"] == []
    assert "download_archive" not in captured_opts
    assert "match_filter" not in captured_opts


def test_download_source_summary_includes_metadata_count(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")
    args = make_args()

    monkeypatch.setattr(dc, "DEFAULT_PLAYER_CLIENTS", ("tv",))
    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: {"one", "two", "three"})

    def fake_run(*_a, **_kw):
        return dc.DownloadAttempt(
            downloaded=0,
            video_unavailable_errors=0,
            other_errors=0,
            detected_video_ids=set(),
            downloaded_video_ids=set(),
            retryable_error_ids=set(),
            stopped_due_to_limit=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run)

    dc.download_source(source, args)

    out = capsys.readouterr().out
    cleaned = re.sub(r"\x1b\[[0-9;]*m", "", out)
    match = re.search(r"Total videos detected:\s*(\d+)", cleaned)
    assert match
    assert match.group(1) == "3"


def test_download_logger_extracts_video_id_from_error() -> None:
    detected: set[str] = set()
    failures: list[Optional[str]] = []

    logger = dc.DownloadLogger(lambda vid: failures.append(vid))
    logger.set_detection_callback(lambda vid: detected.add(vid))
    logger.set_context("https://example.com", "web")

    logger.error("ERROR: [youtube] abcdefghijk: This video is unavailable")

    assert detected == {"abcdefghijk"}
    assert failures[-1] == "abcdefghijk"
    assert logger.video_unavailable_errors == 1


def test_download_logger_handles_warning_failures() -> None:
    failures: list[Optional[str]] = []
    logger = dc.DownloadLogger(lambda vid: failures.append(vid))

    logger.warning("WARNING: [youtube] ZYXWVUTSRQP: Sign in to confirm your age")

    assert failures[-1] == "ZYXWVUTSRQP"
    assert logger.video_unavailable_errors == 1
