"""
remote_db.py — Supabase REST Client (shared central database)

Communicates with Supabase via PostgREST API (pure HTTP, no external libraries needed).
Operations:
  - lookup  : GET  /rest/v1/shared_chapters?anime_id=eq.X&...
  - upsert  : POST /rest/v1/shared_chapters  (Prefer: resolution=merge-duplicates)
  - stats   : GET  /rest/v1/shared_chapters_stats

Required setup:
  Add to .env or supabase_config.py:
    SUPABASE_URL = "https://xxxxxxxxxxxx.supabase.co"
    SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."   # anon key

  Both values are in: Supabase Dashboard → Project Settings → API
"""
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from models import Chapter, MatchSource

# ── Connection setup ─────────────────────────────────────────────────────────────
# Read from supabase_config.py (preferred) or environment variables as fallback
def _load_config() -> tuple[str, str]:
    """
    Read SUPABASE_URL and SUPABASE_KEY from:
      1. supabase_config.py  (file next to the program — preferred)
      2. Environment variables SUPABASE_URL / SUPABASE_KEY
    Returns ("", "") if no config found.
    """
    # Attempt 1: supabase_config.py
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase_config.py")
    if os.path.exists(config_path):
        cfg: dict = {}
        try:
            with open(config_path, encoding="utf-8") as f:
                exec(f.read(), cfg)  # noqa: S102
            url = cfg.get("SUPABASE_URL", "").strip().rstrip("/")
            key = cfg.get("SUPABASE_KEY", "").strip()
            if url and key:
                return url, key
        except Exception:
            pass

    # Attempt 2: Environment variables
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_KEY", "").strip()
    return url, key


_SUPABASE_URL, _SUPABASE_KEY = _load_config()
_REQUEST_TIMEOUT = 10   # seconds — timeout for all HTTP requests


# ─────────────────────────────────────────────────────────────────────────────
#  HTTP helpers
# ─────────────────────────────────────────────────────────────────────────────

def _base_headers() -> dict:
    return {
        "apikey":        _SUPABASE_KEY,
        "Authorization": f"Bearer {_SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Accept":        "application/json",
    }


def _get(path: str, params: dict) -> Optional[list]:
    """HTTP GET → list[dict] or None on failure (silent — read-only)."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return None
    qs  = urllib.parse.urlencode(params)
    url = f"{_SUPABASE_URL}{path}?{qs}"
    req = urllib.request.Request(url, headers=_base_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _get_with_error(path: str, params: dict) -> "tuple[Optional[list], Optional[str]]":
    """HTTP GET with error details — for diagnostics."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return None, "Supabase not configured"
    qs  = urllib.parse.urlencode(params)
    url = f"{_SUPABASE_URL}{path}?{qs}"
    req = urllib.request.Request(url, headers=_base_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode()), None
    except urllib.error.HTTPError as exc:
        try:
            err_body = exc.read().decode(errors="replace")
            try:
                err_json = json.loads(err_body)
                message  = err_json.get("message", err_body)
                code     = err_json.get("code", "")
                return None, f"HTTP {exc.code} {exc.reason} | code={code} msg={message}"
            except Exception:
                return None, f"HTTP {exc.code} {exc.reason}: {err_body[:300]}"
        except Exception:
            return None, f"HTTP {exc.code} {exc.reason}"
    except urllib.error.URLError as exc:
        return None, f"Network error: {exc.reason}"
    except Exception as exc:
        return None, f"Unexpected error: {type(exc).__name__}: {exc}"


