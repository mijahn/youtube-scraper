# YouTube Scraper Architecture Guide

This document describes the advanced architecture features available in youtube-scraper.

## Overview

The youtube-scraper project now includes several architectural improvements designed to enhance reliability, avoid rate limiting, and provide better control over the download process.

## Key Features

### 1. Separate Metadata Scanner (`scan_channels.py`)

A standalone script for collecting channel metadata slowly to avoid rate limiting.

#### Benefits

- **Slow scanning**: One request per minute (configurable) to avoid triggering YouTube's rate limits
- **Cached metadata**: Scan results are saved to JSON for reuse
- **Download separation**: Metadata collection happens separately from video downloads
- **Retry-friendly**: Failed scans can be re-run without re-downloading videos

#### Usage

```bash
# Basic usage - scan channels and save metadata
python scan_channels.py --channels-file channels.txt --output metadata.json

# Slow scanning (recommended for large channel lists)
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 60

# Use authentication for better reliability
python scan_channels.py --channels-file channels.txt --output metadata.json --cookies-from-browser chrome

# Remote channels file
python scan_channels.py --channels-url https://example.com/channels.txt --output metadata.json
```

#### Options

- `--channels-file`: Path to local channels.txt file
- `--channels-url`: URL to remote channels.txt file
- `--output`: Output path for metadata JSON (default: metadata.json)
- `--request-interval`: Seconds between metadata requests (default: 60)
- `--no-shorts`: Skip scanning /shorts tab
- `--youtube-client`: Force specific YouTube client
- `--cookies-from-browser`: Browser to extract cookies from

#### Recommended Workflow

```bash
# Step 1: Scan channels overnight (slow, safe)
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 120

# Step 2: Download videos using cached metadata (next day)
python download_videos.py --metadata metadata.json
```

---

### 2. Metadata-Based Downloader (`download_videos.py`)

Downloads videos using pre-scanned metadata from `scan_channels.py`.

#### Benefits

- **No metadata rate limits**: Uses cached metadata, doesn't make additional scanning requests
- **Faster downloads**: Skips metadata collection phase
- **Multiple attempts**: Can retry downloads without re-scanning
- **Archive integration**: Respects download archive to avoid re-downloading

#### Usage

```bash
# Basic usage - download from metadata
python download_videos.py --metadata metadata.json

# Specify output directory and archive
python download_videos.py --metadata metadata.json --output ./videos --archive downloads.txt

# Limit download rate
python download_videos.py --metadata metadata.json --rate-limit 1M

# Use authentication
python download_videos.py --metadata metadata.json --cookies-from-browser chrome
```

#### Options

- `--metadata`: Path to metadata JSON (required)
- `--output`: Output directory (default: ./downloads)
- `--archive`: Download archive path
- `--max`: Maximum videos to download
- `--rate-limit`: Bandwidth limit for yt-dlp
- `--format`: Video format selector
- All authentication options from main downloader

---

### 3. Queue-Based Download Manager (`queue_manager.py`)

Advanced queue system with persistent state, retry logic, and failure tracking.

#### Benefits

- **Persistent queue**: Survives restarts and crashes
- **Exponential retry**: Failed downloads retry with increasing delays
- **Configurable concurrency**: Control number of parallel downloads (future feature)
- **Status tracking**: Monitor download progress and failures
- **Failure limits**: Automatically skip videos that fail repeatedly

#### Workflow

```bash
# Step 1: Populate queue from metadata
python queue_manager.py --populate --metadata metadata.json

# Step 2: View queue status
python queue_manager.py --status

# Step 3: Start downloading from queue
python queue_manager.py --download --workers 1

# Step 4: Check status again
python queue_manager.py --status

# Step 5: Retry failed downloads (exponential backoff applied automatically)
python queue_manager.py --download
```

#### Queue Management

```bash
# Clear the queue
python queue_manager.py --clear

# Use custom queue file
python queue_manager.py --populate --metadata metadata.json --queue-file my_queue.json
python queue_manager.py --download --queue-file my_queue.json
```

#### Queue States

Videos in the queue can have the following states:

- **pending**: Not yet downloaded
- **downloading**: Currently being downloaded
- **completed**: Successfully downloaded
- **failed**: Failed after all retry attempts
- **retrying**: Failed but will be retried

#### Retry Behavior

- First failure: Retry after 60 seconds
- Second failure: Retry after 120 seconds (2 minutes)
- Third failure: Retry after 240 seconds (4 minutes)
- Fourth failure: Retry after 480 seconds (8 minutes)
- Fifth failure: Retry after 960 seconds (16 minutes)
- After 5 failures: Mark as permanently failed

