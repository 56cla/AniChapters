"""
ffprobe helpers for reading video/audio metadata.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from typing import Optional


def get_video_duration_ms(url: str) -> Optional[int]:
    """Get video duration in milliseconds using ffprobe"""
    if not shutil.which("ffprobe"):
        return None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                url,
            ],
            capture_output=True,
            timeout=30,
        )

        if result.returncode != 0:
            return None

        data     = json.loads(result.stdout)
        duration = float(data.get("format", {}).get("duration", 0))
        return int(duration * 1000)

    except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, ValueError):
        return None
