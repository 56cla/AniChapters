"""
Audio matching engine — powered by librosa + scipy (ported from Auto Chap v4.2).

Replaces the previous NCC-FFT/ffmpeg approach with the proven correlation
method used by Auto_Chap (SubsPlus+), while keeping the same public interface
that analyzer.py expects:

    find_theme_start(video_path, theme_url, theme_duration_ms,
                     search_start_seconds, search_end_seconds,
                     log_func, cancel_event) -> Optional[int]  (ms)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
from typing import Callable, Optional

from constants import API_HEADERS, _THEME_FILE_CACHE
from timestamps import ms_to_display

# Suppress console window on Windows (both native and PyInstaller .exe)
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# ── optional heavy imports (checked at call-time) ────────────────────────────
try:
    import numpy as np
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False

try:
    import librosa
    import audioread.ffdec
    from scipy import signal as _signal
    _LIBROSA_OK = True
except ImportError:
    _LIBROSA_OK = False


# ─────────────────────────────────────────────────────────────────────────────
#  Download helper (unchanged from original — kept for theme URL support)
# ─────────────────────────────────────────────────────────────────────────────

def _download_to_temp(url: str, log_func: Optional[Callable] = None) -> Optional[str]:
    """Download a remote URL to a local temp file with caching + retries."""
    import time

    if url in _THEME_FILE_CACHE:
        cached = _THEME_FILE_CACHE[url]
        if os.path.exists(cached) and os.path.getsize(cached) > 10_000:
            return cached
        del _THEME_FILE_CACHE[url]

    max_retries = 3
    temp_path   = ""

    for attempt in range(max_retries):
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".webm")
            os.close(fd)

            request = urllib.request.Request(url, headers=API_HEADERS)
            with urllib.request.urlopen(request, timeout=90) as resp:
                with open(temp_path, "wb") as f:
                    while True:
                        chunk = resp.read(65_536)
                        if not chunk:
                            break
                        f.write(chunk)

            if os.path.getsize(temp_path) < 10_000:
                os.remove(temp_path)
                if attempt < max_retries - 1:
                    if log_func:
                        log_func("  Download incomplete, retrying…\n", "dim")
                    time.sleep(2 ** attempt)
                    continue
                return None

            mb = os.path.getsize(temp_path) / (1024 * 1024)
            if log_func:
                log_func(f"  Downloaded: {mb:.1f} MB\n", "dim")

            _THEME_FILE_CACHE[url] = temp_path
            return temp_path

        except Exception as exc:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            if attempt < max_retries - 1:
                if log_func:
                    log_func("  Download error, retrying…\n", "dim")
                time.sleep(2 ** attempt)
                continue
            if log_func:
                log_func(f"  Download failed: {exc}\n", "err")
            return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  Core matching  (Auto Chap v4.2 algorithm)
# ─────────────────────────────────────────────────────────────────────────────

def _load_audio_librosa(
    path: str,
    sr: Optional[int] = None,
    log_func: Optional[Callable] = None,
) -> "tuple[Optional[np.ndarray], int]":
    """Load audio using librosa (via audioread/ffdec for non-wav files)."""
    try:
        if path.endswith((".webm", ".ogg", ".mp4", ".mkv")):
            aro = audioread.ffdec.FFmpegAudioFile(path)
            y, file_sr = librosa.load(aro, sr=sr)
        else:
            y, file_sr = librosa.load(path, sr=sr)
        return y, file_sr
    except Exception as exc:
        if log_func:
            log_func(f"  librosa load error: {exc}\n", "err")
        return None, 0


def _extract_segment_ffmpeg(
    source: str,
    start_s: float,
    duration_s: float,
    sr: int,
    log_func: Optional[Callable] = None,
) -> "Optional[np.ndarray]":
    """
    Use ffmpeg to extract a segment as raw PCM then load into numpy.
    Faster than loading the entire file with librosa when we only need a window.
    """
    if not shutil.which("ffmpeg"):
        if log_func:
            log_func("  ffmpeg not found in PATH\n", "err")
        return None

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]

    if start_s > 0.001:
        cmd += ["-ss", f"{start_s:.3f}"]

    cmd += ["-i", source]

    if duration_s > 0.001:
        cmd += ["-t", f"{duration_s:.3f}"]

    cmd += ["-vn", "-ar", str(sr), "-ac", "1", "-f", "s16le", "pipe:1"]

    try:
        result = subprocess.run(cmd, capture_output=True, timeout=300,
                                creationflags=_CREATIONFLAGS)
        if result.returncode != 0 or len(result.stdout) < 512:
            if log_func and result.stderr:
                msg = result.stderr.decode(errors="replace").strip()[-200:]
                log_func(f"  ffmpeg error: {msg}\n", "err")
            return None

        audio = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
        return audio

    except subprocess.TimeoutExpired:
        if log_func:
            log_func("  ffmpeg timeout\n", "err")
        return None
    except Exception as exc:
        if log_func:
            log_func(f"  ffmpeg exception: {exc}\n", "err")
        return None


def _correlate_and_find(
    y_episode: "np.ndarray",
    y_theme_portion: "np.ndarray",
    sr: int,
    downsample: int,
    score_threshold: float,
    silence_prefix_s: float = 5.0,
) -> "tuple[Optional[float], float]":
    """
    Run scipy signal.correlate (same as Auto Chap v4.2 find_offset).

    Returns (offset_seconds, score).  offset_seconds is None if below threshold.
    """
    y_ep  = y_episode[::downsample]
    y_th  = y_theme_portion[::downsample]

    # 5s silence prefix to handle matches right at the start
    sil_len = int(silence_prefix_s * sr / downsample)
    y_ep_padded = np.empty(sil_len + len(y_ep), dtype=y_ep.dtype)
    y_ep_padded[:sil_len] = 0
    y_ep_padded[sil_len:] = y_ep

    try:
        c = _signal.correlate(y_ep_padded, y_th, mode="valid", method="auto")
    except Exception:
        return None, 0.0

    match_idx = int(np.argmax(c))
    score     = float(np.max(c))
    offset_s  = max((match_idx - sil_len) / (sr / downsample), 0.0)

    if score >= score_threshold:
        return offset_s, score
    return None, score


# ─────────────────────────────────────────────────────────────────────────────
#  Public interface  (same signature as the old audio_matcher.py)
# ─────────────────────────────────────────────────────────────────────────────

def find_theme_start(
    video_path: str,
    theme_url: str,
    theme_duration_ms: int,
    search_start_seconds: float,
    search_end_seconds: float,
    log_func: Optional[Callable] = None,
    cancel_event: Optional[threading.Event] = None,
    *,
    # Tuning knobs (same semantics as Auto Chap CLI args)
    downsample: int   = 32,
    score: float      = 2_000.0,
    theme_portion: float = 0.90,
) -> Optional[int]:
    """
    Find the start time of a theme in a video using the Auto Chap v4.2
    correlation algorithm (librosa + scipy).

    Returns start time in milliseconds, or None if not found.
    """
    def log(msg: str, tag: str = "dim"):
        if log_func:
            log_func(msg, tag)

    # ── dependency check ──────────────────────────────────────────────────────
    if not _NUMPY_OK:
        log("  numpy not installed — pip install numpy\n", "err")
        return None
    if not _LIBROSA_OK:
        log("  librosa/scipy not installed — pip install librosa scipy audioread\n", "err")
        return None

    if cancel_event and cancel_event.is_set():
        return None

    theme_duration_s = theme_duration_ms / 1000.0
    scan_duration_s  = max(1.0, search_end_seconds - search_start_seconds)

    # ── download theme if remote ──────────────────────────────────────────────
    if theme_url.startswith("http://") or theme_url.startswith("https://"):
        log("  Downloading theme…\n", "dim")
        local_theme = _download_to_temp(theme_url, log_func)
        if local_theme is None:
            log("  Failed to download theme\n", "err")
            return None
    else:
        local_theme = theme_url

    if cancel_event and cancel_event.is_set():
        return None

    # ── load episode audio (window only, via ffmpeg for speed) ───────────────
    log(
        f"  Loading episode audio {search_start_seconds:.0f}s"
        f"→{search_end_seconds:.0f}s @ native SR…\n",
        "dim",
    )

    # Use a moderate sample rate (22050 Hz like librosa default) for accuracy
    TARGET_SR = 22_050

    y_episode = _extract_segment_ffmpeg(
        video_path,
        start_s=search_start_seconds,
        duration_s=scan_duration_s,
        sr=TARGET_SR,
        log_func=log_func,
    )

    if y_episode is None or len(y_episode) < TARGET_SR:
        log("  Could not extract episode audio\n", "err")
        return None

    if cancel_event and cancel_event.is_set():
        return None

    # ── load theme audio ──────────────────────────────────────────────────────
    log("  Loading theme audio…\n", "dim")

    y_theme, sr_theme = _load_audio_librosa(local_theme, sr=TARGET_SR, log_func=log_func)

    if y_theme is None or len(y_theme) < TARGET_SR:
        log("  Could not load theme audio\n", "err")
        return None

    # Use first `theme_portion` of theme as the needle (same as Auto Chap)
    portion_samples = int(TARGET_SR * theme_duration_s * theme_portion)
    y_theme_portion = y_theme[:portion_samples]

    if len(y_theme_portion) < TARGET_SR:
        log("  Theme portion too short\n", "err")
        return None

    if cancel_event and cancel_event.is_set():
        return None

    # ── correlation ───────────────────────────────────────────────────────────
    log(
        f"  Correlating (downsample={downsample}, "
        f"theme_portion={theme_portion:.0%})…\n",
        "dim",
    )

    required_score = score / downsample

    offset_s, raw_score = _correlate_and_find(
        y_episode,
        y_theme_portion,
        sr=TARGET_SR,
        downsample=downsample,
        score_threshold=required_score,
    )

    if offset_s is not None:
        # offset_s is relative to search_start_seconds
        true_start_s = search_start_seconds + offset_s
        start_ms     = int(round(true_start_s * 1000))
        end_ms       = start_ms + theme_duration_ms

        log(
            f"  ✔ Match  {ms_to_display(start_ms)} → {ms_to_display(end_ms)}"
            f"  (score={raw_score:.0f})\n",
            "ok",
        )
        return start_ms

    log(
        f"  ✗ No match found (score={raw_score:.0f} < required={required_score:.0f})\n",
        "err",
    )
    return None
