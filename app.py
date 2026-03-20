"""
Main Application class and run_app() entry point.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading

# Suppress console windows on Windows when spawning subprocesses
_CREATIONFLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
from typing import Callable, Optional

from api_animethemes import get_anime_themes, search_anime
from analyzer import analyze_video
from chapters import build_chapters, write_chapters_xml
from constants import (
    A1, A2, A3, A4, A5, BG, BORD, DIM,
    FONT, FONTB, FONTL, FONTS, PANEL, TEXT,
    _TEMP_DIRS,
)
from episode import extract_episode_number
from ffprobe_utils import get_video_duration_ms
from models import AnalysisResult, MatchSource, Theme
from timestamps import ms_to_mkv_timestamp
from dialogs import AnimePickerDialog, ReviewDialog, SettingsDialog
from settings import get_chapter_names, load_settings, save_settings
from api_anilist import resolve_anime_ids
from shared_db import get_shared_db

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False

CURRENT_VERSION = "4.0"
GITHUB_REPO     = "56cla/AniChapters"


class Application:
    """Main application class"""

    def __init__(self, root: "tk.Tk"):
        self.root = root
        root.title(f"AniChapters {CURRENT_VERSION}")
        root.geometry("820x720")
        root.configure(bg=BG)
        root.resizable(True, True)
        root.minsize(700, 580)

        self.videos: list[str] = []
        self.themes: list[Theme] = []
        self.results: list[AnalysisResult] = []
        self.anime_name: str = ""
        self.db_meta: dict = {}          # {anime_id, anime_title, season_number}
        self.settings: dict = load_settings()
        self.busy = False
        self.inplace = tk.BooleanVar(value=False)
        self.cancel_event: Optional[threading.Event] = None

        self._build_ui()
        self._check_dependencies()
        threading.Thread(target=self._check_for_updates, daemon=True).start()

    # ─── UI construction ──────────────────────────────────────────────────────

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg=BG)
        header.pack(fill="x", padx=24, pady=(16, 10))

        tk.Label(
            header,
            text="◈",
            font=("Consolas", 20, "bold"),
            fg=A1,
            bg=BG,
        ).pack(side="left", padx=(0, 8))

        tk.Label(
            header,
            text="ANIME CHAPTERS GENERATOR",
            font=FONTL,
            fg=TEXT,
            bg=BG,
        ).pack(side="left")

        # Dependency indicators
        dep_frame = tk.Frame(header, bg=BG)
        dep_frame.pack(side="right")

        self.dep_labels: dict[str, tk.Label] = {}
        for dep in ["mkvmerge", "ffprobe", "ffmpeg", "numpy", "mkvpropedit"]:
            lbl = tk.Label(dep_frame, text=f"● {dep}", font=FONTS, fg=A2, bg=BG)
            lbl.pack(side="left", padx=4)
            self.dep_labels[dep] = lbl

        tk.Frame(self.root, height=1, bg=BORD).pack(fill="x", padx=24)

        # Sections
        self._build_section("STEP 1 — Select Videos",      A3, self._build_step1)
        self._build_section("STEP 2 — animethemes.moe",    A4, self._build_step2)
        self._build_section("STEP 3 — Analyze & Export",   A2, self._build_step3)

        # Progress bar
        self.progress = ttk.Progressbar(
            self.root,
            mode="indeterminate",
            style="P.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x", padx=24, pady=(6, 0))

        style = ttk.Style()
        style.theme_use("default")
        style.configure(
            "P.Horizontal.TProgressbar",
            troughcolor=PANEL,
            background=A2,
            thickness=3,
            borderwidth=0,
        )

        # Log panel
        self._build_log_panel()

    def _build_section(self, title: str, color: str, builder: Callable):
        frame = tk.Frame(self.root, bg=PANEL, highlightbackground=BORD, highlightthickness=1)
        frame.pack(fill="x", padx=24, pady=3)

        tk.Frame(frame, bg=color, width=4).pack(side="left", fill="y")

        inner = tk.Frame(frame, bg=PANEL)
        inner.pack(side="left", fill="both", expand=True, padx=12, pady=8)

        tk.Label(inner, text=title, font=FONTB, fg=color, bg=PANEL).pack(anchor="w")

        builder(inner)

    def _build_step1(self, parent: "tk.Frame"):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(4, 0))

        self.video_label = tk.StringVar(value="No videos selected")
        tk.Label(
            row,
            textvariable=self.video_label,
            font=FONTS,
            fg=DIM,
            bg=PANEL,
            anchor="w",
        ).pack(side="left", fill="x", expand=True)

        tk.Button(
            row,
            text="Add…",
            font=FONTS,
            fg=TEXT,
            bg=BORD,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._select_videos,
        ).pack(side="right", padx=(4, 0))

        tk.Button(
            row,
            text="Clear",
            font=FONTS,
            fg=DIM,
            bg=BG,
            bd=0,
            padx=6,
            pady=3,
            cursor="hand2",
            command=self._clear_videos,
        ).pack(side="right")

    def _build_step2(self, parent: "tk.Frame"):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(4, 0))

        self.theme_label = tk.Label(
            row,
            text="Not loaded yet",
            font=FONTS,
            fg=DIM,
            bg=PANEL,
            anchor="w",
        )
        self.theme_label.pack(side="left", fill="x", expand=True)

        self.btn_fetch = tk.Button(
            row,
            text="Search & Fetch…",
            font=FONTS,
            fg=TEXT,
            bg=BORD,
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            command=self._fetch_themes,
        )
        self.btn_fetch.pack(side="right")

    def _build_step3(self, parent: "tk.Frame"):
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=(4, 0))

        self.btn_analyze = tk.Button(
            row,
            text="[ 1 ]  Analyze All",
            font=FONTB,
            fg=BG,
            bg=A2,
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._run_analysis,
        )
        self.btn_analyze.pack(side="left", padx=(0, 8))

        self.btn_review = tk.Button(
            row,
            text="[ 2 ]  Review",
            font=FONTB,
            fg=BG,
            bg=A4,
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._review_chapters,
        )
        self.btn_review.pack(side="left", padx=(0, 8))

        self.btn_mux = tk.Button(
            row,
            text="[ 3 ]  Merge MKV",
            font=FONTB,
            fg=BG,
            bg=A1,
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self._mux_videos,
        )
        self.btn_mux.pack(side="left", padx=(0, 16))

        tk.Button(
            row,
            text="⚙ Settings",
            font=FONTS,
            fg=TEXT,
            bg=BORD,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
            command=self._open_settings,
        ).pack(side="left", padx=(0, 8))

        # In-place option
        tk.Checkbutton(
            row,
            text="In-place edit (mkvpropedit)",
            variable=self.inplace,
            font=FONTS,
            fg=DIM,
            bg=PANEL,
            selectcolor=PANEL,
            activebackground=PANEL,
            activeforeground=A5,
            cursor="hand2",
            command=self._toggle_inplace,
        ).pack(side="left")

        # Cancel button (hidden initially)
        self.btn_cancel = tk.Button(
            row,
            text="Cancel",
            font=FONTB,
            fg=BG,
            bg=A1,
            bd=0,
            padx=10,
            pady=6,
            cursor="hand2",
            command=self._cancel_operation,
        )

    def _build_log_panel(self):
        frame = tk.Frame(self.root, bg=PANEL, highlightbackground=BORD, highlightthickness=1)
        frame.pack(fill="both", expand=True, padx=24, pady=10)

        # Log header
        header = tk.Frame(frame, bg=PANEL)
        header.pack(fill="x", padx=10, pady=(8, 0))

        tk.Label(header, text="LOG", font=FONTB, fg=DIM, bg=PANEL).pack(side="left")

        tk.Button(
            header,
            text="CLR",
            font=FONTS,
            fg=DIM,
            bg=PANEL,
            bd=0,
            cursor="hand2",
            command=lambda: self.log_text.delete("1.0", tk.END),
        ).pack(side="right")

        tk.Button(
            header,
            text="COPY LOG",
            font=FONTS,
            fg=A3,
            bg=PANEL,
            bd=0,
            cursor="hand2",
            padx=6,
            command=self._copy_log,
        ).pack(side="right", padx=(0, 6))

        tk.Button(
            header,
            text="CLEAR CACHE",
            font=FONTS,
            fg=A1,
            bg=PANEL,
            bd=0,
            cursor="hand2",
            padx=6,
            command=self._clear_cache,
        ).pack(side="right", padx=(0, 6))

        tk.Button(
            header,
            text="DB STATS",
            font=FONTS,
            fg=A4,
            bg=PANEL,
            bd=0,
            cursor="hand2",
            padx=6,
            command=self._show_db_stats,
        ).pack(side="right", padx=(0, 6))

        # Log text
        self.log_text = tk.Text(
            frame,
            bg=PANEL,
            fg=TEXT,
            font=FONT,
            bd=0,
            padx=10,
            pady=6,
            insertbackground=TEXT,
            selectbackground=BORD,
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=4, pady=(4, 8))

        # Configure tags
        for tag, color in [("dim", DIM), ("ok", A2), ("err", A1), ("ch", A3), ("th", A4)]:
            self.log_text.tag_config(tag, foreground=color)

    # ─── Dependency check ─────────────────────────────────────────────────────

    def _check_dependencies(self):
        """Check for required external tools"""
        missing: list[str] = []

        if not shutil.which("mkvmerge"):
            self.dep_labels["mkvmerge"].config(text="✘ mkvmerge", fg=A1)
            missing.append("mkvmerge")

        if not shutil.which("ffprobe"):
            self.dep_labels["ffprobe"].config(text="✘ ffprobe", fg=A1)
            missing.append("ffprobe")

        if not shutil.which("ffmpeg"):
            self.dep_labels["ffmpeg"].config(text="✘ ffmpeg", fg=A1)
            missing.append("ffmpeg")

        try:
            import numpy  # noqa: F401
        except ImportError:
            self.dep_labels["numpy"].config(text="✘ numpy", fg=A1)
            missing.append("numpy (pip install numpy)")

        if not shutil.which("mkvpropedit"):
            self.dep_labels["mkvpropedit"].config(text="○ mkvpropedit", fg=DIM)

        if missing:
            self._log(f"Missing dependencies: {', '.join(missing)}\n", "err")

        self._log("Ready.\n", "dim")

    # ─── Logging helpers ──────────────────────────────────────────────────────

    # ─── Update checker ───────────────────────────────────────────────────────

    def _check_for_updates(self):
        """Check GitHub for a newer release (runs in background thread)."""
        import json
        import urllib.request
        try:
            url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
            req = urllib.request.Request(url, headers={"User-Agent": "AniChapters"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode())
            latest = data.get("tag_name", "").strip()
            if latest and latest != CURRENT_VERSION:
                self.root.after(0, self._show_update_popup, latest, data.get("html_url", ""))
            else:
                self.root.after(0, self._log, f"Version {CURRENT_VERSION} — up to date.\n", "dim")
        except Exception:
            pass  # silent fail — no internet or API error

    def _show_update_popup(self, latest: str, url: str):
        """Show update notification dialog."""
        import webbrowser
        self._log(f"\n⚡ Update available: {latest} (current: {CURRENT_VERSION})\n", "ok")
        if messagebox.askyesno(
            "Update Available",
            f"A new version is available!\n\n"
            f"  Current:  {CURRENT_VERSION}\n"
            f"  Latest:   {latest}\n\n"
            f"Open the download page?",
        ):
            webbrowser.open(url)

    def _log(self, message: str, tag: str = "dim"):
        self.log_text.insert(tk.END, message, tag)
        self.log_text.see(tk.END)

    def _log_async(self, message: str, tag: str = "dim"):
        self.root.after(0, self._log, message, tag)

    # ─── UI state helpers ─────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, show_cancel: bool = False):
        """Update UI state during operations"""
        self.busy = busy
        state = "disabled" if busy else "normal"

        for btn in (self.btn_analyze, self.btn_review, self.btn_mux, self.btn_fetch):
            btn.config(state=state)

        if busy:
            self.progress.start(12)
            if show_cancel:
                self.btn_cancel.pack(side="left", padx=(0, 8))
        else:
            self.progress.stop()
            self.btn_cancel.pack_forget()

    def _toggle_inplace(self):
        """Handle in-place editing toggle"""
        if self.inplace.get():
            if not shutil.which("mkvpropedit"):
                messagebox.showwarning(
                    "mkvpropedit Not Found",
                    "In-place editing requires mkvpropedit.\n"
                    "It comes with MKVToolNix — add it to PATH.\n\n"
                    "The option will be disabled.",
                )
                self.inplace.set(False)
                return

            self.btn_mux.config(text="[ 3 ]  In-place Edit", bg=A5)
            self._log("In-place editing enabled (mkvpropedit)\n", "dim")
        else:
            self.btn_mux.config(text="[ 3 ]  Merge MKV", bg=A1)
            self._log("In-place editing disabled (mkvmerge)\n", "dim")

    # ─── Video selection ──────────────────────────────────────────────────────

    def _select_videos(self):
        """Open file dialog to select video files"""
        paths = filedialog.askopenfilenames(
            title="Select Video Files",
            filetypes=[
                ("Video Files", "*.mkv *.mp4 *.avi *.m4v *.webm *.mov"),
                ("All Files", "*.*"),
            ],
        )

        if not paths:
            return

        for path in paths:
            if path not in self.videos:
                self.videos.append(path)

        self.video_label.set(f"{len(self.videos)} video(s)")
        self._log(f"\nAdded {len(paths)} video(s):\n", "ok")

        for path in paths:
            ep = extract_episode_number(path)
            self._log(f"  [ep{ep or '?'}] {os.path.basename(path)}\n", "dim")

    def _clear_videos(self):
        """Clear video selection"""
        self.videos = []
        self.video_label.set("No videos selected")
        self._log("Video list cleared.\n", "dim")

    def _guess_anime_name(self) -> str:
        """Guess anime name from first video filename"""
        if not self.videos:
            return ""

        name = os.path.basename(self.videos[0])

        # Remove common patterns
        name = re.sub(r"[\[\(].*?[\]\)]", "", name)
        name = re.sub(r"[-_\s]+[Ee][Pp]?\.?\s*\d+.*", "", name)
        name = re.sub(r"[-_\s]+\d{2,3}[vV\s\[].*", "", name)
        name = re.sub(r"\.(mkv|mp4|avi|m4v|webm|mov)$", "", name, flags=re.I)

        return name.strip(" -_.")

    # ─── Theme fetching ───────────────────────────────────────────────────────

    def _fetch_themes(self):
        """Search and fetch themes from animethemes.moe"""
        if self.busy:
            return

        if not self.videos:
            messagebox.showerror("Error", "Add videos first.")
            return

        guess = self._guess_anime_name()
        self._set_busy(True)
        self._log(f'\nSearching for: "{guess}"...\n', "dim")

        def run():
            try:
                results = search_anime(guess)
                self.root.after(0, self._show_anime_picker, results, guess)
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._on_error(err))

        threading.Thread(target=run, daemon=True).start()

    def _show_anime_picker(self, results: list[dict], query: str):
        """Show dialog to select anime from search results"""
        self._set_busy(False)

        if not results:
            self._log("No results found.\n", "dim")
            self._manual_search()
            return

        picker = AnimePickerDialog(self.root, results, query)

        if picker.chosen is None:
            self._log("Skipped.\n", "dim")
            return

        action, data = picker.chosen

        if action == "manual":
            self._manual_search()
        else:
            self._load_themes(data)

    def _manual_search(self):
        """Show manual search dialog"""
        name = simpledialog.askstring(
            "Manual Search",
            "Anime name as it appears on animethemes.moe:",
            parent=self.root,
        )

        if not name:
            return

        self._set_busy(True)
        self._log(f'Searching: "{name}"...\n', "dim")

        def run():
            try:
                results = search_anime(name)
                self.root.after(0, self._show_anime_picker, results, name)
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._on_error(err))

        threading.Thread(target=run, daemon=True).start()

    def _load_themes(self, anime: dict):
        """Load themes for selected anime"""
        slug = anime["slug"]
        name = anime["name"]

        self._log(f"Selected: {name} [{slug}]\n", "th")
        self._set_busy(True)
        self._log("Fetching themes and durations...\n", "dim")

        def run():
            try:
                themes = get_anime_themes(slug, self._log_async)

                for theme in themes:
                    self.root.after(0, self._log, f"  ffprobe ← {theme.label}...\n", "dim")
                    theme.duration_ms = get_video_duration_ms(theme.video_url)

                    dur_s = f"{theme.duration_ms // 1000}s" if theme.duration_ms else "N/A"
                    eps   = sorted(theme.episode_set) if theme.episode_set else []

                    self.root.after(
                        0,
                        self._log,
                        f"  {theme.label} \"{theme.title}\" {dur_s} eps={eps or 'all'}\n",
                        "th",
                    )

                self.root.after(0, self._on_themes_loaded, themes, name)
            except Exception as e:
                self.root.after(0, lambda err=str(e): self._on_error(err))

        threading.Thread(target=run, daemon=True).start()

    def _on_themes_loaded(self, themes: list[Theme], anime_name: str):
        """Handle themes loaded successfully"""
        self.themes     = themes
        self.anime_name = anime_name          # passed to core.py as search_name
        self.db_meta    = {}                  # reset — resolved async below
        self.theme_label.config(text=f"✔ {anime_name}", fg=A2)
        self._log(f"\nReady — click 'Analyze All'\n\n", "ok")
        self._set_busy(False)

        # ── AniList ID resolution (background, non-blocking) ──────────────────
        # Runs in a separate thread to avoid blocking the UI
        threading.Thread(
            target=self._resolve_anilist_ids,
            args=(anime_name,),
            daemon=True,
        ).start()

    def _resolve_anilist_ids(self, anime_name: str) -> None:
        """
        Fetch anime_id and season_number from AniList and store in self.db_meta.
        Runs in the background — silent failure does not stop any feature.
        """
        try:
            ids = resolve_anime_ids(anime_name)
            if ids:
                self.db_meta = ids
                self.root.after(
                    0,
                    self._log,
                    f"  [DB] AniList ID={ids['anime_id']} "
                    f"S{ids['season_number']:02d} resolved\n",
                    "dim",
                )
            else:
                self.root.after(
                    0,
                    self._log,
                    "  [DB] AniList ID not resolved — DB lookup disabled\n",
                    "dim",
                )
        except Exception:
            pass  # silent — AniList is optional

    # ─── Cancellation ─────────────────────────────────────────────────────────

    def _cancel_operation(self):
        """Cancel current operation"""
        if self.cancel_event:
            self.cancel_event.set()
            self._log("\nCancellation requested...\n", "err")

    # ─── Analysis ─────────────────────────────────────────────────────────────

    def _run_analysis(self):
        """Run analysis on all videos"""
        if self.busy:
            return

        if not self.videos:
            messagebox.showerror("Error", "Add videos first.")
            return

        self._set_busy(True, show_cancel=True)
        self.cancel_event = threading.Event()
        self.results      = []
        themes            = self.themes

        def run():
            for video_path in self.videos:
                if self.cancel_event.is_set():
                    break

                try:
                    result = analyze_video(
                        video_path,
                        themes,
                        log_func=self._log_async,
                        cancel_event=self.cancel_event,
                        search_name=self.anime_name,
                        db_meta=self.db_meta if self.db_meta else None,
                    )

                    if self.cancel_event.is_set():
                        break

                    # Build chapters
                    ch_names = get_chapter_names(self.settings)
                    op_label = (
                        f"♪ {result.op_theme.label} — {result.op_theme.title}"
                        if result.op_theme
                        else ch_names["opening"]
                    )
                    ed_label = (
                        f"♪ {result.ed_theme.label} — {result.ed_theme.title}"
                        if result.ed_theme
                        else ch_names["ending"]
                    )

                    chapters = build_chapters(
                        result.op_start_ms, result.op_end_ms, op_label, result.op_source,
                        result.ed_start_ms, result.ed_end_ms, ed_label, result.ed_source,
                        result.video_duration_ms,
                        ch_names=ch_names,
                    )

                    # Write XML
                    xml_path = os.path.splitext(video_path)[0] + "_chapters.xml"
                    write_chapters_xml(chapters, xml_path)
                    result.xml_path  = xml_path
                    result.chapters  = chapters

                    self.results.append(result)
                    self.root.after(0, self._on_video_done, result, chapters, xml_path)

                except Exception as e:
                    self.root.after(
                        0,
                        self._log,
                        f"Error {os.path.basename(video_path)}: {e}\n",
                        "err",
                    )

            if not self.cancel_event.is_set():
                self.root.after(0, self._on_analysis_complete)
            else:
                self.root.after(0, self._on_analysis_cancelled)

        threading.Thread(target=run, daemon=True).start()

    def _on_video_done(self, result: AnalysisResult, chapters: list, xml_path: str):
        """Handle single video analysis completion"""
        op_src = result.op_source.value or "—"
        ed_src = result.ed_source.value or "—"

        self._log(f"\n✔ {result.basename}\n", "ok")
        self._log(
            f"   OP={op_src}  ED={ed_src}\n",
            "ok" if (op_src == "audio" and ed_src == "audio") else "err",
        )

        for chapter in chapters:
            self._log(f"     {ms_to_mkv_timestamp(chapter.timestamp_ms)}  →  {chapter.name}\n", "ch")

        self._log(f"   → {xml_path}\n", "dim")

    def _on_analysis_complete(self):
        """Handle all videos analysis completion"""
        self._log(f"\n{'═' * 54}\n", "dim")
        self._log(f"Completed: {len(self.results)} video(s)\n", "ok")

        fallback_count = sum(
            1 for r in self.results
            if r.op_source == MatchSource.FALLBACK or r.ed_source == MatchSource.FALLBACK
        )

        if fallback_count:
            self._log(f"  ⚠ {fallback_count} video(s) need manual review\n", "err")

        self._log("\n", "dim")
        self._set_busy(False)
        self.cancel_event = None

    def _on_analysis_cancelled(self):
        """Handle analysis cancellation"""
        self._log("\nAnalysis cancelled.\n", "err")
        self._set_busy(False)
        self.cancel_event = None

    # ─── Review dialog ────────────────────────────────────────────────────────


    def _open_settings(self):
        """Open chapter name settings dialog"""
        dialog = SettingsDialog(self.root, self.settings)
        if dialog.saved:
            from settings import load_settings
            self.settings = load_settings()
            self._log("Settings saved.\n", "ok")

    def _review_chapters(self):
        """Open review dialog"""
        if not self.results:
            messagebox.showerror("Error", "Run analysis first.")
            return

        dialog = ReviewDialog(self.root, self.results)
        if dialog.confirmed:
            self._log("Changes saved.\n", "ok")

    # ─── Muxing ───────────────────────────────────────────────────────────────

    def _mux_videos(self):
        """Merge chapters into videos"""
        if self.busy:
            return

        if not self.results:
            messagebox.showerror("Error", "Run analysis first.")
            return

        use_inplace = self.inplace.get()

        if use_inplace:
            if not shutil.which("mkvpropedit"):
                messagebox.showerror("Error", "mkvpropedit not found.")
                return
        else:
            if not shutil.which("mkvmerge"):
                messagebox.showerror("Error", "mkvmerge not found.")
                return

        # Warn about fallback timings
        fallback_results = [
            r for r in self.results
            if r.op_source == MatchSource.FALLBACK or r.ed_source == MatchSource.FALLBACK
        ]

        if fallback_results:
            names = "\n".join(r.basename for r in fallback_results[:5])
            if not messagebox.askyesno(
                "Warning",
                f"{len(fallback_results)} video(s) have estimated timings:\n\n{names}\n\nContinue?",
            ):
                return

        if use_inplace:
            self._log(f"\nBurning chapters in-place ({len(self.results)} videos)...\n", "dim")
            self._log("  ⚠ Original files will be modified — ensure you have backups.\n", "err")
            out_dir = None
        else:
            out_dir = filedialog.askdirectory(title="Select Output Directory")
            if not out_dir:
                return
            self._log(f"\nMerging {len(self.results)} video(s)...\n", "dim")

        self._set_busy(True)

        def run():
            for result in self.results:
                video = next(
                    (v for v in self.videos if os.path.basename(v) == result.basename),
                    None,
                )
                xml = result.xml_path

                if not video or not xml or not os.path.exists(xml):
                    self.root.after(
                        0,
                        self._log,
                        f"  Skipping {result.basename} (no XML)\n",
                        "err",
                    )
                    continue

                if use_inplace:
                    if not video.lower().endswith(".mkv"):
                        self.root.after(
                            0,
                            self._log,
                            f"  Skipping {result.basename}: in-place only supports MKV\n",
                            "err",
                        )
                        continue

                    cmd = ["mkvpropedit", video, "--chapters", xml]
                    self.root.after(0, self._log, f"  🔥 {result.basename}\n", "dim")

                    try:
                        proc = subprocess.run(cmd, capture_output=True, creationflags=_CREATIONFLAGS)
                        if proc.returncode == 0:
                            self.root.after(
                                0,
                                self._log,
                                f"  ✔ Chapters burned into {result.basename}\n",
                                "ok",
                            )
                        else:
                            msg = (proc.stderr or proc.stdout).decode(errors="replace").strip()
                            self.root.after(0, self._log, f"  ✘ {msg}\n", "err")
                    except Exception as e:
                        self.root.after(0, self._log, f"  ✘ {e}\n", "err")

                else:
                    stem, ext = os.path.splitext(os.path.basename(video))
                    output = os.path.join(out_dir, f"{stem} chapters{ext or '.mkv'}")
                    cmd    = ["mkvmerge", "-o", output, "--chapters", xml, video]

                    self.root.after(0, self._log, f"  → {os.path.basename(output)}\n", "dim")

                    try:
                        proc = subprocess.run(cmd, capture_output=True, creationflags=_CREATIONFLAGS)
                        if proc.returncode in (0, 1):
                            self.root.after(
                                0,
                                self._log,
                                f"  ✔ {os.path.basename(output)}\n",
                                "ok",
                            )
                        else:
                            msg = (proc.stderr or proc.stdout).decode(errors="replace").strip()
                            self.root.after(0, self._log, f"  ✘ {msg}\n", "err")
                    except Exception as e:
                        self.root.after(0, self._log, f"  ✘ {e}\n", "err")

            self.root.after(0, lambda: (
                self._log("Done.\n\n", "ok"),
                self._set_busy(False),
            ))

        threading.Thread(target=run, daemon=True).start()

    # ─── Cache / log utilities ────────────────────────────────────────────────

    def _clear_cache(self):
        """
        Ask user for a root folder, then recursively delete all
        .themes directories and _chapters.xml files inside it.
        """
        import glob
        import tempfile

        # Ask user to pick the root folder
        root_folder = filedialog.askdirectory(
            title="Select root folder to clean (e.g. C:\\Users\\503\\Videos\\Fun\\Anime)"
        )
        if not root_folder:
            self._log("Clear cache cancelled.\n", "dim")
            return

        deleted_files = 0
        deleted_dirs  = 0
        freed         = 0

        def _file_size(path):
            try:
                return os.path.getsize(path)
            except Exception:
                return 0

        def _del_dir(dir_path):
            nonlocal deleted_dirs, freed
            if os.path.isdir(dir_path):
                try:
                    size = sum(
                        _file_size(os.path.join(dp, f))
                        for dp, _, files in os.walk(dir_path)
                        for f in files
                    )
                    shutil.rmtree(dir_path, ignore_errors=True)
                    freed        += size
                    deleted_dirs += 1
                    self._log(f"  Deleted {dir_path}\n", "dim")
                except Exception:
                    pass

        def _del_file(file_path):
            nonlocal deleted_files, freed
            if os.path.isfile(file_path):
                try:
                    freed         += _file_size(file_path)
                    os.remove(file_path)
                    deleted_files += 1
                    self._log(f"  Deleted {os.path.basename(file_path)}\n", "dim")
                except Exception:
                    pass

        self._log(f"\nScanning: {root_folder}\n", "ch")

        # Walk recursively through the chosen folder
        for dirpath, dirnames, filenames in os.walk(root_folder, topdown=True):
            # Delete .themes folders
            if ".themes" in dirnames:
                _del_dir(os.path.join(dirpath, ".themes"))
                dirnames.remove(".themes")  # don't recurse into it

            # Delete _chapters.xml files
            for fname in filenames:
                if fname.endswith("_chapters.xml"):
                    _del_file(os.path.join(dirpath, fname))

        # Also clean tracked temp dirs and system temp
        for dir_path in list(_TEMP_DIRS):
            _del_dir(dir_path)
            if dir_path in _TEMP_DIRS:
                _TEMP_DIRS.remove(dir_path)

        tmp_dir = tempfile.gettempdir()
        for dir_path in glob.glob(os.path.join(tmp_dir, "animechap_*")):
            _del_dir(dir_path)

        mb = freed / (1024 * 1024)
        total = deleted_dirs + deleted_files
        if total:
            self._log(
                f"\nCleared {deleted_dirs} .themes folder(s) + {deleted_files} XML file(s) "
                f"({mb:.1f} MB freed)\n", "ok"
            )
        else:
            self._log("Nothing to clean in that folder.\n", "dim")

    def _copy_log(self):
        """Copy log content to clipboard"""
        content = self.log_text.get("1.0", tk.END).strip()
        if not content:
            self._log("Log is empty.\n", "dim")
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self.root.update()
        self._log("Log copied to clipboard.\n", "ok")

    # ─── Shared DB stats ─────────────────────────────────────────────────────

    def _show_db_stats(self) -> None:
        """Display full stats and diagnostics for the shared database."""
        import threading
        import remote_db as _rdb

        self._log("\n── Shared Database ─────────────────────────\n", "dim")

        # Local cache stats (instant)
        try:
            db    = get_shared_db()
            stats = db.get_stats()
            self._log(f"  💾 Local cache: {stats['cache_episodes']} episode(s)\n", "ok")
            self._log(f"  💾 Cache path : {db.cache_path()}\n", "dim")
        except Exception as exc:
            self._log(f"  💾 Cache error: {exc}\n", "err")

        # Supabase diagnostics in background (requires network)
        self._log("  ☁  Checking Supabase…\n", "dim")

        def run_diagnose():
            try:
                msgs = _rdb.diagnose()
                def show():
                    for m in msgs:
                        tag = "ok" if m.startswith("✔") else ("err" if m.startswith("✘") else "dim")
                        self._log(f"  ☁  {m}\n", tag)
                    self._log("────────────────────────────────────────────\n", "dim")
                self.root.after(0, show)
            except Exception as exc:
                self.root.after(0, self._log, f"  ☁  diagnose error: {exc}\n", "err")

        threading.Thread(target=run_diagnose, daemon=True).start()

    # ─── Error handler ────────────────────────────────────────────────────────

    def _on_error(self, message: str):
        """Handle errors"""
        self._log(f"\nError: {message}\n\n", "err")
        messagebox.showerror("Error", message)
        self._set_busy(False)


def run_app() -> int:
    """Create the Tk root and run the application. Returns exit code."""
    if not GUI_AVAILABLE:
        print("Error: tkinter is not available. Please install python3-tk.")
        return 1

    root = tk.Tk()

    # Set window icon
    try:
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app_icon.ico")
        root.iconbitmap(icon_path)
    except Exception:
        pass

    Application(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_app())
