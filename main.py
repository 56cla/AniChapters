"""
Anime Chapters Generator  v9.2
Entry point.
"""
import sys
import os

# Ensure the directory containing this file is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import run_app

if __name__ == "__main__":
    raise SystemExit(run_app())
