"""
Shared constants: UI colors/fonts, API settings, audio params, search boundaries,
and module-level mutable caches.
"""
from __future__ import annotations

# ─── Colors ───────────────────────────────────────────────────────────────────
BG    = "#0f0f14"
PANEL = "#16161e"
BORD  = "#2a2a3a"
A1    = "#e05c5c"   # Red
A2    = "#5ce0b0"   # Green
A3    = "#5c9ee0"   # Blue
A4    = "#e0b05c"   # Yellow
A5    = "#b05ce0"   # Purple
TEXT  = "#e8e8f0"
DIM   = "#7070a0"

# ─── Fonts ────────────────────────────────────────────────────────────────────
FONT  = ("Consolas", 10)
FONTB = ("Consolas", 10, "bold")
FONTS = ("Consolas", 9)
FONTL = ("Consolas", 13, "bold")

# ─── API ──────────────────────────────────────────────────────────────────────
API_BASE    = "https://api.animethemes.moe"
API_HEADERS = {"User-Agent": "AnimeChaptersGenerator/9.2"}
API_TIMEOUT = 20

# ─── Audio matching parameters ────────────────────────────────────────────────
NEEDLE_SKIP_SECONDS     = 3.0   # Skip first 3s of theme (fade-in)
NEEDLE_DURATION_SECONDS = 20.0  # Use 20s of theme as reference
NCC_THRESHOLD           = 0.30  # Minimum correlation score

# ─── Search boundaries ────────────────────────────────────────────────────────
OP_SEARCH_END_SECONDS   = 5 * 60   # Search OP in first 5 minutes
ED_SEARCH_START_SECONDS = 8 * 60   # Search ED in last 8 minutes

# ─── Temp directories tracking ────────────────────────────────────────────────
_TEMP_DIRS: list[str] = []

# ─── Theme file download cache ────────────────────────────────────────────────
_THEME_FILE_CACHE: dict[str, str] = {}
