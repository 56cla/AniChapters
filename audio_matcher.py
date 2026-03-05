"""
Audio matching engine: download helper, PCM extraction, NCC-FFT, multi-phase search.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from typing import Callable, Optional

from constants import (
    API_HEADERS,
    NEEDLE_DURATION_SECONDS,
    NEEDLE_SKIP_SECONDS,
    NCC_THRESHOLD,
    _THEME_FILE_CACHE,
)
from timestamps import ms_to_display


def _download_to_temp(url: str, log_func: Optional[Callable] = None) -> Optional[str]:
    """
    Download a remote URL to a local temp file.
    Returns the local file path, or None on failure.
    Uses caching to avoid re-downloading the same URL.
    """
    import time

    # Check cache first
    if url in _THEME_FILE_CACHE:
        cached = _THEME_FILE_CACHE[url]
        if os.path.exists(cached) and os.path.getsize(cached) > 10000:
            return cached
        else:
            del _THEME_FILE_CACHE[url]

    # Try downloading with retries
    max_retries = 3
    temp_path   = ""
    for attempt in range(max_retries):
        try:
            fd, temp_path = tempfile.mkstemp(suffix=".webm")
            os.close(fd)

            request = urllib.request.Request(url, headers=API_HEADERS)
            with urllib.request.urlopen(request, timeout=90) as response:
                with open(temp_path, "wb") as f:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)

            # Verify download
            if os.path.getsize(temp_path) < 10000:
                os.remove(temp_path)
                if attempt < max_retries - 1:
                    if log_func:
                        log_func(f"  Download incomplete, retrying...\n", "dim")
                    time.sleep(2 ** attempt)
                    continue
                return None

            file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
            if log_func:
                log_func(f"  Downloaded: {file_size_mb:.1f} MB → {os.path.basename(temp_path)}\n", "dim")
            _THEME_FILE_CACHE[url] = temp_path
            return temp_path

        except Exception as e:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            if attempt < max_retries - 1:
                if log_func:
                    log_func(f"  Download error, retrying...\n", "dim")
                time.sleep(2 ** attempt)
                continue
            if log_func:
                log_func(f"  Download failed: {e}\n", "err")
            return None

    return None


def extract_audio_pcm(
    source: str,
    start_seconds: float = 0,
    duration_seconds: float = 0,
    sample_rate: int = 8000,
    log_func: Optional[Callable] = None,
) -> "Optional[numpy.ndarray]":
    """
    Extract audio as PCM data using ffmpeg.

    Args:
        source: Video or audio file path/URL
        start_seconds: Start position in seconds
        duration_seconds: Duration to extract (0 = all)
        sample_rate: Target sample rate
        log_func: Logging function

    Returns:
        Normalized numpy float32 mono PCM array, or None on failure
    """
    try:
        import numpy as np
    except ImportError:
        if log_func:
            log_func("  numpy not installed — pip install numpy\n", "err")
        return None

    if not shutil.which("ffmpeg"):
        if log_func:
            log_func("  ffmpeg not found in PATH\n", "err")
        return None

    # If source is a remote URL, download to temp file first for reliability
    actual_source = source
    if source.startswith("http://") or source.startswith("https://"):
        local_file = _download_to_temp(source, log_func)
        if local_file is None:
            return None
        actual_source = local_file

    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel", "error",
    ]

    if start_seconds > 0.001:
        cmd.extend(["-ss", f"{start_seconds:.3f}"])

    cmd.extend(["-i", actual_source])

    if duration_seconds > 0.001:
        cmd.extend(["-t", f"{duration_seconds:.3f}"])

    cmd.extend([
        "-vn",
        "-ar", str(sample_rate),
        "-ac", "1",
        "-f", "s16le",
        "pipe:1",
    ])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=300,
        )

        if result.returncode != 0 or len(result.stdout) < 512:
            if log_func and result.stderr:
                error_msg = result.stderr.decode(errors="replace").strip()[-200:]
                log_func(f"  ffmpeg error: {error_msg}\n", "err")
            return None

        # Convert to float32
        audio = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)

        if log_func:
            duration_extracted = len(audio) / sample_rate
            log_func(f"  PCM: {len(result.stdout)//1024}KB → {duration_extracted:.1f}s audio at {sample_rate}Hz\n", "dim")

        # Normalize by RMS for volume invariance
        rms = np.sqrt(np.mean(audio ** 2))
        if rms > 1.0:
            audio = audio / rms

        return audio

    except subprocess.TimeoutExpired:
        if log_func:
            log_func("  ffmpeg timeout\n", "err")
        return None
    except Exception as e:
        if log_func:
            log_func(f"  ffmpeg exception: {e}\n", "err")
        return None


def compute_ncc_fft(needle: "numpy.ndarray", haystack: "numpy.ndarray") -> "numpy.ndarray":
    """
    Compute Normalized Cross-Correlation using FFT.

    Returns array of correlation scores (values between -1 and 1).
    Length = len(haystack) - len(needle) + 1

    Uses proper local mean normalization for each window.
    """
    import numpy as np

    needle_len   = len(needle)
    haystack_len = len(haystack)

    if haystack_len < needle_len:
        return np.array([0.0])

    # Normalize needle (zero mean, unit norm)
    needle_centered = needle - needle.mean()
    needle_norm     = np.linalg.norm(needle_centered)

    if needle_norm < 1e-7:
        return np.zeros(haystack_len - needle_len + 1)

    needle_normalized = needle_centered / needle_norm

    # Compute FFT size (next power of 2)
    fft_size = 1
    while fft_size < (haystack_len + needle_len):
        fft_size <<= 1

    # ─── FFT cross-correlation ────────────────────────────────────────────────
    H           = np.fft.rfft(haystack, n=fft_size)
    N           = np.fft.rfft(needle_normalized[::-1], n=fft_size)
    correlation = np.fft.irfft(H * N, n=fft_size)[needle_len - 1:haystack_len]

    # ─── Local means via cumsum ───────────────────────────────────────────────
    cumsum      = np.concatenate(([0.0], np.cumsum(haystack)))
    local_sums  = cumsum[needle_len:haystack_len + 1] - cumsum[:haystack_len - needle_len + 1]
    local_means = local_sums / needle_len

    # ─── Local variances via cumsum ───────────────────────────────────────────
    cumsum_sq      = np.concatenate(([0.0], np.cumsum(haystack ** 2)))
    local_sums_sq  = cumsum_sq[needle_len:haystack_len + 1] - cumsum_sq[:haystack_len - needle_len + 1]
    local_means_sq = local_sums_sq / needle_len
    local_vars     = local_means_sq - local_means ** 2
    local_stds     = np.sqrt(np.maximum(local_vars, 0.0))
    local_stds[local_stds < 1e-7] = 1e-7  # Prevent division by zero

    # ─── NCC with local mean correction ──────────────────────────────────────
    ncc = correlation / (local_stds * np.sqrt(needle_len))

    # Clamp to valid range [-1, 1] (numerical errors can exceed this)
    ncc = np.clip(ncc, -1.0, 1.0)

    return ncc


def find_theme_start(
    video_path: str,
    theme_url: str,
    theme_duration_ms: int,
    search_start_seconds: float,
    search_end_seconds: float,
    log_func: Optional[Callable] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Optional[int]:
    """
    Find the start time of a theme song in a video using multi-phase audio matching.

    Uses three phases:
      1. COARSE (4kHz, 3s step) - Find approximate location
      2. MEDIUM (8kHz, 0.5s step) - Narrow down
      3. FINE (16kHz, 40ms step) - Precise timing

    Returns start time in milliseconds, or None if not found.
    """
    try:
        import numpy as np
    except ImportError:
        if log_func:
            log_func("  numpy not installed — pip install numpy\n", "err")
        return None

    if cancel_event and cancel_event.is_set():
        return None

    scan_duration = max(1.0, search_end_seconds - search_start_seconds)

    # ═══════════════════════════════════════════
    #  PHASE 1 — COARSE (SR=4000)
    # ═══════════════════════════════════════════
    sr1 = 4000

    if log_func:
        log_func(
            f"  [1/3] Loading (4kHz) theme={NEEDLE_DURATION_SECONDS:.0f}s "
            f"video={search_start_seconds:.0f}s→{search_end_seconds:.0f}s...\n",
            "dim",
        )

    needle1 = extract_audio_pcm(
        theme_url,
        start_seconds=NEEDLE_SKIP_SECONDS,
        duration_seconds=NEEDLE_DURATION_SECONDS,
        sample_rate=sr1,
        log_func=log_func,
    )

    haystack1 = extract_audio_pcm(
        video_path,
        start_seconds=search_start_seconds,
        duration_seconds=scan_duration,
        sample_rate=sr1,
        log_func=log_func,
    )

    if needle1 is None or haystack1 is None:
        if log_func:
            log_func("  Failed to load audio\n", "err")
        return None

    if cancel_event and cancel_event.is_set():
        return None

    needle1 = needle1[:int(NEEDLE_DURATION_SECONDS * sr1)]

    scores1    = compute_ncc_fft(needle1, haystack1)
    best_idx1  = int(np.argmax(scores1))
    best_score1 = float(scores1[best_idx1])
    best_time1  = search_start_seconds + best_idx1 / sr1

    if log_func:
        log_func(f"  [1/3] Best t={best_time1:.1f}s score={best_score1:.4f}\n", "dim")

    if best_score1 < NCC_THRESHOLD * 0.5:
        if log_func:
            log_func(
                f"  ✗ No match found in coarse scan (score={best_score1:.4f} < {NCC_THRESHOLD * 0.5:.2f})\n",
                "err",
            )
            log_func(f"    → Theme audio not detected in search window\n", "err")
        return None

    # ═══════════════════════════════════════════
    #  PHASE 2 — MEDIUM (SR=8000, ±15s)
    # ═══════════════════════════════════════════
    sr2     = 8000
    window2 = 15.0

    p2_start    = max(search_start_seconds, best_time1 - window2)
    p2_end      = min(search_end_seconds, best_time1 + window2 + NEEDLE_DURATION_SECONDS)
    p2_duration = max(1.0, p2_end - p2_start)

    if log_func:
        log_func(f"  [2/3] Refining (8kHz) {p2_start:.1f}s→{p2_end:.1f}s...\n", "dim")

    needle2 = extract_audio_pcm(
        theme_url,
        start_seconds=NEEDLE_SKIP_SECONDS,
        duration_seconds=NEEDLE_DURATION_SECONDS,
        sample_rate=sr2,
        log_func=log_func,
    )

    haystack2 = extract_audio_pcm(
        video_path,
        start_seconds=p2_start,
        duration_seconds=p2_duration,
        sample_rate=sr2,
        log_func=log_func,
    )

    best_time2  = best_time1
    best_score2 = best_score1

    if needle2 is not None and haystack2 is not None:
        if cancel_event and cancel_event.is_set():
            return None

        needle2 = needle2[:int(NEEDLE_DURATION_SECONDS * sr2)]
        scores2    = compute_ncc_fft(needle2, haystack2)
        best_idx2  = int(np.argmax(scores2))
        best_score2 = float(scores2[best_idx2])
        best_time2  = p2_start + best_idx2 / sr2

        if log_func:
            log_func(f"  [2/3] Best t={best_time2:.2f}s score={best_score2:.4f}\n", "dim")

    if best_score2 < NCC_THRESHOLD * 0.7:
        if log_func:
            log_func(
                f"  ✗ Low score after refining (score={best_score2:.4f} < {NCC_THRESHOLD * 0.7:.2f})\n",
                "err",
            )
            log_func(f"    → Weak match, likely false positive\n", "err")
        return None

    # ═══════════════════════════════════════════
    #  PHASE 3 — FINE (SR=16000, ±3s)
    # ═══════════════════════════════════════════
    sr3     = 16000
    window3 = 3.0

    p3_start    = max(search_start_seconds, best_time2 - window3)
    p3_end      = min(search_end_seconds, best_time2 + window3 + NEEDLE_DURATION_SECONDS)
    p3_duration = max(1.0, p3_end - p3_start)

    if log_func:
        log_func(f"  [3/3] Precision (16kHz) {p3_start:.1f}s→{p3_end:.1f}s...\n", "dim")

    needle3 = extract_audio_pcm(
        theme_url,
        start_seconds=NEEDLE_SKIP_SECONDS,
        duration_seconds=NEEDLE_DURATION_SECONDS,
        sample_rate=sr3,
        log_func=log_func,
    )

    haystack3 = extract_audio_pcm(
        video_path,
        start_seconds=p3_start,
        duration_seconds=p3_duration,
        sample_rate=sr3,
        log_func=log_func,
    )

    best_time3  = best_time2
    best_score3 = best_score2

    if needle3 is not None and haystack3 is not None:
        if cancel_event and cancel_event.is_set():
            return None

        needle3 = needle3[:int(NEEDLE_DURATION_SECONDS * sr3)]
        scores3    = compute_ncc_fft(needle3, haystack3)
        best_idx3  = int(np.argmax(scores3))
        best_score3 = float(scores3[best_idx3])
        best_time3  = p3_start + best_idx3 / sr3

        if log_func:
            log_func(f"  [3/3] Best t={best_time3:.3f}s score={best_score3:.4f}\n", "dim")

    # ─── Final Result ──────────────────────────────────────────────────────────
    if best_score3 >= NCC_THRESHOLD:
        # Adjust for needle skip to get true start
        true_start = best_time3 - NEEDLE_SKIP_SECONDS
        true_start = max(search_start_seconds, true_start)
        start_ms   = int(round(true_start * 1000))

        confidence = "high" if best_score3 >= 0.55 else "medium"

        if log_func:
            log_func(
                f"  Match [{confidence}] {ms_to_display(start_ms)} "
                f"({true_start:.3f}s) score={best_score3:.4f}\n",
                "ok",
            )

        return start_ms

    if log_func:
        log_func(
            f"  ✗ No match found. Best score={best_score3:.4f} < threshold={NCC_THRESHOLD}\n",
            "err",
        )
        log_func(
            f"    → Possible causes: wrong theme, different audio mix, or theme not in video\n",
            "err",
        )

    return None
