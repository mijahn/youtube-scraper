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
        "failure_limit": dc.DEFAULT_FAILURE_LIMIT,
    }
    defaults.update(overrides)
    args = SimpleNamespace(**defaults)
    dc.apply_authentication_defaults(args, environ={})
    return args


def test_load_download_archive_reads_existing_ids(tmp_path):
    archive = tmp_path / "download-archive.txt"
    archive.write_text("abc123\n\n# comment\nxyz789\n", encoding="utf-8")

    entries = dc._load_download_archive(str(archive))
    assert entries == {"abc123", "xyz789"}

    missing = tmp_path / "missing.txt"
    assert dc._load_download_archive(str(missing)) == set()


def test_run_download_attempt_appends_to_archive(monkeypatch: pytest.MonkeyPatch, tmp_path):
    archive_path = tmp_path / "archive.txt"
    args = make_args(output=str(tmp_path), archive=str(archive_path))

    appended = []

    def fake_append(path, video_id):
        appended.append((path, video_id))

    monkeypatch.setattr(dc, "_append_to_download_archive", fake_append)

    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            hook = self.params["progress_hooks"][0]
            info = {"id": "fresh-video", "title": "Example"}
            hook({"status": "downloading", "info_dict": info})
            hook({"status": "finished", "info_dict": info})

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    attempt = dc.run_download_attempt(
        ["https://www.youtube.com/watch?v=example"],
        args,
        player_client=None,
        max_total=None,
        downloaded_ids=set(),
    )

    assert attempt.downloaded == 1
    assert appended == [(str(archive_path), "fresh-video")]


def test_run_download_attempt_skips_seen_ids(monkeypatch: pytest.MonkeyPatch, tmp_path):
    args = make_args(output=str(tmp_path), archive=str(tmp_path / "archive.txt"))
    seen_ids = {"existing"}

    monkeypatch.setattr(dc, "_append_to_download_archive", lambda *a, **k: None)

    class FakeYoutubeDL:
        def __init__(self, params):
            self.params = params
            self.match_filter = params.get("match_filter")

        def __enter__(self):
            assert self.match_filter is not None
            reason = self.match_filter({"id": "existing"})
            assert reason and "already" in reason.lower()
            assert self.match_filter({"id": "new"}) is None
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def download(self, urls):
            # Skip actual downloads; the match filter should prevent them.
            return None

    monkeypatch.setattr(dc.yt_dlp, "YoutubeDL", FakeYoutubeDL)

    attempt = dc.run_download_attempt(
        ["https://www.youtube.com/watch?v=example"],
        args,
        player_client=None,
        max_total=None,
        downloaded_ids=seen_ids,
    )

    assert attempt.downloaded == 0
    assert "existing" in seen_ids


def test_download_source_loads_archive_and_updates(monkeypatch: pytest.MonkeyPatch, tmp_path):
    archive_path = tmp_path / "archive.txt"
    archive_path.write_text("seen-id\n", encoding="utf-8")

    args = make_args(output=str(tmp_path), archive=str(archive_path))
    source = dc.Source(dc.SourceType.CHANNEL, "https://www.youtube.com/@Example")

    monkeypatch.setattr(dc, "collect_all_video_ids", lambda *a, **k: [])

    appended = []

    def fake_append(path, video_id):
        appended.append((path, video_id))

    monkeypatch.setattr(dc, "_append_to_download_archive", fake_append)

    writes = []

    def fake_write(path, video_ids):
        writes.append((path, set(video_ids)))

    monkeypatch.setattr(dc, "_write_download_archive", fake_write)

    captured_sets = []

    def fake_run_download_attempt(
        urls,
        args_,
        client,
        max_total,
        downloaded_ids,
        target_video_ids=None,
        failure_limit=dc.DEFAULT_FAILURE_LIMIT,
    ):
        captured_sets.append(set(downloaded_ids))
        downloaded_ids.add("fresh-id")
        fake_append(args_.archive, "fresh-id")
        return dc.DownloadAttempt(
            downloaded=1,
            video_unavailable_errors=0,
            other_errors=0,
            detected_video_ids={"fresh-id"},
            downloaded_video_ids={"fresh-id"},
            retryable_error_ids=set(),
            stopped_due_to_limit=True,
            failure_count=0,
            failure_limit_reached=False,
        )

    monkeypatch.setattr(dc, "run_download_attempt", fake_run_download_attempt)

    dc.download_source(source, args)

    assert captured_sets and "seen-id" in captured_sets[0]
    assert appended.count((str(archive_path), "fresh-id")) == 1
    assert writes
    written_path, recorded_ids = writes[-1]
    assert written_path == str(archive_path)
    assert recorded_ids >= {"seen-id", "fresh-id"}
