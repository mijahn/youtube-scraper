"""Error analysis and exception handling for YouTube downloader."""

import sys
from datetime import datetime
from typing import Dict, List, Optional

from .models import ErrorPattern


class ErrorAnalyzer:
    """Analyzes error patterns and suggests remediation strategies."""

    def __init__(self) -> None:
        self.patterns: Dict[str, ErrorPattern] = {
            "geo_restricted": ErrorPattern("geo_restricted"),
            "age_restricted": ErrorPattern("age_restricted"),
            "members_only": ErrorPattern("members_only"),
            "private_deleted": ErrorPattern("private_deleted"),
            "video_unavailable": ErrorPattern("video_unavailable"),
            "rate_limit": ErrorPattern("rate_limit"),
            "po_token": ErrorPattern("po_token"),
            "auth_required": ErrorPattern("auth_required"),
            "unknown": ErrorPattern("unknown"),
        }
        self.total_errors = 0
        self.error_log_path: Optional[str] = None

    def set_error_log_path(self, path: str) -> None:
        """Set the path for the detailed error log file."""
        self.error_log_path = path

    def categorize_and_record(self, video_id: Optional[str], error_message: str) -> str:
        """Categorize an error and record it. Returns the error category."""
        self.total_errors += 1
        lowered = error_message.lower()

        category = "unknown"

        # Categorize the error (order matters - more specific first)
        if any(x in lowered for x in ["not available in your country", "geo", "region"]):
            category = "geo_restricted"
        elif any(x in lowered for x in ["age", "sign in to confirm"]):
            category = "age_restricted"
        elif any(x in lowered for x in ["members only", "member", "subscription", "subscriber"]):
            category = "members_only"
        elif any(x in lowered for x in ["private", "deleted", "removed", "uploader has not made"]):
            category = "private_deleted"
        elif any(x in lowered for x in ["video unavailable", "content isn't available", "content is not available", "this content isn't available"]):
            category = "video_unavailable"
        elif any(x in lowered for x in ["403", "forbidden", "too many requests", "rate limit"]):
            category = "rate_limit"
        elif any(x in lowered for x in ["po token", "po_token"]):
            category = "po_token"
        elif any(x in lowered for x in ["login required", "authentication"]):
            category = "auth_required"

        # Record the error
        pattern = self.patterns[category]
        pattern.record(video_id, error_message)

        # Log to error file if configured
        if self.error_log_path:
            self._append_to_error_log(video_id, category, error_message)

        return category

    def _append_to_error_log(self, video_id: Optional[str], category: str, message: str) -> None:
        """Append error details to the error log file."""
        try:
            timestamp = datetime.now().isoformat()
            video_id_str = video_id or "unknown"
            log_entry = f"[{timestamp}] [{category}] {video_id_str}: {message}\n"

            with open(self.error_log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except Exception as e:
            # Don't fail the scan if error logging fails
            print(f"Warning: Failed to write to error log: {e}", file=sys.stderr)

    def get_recommendations(self) -> List[str]:
        """Generate recommendations based on error patterns."""
        recommendations = []

        if self.total_errors == 0:
            return ["No errors detected - scan completed successfully!"]

        # Analyze each pattern and provide specific recommendations
        if self.patterns["geo_restricted"].count > 0:
            recommendations.append(
                f"🌍 Geo-restriction ({self.patterns['geo_restricted'].count} videos): "
                "Use a VPN or proxy from a different region. Try --proxy with a different location."
            )

        if self.patterns["age_restricted"].count > 0:
            recommendations.append(
                f"🔞 Age-restricted ({self.patterns['age_restricted'].count} videos): "
                "Ensure your browser cookies are fresh. Sign in to YouTube in your browser and retry. "
                "Consider using --cookies-from-browser with a recently authenticated browser."
            )

        if self.patterns["members_only"].count > 0:
            recommendations.append(
                f"👥 Members-only ({self.patterns['members_only'].count} videos): "
                "These videos require channel membership. Use --allow-restricted if you have membership "
                "and are authenticated."
            )

        if self.patterns["private_deleted"].count > 0:
            recommendations.append(
                f"🗑️  Private/Deleted ({self.patterns['private_deleted'].count} videos): "
                "These videos are no longer available. This is expected - channels often delete old content."
            )

        if self.patterns["video_unavailable"].count > 0:
            recommendations.append(
                f"⚠️  Video Unavailable ({self.patterns['video_unavailable'].count} videos): "
                "YouTube is blocking access. This may indicate rate limiting or bot detection. "
                "The script now automatically rotates clients and adds delays. "
                "Try: (1) Increase --request-interval to 180-300 seconds, "
                "(2) Use --cookies-from-browser with a recently authenticated browser, "
                "(3) Add --proxy or --proxy-file to use different IP addresses, "
                "(4) Reduce scan frequency and try again later."
            )

        if self.patterns["rate_limit"].count > 0:
            recommendations.append(
                f"⏱️  Rate limiting ({self.patterns['rate_limit'].count} errors): "
                "YouTube is detecting automated access. Increase --request-interval to 180-300 seconds. "
                "Consider using a different proxy or adding more delay between requests."
            )

        if self.patterns["po_token"].count > 0:
            recommendations.append(
                f"🔑 PO Token issues ({self.patterns['po_token'].count} errors): "
                "BGUtil may be failing. Check if BGUtil is running (curl http://127.0.0.1:4416). "
                "Try --bgutil-http-disable-innertube or --bgutil-provider script. "
                "Consider --youtube-fetch-po-token auto instead of always."
            )

        if self.patterns["auth_required"].count > 0:
            recommendations.append(
                f"🔐 Authentication ({self.patterns['auth_required'].count} videos): "
                "These videos require login. Ensure --cookies-from-browser is working correctly. "
                "Sign in to YouTube in your browser and try again."
            )

        if self.patterns["unknown"].count > 0:
            recommendations.append(
                f"❓ Unknown errors ({self.patterns['unknown'].count}): "
                "Check the error log for details. May require manual investigation."
            )

        # Add percentage analysis
        error_rate = (self.total_errors / max(1, self.total_errors)) * 100
        if error_rate > 20:
            recommendations.append(
                f"\n⚠️  High error rate detected! Consider systematic fixes rather than individual retries."
            )

        return recommendations

    def print_summary(self) -> None:
        """Print a formatted summary of error patterns."""
        if self.total_errors == 0:
            print("\n✅ No errors detected during scan!")
            return

        print("\n" + "=" * 70)
        print("Error Pattern Analysis")
        print("=" * 70)
        print(f"Total errors: {self.total_errors}\n")

        # Sort patterns by count
        sorted_patterns = sorted(
            [(name, pattern) for name, pattern in self.patterns.items()],
            key=lambda x: x[1].count,
            reverse=True
        )

        for name, pattern in sorted_patterns:
            if pattern.count > 0:
                print(f"{name.replace('_', ' ').title()}: {pattern.count} occurrences")
                print(f"  Affected videos: {len(pattern.video_ids)}")
                if pattern.sample_messages:
                    print(f"  Sample: {pattern.sample_messages[0][:80]}...")
                print()

        print("=" * 70)
        print("Recommendations")
        print("=" * 70)
        for rec in self.get_recommendations():
            print(f"{rec}\n")
        print("=" * 70)

        if self.error_log_path:
            print(f"\nDetailed error log: {self.error_log_path}")


class RemoteSourceError(Exception):
    """Raised when a remote channels list cannot be retrieved or parsed."""
