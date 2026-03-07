"""
api_anilist.py — AniList GraphQL Client

يُستخدم فقط لجلب:
  - anime_id     : المعرّف الرقمي في AniList (ثابت وفريد لكل أنمي)
  - season_number: رقم الموسم المستنتَج من relations في AniList

لماذا AniList وليس animethemes.moe؟
  animethemes.moe يستخدم slug نصياً (غير ثابت عند تغيير الاسم)،
  بينما AniList ID رقم صحيح ثابت يصلح مفتاحاً أبدياً لقاعدة البيانات.

الاستخدام:
    from api_anilist import resolve_anime_ids

    ids = resolve_anime_ids(anime_title="Fullmetal Alchemist Brotherhood", year=2009)
    # ids = {"anime_id": 5114, "season_number": 1}   أو None عند الفشل
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

# ── GraphQL endpoint ──────────────────────────────────────────────────────────
ANILIST_API  = "https://graphql.anilist.co"
_REQ_TIMEOUT = 12

# ── الـ Query ─────────────────────────────────────────────────────────────────
# نجلب المعرّف + العلاقات لاستنتاج رقم الموسم
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

# Query أبسط بدون year عند عدم توفره
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
#  دوال مساعدة داخلية
# ─────────────────────────────────────────────────────────────────────────────

def _anilist_request(query: str, variables: dict) -> Optional[dict]:
    """أرسل طلب GraphQL وأعِد بيانات media أو None."""
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
    استنتج رقم الموسم من علاقات AniList.

    المنطق:
      - إذا كان الأنمي ليس له سابق (PREQUEL) → موسم 1
      - عدد الـ PREQUEL relations + 1 = رقم الموسم
      (تقريب معقول — AniList لا يخزن season_number صراحةً)
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
#  الواجهة العامة
# ─────────────────────────────────────────────────────────────────────────────

def resolve_anime_ids(
    anime_title: str,
    year:        Optional[int] = None,
) -> Optional[dict]:
    """
    ابحث في AniList عن الأنمي وأعِد:
        {
            "anime_id":      <int>,   # AniList ID
            "season_number": <int>,   # 1, 2, 3, …
            "anime_title":   <str>,   # الاسم الرسمي
        }

    يُعيد None إذا فشل الطلب أو لم يُوجد نتيجة.

    الأولوية:
      1. يجرب مع سنة الإصدار إذا توفرت.
      2. إذا فشل يجرب بدون السنة (fallback).
    """
    if not anime_title or not anime_title.strip():
        return None

    # ── محاولة مع السنة ───────────────────────────────────────────────────────
    if year:
        media = _anilist_request(
            _SEARCH_QUERY,
            {"search": anime_title, "year": year},
        )
        if media:
            return _build_result(media)

    # ── محاولة بدون السنة ────────────────────────────────────────────────────
    media = _anilist_request(
        _SEARCH_QUERY_NO_YEAR,
        {"search": anime_title},
    )
    if media:
        return _build_result(media)

    return None


def _build_result(media: dict) -> dict:
    """بنِ dict النتيجة من media object."""
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
