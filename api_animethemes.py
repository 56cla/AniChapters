"""
animethemes.moe API client: search, theme fetching, episode-aware theme selection.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Callable, Optional

from constants import API_BASE, API_HEADERS, API_TIMEOUT
from models import Theme


def api_request(url: str, timeout: int = API_TIMEOUT) -> dict:
    """Make API request and return JSON response"""
    request = urllib.request.Request(url, headers=API_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search_anime(query: str) -> list[dict]:
    """Search for anime by name"""
    encoded_query = urllib.parse.quote(query)
    url = f"{API_BASE}/anime?q={encoded_query}&fields[anime]=name,slug,year&page[size]=10"
    data = api_request(url)
    return data.get("anime", [])


def parse_episode_set(episodes_str: str) -> set[int]:
    """Parse episode string like '1-3, 5, 7-9' into set of integers"""
    if not episodes_str or not episodes_str.strip():
        return set()

    episodes: set[int] = set()

    for part in re.split(r"[,\u060c]", episodes_str):  # support ASCII and Arabic comma
        part = part.strip()

        # Range like "1-3" or "1–3"
        range_match = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if range_match:
            start, end = int(range_match.group(1)), int(range_match.group(2))
            episodes.update(range(start, end + 1))
            continue

        # Open-ended like "1-"
        open_match = re.match(r"^(\d+)\s*[-–]$", part)
        if open_match:
            start = int(open_match.group(1))
            episodes.update(range(start, 1000))  # Arbitrary upper limit
            continue

        # Single episode
        single_match = re.match(r"^(\d+)$", part)
        if single_match:
            episodes.add(int(single_match.group(1)))

    return episodes


def get_anime_themes(slug: str, log_func: Optional[Callable] = None) -> list[Theme]:
    """
    Fetch all theme songs for an anime.

    Returns list of Theme objects with:
      - label: "OP1", "ED1", "ED1v2", etc.
      - type: "OP" | "ED"
      - title: Song title
      - video_url: Direct video link
      - duration_ms: Duration (filled later with ffprobe)
      - episode_set: Episodes this theme applies to
    """
    url = (
        f"{API_BASE}/anime/{slug}"
        "?include=animethemes.animethemeentries.videos,animethemes.song"
        "&fields[animetheme]=type,sequence"
        "&fields[animethemeentry]=episodes,version"
        "&fields[video]=link"
        "&fields[song]=title"
    )

    data = api_request(url)
    themes_data = data.get("anime", {}).get("animethemes", [])
    themes: list[Theme] = []

    for theme in themes_data:
        theme_type  = theme.get("type", "OP")
        sequence    = theme.get("sequence") or 1
        song_title  = (theme.get("song") or {}).get("title", "Unknown")

        for entry in theme.get("animethemeentries", []):
            version     = entry.get("version") or 1
            episode_set = parse_episode_set(entry.get("episodes") or "")
            videos      = entry.get("videos") or []

            if not videos:
                continue

            video_url = videos[0].get("link")
            if not video_url:
                continue

            theme_obj = Theme(
                label=f"{theme_type}{sequence}" + (f"v{version}" if version > 1 else ""),
                type=theme_type,
                sequence=sequence,
                version=version,
                title=song_title,
                video_url=video_url,
                duration_ms=None,
                episode_set=episode_set,
            )
            themes.append(theme_obj)

    return themes


def select_theme_for_episode(
    themes: list[Theme],
    theme_type: str,
    episode: Optional[int],
    log_func: Optional[Callable] = None
) -> Optional[Theme]:
    """
    Select the appropriate theme version for a specific episode.

    Selection priority:
    1. Theme whose episode_set explicitly contains the episode
    2. Theme without episode restrictions (applies to all)
    3. Theme with highest sequence whose min_episode <= current episode
    4. Fallback to last theme (most recent)
    """
    candidates = [t for t in themes if t.type == theme_type]

    if not candidates:
        return None

    if episode is None:
        if log_func:
            log_func(f"    [{theme_type}] No episode number, using first theme: {candidates[0].label}\n", "dim")
        return candidates[0]

    # First, try to find a theme that specifically includes this episode
    for theme in candidates:
        if theme.episode_set and episode in theme.episode_set:
            if log_func:
                eps = sorted(theme.episode_set) if theme.episode_set else []
                log_func(f"    [{theme_type}] Selected {theme.label}: ep {episode} in {eps}\n", "dim")
            return theme

    # Then, try themes without episode restrictions (applies to all)
    for theme in candidates:
        if not theme.episode_set:
            if log_func:
                log_func(f"    [{theme_type}] Selected {theme.label}: no episode restriction\n", "dim")
            return theme

    # If episode not in any set, pick theme with highest sequence whose min_episode <= episode
    # This handles cases like ED2 with eps=[3] meaning "from episode 3 onwards"
    best_theme: Optional[Theme] = None
    best_min_ep = -1

    for theme in candidates:
        if theme.episode_set:
            min_ep = min(theme.episode_set)
            # Theme applies if its range starts at or before current episode
            if min_ep <= episode:
                # Prefer higher sequence or later starting episode
                if best_theme is None or theme.sequence > best_theme.sequence or min_ep > best_min_ep:
                    best_theme  = theme
                    best_min_ep = min_ep

    if best_theme:
        if log_func:
            eps = sorted(best_theme.episode_set) if best_theme.episode_set else []
            log_func(
                f"    [{theme_type}] Selected {best_theme.label}: "
                f"min_ep={best_min_ep} <= ep{episode} (fallback logic)\n",
                "dim"
            )
        return best_theme

    # Ultimate fallback: use the last theme (most recent sequence)
    result = candidates[-1]
    if log_func:
        log_func(
            f"    [{theme_type}] No matching theme found for ep {episode}, "
            f"using last: {result.label}\n",
            "err"
        )
    return result
