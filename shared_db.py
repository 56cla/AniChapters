"""
shared_db.py — Shared Chapters Database Orchestrator  (v2 — Remote + Local Cache)

Workflow:
  lookup(anime_id, season, episode)
    ├── [1] Check local cache (SQLite) — instant
    ├── [2] If not found → connect to Supabase (remote)
    ├── [3] If found remotely → save to local cache → return it
    └── [4] If not found anywhere → return None (analysis will run)

  upsert(...)
    ├── [1] Upload to Supabase (remote) — first and most important
    └── [2] Save to local cache — for fast access later
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from models import Chapter, MatchSource
import remote_db

_CACHE_TTL_DAYS = 30
def _get_app_dir() -> str:
    """Return the folder next to the .exe (PyInstaller) or next to this script."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_CACHE_PATH = os.path.join(_get_app_dir(), "shared_chapters_cache.db")

_CREATE_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS chapters_cache (
    anime_id        INTEGER NOT NULL,
    season_number   INTEGER NOT NULL DEFAULT 1,
    episode_number  INTEGER NOT NULL,
    anime_title     TEXT    NOT NULL DEFAULT '',
    chapters_json   TEXT    NOT NULL,
    confidence      TEXT    NOT NULL DEFAULT 'medium',
    use_count       INTEGER NOT NULL DEFAULT 0,
    cached_at       TEXT    NOT NULL,
    PRIMARY KEY (anime_id, season_number, episode_number)
);
CREATE INDEX IF NOT EXISTS idx_cache_lookup
    ON chapters_cache (anime_id, season_number, episode_number);
