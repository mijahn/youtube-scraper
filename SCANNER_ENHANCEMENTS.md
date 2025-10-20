# YouTube Scanner Enhancements

## Overview

The YouTube channel scanner has been significantly enhanced with robust error handling, retry logic, and comprehensive error analysis. These improvements make the scanner more resilient to YouTube's anti-bot measures and provide detailed insights into why videos fail to download.

## New Features

### 1. **Retry Logic with Client Rotation**

The scanner now automatically retries failed requests with different YouTube player clients:

- **Multiple attempts**: Up to 3 retry attempts per URL
- **Client rotation**: Automatically switches between `tv`, `web`, `android`, `ios` clients
- **Smart retry**: Only retries on recoverable errors (403, forbidden, PO token issues)

**Benefits:**
- Increased success rate for metadata extraction
- Automatic recovery from temporary YouTube API issues
- Better handling of client-specific restrictions

### 2. **Exponential Backoff**

Implements intelligent delay management based on failure patterns:

- **Base delay**: Uses your configured `--request-interval`
- **Automatic increase**: Doubles delay after consecutive failures
- **Maximum cap**: Limits backoff to 8x the base delay
- **Auto-reset**: Returns to base delay after successful requests

**Example:**
```
Base delay: 120s
After 1 failure: 240s
After 2 failures: 480s
After 3 failures: 960s (capped at 960s if base is 120s)
Success: Reset to 120s
```

### 3. **Error Pattern Analysis**

Comprehensive error categorization and tracking:

**Error Categories:**
- 🌍 **Geo-restricted**: Videos blocked in your region
- 🔞 **Age-restricted**: Requires sign-in for age verification
- 👥 **Members-only**: Requires channel membership
- 🗑️ **Private/Deleted**: Videos no longer available
- ⏱️ **Rate limiting**: YouTube detecting automated access
- 🔑 **PO Token issues**: BGUtil token generation failures
- 🔐 **Authentication**: Login required
- ❓ **Unknown**: Unclassified errors

**Features:**
- Counts occurrences per category
- Tracks affected video IDs
- Stores sample error messages
- Provides actionable recommendations

### 4. **Enhanced Logging**

**Error Log File** (`scan_errors.log`):
```
[2025-10-20T10:15:23] [age_restricted] abc123XYZ: Sign in to confirm your age
[2025-10-20T10:16:45] [rate_limit] def456ABC: HTTP Error 403: Forbidden
[2025-10-20T10:18:12] [private_deleted] ghi789DEF: This video is private
```

**Console Output:**
- Real-time progress with context
- Retry notifications
- Exponential backoff indicators
- Client rotation messages

### 5. **Intelligent Recommendations**

After each scan, get specific recommendations based on error patterns:

**Example Output:**
```
======================================================================
Error Pattern Analysis
======================================================================
Total errors: 15

Rate Limit: 8 occurrences
  Affected videos: 8
  Sample: HTTP Error 403: Forbidden...

Age Restricted: 4 occurrences
  Affected videos: 4
  Sample: Sign in to confirm your age...

Private Deleted: 3 occurrences
  Affected videos: 3
  Sample: This video is private...

======================================================================
Recommendations
======================================================================
⏱️  Rate limiting (8 errors): YouTube is detecting automated access.
Increase --request-interval to 180-300 seconds. Consider using a
different proxy or adding more delay between requests.

🔞 Age-restricted (4 videos): Ensure your browser cookies are fresh.
Sign in to YouTube in your browser and retry. Consider using
--cookies-from-browser with a recently authenticated browser.

🗑️  Private/Deleted (3 videos): These videos are no longer available.
This is expected - channels often delete old content.

======================================================================

Detailed error log: ./scan_errors.log
```

## Usage

### Basic Scan (with all enhancements)

```bash
python3 scan_channels.py \
  --channels-url https://raw.githubusercontent.com/mijahn/youtube-scraper/main/channels.txt \
  --output metadata.json \
  --request-interval 120 \
  --cookies-from-browser chrome
```

The scanner will automatically:
- Retry failed requests with different clients
- Apply exponential backoff on consecutive failures
- Log all errors to `scan_errors.log`
- Provide detailed error analysis at the end

