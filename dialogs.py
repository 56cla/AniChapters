"""
Modal dialogs: AnimePickerDialog and ReviewDialog.
"""
from __future__ import annotations

from typing import Optional

from constants import A1, A2, A3, BG, BORD, DIM, FONT, FONTB, FONTL, FONTS, PANEL, TEXT
from chapters import write_chapters_xml
from models import AnalysisResult, Chapter, MatchSource
from timestamps import ms_to_mkv_timestamp, timestamp_to_ms

try:
    import tkinter as tk
    from tkinter import ttk
    GUI_AVAILABLE = True
except ImportError:
    GUI_AVAILABLE = False


class AnimePickerDialog(tk.Toplevel if GUI_AVAILABLE else object):
    """Dialog for selecting anime from search results"""

    def __init__(self, parent, results: list[dict], query: str = ""):
        if not GUI_AVAILABLE:
            self.chosen = None
            return

        super().__init__(parent)
        self.title("Select Anime")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.grab_set()

        self.chosen: Optional[tuple[str, Optional[dict]]] = None
        self._results = results

        # Header
        tk.Label(
            self,
            text="Select the correct anime:",
            font=FONTB,
            fg=TEXT,
            bg=BG,
        ).pack(padx=20, pady=(16, 4))

        tk.Label(
            self,
            text=f'Search: "{query}"',
            font=FONTS,
            fg=DIM,
            bg=BG,
        ).pack(padx=20, pady=(0, 8))

        # Results list
        self.var = tk.IntVar(value=0)
        for i, anime in enumerate(results):
            name = anime.get("name", "?")
            year = anime.get("year", "?")
            tk.Radiobutton(
                self,
                text=f"{name}  ({year})",
                variable=self.var,
                value=i,
                font=FONT,
                fg=TEXT,
                bg=BG,
                selectcolor=PANEL,
                activebackground=BG,
                activeforeground=A2,
                anchor="w",
            ).pack(fill="x", padx=20, pady=2)

        # Buttons
        tk.Frame(self, height=1, bg=BORD).pack(fill="x", padx=20, pady=8)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=(0, 14))

        tk.Button(
            btn_frame,
            text="OK",
            font=FONTB,
            fg=BG,
            bg=A2,
            bd=0,
            padx=14,
            pady=5,
            cursor="hand2",
            command=self._on_ok,
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="Manual Search",
            font=FONTB,
            fg=TEXT,
            bg=BORD,
            bd=0,
            padx=14,
            pady=5,
            cursor="hand2",
            command=self._on_manual,
        ).pack(side="left", padx=5)

        tk.Button(
            btn_frame,
            text="Skip",
            font=FONTS,
            fg=DIM,
            bg=BG,
            bd=0,
            padx=10,
            pady=5,
            cursor="hand2",
            command=self.destroy,
        ).pack(side="left", padx=5)

        self.wait_window()

    def _on_ok(self):
        if self._results:
            self.chosen = ("pick", self._results[self.var.get()])
        self.destroy()

    def _on_manual(self):
        self.chosen = ("manual", None)
        self.destroy()


