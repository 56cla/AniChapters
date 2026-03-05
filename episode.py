"""
Episode number extraction from filenames.
"""
from __future__ import annotations

import os
import re
from typing import Optional


def extract_episode_number(filename: str) -> Optional[int]:
    """
    Extract episode number from filename using prioritized patterns.

    Handles tricky cases like:
      - "[Group] Show - Part 2 - 01 [E970B890].mkv"  -> 1  (not 970 or 2)
      - "[SubsPlease] Show - 23 (1080p) [AB12CD34].mkv" -> 23
      - "Attack on Titan S04E28.mkv"                  -> 28
      - "One Piece - Episode 1050.mkv"                -> 1050
    """
    name = os.path.basename(filename)

    # Step 1: Remove hex hashes like [E970B890] to avoid false matches
    name_clean = re.sub(r'\[[0-9A-Fa-f]{6,8}\]', '', name)

    # Step 2: SxxExx (highest priority)
    m = re.search(r'[Ss]\d{1,2}[Ee](\d+)', name_clean)
    if m:
        ep = int(m.group(1))
        if 0 < ep < 2000:
            return ep

    # Step 3: "Episode N" / "Ep.N"
    m = re.search(r'(?:Episode|Ep)\.?\s*(\d+)', name_clean, re.IGNORECASE)
    if m:
        ep = int(m.group(1))
        if 0 < ep < 2000:
            return ep

    # Step 4: dash-separated — take the LAST " - NN " before brackets
    # Correctly handles "Show - Part 2 - 01 [720p]" -> 01
    matches = list(re.finditer(r'(?<!\w)-\s*(\d{1,4})\s*(?=[\-\[\.\s]|$)', name_clean))
    if matches:
        ep = int(matches[-1].group(1))
        if 0 < ep < 2000:
            return ep

    # Step 5: [NN] number in brackets
    m = re.search(r'\[(\d{2,4})\]', name_clean)
    if m:
        ep = int(m.group(1))
        if 0 < ep < 2000:
            return ep

    # Step 6: NNv2 version tag
    m = re.search(r'\s(\d{1,3})[vV]\d', name_clean)
    if m:
        ep = int(m.group(1))
        if 0 < ep < 2000:
            return ep

    # Step 7: _NN_ underscore fallback
    m = re.search(r'[-_](\d{2,3})[-_]', name_clean)
    if m:
        ep = int(m.group(1))
        if 0 < ep < 2000:
            return ep

    return None