---

### 4. Health Check Mode (`health_check.py`)

Test YouTube connectivity and detect rate limiting.

#### Benefits

- **Rate limit detection**: Check if your IP is currently rate limited
- **Authentication testing**: Verify cookies and tokens are working
- **Estimated wait time**: Get recommendations for how long to wait
- **Single test request**: Minimal impact on rate limits

#### Usage

```bash
# Basic health check
python health_check.py

# Test with authentication
python health_check.py --cookies-from-browser chrome

# Test specific video
python health_check.py --test-video dQw4w9WgXcQ

# Test with specific client
python health_check.py --youtube-client tv
```

#### Interpreting Results

**Success (Exit Code 0)**:
```
✓ SUCCESS
  Video title: ...
  → Your IP is NOT rate limited
  → Authentication is working
```

**Rate Limited (Exit Code 1)**:
```
✗ FAILED
  Error: HTTP Error 403: Forbidden
  → HTTP 403 Forbidden detected
  → Your IP is likely RATE LIMITED
  → Estimated wait time: 60 seconds
```

**Authentication Issues**:
```
✗ FAILED
  → Authentication required
  → Try using --cookies-from-browser
```

#### Built-in Health Check

You can also use the built-in health check:

```bash
# From command line
python download_channel_videos.py --health-check

# From interactive interface
python interactive_interface.py
# Choose option 4: Run health check
```

---

## Complete Workflows

### Workflow 1: Safe Overnight Scanning + Next-Day Downloads

Best for large channel lists where you want to avoid rate limits completely.

```bash
# Evening: Start slow metadata scan (2 minutes between requests)
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 120

# Next morning: Download videos using cached metadata
python download_videos.py --metadata metadata.json --output ./videos
```

**Estimated time**:
- 50 channels × 2 minutes = ~100 minutes for scanning
- Downloads proceed at normal speed

### Workflow 2: Queue-Based Downloads with Retry

Best for unreliable connections or when you expect some failures.

```bash
# Step 1: Scan channels
python scan_channels.py --channels-file channels.txt --output metadata.json

# Step 2: Populate queue
python queue_manager.py --populate --metadata metadata.json

# Step 3: Download (handles failures automatically)
python queue_manager.py --download

# Step 4: Check for permanent failures
python queue_manager.py --status
```

### Workflow 3: Health Check Before Downloading

Best when you're unsure if you're currently rate limited.

```bash
# Step 1: Run health check
python health_check.py --cookies-from-browser chrome

# If health check passes:
python download_channel_videos.py --channels-file channels.txt

# If rate limited, wait and try again:
# (wait the recommended time, then run health check again)
```

### Workflow 4: Incremental Updates

Best for regularly updating a collection.

```bash
# Week 1: Initial download
python scan_channels.py --channels-file channels.txt --output week1.json
python download_videos.py --metadata week1.json --archive archive.txt

# Week 2: Update (only downloads new videos)
python scan_channels.py --channels-file channels.txt --output week2.json
python download_videos.py --metadata week2.json --archive archive.txt
```

---

## Architecture Comparison

### Traditional Approach (download_channel_videos.py)

```
┌─────────────────────────────────────────┐
│  Scan metadata + Download videos        │
│  (happens together, can trigger limits) │
└─────────────────────────────────────────┘
```

**Pros**: Simple, one command
**Cons**: Metadata requests + downloads can trigger rate limits

### New Architecture

```
┌──────────────────┐     ┌──────────────────┐
│ Scan Metadata    │ --> │ Download Videos  │
│ (slow, safe)     │     │ (from cache)     │
└──────────────────┘     └──────────────────┘
        │
        v
┌──────────────────┐
│ Queue Manager    │
│ (retry logic)    │
└──────────────────┘
```

**Pros**:
- Separate concerns
- Can scan slowly overnight
- Can retry downloads without re-scanning
- Better rate limit avoidance

**Cons**: Requires multiple steps

---

## Rate Limiting Best Practices

### Understanding Rate Limits

YouTube applies rate limits based on:
1. **Request frequency**: Too many requests per minute
2. **IP address**: Limits are per-IP
3. **Authentication status**: Anonymous users have lower limits
4. **Request pattern**: Suspicious patterns trigger stricter limits

### Recommendations

#### 1. Use Slow Metadata Scanning

```bash
# Conservative: 2 minutes between requests
python scan_channels.py --channels-file channels.txt --request-interval 120

# Very conservative: 5 minutes between requests
python scan_channels.py --channels-file channels.txt --request-interval 300
```

#### 2. Use Authentication

