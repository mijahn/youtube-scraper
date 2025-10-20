from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import download_channel_videos as dc


def test_user_agent_selection():
    """Test that user agent rotation selects from the pool."""
    ua1 = dc.select_random_user_agent()
    ua2 = dc.select_random_user_agent()

    # Should return valid user agents from the pool
    assert ua1 in dc.USER_AGENTS
    assert ua2 in dc.USER_AGENTS
    assert len(ua1) > 0
    assert len(ua2) > 0


def test_user_agent_pool_not_empty():
    """Test that the user agent pool contains entries."""
    assert len(dc.USER_AGENTS) > 0
    # Should have multiple user agents for rotation
    assert len(dc.USER_AGENTS) >= 5


def test_load_proxies_from_file(tmp_path):
    """Test loading proxies from a file."""
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text(
        "http://proxy1.example.com:8080\n"
        "http://proxy2.example.com:8080\n"
        "# This is a comment\n"
        "\n"
        "socks5://proxy3.example.com:1080\n"
    )

    proxies = dc.load_proxies_from_file(str(proxy_file))

    assert len(proxies) == 3
    assert "http://proxy1.example.com:8080" in proxies
    assert "http://proxy2.example.com:8080" in proxies
    assert "socks5://proxy3.example.com:1080" in proxies


def test_load_proxies_from_nonexistent_file():
    """Test loading proxies from a file that doesn't exist."""
    proxies = dc.load_proxies_from_file("/nonexistent/file.txt")
    assert proxies == []


def test_select_proxy_with_single_proxy():
    """Test proxy selection with a single proxy."""
    args = SimpleNamespace(proxy="http://proxy.example.com:8080", proxy_file=None)
    proxy = dc.select_proxy(args)
    assert proxy == "http://proxy.example.com:8080"


def test_select_proxy_with_proxy_file(tmp_path):
    """Test proxy selection with a proxy file."""
    proxy_file = tmp_path / "proxies.txt"
    proxy_file.write_text(
        "http://proxy1.example.com:8080\n"
        "http://proxy2.example.com:8080\n"
    )

    args = SimpleNamespace(proxy=None, proxy_file=str(proxy_file))
    proxy = dc.select_proxy(args)

    # Should select one of the proxies
    assert proxy in ["http://proxy1.example.com:8080", "http://proxy2.example.com:8080"]


def test_select_proxy_with_no_proxy():
    """Test proxy selection when no proxy is configured."""
    args = SimpleNamespace(proxy=None, proxy_file=None)
    proxy = dc.select_proxy(args)
    assert proxy is None


def test_proxy_file_takes_precedence_over_none():
    """Test that single proxy takes precedence over proxy file."""
    args = SimpleNamespace(
        proxy="http://single.example.com:8080",
        proxy_file="/some/file.txt"
    )
    proxy = dc.select_proxy(args)
    # Single proxy should take precedence
    assert proxy == "http://single.example.com:8080"


def test_rate_limit_backoff_calculation():
    """Test exponential backoff calculation for 403 errors."""
    logger = dc.DownloadLogger()

    # No backoff for 0 errors
    assert logger.check_rate_limit_backoff() is None

    # First 403: 30 seconds
    logger.http_403_count = 1
    assert logger.check_rate_limit_backoff() == 30

    # Second 403: 60 seconds
    logger.http_403_count = 2
    assert logger.check_rate_limit_backoff() == 60

    # Third 403: 120 seconds
    logger.http_403_count = 3
    assert logger.check_rate_limit_backoff() == 120

    # Fourth+ 403: still 120 seconds (client switch should be triggered by caller)
    logger.http_403_count = 4
    assert logger.check_rate_limit_backoff() == 120


def test_unavailable_rate_limiting_detection():
    """Test detection of excessive 'video unavailable' errors."""
    import time
    logger = dc.DownloadLogger()

    # No rate limiting with less than 3 errors
    logger.unavailable_timestamps = [time.time(), time.time()]
    assert logger.check_unavailable_rate_limiting() is False

    # Rate limiting detected with 3+ errors in 10 seconds
    now = time.time()
    logger.unavailable_timestamps = [now - 5, now - 3, now - 1]
    assert logger.check_unavailable_rate_limiting() is True

    # No rate limiting if errors are old
    old_time = time.time() - 15
    logger.unavailable_timestamps = [old_time, old_time, old_time]
    assert logger.check_unavailable_rate_limiting() is False