class ReviewDialog(tk.Toplevel if GUI_AVAILABLE else object):
    """Dialog for reviewing and editing detected chapters"""

    def __init__(self, parent, results: list[AnalysisResult]):
        if not GUI_AVAILABLE:
            self.confirmed = False
            return

        super().__init__(parent)
        self.title("Review Chapters")
        self.configure(bg=BG)
        self.geometry("920x660")
        self.resizable(True, True)
        self.grab_set()

        self.confirmed = False
        self._results  = results
        self._rows: dict[str, list] = {}

        self._build_ui()
        self._fill_tabs()

        self.wait_window()

    def _build_ui(self):
        # Header
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=16, pady=(14, 6))

        tk.Label(header, text="Review Chapters", font=FONTL, fg=TEXT, bg=BG).pack(side="left")
        tk.Label(
            header,
            text="  Yellow = Estimated (review manually)",
            font=FONTS,
            fg=A1,
            bg=BG,
        ).pack(side="left", padx=8)

        tk.Frame(self, height=1, bg=BORD).pack(fill="x", padx=16)

        # Notebook for tabs
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=8)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=DIM, padding=[10, 4], font=FONTS)
        style.map("TNotebook.Tab", background=[("selected", BORD)], foreground=[("selected", TEXT)])

        # Buttons
        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=(0, 12))

        tk.Button(
            btn_frame,
            text="Save & Confirm",
            font=FONTB,
            fg=BG,
            bg=A2,
            bd=0,
            padx=16,
            pady=7,
            cursor="hand2",
            command=self._on_confirm,
        ).pack(side="left", padx=6)

        tk.Button(
            btn_frame,
            text="Cancel",
            font=FONTB,
            fg=TEXT,
            bg=BORD,
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            command=self.destroy,
        ).pack(side="left", padx=6)

    def _fill_tabs(self):
        for result in self._results:
            frame = tk.Frame(self.notebook, bg=PANEL)
            self.notebook.add(frame, text=f"ep{result.episode or '?'}")

            # Filename
            tk.Label(
                frame,
                text=result.basename,
                font=FONTS,
                fg=DIM,
                bg=PANEL,
                anchor="w",
            ).pack(fill="x", padx=10, pady=(8, 2))

            # Legend
            legend = tk.Frame(frame, bg=PANEL)
            legend.pack(fill="x", padx=10, pady=(0, 4))

            for lbl, col in [("Audio match", A2), ("Fallback", A1)]:
                tk.Label(legend, text=lbl, font=FONTS, fg=col, bg=PANEL).pack(side="left", padx=8)

            # Header row
            header_row = tk.Frame(frame, bg=BORD)
            header_row.pack(fill="x", padx=10)

            for col, w in [("Chapter Name", 28), ("Timestamp HH:MM:SS.mmm", 26), ("Source", 10)]:
                tk.Label(
                    header_row,
                    text=col,
                    font=FONTB,
                    fg=DIM,
                    bg=BORD,
                    width=w,
                    anchor="w",
                ).pack(side="left", padx=4, pady=3)

            # Scrollable content
            canvas    = tk.Canvas(frame, bg=PANEL, highlightthickness=0)
            scrollbar = tk.Scrollbar(frame, orient="vertical", command=canvas.yview)
            canvas.configure(yscrollcommand=scrollbar.set)

            scrollbar.pack(side="right", fill="y")
            canvas.pack(fill="both", expand=True, padx=10)

            content_frame = tk.Frame(canvas, bg=PANEL)
            canvas.create_window((0, 0), window=content_frame, anchor="nw")
            content_frame.bind(
                "<Configure>",
                lambda e, c=canvas: c.configure(scrollregion=c.bbox("all")),
            )

            # Chapter rows
            chapters = self._make_chapter_rows(result)
            row_list = []

            for i, (ms, name, source) in enumerate(chapters):
                bg_color = PANEL if i % 2 == 0 else "#1a1a24"
                row = tk.Frame(content_frame, bg=bg_color)
                row.pack(fill="x", pady=1)

                # Name entry
                name_var = tk.StringVar(value=name)
                tk.Entry(
                    row,
                    textvariable=name_var,
                    font=FONT,
                    bg=bg_color,
                    fg=TEXT,
                    insertbackground=TEXT,
                    bd=0,
                    width=30,
                    highlightthickness=1,
                    highlightbackground=BORD,
                    highlightcolor=A3,
                ).pack(side="left", padx=(0, 4), ipady=3)

                # Timestamp entry
                ts_var    = tk.StringVar(value=ms_to_mkv_timestamp(ms) if ms is not None else "")
                ts_color  = A1 if source == "fallback" else (A2 if source == "audio" else TEXT)
                tk.Entry(
                    row,
                    textvariable=ts_var,
                    font=FONT,
                    bg=bg_color,
                    fg=ts_color,
                    insertbackground=TEXT,
                    bd=0,
                    width=28,
                    highlightthickness=1,
                    highlightbackground=BORD,
                    highlightcolor=A3,
                ).pack(side="left", padx=(0, 4), ipady=3)

                # Source label
                src_color = {"audio": A2, "fallback": A1}.get(source, DIM)
                tk.Label(
                    row,
                    text=source or "—",
                    font=FONTS,
                    fg=src_color,
                    bg=bg_color,
                    width=10,
                    anchor="w",
                ).pack(side="left", padx=4)

                row_list.append((ts_var, name_var, source))

            self._rows[result.basename] = row_list

    def _make_chapter_rows(self, result: AnalysisResult) -> list[tuple]:
        """Generate chapter row data from analysis result"""
        op_label = (
            f"♪ {result.op_theme.label} — {result.op_theme.title}"
            if result.op_theme
            else "♪ Opening"
        )
        ed_label = (
            f"♪ {result.ed_theme.label} — {result.ed_theme.title}"
            if result.ed_theme
            else "♪ Ending"
        )

        raw: list[tuple] = []

        if result.op_start_ms is None or result.op_start_ms > 3000:
            raw.append((0, "Cold Open", ""))

        if result.op_start_ms is not None:
            raw.append((result.op_start_ms, op_label, result.op_source.value))

        if result.op_end_ms is not None:
            raw.append((result.op_end_ms, "Episode", result.op_source.value))

        # ED section - only add After Credits if ED was found via audio match
        if result.ed_start_ms is not None and result.ed_source == MatchSource.AUDIO:
            raw.append((result.ed_start_ms, ed_label, result.ed_source.value))
            if result.ed_end_ms is not None and result.video_duration_ms:
                if result.video_duration_ms - result.ed_end_ms > 5000:
                    raw.append((result.ed_end_ms, "After Credits", result.ed_source.value))
        elif result.ed_start_ms is not None and result.ed_source == MatchSource.FALLBACK:
            raw.append((result.ed_start_ms, f"{ed_label} (estimated)", result.ed_source.value))
        # If ed_source is NONE, don't add ED chapter (user can add manually)

        if result.video_duration_ms:
            raw.append((result.video_duration_ms, "End", ""))

        # Remove duplicates
        seen: set[int] = set()
        unique: list[tuple] = []
        for item in sorted(raw, key=lambda x: x[0] if x[0] is not None else 0):
            if item[0] not in seen:
                seen.add(item[0])
                unique.append(item)

        return unique

    def _on_confirm(self):
        self.confirmed = True

        for result in self._results:
            chapters: list[Chapter] = []
            for ts_var, name_var, _ in self._rows.get(result.basename, []):
                ms   = timestamp_to_ms(ts_var.get())
                name = name_var.get().strip()
                if ms is not None and name:
                    chapters.append(Chapter(ms, name))

            if result.xml_path and chapters:
                write_chapters_xml(chapters, result.xml_path)
                result.chapters = chapters

        self.destroy()
