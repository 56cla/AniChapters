"""
api_anilist.py — AniList GraphQL Client

Used only to fetch:
  - anime_id     : the numeric ID in AniList (stable and unique per anime)
  - season_number: season number inferred from AniList relations

Why AniList and not animethemes.moe?
  animethemes.moe uses a text slug (unstable if the name changes),
  while AniList ID is a stable integer — a permanent database key.

Usage:
    from api_anilist import resolve_anime_ids

    ids = resolve_anime_ids(anime_title="Fullmetal Alchemist Brotherhood", year=2009)
    # ids = {"anime_id": 5114, "season_number": 1}   or None on failure
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

# ── GraphQL endpoint ──────────────────────────────────────────────────────────
ANILIST_API  = "https://graphql.anilist.co"
_REQ_TIMEOUT = 12

# ── Query ─────────────────────────────────────────────────────────────────────
# Fetch ID + relations to infer season number
_SEARCH_QUERY = """
query ($search: String, $year: Int) {
  Media(search: $search, seasonYear: $year, type: ANIME, format_in: [TV, TV_SHORT]) {
    id
    title { romaji english native }
    seasonYear
    relations {
      edges {
        relationType(version: 2)
        node {
          id
          type
          format
          sequelOf: relations {
            edges {
              relationType(version: 2)
              node { id }
            }
          }
        }
      }
    }
  }
}
"""

# Simpler query without year when not provided
_SEARCH_QUERY_NO_YEAR = """
query ($search: String) {
  Media(search: $search, type: ANIME, format_in: [TV, TV_SHORT]) {
    id
    title { romaji english native }
    seasonYear
    relations {
      edges {
        relationType(version: 2)
        node { id type format }
      }
    }
  }
}
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _anilist_request(query: str, variables: dict) -> Optional[dict]:
    """Send a GraphQL request and return media data or None."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        ANILIST_API,
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Accept":        "application/json",
            "User-Agent":    "AniChapters/9.2",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_REQ_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
        return data.get("data", {}).get("Media")
    except Exception:
        return None


def _estimate_season_number(media: dict) -> int:
    """
    Infer season number from AniList relations.

    Logic:
      - If the anime has no PREQUEL → season 1
      - Number of PREQUEL relations + 1 = season number
      (reasonable approximation — AniList does not store season_number explicitly)
    """
    relations = media.get("relations", {}).get("edges", [])
    prequel_count = sum(
        1
        for edge in relations
        if edge.get("relationType") == "PREQUEL"
        and edge.get("node", {}).get("type") == "ANIME"
        and edge.get("node", {}).get("format") in ("TV", "TV_SHORT", None)
    )
    return prequel_count + 1


# ─────────────────────────────────────────────────────────────────────────────
#  Public interface
# ─────────────────────────────────────────────────────────────────────────────

def resolve_anime_ids(
    anime_title: str,
    year:        Optional[int] = None,
) -> Optional[dict]:
    """
    Search AniList for the anime and return:
        {
            "anime_id":      <int>,   # AniList ID
            "season_number": <int>,   # 1, 2, 3, ...
            "anime_title":   <str>,   # official title
        }

    Returns None if the request fails or no result is found.

    Priority:
      1. Try with release year if provided.
      2. If that fails, try without year (fallback).
    """
    if not anime_title or not anime_title.strip():
        return None

    # ── Attempt with year ────────────────────────────────────────────────────
    if year:
        media = _anilist_request(
            _SEARCH_QUERY,
            {"search": anime_title, "year": year},
        )
        if media:
            return _build_result(media)

    # ── Attempt without year (fallback) ──────────────────────────────────────
    media = _anilist_request(
        _SEARCH_QUERY_NO_YEAR,
        {"search": anime_title},
    )
    if media:
        return _build_result(media)

    return None


def _build_result(media: dict) -> dict:
    """Build result dict from media object."""
    titles = media.get("title", {})
    title  = (
        titles.get("english")
        or titles.get("romaji")
        or titles.get("native")
        or "Unknown"
    )
    return {
        "anime_id":      int(media["id"]),
        "season_number": _estimate_season_number(media),
        "anime_title":   title,
    }
