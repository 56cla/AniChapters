"""
auto_chap_wrapper.py
Sole purpose: bridges main.py with core.py
"""
from __future__ import annotations

import os
import shutil
import sys
import types
from pathlib import Path
from typing import Optional


class AutoChapError(Exception):
    pass

class NoMatchesError(AutoChapError):
    pass

class DependencyMissingError(AutoChapError):
    pass


def generate_chapters_from_video(
    video_path: str,
    series_query: str,
    out_path: Optional[str] = None,
    *,
    year: Optional[int] = None,
    score: int = 2000,
    theme_portion: float = 0.9,
    downsample: int = 32,
    work_path: Optional[str] = None,
    delete_themes: bool = False,
    charts: bool = False,
) -> str:

    # Check for ffmpeg/ffprobe
    missing = []
    if not shutil.which("ffmpeg"):
        missing.append("ffmpeg")
    if not shutil.which("ffprobe"):
        missing.append("ffprobe")
    if missing:
        raise DependencyMissingError(f"Missing: {', '.join(missing)}")

    input_path = Path(video_path)
    output = Path(out_path) if out_path else input_path.with_name(input_path.stem + ".chapters.txt")
    wp = Path(work_path) if work_path else Path(os.path.dirname(input_path.resolve()))

    args = types.SimpleNamespace(
        input=input_path,
        output=output,
        search_name=series_query if series_query else None,
        no_download=not bool(series_query),
        year=year,
        snap=None,
        episode_snap=4.0,
        score=score,
        theme_portion=theme_portion,
        downsample=downsample,
        parallel_dl=10,
        work_path=wp,
        delete_themes=delete_themes,
        charts=charts,
        episode_audio_path=None,
    )

    from core import run_autochap

    import io
    stderr_buf = io.StringIO()
    old_stderr = sys.stderr
    old_exit   = sys.exit
    sys.stderr = stderr_buf

    def _trap_exit(code=0):
        captured = stderr_buf.getvalue()
        if "No matches" in captured or "Chapters not valid" in captured:
            raise NoMatchesError(captured.strip())
        raise AutoChapError(f"exited {code}:\n{captured.strip()}")

    sys.exit = _trap_exit

    try:
        run_autochap(args)
    except (NoMatchesError, AutoChapError, DependencyMissingError):
        raise
    except Exception as e:
        raise AutoChapError(str(e)) from e
    finally:
        sys.stderr = old_stderr
        sys.exit   = old_exit
        captured = stderr_buf.getvalue()
        if captured:
            sys.stderr.write(captured)

    if not output.exists():
        raise NoMatchesError(f"No output produced for '{video_path}'")

    return str(output.resolve())
