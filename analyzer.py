"""
High-level video analysis: bridges the GUI (app.py) with the Auto_Chap v4.2
engine (core.py).

All matching logic lives in core.py — untouched.
This file only:
  1. Builds the args namespace that core.py expects.
  2. Calls core.run_autochap().
  3. Reads the resulting .chapters.txt and converts it to an AnalysisResult
     that the rest of the GUI (chapters.py, dialogs.py, …) can consume.
"""
from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Callable, Optional

import core  # core.py — Auto_Chap v4.2, DO NOT MODIFY

from episode import extract_episode_number
from ffprobe_utils import get_video_duration_ms
from models import AnalysisResult, Chapter, MatchSource, Theme
from timestamps import ms_to_display, timestamp_to_ms
from shared_db import compute_confidence, get_shared_db


# ── helpers ───────────────────────────────────────────────────────────────────

class _Args:
    """Minimal namespace that mimics the argparse result core.py expects."""

    def __init__(
        self,
        input_path: Path,
        output_path: Path,
        work_path: Path,
        search_name: str,
        year: Optional[int]  = None,
        score: int           = 2000,
        theme_portion: float = 0.90,
        downsample: int      = 32,
        parallel_dl: int     = 10,
        episode_snap: float  = 4.0,
        snap: Optional[int]  = None,
        delete_themes: bool  = False,
        charts: bool         = False,
    ):
        self.input               = input_path
        self.output              = output_path
        self.work_path           = work_path
        self.search_name         = search_name if search_name else None
        self.no_download         = not bool(search_name)
        self.year                = year
        self.score               = score
        self.theme_portion       = theme_portion
        self.downsample          = downsample
        self.parallel_dl         = parallel_dl
        self.episode_snap        = episode_snap
        self.snap                = snap
        self.delete_themes       = delete_themes
        self.charts              = charts
        self.episode_audio_path  = None   # filled by core.extract_episode_audio