### Advanced Options

**Increase resilience:**
```bash
--request-interval 180  # More conservative timing (3 minutes)
```

**Force specific client:**
```bash
--youtube-client web  # Override automatic client rotation
```

**Custom error log location:**
The error log is automatically created in the same directory as your output file:
- Output: `metadata.json` → Error log: `scan_errors.log`
- Output: `data/meta.json` → Error log: `data/scan_errors.log`

## How It Works

### Request Flow

1. **Initial Request**
   - Uses primary player client (default: `tv`)
   - Applies base request interval delay

2. **On Failure**
   - Categorizes error (retryable vs permanent)
   - For retryable errors:
     - Rotates to next client (`web`, `android`, `ios`)
     - Applies retry backoff (5s, 10s, 15s)
     - Attempts up to 3 times
   - For permanent errors:
     - Logs to error analyzer
     - Continues to next video

3. **Consecutive Failures**
   - Activates exponential backoff
   - Doubles delay between requests
   - Caps at 8x base delay
   - Resets on first success

4. **Error Analysis**
   - All errors logged in real-time
   - Categorized automatically
   - Statistics compiled
   - Recommendations generated

### Error Detection

The system detects errors from:
- **yt-dlp exceptions**: `DownloadError`, `ExtractorError`
- **HTTP status codes**: 403 Forbidden, 410 Gone
- **YouTube messages**: "Video unavailable", "Private video", etc.
- **Authentication issues**: "Login required", "PO token"

## Performance Impact

**Memory:**
- Minimal increase (~1-2MB for error tracking)
- Sample messages limited to 5 per category

**Time:**
- Same base speed (uses configured intervals)
- Retries add 5-15s per failed request (rare)
- Exponential backoff only on consecutive failures

**Network:**
- Slightly more requests due to retries
- More conservative to avoid rate limits
- Better overall success rate

## Interpreting Results

### Good Scan
```
Total errors: 0
✅ No errors detected during scan!
```

### Expected Errors (<5%)
```
Total errors: 12 (out of 500 videos)
Private/Deleted: 8 videos
Age-restricted: 4 videos
```
✅ Normal - some videos are naturally unavailable

### Concerning Errors (>20%)
```
Total errors: 120 (out of 500 videos)
Rate Limit: 95 errors
PO Token: 25 errors
```
⚠️ Systematic issue - follow recommendations

## Troubleshooting

### High Rate Limit Errors

**Symptoms:**
- Many 403 Forbidden errors
- "Too many requests" messages
- Exponential backoff activating frequently

**Solutions:**
1. Increase `--request-interval` to 180-300s
2. Check proxy is working: `curl --proxy http://127.0.0.1:4416 https://www.youtube.com`
3. Try different proxy location
4. Reduce concurrent operations

### PO Token Failures

**Symptoms:**
- "PO token" error messages
- 403 errors with token mentions
- Authentication failures

**Solutions:**
1. Verify BGUtil is running: `curl http://127.0.0.1:4416/health`
2. Try: `--bgutil-http-disable-innertube`
3. Switch provider: `--bgutil-provider script`
4. Use auto mode: `--youtube-fetch-po-token auto`

### Age-Restricted Content

**Symptoms:**
- "Sign in to confirm your age"
- Multiple age-verification messages

**Solutions:**
1. Re-authenticate in browser (sign out and back in)
2. Ensure cookies are fresh
3. Try different browser: `--cookies-from-browser firefox`
4. Use `--allow-restricted` if authenticated

## Files Generated

1. **metadata.json**: Video metadata (as before)
2. **scan_errors.log**: Detailed error log with timestamps
3. **Console output**: Real-time progress and final analysis

## Backward Compatibility

All existing functionality is preserved:
- Same command-line arguments
- Same metadata.json format
- Same basic workflow

New features activate automatically - no configuration required!

## Future Enhancements

Potential additions:
- [ ] Configurable retry limits
- [ ] Custom error categorization rules
- [ ] Error pattern persistence across scans
- [ ] Webhook notifications for critical errors
- [ ] Export error analysis to JSON

---

**Note:** These enhancements focus on resilience and diagnostics. They help you understand *why* videos fail and provide specific fixes, rather than just showing generic error messages.