def _post(
    path: str,
    body: dict,
    headers_extra: Optional[dict] = None,
) -> "tuple[Optional[list], Optional[str]]":
    """
    HTTP POST → (result, error_message).

    result       : list[dict] on success, or [] for upsert with no content
    error_message: detailed error string, or None on success
    """
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return None, "Supabase not configured (missing URL or KEY)"

    url     = f"{_SUPABASE_URL}{path}"
    headers = {**_base_headers(), **(headers_extra or {})}
    data    = json.dumps(body, ensure_ascii=False).encode()
    req     = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode()
            return (json.loads(raw) if raw.strip() else []), None

    except urllib.error.HTTPError as exc:
        # Read full error body from Supabase/PostgREST
        try:
            err_body = exc.read().decode(errors="replace")
            try:
                err_json = json.loads(err_body)
                # PostgREST returns: {"code":"...", "message":"...", "details":"...", "hint":"..."}
                code    = err_json.get("code",    "")
                message = err_json.get("message", err_body)
                details = err_json.get("details", "")
                hint    = err_json.get("hint",    "")
                parts   = [f"HTTP {exc.code} {exc.reason}"]
                if code:    parts.append(f"code={code}")
                if message: parts.append(f"msg={message}")
                if details: parts.append(f"details={details}")
                if hint:    parts.append(f"hint={hint}")
                err_str = " | ".join(parts)
            except Exception:
                err_str = f"HTTP {exc.code} {exc.reason}: {err_body[:300]}"
        except Exception:
            err_str = f"HTTP {exc.code} {exc.reason}"

        # 409 Conflict on upsert = conflict resolved by merge — not a real error
        if exc.code == 409:
            return [], None

        return None, err_str

    except urllib.error.URLError as exc:
        return None, f"Network error: {exc.reason}"

    except TimeoutError:
        return None, f"Request timed out after {_REQUEST_TIMEOUT}s"

    except Exception as exc:
        return None, f"Unexpected error: {type(exc).__name__}: {exc}"


def _rpc(function_name: str, params: dict) -> bool:
    """Call a Supabase RPC function — returns True on success."""
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return False
    url     = f"{_SUPABASE_URL}/rest/v1/rpc/{function_name}"
    headers = _base_headers()
    data    = json.dumps(params, ensure_ascii=False).encode()
    req     = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as _:
            return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Data conversion
# ─────────────────────────────────────────────────────────────────────────────

def serialize_chapters(chapters: list[Chapter]) -> list[dict]:
    """Chapter list → JSON-serialisable list (sent as JSONB)."""
    return [
        {
            "timestamp_ms": ch.timestamp_ms,
            "name":         ch.name,
            "source":       ch.source.value,
        }
        for ch in chapters
    ]


def deserialize_chapters(chapters_json) -> list[Chapter]:
    """
    JSONB from Supabase → list[Chapter].
    Accepts str (JSON text) or list (auto-parsed JSONB).
    """
    try:
        raw = chapters_json if isinstance(chapters_json, list) else json.loads(chapters_json)
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


# ─────────────────────────────────────────────────────────────────────────────
#  Public interface
# ─────────────────────────────────────────────────────────────────────────────

def is_configured() -> bool:
    """Is Supabase configured?"""
    return bool(_SUPABASE_URL and _SUPABASE_KEY)


def lookup(
    anime_id:       int,
    season_number:  int,
    episode_number: int,
) -> Optional[dict]:
    """
    Look up chapters for an episode in the shared database.

    Returns a dict containing:
        chapters_json, confidence, use_count, anime_title, ...
    or None if no record found or connection failed.

    Increments use_count via RPC asynchronously (non-blocking).
    """
    rows = _get(
        "/rest/v1/shared_chapters",
        {
            "anime_id":       f"eq.{anime_id}",
            "season_number":  f"eq.{season_number}",
            "episode_number": f"eq.{episode_number}",
            "select":         "id,anime_title,chapters_json,confidence,use_count,created_at",
            "limit":          "1",
        },
    )

    if not rows:
        return None

    row = rows[0]

    # Increment use_count in background (fire and forget)
    threading.Thread(
        target=_rpc,
        args=("increment_use_count", {
            "p_anime_id": anime_id,
            "p_season":   season_number,
            "p_episode":  episode_number,
        }),
        daemon=True,
    ).start()

    return row