def _parse_chapters_txt(txt_path: str) -> list[tuple[int, str]]:
    """
    Parse the .chapters.txt written by core.generate_chapters().

    Format (OGM / mkvmerge simple):
        CHAPTER01=00:00:00.000
        CHAPTER01NAME=Opening
        ...

    Returns list of (timestamp_ms, name) sorted by time.
    """
    entries: dict[str, dict] = {}

    try:
        with open(txt_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                m_time = re.match(r"CHAPTER(\d+)=(.+)", line)
                m_name = re.match(r"CHAPTER(\d+)NAME=(.+)", line)
                if m_time:
                    idx, ts = m_time.group(1), m_time.group(2)
                    entries.setdefault(idx, {})["ts"] = ts
                elif m_name:
                    idx, name = m_name.group(1), m_name.group(2)
                    entries.setdefault(idx, {})["name"] = name
    except Exception:
        return []

    result = []
    for idx in sorted(entries.keys()):
        entry = entries[idx]
        ts   = entry.get("ts", "")
        name = entry.get("name", "")
        ms   = timestamp_to_ms(ts) if ts else None
        if ms is not None and name:
            result.append((ms, name))

    return sorted(result, key=lambda x: x[0])


def _chapters_to_analysis_result(
    video_path: str,
    chapters: list[tuple[int, str]],
    xml_path: str,
    video_duration_ms: Optional[int],
) -> AnalysisResult:
    """
    Convert parsed chapter list into an AnalysisResult for the GUI.

    core.py uses these names:
        Prologue / Opening / Episode / Ending / Epilogue
    We map them back to op_start/op_end/ed_start/ed_end.
    """
    basename = os.path.basename(video_path)
    episode  = extract_episode_number(video_path)

    result = AnalysisResult(
        video_path=video_path,
        basename=basename,
        episode=episode,
        video_duration_ms=video_duration_ms,
        xml_path=xml_path,
    )

    name_map = {name.lower(): ms for ms, name in chapters}

    # Opening start/end
    if "opening" in name_map:
        result.op_start_ms = name_map["opening"]
        result.op_source   = MatchSource.AUDIO
        if "episode" in name_map:
            result.op_end_ms = name_map["episode"]

    # Ending start/end
    if "ending" in name_map:
        result.ed_start_ms = name_map["ending"]
        result.ed_source   = MatchSource.AUDIO
        if "epilogue" in name_map:
            result.ed_end_ms = name_map["epilogue"]

    # Build Chapter objects for the XML / review dialog
    result.chapters = [
        Chapter(ms, name, MatchSource.AUDIO)
        for ms, name in chapters
    ]

    return result




def _result_from_cached_chapters(
    video_path: str,
    chapters: list[Chapter],
    xml_path: str,
    video_duration_ms: Optional[int],
) -> AnalysisResult:
    """
    Build an AnalysisResult from chapters loaded from the Shared Database.

    Returns an AnalysisResult with op_source / ed_source = AUDIO
    so the chapters are treated as reliable results elsewhere in the program.
    """
    basename = os.path.basename(video_path)
    episode  = extract_episode_number(video_path)

    result = AnalysisResult(
        video_path=video_path,
        basename=basename,
        episode=episode,
        video_duration_ms=video_duration_ms,
        xml_path=xml_path,
        chapters=chapters,
    )

    # Infer op/ed timings from known chapter names
    name_map = {ch.name.lower(): ch.timestamp_ms for ch in chapters}

    if "opening" in name_map:
        result.op_start_ms = name_map["opening"]
        result.op_source   = MatchSource.AUDIO
        if "episode" in name_map:
            result.op_end_ms = name_map["episode"]

    if "ending" in name_map:
        result.ed_start_ms = name_map["ending"]
        result.ed_source   = MatchSource.AUDIO
        if "epilogue" in name_map:
            result.ed_end_ms = name_map["epilogue"]

    return result

# ── public API (same signature app.py / analyzer-callers expect) ──────────────

def analyze_video(
    video_path: str,
    themes: list[Theme],           # kept for API compatibility — core.py fetches its own
    log_func: Optional[Callable]  = None,
    cancel_event: Optional[threading.Event] = None,
    *,
    search_name: str      = "",
    year: Optional[int]   = None,
    score: int            = 2000,
    theme_portion: float  = 0.90,
    downsample: int       = 32,
    work_path: Optional[str] = None,
    # ── Shared Database ─────────────────────────────────────────────────────
    db_meta: Optional[dict] = None,
    # db_meta = {
    #     "anime_id":      int,    # AniList ID    (required for DB lookup)
    #     "anime_title":   str,    # Anime title   (stored in DB)
    #     "season_number": int,    # Season number (required for DB lookup)
    # }
    # Passed from app.py after anime selection and ID resolution.
    # If None, DB integration is skipped entirely.
) -> AnalysisResult:
    """
    Analyze a single video file using Auto_Chap v4.2 (core.py).

    Parameters
    ----------
    video_path   : Path to the .mkv / video file.
    themes       : Ignored — kept so app.py doesn't need changes.
                   core.py downloads its own themes from animethemes.moe.
    log_func     : Optional callback(message, tag) for GUI log panel.
    cancel_event : Optional threading.Event for cancellation support.
    search_name  : Anime name to search on animethemes.moe.
    db_meta      : Optional dict with anime_id, anime_title, season_number.
                   When provided, the shared DB is checked before analysis
                   and updated after.
    """

    def log(msg: str, tag: str = "dim"):
        if log_func:
            log_func(msg, tag)

    basename          = os.path.basename(video_path)
    episode           = extract_episode_number(video_path)
    video_duration_ms = get_video_duration_ms(video_path)

    log(f"\n{'─' * 54}\n", "dim")
    log(f"▶ {basename}\n", "ch")
    log(f"  Episode: {episode if episode is not None else '?'}\n", "dim")

    # ── [1] Shared Database — check before analysis ───────────────────────────
    if db_meta and episode is not None:
        anime_id      = db_meta.get("anime_id")
        anime_title   = db_meta.get("anime_title", search_name or "Unknown")
        season_number = db_meta.get("season_number", 1)

        if anime_id:
            db = get_shared_db()
            log(
                f"  [DB] Checking shared DB "
                f"(anime_id={anime_id}, S{season_number:02d}E{episode:02d})…\n",
                "dim",
            )
            cached_row = db.lookup(
                anime_id=anime_id,
                season_number=season_number,
                episode_number=episode,
            )
            if cached_row:
                # cached_row["chapters"] is a ready list[Chapter] from the orchestrator
                cached_chapters = cached_row.get("chapters", [])
                if cached_chapters:
                    src_label = cached_row.get("source", "db")
                    log(
                        f"  [DB] ✔ Found in shared DB ({src_label})"
                        f" confidence={cached_row['confidence']}"
                        f" used={cached_row['use_count']}x\n",
                        "ok",
                    )
                    log("  [DB] Skipping audio analysis — loading from DB\n", "ok")

                    # Build a full AnalysisResult from the cached chapters
                    xml_path = os.path.splitext(video_path)[0] + "_chapters.xml"
                    result   = _result_from_cached_chapters(
                        video_path, cached_chapters, xml_path, video_duration_ms,
                    )

                    from chapters import write_chapters_xml
                    write_chapters_xml(result.chapters, xml_path)

                    # Log the chapters
                    for ch in cached_chapters:
                        log(f"    {ms_to_display(ch.timestamp_ms)}  →  {ch.name}\n", "ch")

                    return result

            log("  [DB] Not found — proceeding with audio analysis\n", "dim")
    else:
        anime_id      = None
        anime_title   = search_name or "Unknown"
        season_number = 1

    # ── temp output paths ─────────────────────────────────────────────────────
    chapters_txt = os.path.splitext(video_path)[0] + ".autochap_tmp.txt"
    xml_path     = os.path.splitext(video_path)[0] + "_chapters.xml"

    # Themes stored in .themes folder next to the video file
    effective_work_path = Path(work_path) if work_path else Path(os.path.dirname(video_path))

    args = _Args(
        input_path   = Path(video_path),
        output_path  = Path(chapters_txt),
        work_path    = effective_work_path,
        search_name  = search_name,
        year         = year,
        score        = score,
        theme_portion= theme_portion,
        downsample   = downsample,
    )

    # ── run Auto_Chap engine ──────────────────────────────────────────────────
    if cancel_event and cancel_event.is_set():
        return AnalysisResult(
            video_path=video_path, basename=basename,
            episode=episode, video_duration_ms=video_duration_ms,
        )

    try:
        core.run_autochap(args)
    except SystemExit:
        # core.py calls sys.exit() on fatal errors — treat as no-match
        log("  Auto_Chap exited (no match or error)\n", "err")
        return AnalysisResult(
            video_path=video_path, basename=basename,
            episode=episode, video_duration_ms=video_duration_ms,
        )
    except Exception as exc:
        log(f"  Auto_Chap error: {exc}\n", "err")
        return AnalysisResult(
            video_path=video_path, basename=basename,
            episode=episode, video_duration_ms=video_duration_ms,
        )

    # ── read output ───────────────────────────────────────────────────────────
    if not os.path.exists(chapters_txt):
        log("  No chapters output produced\n", "err")
        return AnalysisResult(
            video_path=video_path, basename=basename,
            episode=episode, video_duration_ms=video_duration_ms,
        )

    chapters = _parse_chapters_txt(chapters_txt)

    # Clean up temp file
    try:
        os.remove(chapters_txt)
    except Exception:
        pass

    if not chapters:
        log("  Could not parse chapters output\n", "err")
        return AnalysisResult(
            video_path=video_path, basename=basename,
            episode=episode, video_duration_ms=video_duration_ms,
        )

    log(f"  Chapters found: {len(chapters)}\n", "ok")
    for ms, name in chapters:
        log(f"    {ms_to_display(ms)}  →  {name}\n", "ch")

    # ── convert to AnalysisResult + write XML ─────────────────────────────────
    result = _chapters_to_analysis_result(video_path, chapters, xml_path, video_duration_ms)

    from chapters import write_chapters_xml
    write_chapters_xml(result.chapters, xml_path)

    # ── [2] Shared Database — save after analysis ─────────────────────────────
    if anime_id and episode is not None and result.chapters:
        db         = get_shared_db()
        confidence = compute_confidence(result.op_source, result.ed_source)
        saved      = db.upsert(
            anime_id=anime_id,
            anime_title=anime_title,
            season_number=season_number,
            episode_number=episode,
            chapters=result.chapters,
            confidence=confidence,
        )
        if saved:
            log(
                f"  [DB] ✔ Saved to shared DB "
                f"(confidence={confidence})\n",
                "ok",
            )
        else:
            # Show the real reason for the failure
            err_detail = getattr(db, "_last_remote_error", None) or "unknown error"
            log(f"  [DB] ✘ Could not save to shared DB\n", "err")
            log(f"  [DB]   Reason: {err_detail}\n", "err")

            # Suggestions based on error type
            if "not configured" in err_detail.lower():
                log("  [DB]   → Add SUPABASE_URL and SUPABASE_KEY to supabase_config.py\n", "dim")
            elif "42P01" in err_detail or "does not exist" in err_detail:
                log("  [DB]   → Table missing — run supabase_setup.sql in Supabase SQL Editor\n", "dim")
            elif "42501" in err_detail or "permission" in err_detail.lower():
                log("  [DB]   → RLS blocking write — check policy allow_public_insert\n", "dim")
            elif "JWT" in err_detail or "401" in err_detail:
                log("  [DB]   → anon key is invalid or expired\n", "dim")
            elif "Network" in err_detail or "timed out" in err_detail:
                log("  [DB]   → Check your internet connection\n", "dim")

            log("  [DB]   Chapters are saved in the local cache regardless\n", "dim")
            log("  [DB]   Click 'DB STATS' for full diagnostics\n", "dim")

    return result

