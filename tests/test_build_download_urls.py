from download_channel_videos import Source, SourceType


def test_channel_url_expands_to_videos_and_shorts():
    source = Source(SourceType.CHANNEL, "https://www.youtube.com/@example")

    assert source.build_download_urls() == [
        "https://www.youtube.com/@example/videos",
        "https://www.youtube.com/@example/shorts",
    ]


def test_channel_url_with_existing_tab_does_not_duplicate_tab():
    source = Source(SourceType.CHANNEL, "https://www.youtube.com/@example/shorts")

    assert source.build_download_urls() == [
        "https://www.youtube.com/@example/shorts",
    ]


def test_channel_url_with_existing_tab_keeps_other_tab_unique():
    source = Source(SourceType.CHANNEL, "https://www.youtube.com/@example/videos")

    assert source.build_download_urls() == [
        "https://www.youtube.com/@example/videos",
        "https://www.youtube.com/@example/shorts",
    ]
