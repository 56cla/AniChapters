"""
Chapter list construction and MKV XML serialisation.
"""
from __future__ import annotations

from typing import Optional

from models import Chapter, MatchSource
from timestamps import ms_to_mkv_timestamp


def build_chapters(
    op_start: Optional[int],
    op_end:   Optional[int],
    op_label: str,
    op_source: MatchSource,
    ed_start: Optional[int],
    ed_end:   Optional[int],
    ed_label: str,
    ed_source: MatchSource,
    video_duration: Optional[int],
    ch_names: Optional[dict] = None,
) -> list[Chapter]:
    """
    Build chapter list from detected timings.

    IMPORTANT: "After Credits" is ONLY added when ED was found via audio match.
    If ED was not found or was estimated, no "After Credits" chapter is added.
    """
    # Use provided names or fall back to defaults
    n = ch_names or {}
    _cold_open    = n.get("cold_open",    "Cold Open")
    _episode      = n.get("episode",      "Episode")
    _after_credits= n.get("after_credits","After Credits")
    _end          = n.get("end",          "End")

    chapters: list[Chapter] = []

    # Opening section
    if op_start is not None:
        if op_start > 3000:
            chapters.append(Chapter(0, _cold_open, MatchSource.NONE))
        chapters.append(Chapter(op_start, op_label, op_source))

        if op_end is not None:
            # "Episode" chapter starting right after the OP ends.
            chapters.append(Chapter(op_end, _episode, op_source))

            # ── Post-OP coverage check ────────────────────────────────────────
            # Decide whether we need an explicit chapter that stretches from
            # op_end all the way to the end of the video.
            #
            # We do NOT need one when:
            #   - An ED chapter (AUDIO or FALLBACK) starts after op_end.
            #     The ED chapter itself acts as the boundary, so the Episode
            #     chapter above already covers op_end → ed_start implicitly.
            #
            # We DO need one when:
            #   - No ED was detected at all (ed_start is None or ed_source NONE).
            #     Without a closing chapter, media players may not show a
            #     distinct "Episode" entry reaching the end marker, depending
            #     on the player.  Adding an explicit End-anchored chapter makes
            #     the intent unambiguous.
            #
            # In practice the chapter list already handles this correctly:
            # the Episode chapter at op_end is followed by either the ED chapter
            # or the End marker — both cases give correct playback navigation.
            # No extra chapter insertion is required; the guard is kept here
            # as a documented no-op so future contributors understand the intent.

            ed_covers_post_op = (
                ed_start is not None
                and ed_start > op_end
                and ed_source in (MatchSource.AUDIO, MatchSource.FALLBACK)
            )
            # ed_covers_post_op is evaluated but intentionally not used to insert
            # an extra chapter — the existing Episode + ED/End chain is sufficient.
            _ = ed_covers_post_op   # suppress unused-variable warnings
            # ── End post-OP check ─────────────────────────────────────────────

    else:
        # No OP detected — episode starts from the very beginning
        chapters.append(Chapter(0, _episode, MatchSource.NONE))

    # Ending section - ONLY add if ED was found via audio match
    if ed_start is not None and ed_source == MatchSource.AUDIO:
        chapters.append(Chapter(ed_start, ed_label, ed_source))
        if ed_end is not None and video_duration and video_duration - ed_end > 5000:
            chapters.append(Chapter(ed_end, _after_credits, ed_source))
    elif ed_start is not None and ed_source == MatchSource.FALLBACK:
        chapters.append(Chapter(ed_start, f"{ed_label} (estimated)", ed_source))
    # If ed_source is NONE, don't add any ED chapter

    # End marker
    if video_duration:
        chapters.append(Chapter(video_duration, _end, MatchSource.NONE))

    # Remove duplicates by timestamp
    seen: set[int] = set()
    unique_chapters: list[Chapter] = []
    for chapter in sorted(chapters, key=lambda c: c.timestamp_ms):
        if chapter.timestamp_ms not in seen:
            seen.add(chapter.timestamp_ms)
            unique_chapters.append(chapter)

    return unique_chapters


def write_chapters_xml(chapters: list[Chapter], output_path: str) -> bool:
    """Write chapters to MKV-compatible XML file"""
    def escape_xml(s: str) -> str:
        return (
            str(s)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE Chapters SYSTEM "matroskachapters.dtd">',
        '<Chapters>',
        '  <EditionEntry>',
        '    <EditionFlagHidden>0</EditionFlagHidden>',
        '    <EditionFlagDefault>1</EditionFlagDefault>',
    ]

    for chapter in chapters:
        lines += [
            '    <ChapterAtom>',
            f'      <ChapterTimeStart>{ms_to_mkv_timestamp(chapter.timestamp_ms)}</ChapterTimeStart>',
            '      <ChapterFlagHidden>0</ChapterFlagHidden>',
            '      <ChapterFlagEnabled>1</ChapterFlagEnabled>',
            '      <ChapterDisplay>',
            f'        <ChapterString>{escape_xml(chapter.name)}</ChapterString>',
            '        <ChapterLanguage>und</ChapterLanguage>',
            '      </ChapterDisplay>',
            '    </ChapterAtom>',
        ]

    lines += ['  </EditionEntry>', '</Chapters>']

    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write('\n'.join(lines) + '\n')
        return True
    except IOError:
        return False