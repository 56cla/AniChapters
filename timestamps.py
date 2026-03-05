"""
Timestamp conversion utilities.
"""
from __future__ import annotations

from typing import Optional


def ms_to_mkv_timestamp(ms: int) -> str:
    """Convert milliseconds to MKV timestamp format (HH:MM:SS.nnnnnnnnn)"""
    ms = max(0, int(ms))
    total_seconds = ms // 1000
    nanoseconds = (ms % 1000) * 1_000_000

    hours   = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{nanoseconds:09d}"


def ms_to_display(ms: int) -> str:
    """Convert milliseconds to human-readable format (HH:MM:SS)"""
    seconds = max(0, int(ms)) // 1000
    hours   = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs    = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def timestamp_to_ms(timestamp: str) -> Optional[int]:
    """Parse timestamp string to milliseconds"""
    try:
        timestamp = timestamp.strip().replace(",", ".")
        parts = timestamp.split(":")

        if len(parts) != 3:
            return None

        hours   = int(parts[0])
        minutes = int(parts[1])
        seconds = float(parts[2])

        return int((hours * 3600 + minutes * 60 + seconds) * 1000)
    except (ValueError, IndexError):
        return None
