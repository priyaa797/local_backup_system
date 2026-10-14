"""Formatting helpers for sizes, speeds, durations, and safe filenames."""

from __future__ import annotations

from datetime import datetime
import re
from typing import Optional


def format_bytes(num_bytes: Optional[int | float]) -> str:
    """Format byte count into human-readable string (B, KB, MB, GB, TB)."""
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    num_bytes = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if abs(num_bytes) < 1024.0:
            if unit == "B":
                return f"{int(num_bytes)} {unit}"
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} EB"


def format_speed(bytes_per_sec: Optional[int | float]) -> str:
    """Format speed in bytes/sec into human-readable string (e.g. 12.4 MB/s)."""
    if bytes_per_sec is None or bytes_per_sec <= 0:
        return "0 B/s"
    return f"{format_bytes(bytes_per_sec)}/s"


def format_duration(seconds: Optional[int | float]) -> str:
    """Format seconds into human-readable duration (e.g. 12s, 02m 15s, 01h 04m 20s)."""
    if seconds is None or seconds < 0:
        return "--:--"
    total_seconds = int(round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours > 0:
        return f"{hours:02d}h {minutes:02d}m {secs:02d}s"
    if minutes > 0:
        return f"{minutes:02d}m {secs:02d}s"
    return f"{secs}s"


def format_timestamp(dt: Optional[datetime | str]) -> str:
    """Format datetime or ISO string to friendly display string."""
    if dt is None:
        return "Never"
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt)
        except ValueError:
            return dt
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def sanitize_filename(name: str) -> str:
    """Sanitize a name to make it safe for filesystem folders on Windows and Linux.

    Removes invalid characters: < > : " / \\ | ? * and control chars.
    """
    if not name:
        return "unnamed"
    # Replace characters not allowed in Windows filenames
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    # Remove leading/trailing spaces and dots
    sanitized = sanitized.strip(" .")
    return sanitized or "unnamed"
