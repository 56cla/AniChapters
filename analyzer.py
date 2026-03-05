"""
High-level video analysis: ties together episode detection, theme selection,
and audio matching to produce an AnalysisResult.
"""
from __future__ import annotations

import os
import threading
from typing import Callable, Optional

from api_animethemes import select_theme_for_episode
from audio_matcher import find_theme_start
from constants import ED_SEARCH_START_SECONDS, OP_SEARCH_END_SECONDS
from episode import extract_episode_number
from ffprobe_utils import get_video_duration_ms
from models import AnalysisResult, MatchSource, Theme
from timestamps import ms_to_display


def analyze_video(
    video_path: str,
    themes: list[Theme],
    log_func: Optional[Callable] = None,
    cancel_event: Optional[threading.Event] = None,
) -> AnalysisResult:
    """Analyze a single video file for OP/ED timestamps"""

    def log(msg: str, tag: str = "dim"):
        if log_func:
            log_func(msg, tag)

    basename         = os.path.basename(video_path)
    episode          = extract_episode_number(video_path)

    log(f"\n{'─' * 54}\n", "dim")
    log(f"▶ {basename}\n", "ch")
    log(f"  Episode: {episode if episode is not None else '?'}\n", "dim")

    video_duration_ms = get_video_duration_ms(video_path)
    video_duration_s  = (video_duration_ms or 1_440_000) / 1000.0

    # Select themes with diagnostics
    log("  [Theme Selection]\n", "dim")
    op_theme = select_theme_for_episode(themes, "OP", episode, log_func)
    ed_theme = select_theme_for_episode(themes, "ED", episode, log_func)

    # Log theme info
    for role, theme in [("OP", op_theme), ("ED", ed_theme)]:
        if theme:
            eps = sorted(theme.episode_set) if theme.episode_set else []
            dur = f"{theme.duration_ms // 1000}s" if theme.duration_ms else "?s"
            log(
                f"  {role} → {theme.label} \"{theme.title}\" "
                f"dur={dur} eps={eps or 'all'}\n",
                "th",
            )
        else:
            log(f"  {role} → not found in API\n", "err")

    result = AnalysisResult(
        video_path=video_path,
        basename=basename,
        episode=episode,
        video_duration_ms=video_duration_ms,
        op_theme=op_theme,
        ed_theme=ed_theme,
    )

    # ─── Search for OP (first 5 minutes) ──────────────────────────────────────
    if op_theme and op_theme.video_url and op_theme.duration_ms:
        if cancel_event and cancel_event.is_set():
            return result

        op_dur   = op_theme.duration_ms
        op_end_s = min(video_duration_s, OP_SEARCH_END_SECONDS)

        log(f"  [OP] Searching 0s → {op_end_s:.0f}s...\n", "dim")

        start_ms = find_theme_start(
            video_path,
            op_theme.video_url,
            op_dur,
            search_start_seconds=0.0,
            search_end_seconds=op_end_s,
            log_func=log_func,
            cancel_event=cancel_event,
        )

        if start_ms is not None:
            result.op_start_ms = start_ms
            result.op_end_ms   = start_ms + op_dur
            result.op_source   = MatchSource.AUDIO
            log(f"  [OP] Found {ms_to_display(start_ms)} → {ms_to_display(result.op_end_ms)}\n", "ok")
        else:
            result.op_start_ms = 0
            result.op_end_ms   = op_dur
            result.op_source   = MatchSource.FALLBACK
            log(f"  [OP] Fallback: 00:00:00 → {ms_to_display(op_dur)}\n", "err")

    # ─── Search for ED (last 8 minutes) ───────────────────────────────────────
    if ed_theme and ed_theme.video_url and ed_theme.duration_ms:
        if cancel_event and cancel_event.is_set():
            return result

        ed_dur    = ed_theme.duration_ms
        ed_start_s = max(0.0, video_duration_s - ED_SEARCH_START_SECONDS)

        log(f"  [ED] Searching {ed_start_s:.0f}s → {video_duration_s:.0f}s...\n", "dim")
        log(
            f"  [ED] Theme: {ed_theme.label}, Duration: {ed_dur // 1000}s, "
            f"URL: {ed_theme.video_url[:50]}...\n",
            "dim",
        )

        start_ms = find_theme_start(
            video_path,
            ed_theme.video_url,
            ed_dur,
            search_start_seconds=ed_start_s,
            search_end_seconds=video_duration_s,
            log_func=log_func,
            cancel_event=cancel_event,
        )

        if start_ms is not None:
            result.ed_start_ms = start_ms
            result.ed_end_ms   = start_ms + ed_dur
            result.ed_source   = MatchSource.AUDIO
            log(f"  [ED] Found {ms_to_display(start_ms)} → {ms_to_display(result.ed_end_ms)}\n", "ok")

            # Only add After Credits if there's meaningful content after ED ends
            if video_duration_ms and video_duration_ms - result.ed_end_ms > 5000:
                log(
                    f"  [ED] After Credits: {ms_to_display(result.ed_end_ms)} "
                    f"→ {ms_to_display(video_duration_ms)}\n",
                    "dim",
                )
        else:
            # ED not found via audio matching - DO NOT add fallback timing
            # Let user manually set it in review dialog
            result.ed_source = MatchSource.NONE
            log(f"  [ED] NOT FOUND - audio match failed. Check if correct theme was selected.\n", "err")
            log(
                f"  [ED] Possible issues: wrong theme selected, audio differs from reference, "
                f"or ED missing from video\n",
                "err",
            )
    else:
        # No ED theme available from API
        if ed_theme:
            if not ed_theme.video_url:
                log(f"  [ED] Theme exists but no video URL available\n", "err")
            elif not ed_theme.duration_ms:
                log(f"  [ED] Theme exists but duration unknown\n", "err")
        result.ed_source = MatchSource.NONE

    return result