```bash
# Extract cookies from browser
python scan_channels.py --cookies-from-browser chrome --channels-file channels.txt
```

#### 3. Separate Metadata from Downloads

```bash
# Do NOT do this (risky):
python download_channel_videos.py --channels-file channels.txt

# DO this instead (safe):
python scan_channels.py --channels-file channels.txt --output metadata.json --request-interval 120
python download_videos.py --metadata metadata.json
```

#### 4. Monitor with Health Checks

```bash
# Before starting downloads
python health_check.py

# If you get 403 errors during downloads
python health_check.py  # Check if rate limited

# Wait if needed, then check again
sleep 300  # Wait 5 minutes
python health_check.py
```

#### 5. Use Download Delays

```bash
python download_videos.py --metadata metadata.json \
  --sleep-requests 3.0 \
  --sleep-interval 5.0 \
  --max-sleep-interval 15.0
```

---

## Troubleshooting

### "HTTP Error 403: Forbidden"

**Cause**: Rate limited by YouTube

**Solutions**:
1. Run health check: `python health_check.py`
2. Wait 30-60 minutes
3. Use authentication: `--cookies-from-browser chrome`
4. Use slower scanning: `--request-interval 300`
5. Switch networks or use VPN

### "Video unavailable" errors

**Cause**: Video is private, deleted, or geo-restricted

**Solutions**:
- Use authentication for private videos: `--cookies-from-browser chrome`
- Enable restricted content: `--allow-restricted`
- These errors are often normal and don't indicate rate limiting

### Queue shows many "failed" videos

**Cause**: Various (rate limits, unavailable videos, network errors)

**Solutions**:
```bash
# Check queue status to see errors
python queue_manager.py --status

# Run health check
python health_check.py

# If not rate limited, retry downloads
python queue_manager.py --download
```

### Metadata scan is too slow

**Balance**: Faster scanning = higher rate limit risk

**Options**:
```bash
# Faster (30 seconds between requests) - moderate risk
python scan_channels.py --channels-file channels.txt --request-interval 30

# Default (60 seconds) - low risk
python scan_channels.py --channels-file channels.txt --request-interval 60

# Slowest (120 seconds) - minimal risk
python scan_channels.py --channels-file channels.txt --request-interval 120
```

---

## Integration with Existing Tools

### Using with interactive_interface.py

The interactive interface now includes a health check option:

```bash
python interactive_interface.py

# Menu options:
#   1. Check for new videos (uses scan internally)
#   2. Download videos from a specific source
#   3. Download all pending videos
#   4. Run health check (NEW!)
#   q. Quit
```

### Using with existing channels.txt

All new tools support the same channels.txt format:

```
# channels.txt
https://www.youtube.com/@channel1
playlist:https://www.youtube.com/playlist?list=PLxxx
video:https://www.youtube.com/watch?v=xxxx
```

---

## Advanced Configuration

### Environment Variables

All tools support the same environment variables as the main downloader:

```bash
export YOUTUBE_SCRAPER_COOKIES_FROM_BROWSER=chrome
export YOUTUBE_SCRAPER_FETCH_PO_TOKEN=always

python scan_channels.py --channels-file channels.txt
python download_videos.py --metadata metadata.json
python health_check.py
```

### Custom Queue File Locations

```bash
# Use project-specific queue
python queue_manager.py --populate --metadata project_a.json --queue-file project_a_queue.json
python queue_manager.py --download --queue-file project_a_queue.json
```

---

## Performance Recommendations

### For Small Channel Lists (<10 channels)

Use traditional downloader:
```bash
python download_channel_videos.py --channels-file channels.txt
```

### For Medium Channel Lists (10-50 channels)

Use metadata scanner with moderate delays:
```bash
python scan_channels.py --channels-file channels.txt --request-interval 60
python download_videos.py --metadata metadata.json
```

### For Large Channel Lists (>50 channels)

Use slow scanning + queue manager:
```bash
# Scan overnight (2-5 minutes between requests)
python scan_channels.py --channels-file channels.txt --request-interval 180 --output metadata.json

# Populate queue
python queue_manager.py --populate --metadata metadata.json

# Download with retry support
python queue_manager.py --download
```

---

## Summary

The new architecture provides:

1. ✅ **Separate metadata scanning** - avoid rate limits during downloads
2. ✅ **Persistent queue system** - survive crashes and retry failures
3. ✅ **Health check mode** - detect rate limiting proactively
4. ✅ **Configurable delays** - tune for your risk tolerance
5. ✅ **Better failure handling** - exponential backoff and retry logic

**Recommended for most users**: Use `scan_channels.py` + `download_videos.py` workflow to separate metadata collection from downloads.