"""


class SharedDatabase:
    """
    Orchestrator combining:
      - remote_db  (Supabase) : the true shared source across all users
      - SQLite cache          : local speed-up, TTL = 30 days
    All operations are thread-safe. Silent failure is guaranteed.
    """

    def __init__(self, cache_path: str = _CACHE_PATH):
        self._lock              = threading.Lock()
        self._cache_path        = cache_path
        self._last_remote_error: Optional[str] = None
        self._init_cache()

    def _init_cache(self) -> None:
        with self._connect_cache() as conn:
            conn.executescript(_CREATE_CACHE_SQL)

    def _connect_cache(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._cache_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    @staticmethod
    def serialize_chapters(chapters: list) -> str:
        return json.dumps(
            [{"timestamp_ms": c.timestamp_ms, "name": c.name, "source": c.source.value}
             for c in chapters],
            ensure_ascii=False,
        )

    @staticmethod
    def deserialize_chapters(chapters_json) -> list:
        try:
            raw = json.loads(chapters_json) if isinstance(chapters_json, str) else chapters_json
            result = []
            for item in raw:
                try:
                    source = MatchSource(item.get("source", "none"))
                except ValueError:
                    source = MatchSource.NONE
                result.append(Chapter(
                    timestamp_ms=int(item["timestamp_ms"]),
                    name=str(item["name"]),
                    source=source,
                ))
            return result
        except Exception:
            return []

    def _cache_get(self, anime_id, season_number, episode_number) -> Optional[dict]:
        try:
            with self._connect_cache() as conn:
                row = conn.execute(
                    "SELECT * FROM chapters_cache "
                    "WHERE anime_id=? AND season_number=? AND episode_number=? LIMIT 1",
                    (anime_id, season_number, episode_number),
                ).fetchone()
                if not row:
                    return None
                cached_at = datetime.fromisoformat(row["cached_at"])
                if datetime.now(timezone.utc) - cached_at > timedelta(days=_CACHE_TTL_DAYS):
                    conn.execute(
                        "DELETE FROM chapters_cache "
                        "WHERE anime_id=? AND season_number=? AND episode_number=?",
                        (anime_id, season_number, episode_number),
                    )
                    return None
                return dict(row)
        except Exception:
            return None

    def _cache_set(self, anime_id, season_number, episode_number,
                   anime_title, chapters_json, confidence, use_count=0) -> None:
        try:
            with self._connect_cache() as conn:
                conn.execute(
                    """
                    INSERT INTO chapters_cache
                        (anime_id, season_number, episode_number, anime_title,
                         chapters_json, confidence, use_count, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (anime_id, season_number, episode_number) DO UPDATE SET
                        anime_title   = excluded.anime_title,
                        chapters_json = excluded.chapters_json,
                        confidence    = excluded.confidence,
                        use_count     = excluded.use_count,
                        cached_at     = excluded.cached_at
                    """,
                    (anime_id, season_number, episode_number, anime_title,
                     chapters_json, confidence, use_count, _utcnow()),
                )
        except Exception:
            pass

    def invalidate_episode(self, anime_id: int, season_number: int, episode_number: int) -> bool:
        """
        Delete a single episode from the local cache so it will be re-analyzed.
        Does NOT touch the remote Supabase DB — only clears local cache.
        Returns True if a row was deleted.
        """
        try:
            with self._connect_cache() as conn:
                cur = conn.execute(
                    "DELETE FROM chapters_cache "
                    "WHERE anime_id=? AND season_number=? AND episode_number=?",
                    (anime_id, season_number, episode_number),
                )
                return cur.rowcount > 0
        except Exception:
            return False

    def lookup(self, anime_id: int, season_number: int, episode_number: int) -> Optional[dict]:
        """
        Look up chapters for an episode.
        Returns dict: {"chapters": list[Chapter], "confidence", "use_count",
                        "anime_title", "source": "cache"|"remote"}
        or None if not found anywhere.

        Cache policy:
          - High/medium confidence cache hits are returned immediately (fast path).
          - Low/fallback confidence cache hits still check remote for a better entry.
            If remote has something better (higher confidence), it replaces the cache.
        """
        with self._lock:
            # [1] Local cache
            cached = self._cache_get(anime_id, season_number, episode_number)
            if cached:
                chapters = self.deserialize_chapters(cached["chapters_json"])
                cached_conf = cached.get("confidence", "medium")
                # Fast path: trust high/medium confidence cache entries
                if chapters and cached_conf in ("high", "medium"):
                    return {
                        "chapters":    chapters,
                        "confidence":  cached_conf,
                        "use_count":   cached["use_count"],
                        "anime_title": cached["anime_title"],
                        "source":      "cache",
                    }
                # For low/fallback confidence: check if remote has a better entry
                if chapters and not remote_db.is_configured():
                    return {
                        "chapters":    chapters,
                        "confidence":  cached_conf,
                        "use_count":   cached["use_count"],
                        "anime_title": cached["anime_title"],
                        "source":      "cache",
                    }

            # [2] Remote (Supabase)
            if not remote_db.is_configured():
                return None

            remote_row = remote_db.lookup(anime_id, season_number, episode_number)
            if not remote_row:
                return None

            # [3] Save to local cache
            chapters_raw = remote_row.get("chapters_json", [])
            chapters_str = (
                json.dumps(chapters_raw, ensure_ascii=False)
                if isinstance(chapters_raw, list)
                else str(chapters_raw)
            )
            self._cache_set(
                anime_id=anime_id, season_number=season_number,
                episode_number=episode_number,
                anime_title=remote_row.get("anime_title", ""),
                chapters_json=chapters_str,
                confidence=remote_row.get("confidence", "medium"),
                use_count=remote_row.get("use_count", 0),
            )

            chapters = remote_db.deserialize_chapters(chapters_raw)
            if not chapters:
                return None

            return {
                "chapters":    chapters,
                "confidence":  remote_row.get("confidence", "medium"),
                "use_count":   remote_row.get("use_count", 0),
                "anime_title": remote_row.get("anime_title", ""),
                "source":      "remote",
            }

    def upsert(self, anime_id: int, anime_title: str, season_number: int,
               episode_number: int, chapters: list, confidence: str = "medium") -> bool:
        """
        Upload to central Supabase + save to local cache.
        Returns True if remote upload succeeded.
        """
        if not chapters:
            return False

        chapters_json = self.serialize_chapters(chapters)
        remote_ok     = False

        with self._lock:
            # [1] Remote first — remote_db.upsert() returns (bool, error_str)
            if remote_db.is_configured():
                remote_ok, remote_err = remote_db.upsert(
                    anime_id=anime_id, anime_title=anime_title,
                    season_number=season_number, episode_number=episode_number,
                    chapters=chapters, confidence=confidence,
                )
                self._last_remote_error = remote_err  # used in analyzer.py
            else:
                self._last_remote_error = "Supabase not configured"

            # [2] Always cache regardless of remote result
            self._cache_set(
                anime_id=anime_id, season_number=season_number,
                episode_number=episode_number, anime_title=anime_title,
                chapters_json=chapters_json, confidence=confidence,
            )

        return remote_ok

    def get_stats(self) -> dict:
        stats = {
            "remote_configured": remote_db.is_configured(),
            "cache_episodes":    0,
            "remote_episodes":   None,
            "remote_total_hits": None,
        }
        try:
            with self._connect_cache() as conn:
                row = conn.execute("SELECT COUNT(*) AS total FROM chapters_cache").fetchone()
                stats["cache_episodes"] = row["total"] if row else 0
        except Exception:
            pass
        if remote_db.is_configured():
            rs = remote_db.get_stats()
            if rs:
                stats["remote_episodes"]   = rs.get("total_episodes")
                stats["remote_total_hits"] = rs.get("total_hits")
        return stats

    def cache_path(self) -> str:
        return self._cache_path


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_instance = None
_inst_lock = threading.Lock()


def get_shared_db() -> SharedDatabase:
    global _instance
    if _instance is None:
        with _inst_lock:
            if _instance is None:
                _instance = SharedDatabase()
    return _instance


def compute_confidence(op_source: MatchSource, ed_source: MatchSource) -> str:
    audio = MatchSource.AUDIO
    if op_source == audio and ed_source == audio:
        return "high"
    if op_source == audio or ed_source == audio:
        return "medium"
    if op_source == MatchSource.FALLBACK or ed_source == MatchSource.FALLBACK:
        return "low"
    return "fallback"
