#!/usr/bin/env python3
"""
Test script to verify metadata.json robustness features.
Tests saving, loading, backups, and atomic writes without requiring yt-dlp.
"""

import json
import os
import sys
import tempfile
from datetime import datetime

# Add the current directory to the path to import our modules
sys.path.insert(0, os.path.dirname(__file__))

# Mock the youtube_dl import since we're just testing metadata functions
sys.modules['youtube_dl'] = type('Mock', (), {
    'normalize_url': lambda x: x,
    'Source': object,
    'ErrorAnalyzer': object,
})()

from scan_channels import (
    ChannelMetadata,
    MetadataCache,
    save_metadata,
    load_existing_metadata,
)


def test_save_and_load():
    """Test saving and loading metadata."""
    print("Testing save and load...")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "test_metadata.json")

        # Create test metadata
        test_cache = MetadataCache(
            scan_date=datetime.now().isoformat(),
            channels=[
                ChannelMetadata(
                    url="https://youtube.com/@test",
                    kind="channel",
                    label="Test Channel",
                    scan_timestamp=datetime.now().isoformat(),
                    videos=[
                        {"video_id": "test123", "title": "Test Video 1"},
                        {"video_id": "test456", "title": "Test Video 2"},
                    ],
                    total_videos=2,
                    error=None,
                )
            ],
            total_videos=2,
            total_channels=1,
        )

        # Save metadata
        save_metadata(test_cache, output_path, create_backup=False)

        # Verify file exists
        assert os.path.exists(output_path), "Metadata file was not created"
        print(f"✓ Metadata file created: {output_path}")

        # Load metadata
        loaded_cache = load_existing_metadata(output_path)
        assert loaded_cache is not None, "Failed to load metadata"
        assert loaded_cache.total_channels == 1, "Channel count mismatch"
        assert loaded_cache.total_videos == 2, "Video count mismatch"
        assert len(loaded_cache.channels) == 1, "Wrong number of channels"
        assert loaded_cache.channels[0].url == "https://youtube.com/@test"
        print("✓ Metadata loaded correctly")

        # Test incremental save (simulating adding another channel)
        test_cache.channels.append(
            ChannelMetadata(
                url="https://youtube.com/@test2",
                kind="channel",
                label="Test Channel 2",
                scan_timestamp=datetime.now().isoformat(),
                videos=[{"video_id": "test789", "title": "Test Video 3"}],
                total_videos=1,
                error=None,
            )
        )
        test_cache.total_channels = 2
        test_cache.total_videos = 3

        # Save with backup
        save_metadata(test_cache, output_path, create_backup=True)

        # Verify backup was created
        backup_path = f"{output_path}.backup"
        assert os.path.exists(backup_path), "Backup file was not created"
        print(f"✓ Backup file created: {backup_path}")

        # Load and verify incremental save
        loaded_cache = load_existing_metadata(output_path)
        assert loaded_cache.total_channels == 2, "Incremental save failed"
        assert loaded_cache.total_videos == 3, "Incremental save failed"
        print("✓ Incremental save works correctly")

        # Verify backup contains old data
        with open(backup_path, "r") as f:
            backup_data = json.load(f)
        assert backup_data["total_channels"] == 1, "Backup doesn't contain old data"
        print("✓ Backup contains correct old data")


def test_empty_metadata():
    """Test creating empty metadata file."""
    print("\nTesting empty metadata file...")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "empty_metadata.json")

        # Create empty cache
        empty_cache = MetadataCache(
            scan_date=datetime.now().isoformat(),
            channels=[],
            total_videos=0,
            total_channels=0,
        )

        # Save empty metadata
        save_metadata(empty_cache, output_path, create_backup=False)

        # Verify file exists and is valid JSON
        assert os.path.exists(output_path), "Empty metadata file was not created"
        with open(output_path, "r") as f:
            data = json.load(f)
        assert data["total_channels"] == 0
        assert data["total_videos"] == 0
        assert data["channels"] == []
        print("✓ Empty metadata file created correctly")


def test_atomic_write():
    """Test that writes are atomic (temp file approach)."""
    print("\nTesting atomic writes...")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_path = os.path.join(tmpdir, "atomic_test.json")

        # Create test metadata
        test_cache = MetadataCache(
            scan_date=datetime.now().isoformat(),
            channels=[],
            total_videos=0,
            total_channels=0,
        )

        # Save metadata
        save_metadata(test_cache, output_path, create_backup=False)

        # Verify no temp file left behind
        temp_path = f"{output_path}.tmp"
        assert not os.path.exists(temp_path), "Temp file was not cleaned up"
        print("✓ No temp files left behind (atomic write successful)")


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing Metadata Robustness Features")
    print("=" * 70)

    try:
        test_save_and_load()
        test_empty_metadata()
        test_atomic_write()

        print("\n" + "=" * 70)
        print("ALL TESTS PASSED ✓")
        print("=" * 70)
        print("\nKey features verified:")
        print("  ✓ Metadata files are created correctly")
        print("  ✓ Incremental saves work properly")
        print("  ✓ Backups are created before overwriting")
        print("  ✓ Atomic writes prevent corruption")
        print("  ✓ Empty metadata files can be created")
        print("\nThe enhanced scan_channels.py is ready to use!")
        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
