from pathlib import Path
import json
import os
from typing import Optional

def _get_settings_path() -> Path:
    appdata = os.environ.get("APPDATA")
    if appdata:
        settings_dir = Path(appdata) / "AniChapters"
    else:
        settings_dir = Path.home() / ".anichapters"

    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir / "settings.json"

_SETTINGS_PATH = _get_settings_path()
# ── Presets ───────────────────────────────────────────────────────────────────
PRESETS: dict[str, dict[str, str]] = {
    "Default": {
        "cold_open":    "Cold Open",
        "opening":      "Opening",
        "episode":      "Episode",
        "ending":       "Ending",
        "after_credits":"After Credits",
        "end":          "End",
    },
    "Anime Style": {
        "cold_open":    "Prologue",
        "opening":      "♪ OP",
        "episode":      "Episode",
        "ending":       "♪ ED",
        "after_credits":"Epilogue",
        "end":          "End",
    },
    "Minimal": {
        "cold_open":    "Intro",
        "opening":      "OP",
        "episode":      "EP",
        "ending":       "ED",
        "after_credits":"Post",
        "end":          "End",
    },
    "Descriptive": {
        "cold_open":    "Pre-Opening",
        "opening":      "Opening Theme",
        "episode":      "Main Episode",
        "ending":       "Ending Theme",
        "after_credits":"Post-Credits",
        "end":          "End",
    },
    "Japanese": {
        "cold_open":    "前口上",
        "opening":      "オープニング",
        "episode":      "本編",
        "ending":       "エンディング",
        "after_credits":"エピローグ",
        "end":          "エンド",
    },
    "Custom": {  # placeholder — user fills this
        "cold_open":    "Cold Open",
        "opening":      "Opening",
        "episode":      "Episode",
        "ending":       "Ending",
        "after_credits":"After Credits",
        "end":          "End",
    },
}

_DEFAULT_SETTINGS = {
    "preset":       "Default",
    "custom_names": dict(PRESETS["Default"]),
    "ui_state": {
        "inplace": False,
    },
}


def load_settings() -> dict:
    """Load settings from settings.json, or return defaults."""
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        # Merge with defaults to handle missing keys
        merged = dict(_DEFAULT_SETTINGS)
        merged.update(data)
        return merged
    except Exception:
        return dict(_DEFAULT_SETTINGS)


def save_settings(settings: dict) -> None:
    """Persist settings to settings.json."""
    try:
        with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
    except Exception:
        pass


def get_chapter_names(settings: Optional[dict] = None) -> dict[str, str]:
    """
    Return the active chapter name mapping.
    If preset is 'Custom', returns custom_names.
    Otherwise returns the named preset.
    """
    if settings is None:
        settings = load_settings()

    preset = settings.get("preset", "Default")

    if preset == "Custom":
        return settings.get("custom_names", dict(PRESETS["Default"]))

    return dict(PRESETS.get(preset, PRESETS["Default"]))