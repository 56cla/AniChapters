"""
Data models: MatchSource enum, Theme, Chapter, AnalysisResult dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MatchSource(Enum):
    """Source of chapter timing match"""
    AUDIO    = "audio"
    FALLBACK = "fallback"
    MANUAL   = "manual"
    NONE     = "none"


@dataclass
class Theme:
    """Anime theme song information"""
    label:       str
    type:        str          # "OP" or "ED"
    sequence:    int
    version:     int
    title:       str
    video_url:   str
    duration_ms: Optional[int]       = None
    episode_set: set[int]            = field(default_factory=set)

    @property
    def full_label(self) -> str:
        """Get full label with version if applicable"""
        if self.version > 1:
            return f"{self.type}{self.sequence}v{self.version}"
        return f"{self.type}{self.sequence}"


@dataclass
class Chapter:
    """Single chapter entry"""
    timestamp_ms: int
    name:         str
    source:       MatchSource = MatchSource.NONE


@dataclass
class AnalysisResult:
    """Result of analyzing a single video"""
    video_path:        str
    basename:          str
    episode:           Optional[int]
    video_duration_ms: Optional[int]

    op_theme:   Optional[Theme] = None
    ed_theme:   Optional[Theme] = None

    op_start_ms: Optional[int] = None
    op_end_ms:   Optional[int] = None
    op_source:   MatchSource   = MatchSource.NONE

    ed_start_ms: Optional[int] = None
    ed_end_ms:   Optional[int] = None
    ed_source:   MatchSource   = MatchSource.NONE

    xml_path: Optional[str]    = None
    chapters: list[Chapter]    = field(default_factory=list)