def upsert(
    anime_id:       int,
    anime_title:    str,
    season_number:  int,
    episode_number: int,
    chapters:       list[Chapter],
    confidence:     str = "medium",
) -> "tuple[bool, Optional[str]]":
    """
    Upload chapters to the shared database.

    Uses Supabase upsert (Prefer: resolution=merge-duplicates).
    Returns (True, None) on success or (False, "reason") on failure.

    Technical note:
      chapters_json is defined as JSONB in Supabase.
      PostgREST expects a Python list directly (not a JSON string)
      because json.dumps() serializes everything at once.
    """
    if not chapters:
        return False, "No chapters to save"

    if not _SUPABASE_URL or not _SUPABASE_KEY:
        return False, "Supabase not configured"

    body = {
        "anime_id":       anime_id,
        "anime_title":    anime_title,
        "season_number":  season_number,
        "episode_number": episode_number,
        # JSONB column: send as list directly — PostgREST handles it as JSONB
        "chapters_json":  serialize_chapters(chapters),
        "confidence":     confidence,
    }

    result, error = _post(
        "/rest/v1/shared_chapters",
        body,
        headers_extra={
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )

    if result is not None:
        return True, None
    return False, error


def get_stats() -> Optional[dict]:
    """
    Fetch shared database statistics from the view.
    Returns dict or None on failure.
    """
    rows = _get("/rest/v1/shared_chapters_stats", {"select": "*", "limit": "1"})
    if rows:
        return rows[0]
    return None




def diagnose() -> list[str]:
    """
    Run a full Supabase configuration check and return a list of messages for the log.
    Useful for diagnosing connectivity or permission issues.
    """
    msgs: list[str] = []

    # [1] Configuration
    if not _SUPABASE_URL or not _SUPABASE_KEY:
        msgs.append("✘ SUPABASE_URL or SUPABASE_KEY not set in supabase_config.py")
        return msgs
    msgs.append(f"✔ URL: {_SUPABASE_URL}")
    key_preview = _SUPABASE_KEY[:12] + "..." if len(_SUPABASE_KEY) > 12 else _SUPABASE_KEY
    msgs.append(f"✔ KEY: {key_preview} (anon)")

    # [2] GET — can we read?
    rows, err = _get_with_error(
        "/rest/v1/shared_chapters",
        {"select": "id", "limit": "1"},
    )
    if err:
        msgs.append(f"✘ Table read failed: {err}")
        if "relation" in (err or "").lower() or "does not exist" in (err or "").lower():
            msgs.append("  ← Table does not exist — run supabase_setup.sql first")
        elif "JWT" in (err or "") or "401" in (err or ""):
            msgs.append("  ← anon key is invalid")
        elif "42501" in (err or "") or "permission" in (err or "").lower():
            msgs.append("  ← RLS blocking read — check policy allow_public_read")
    else:
        msgs.append(f"✔ Table read succeeded ({len(rows or [])} row(s))")

    # [3] POST — can we write?
    test_body = {
        "anime_id":       0,
        "anime_title":    "__diagnose_test__",
        "season_number":  0,
        "episode_number": 0,
        "chapters_json":  [],
        "confidence":     "low",
    }
    result, err2 = _post(
        "/rest/v1/shared_chapters",
        test_body,
        headers_extra={"Prefer": "resolution=merge-duplicates,return=minimal"},
    )
    if err2:
        msgs.append(f"✘ Table write failed: {err2}")
        if "42501" in (err2 or "") or "permission" in (err2 or "").lower():
            msgs.append("  ← RLS blocking INSERT — check policy allow_public_insert")
        elif "23514" in (err2 or ""):
            msgs.append("  ← CHECK constraint — confidence value not accepted")
        elif "not-null" in (err2 or "").lower() or "23502" in (err2 or ""):
            msgs.append("  ← Required column (NOT NULL) was not sent")
        elif "column" in (err2 or "").lower() and "does not exist" in (err2 or "").lower():
            msgs.append("  ← Wrong column name — check supabase_setup.sql")
    else:
        msgs.append("✔ Table write succeeded")
        # Delete the test record
        try:
            del_url = (
                f"{_SUPABASE_URL}/rest/v1/shared_chapters"
                "?anime_id=eq.0&season_number=eq.0&episode_number=eq.0"
            )
            del_req = urllib.request.Request(
                del_url,
                headers={**_base_headers(), "Prefer": "return=minimal"},
                method="DELETE",
            )
            urllib.request.urlopen(del_req, timeout=_REQUEST_TIMEOUT).close()
            msgs.append("✔ Test record deleted")
        except Exception:
            msgs.append("○ Test record not deleted (not critical)")

    return msgs

def reload_config() -> None:
    """
    Reload Supabase config from file.
    Useful if the user adds supabase_config.py after the program has started.
    """
    global _SUPABASE_URL, _SUPABASE_KEY
    _SUPABASE_URL, _SUPABASE_KEY = _load_config()
