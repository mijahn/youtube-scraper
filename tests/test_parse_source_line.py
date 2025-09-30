"""Focused tests for inline comment handling in parse_source_line."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import download_channel_videos as dc


@pytest.mark.parametrize(
    "raw, expected_kind, expected_url",
    [
        ("https://example.com # trailing", dc.SourceType.CHANNEL, "https://example.com"),
        (
            "https://example.com/watch?v=1#fragment # keep fragment",
            dc.SourceType.CHANNEL,
            "https://example.com/watch?v=1#fragment",
        ),
        (
            "playlist: https://example.com/list # playlist comment",
            dc.SourceType.PLAYLIST,
            "https://example.com/list",
        ),
        (
            "playlist: https://example.com/list#frag # keep",
            dc.SourceType.PLAYLIST,
            "https://example.com/list#frag",
        ),
    ],
)
def test_parse_source_line_strips_inline_comments(raw, expected_kind, expected_url):
    source = dc.parse_source_line(raw)
    assert source is not None
    assert source.kind is expected_kind
    assert source.url == expected_url


def test_parse_source_line_missing_url_after_comment():
    with pytest.raises(ValueError):
        dc.parse_source_line("channel:    # comment only")
