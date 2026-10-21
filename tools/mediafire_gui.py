#!/usr/bin/env python3
"""A small, dependency-free front end for tools/mediafire_dl.py.

The window follows the layout of the ArkLoad window: a compact toolbar, a large
download list with a dashed drop zone, and a slim status bar. Behind it sits a real download
engine – MediaFire folders and files are listed, verified against MediaFire's own SHA-256,
resumed from ``.part`` files and retried until every file is present and verified.

    python tools/mediafire_gui.py
    python -m tools.mediafire_gui

On Windows, double-click tools/ArkLoad Downloader.cmd instead.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import time
import tkinter as tk
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, font as tkfont, messagebox, ttk

try:  # package import (python -m tools.mediafire_gui)
    from . import mediafire_dl as dl
    from . import mediafire_resume as rs
    from .mediafire_session import DownloadSession
except ImportError:  # direct script run (python tools/mediafire_gui.py)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import mediafire_dl as dl  # type: ignore[no-redef]
    import mediafire_resume as rs  # type: ignore[no-redef]
    from mediafire_session import DownloadSession  # type: ignore[no-redef]

try:  # optional: real OS drag & drop (pip install tkinterdnd2)
    from tkinterdnd2 import DND_FILES, TkinterDnD

    HAVE_DND = True
except ImportError:  # pragma: no cover - depends on the machine
    HAVE_DND = False

APP = "ArkLoad"
SETTINGS = Path.home() / ".mediafire-dl-gui.json"
LOG = Path.home() / ".mediafire-dl-gui.log"

#: file type groups used by the toolbar filter
FILE_GROUPS = {
    "All types": None,
    "Archives": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".jar", ".mod", ".pak"),
    "Images": (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tga", ".dds", ".svg"),
    "Audio": (".mp3", ".ogg", ".wav", ".flac", ".m4a", ".opus"),
    "Video": (".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv"),
    "Text": (".txt", ".md", ".json", ".xml", ".ini", ".cfg", ".csv", ".log"),
}

PALETTES = {
    "dark": {
        "bg": "#0e1013",
        "toolbar": "#14171c",
        "surface": "#171a20",
        "field": "#1c2027",
        "border": "#272c35",
        "border_soft": "#1f242c",
        "text": "#e7eaf0",
        "muted": "#8b93a3",
        "accent": "#3d8bfd",
        "accent_hover": "#5b9dff",
        "accent_press": "#2f6fd6",
        "success": "#3fbf7f",
        "danger": "#ef5f5f",
        "warning": "#e0a93a",
        "select": "#1d2836",
        "select_fg": "#ffffff",
        "white": "#ffffff",
    },
    "light": {
        "bg": "#f4f5f7",
        "toolbar": "#ffffff",
        "surface": "#ffffff",
        "field": "#ffffff",
        "border": "#dfe3ea",
        "border_soft": "#e9ecf1",
        "text": "#151922",
        "muted": "#69707e",
        "accent": "#2f7dfa",
        "accent_hover": "#1f6ae8",
        "accent_press": "#1b5ac9",
        "success": "#11925a",
        "danger": "#cf3b3b",
        "warning": "#b7791f",
        "select": "#e3edff",
        "select_fg": "#0f172a",
        "white": "#ffffff",
    },
}


# --------------------------------------------------------------------------- settings

def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(data: dict) -> None:
    try:
        SETTINGS.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def default_destination() -> str:
    downloads = Path.home() / "Downloads"
    return str(downloads if downloads.is_dir() else Path.home())


def system_prefers_dark() -> bool:
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except (OSError, ValueError, TypeError):
            return False
    return "dark" in os.environ.get("GTK_THEME", "").lower()


def set_titlebar_theme(root: tk.Tk, dark: bool) -> None:
    """Ask Windows' DWM for a dark caption; harmless everywhere else."""
    if os.name != "nt":
        return
    try:
        import ctypes
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, older Windows 10
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:  # noqa: BLE001 - older Windows, no DWM, or a hidden window
        pass


def open_path(path: Path) -> None:
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# --------------------------------------------------------------------------- helpers

def parse_patterns(text: str) -> list[str]:
    """Split ``*.zip, *.jar; mods/*`` into globs; commas, semicolons and newlines separate."""
    return [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]


def matches_patterns(rel, patterns: list[str]) -> bool:
    """True when a planned path matches any glob (``*.jar``, ``mods/*`` …)."""
    return rs.matches_patterns(rel, patterns)


def fmt_speed(bytes_per_second: float) -> str:
    bits = max(0.0, float(bytes_per_second)) * 8.0
    if bits >= 1_000_000:
        return f"{bits / 1_000_000:.2f} Mbps"
    if bits >= 1_000:
        return f"{bits / 1_000:.0f} Kbps"
    if bits > 0:
        return f"{bits:.0f} bps"
    return "0.00 Mbps"


def fmt_duration(seconds: float) -> str:
    total = int(max(0.0, seconds))
    if total < 10:
        return "<10s"
    if total < 60:
        return f"{total}s"
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def fmt_bytes(value: int | None) -> str:
    return dl.fmt_size(value)


def file_group(name: str) -> str:
    suffix = Path(name).suffix.lower()
    for group, suffixes in FILE_GROUPS.items():
        if suffixes and suffix in suffixes:
            return group
    return "Other"


class SpeedMeter:
    """A short rolling window over transferred bytes, so the label does not jitter."""

    def __init__(self, window: float = 3.0):
        self.window = window
        self.samples: deque[tuple[float, int]] = deque()

    def reset(self) -> None:
        self.samples.clear()

    def update(self, total: int, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        self.samples.append((now, int(total)))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()
        if len(self.samples) < 2:
            return 0.0
        t0, v0 = self.samples[0]
        t1, v1 = self.samples[-1]
        return max(0.0, (v1 - v0) / (t1 - t0)) if t1 > t0 else 0.0


# --------------------------------------------------------------------------- model

class Download:
    """One download in the list: a MediaFire folder or file heading for a destination."""

    def __init__(self, job_id: str, url: str, destination: str, title: str = "",
                 source_kind: str = "folder", source_key: str = ""):
        self.job_id = job_id
        self.url = url
        self.destination = destination
        self.title = title or source_key or url
        self.source_kind = source_kind
        self.source_key = source_key
        self.root: Path | None = None
        self.plan = None
        self.files: list[rs.FileRecord] = []
        self.status = rs.JOB_QUEUED
        self.error = ""
        self.speed = 0.0
        self.bytes_done = 0
        self.bytes_total = 0
        self.created = time.time()
        self.iid = ""
        self.file_iids: list[str] = []
        self.session: "DownloadSession | None" = None
        self.progress_by_index: dict[int, int] = {}
        self.network_bytes = 0
        self.speed_meter = SpeedMeter()
        self.paused = False
        self.failures: list[tuple[str, str]] = []
        self.verifying = False
        self.options: dict = {}

    # -- helpers --------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self.session is not None and self.session.is_alive()

    @property
    def verified(self) -> int:
        return sum(1 for record in self.files if record.status == rs.FILE_COMPLETE)

    @property
    def total(self) -> int:
        return len(self.files)

    def percent(self) -> int:
        if not self.files:
            return 0
        if self.bytes_total:
            return int(100 * min(self.done_bytes, self.bytes_total) / self.bytes_total)
        return int(100 * self.verified / len(self.files))

    def status_label(self) -> str:
        labels = {
            rs.JOB_QUEUED: "Queued",
            rs.JOB_RUNNING: "Downloading",
            rs.JOB_PAUSED: "Paused",
            rs.JOB_INTERRUPTED: "Interrupted",
            rs.JOB_CANCELLED: "Cancelled",
            rs.JOB_COMPLETE: "Completed",
            rs.JOB_INCOMPLETE: "Incomplete",
            rs.JOB_FAILED: "Failed",
        }
        if self.status == rs.JOB_RUNNING and self.verifying:
            return "Verifying"
        return labels.get(self.status, "Queued")

    def status_tag(self) -> str:
        if self.status == rs.JOB_COMPLETE:
            return "done"
        if self.status in (rs.JOB_FAILED, rs.JOB_INCOMPLETE):
            return "failed"
        if self.status in (rs.JOB_CANCELLED, rs.JOB_INTERRUPTED, rs.JOB_PAUSED):
            return "cancelled"
        if self.status == rs.JOB_RUNNING:
            return "running"
        return "waiting"


# --------------------------------------------------------------------------- dialog

class AddDownloadDialog:
    """The real 'Add download' configuration window (link, destination, options, filters)."""

    def __init__(self, parent: tk.Misc, settings: dict, palette: dict):
        self.parent = parent
        self.palette = palette
        self.result: dict | None = None
        self.win = tk.Toplevel(parent)
        self.win.title("Add download")
        self.win.configure(bg=palette["bg"])
        self.win.transient(parent)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        self.url_var = tk.StringVar()
        self.dest_var = tk.StringVar(value=settings.get("destination") or default_destination())
        self.subfolder_var = tk.BooleanVar(value=bool(settings.get("subfolder", True)))
        self.overwrite_var = tk.BooleanVar(value=bool(settings.get("overwrite", False)))
        self.jobs_var = tk.StringVar(value=str(settings.get("jobs", 4)))
        self.retries_var = tk.StringVar(value=str(settings.get("retries", 3)))
        self.recovery_var = tk.StringVar(value=str(settings.get("recovery_rounds", 3)))
        self.filter_var = tk.StringVar()
        self.cookie_var = tk.StringVar()

        frame = ttk.Frame(self.win, padding=(22, 20))
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, text="MEDIAFIRE LINK OR FOLDER KEY",
                  style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        url_row = ttk.Frame(frame)
        url_row.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        url_row.columnconfigure(0, weight=1)
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var)
        self.url_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(url_row, text="Paste", style="Ghost.TButton",
                   command=self._paste).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(frame, text="A folder link, a file link or a bare folder key.",
                  style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))

        ttk.Label(frame, text="SAVE TO", style="FieldLabel.TLabel").grid(
            row=3, column=0, sticky="w", pady=(16, 0))
        dest_row = ttk.Frame(frame)
        dest_row.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        dest_row.columnconfigure(0, weight=1)
        ttk.Entry(dest_row, textvariable=self.dest_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(dest_row, text="Browse…", style="Ghost.TButton",
                   command=self._browse).grid(row=0, column=1, padx=(8, 0))

        options = ttk.Frame(frame)
        options.grid(row=5, column=0, sticky="ew", pady=(16, 0))
        options.columnconfigure(0, weight=1)
        ttk.Checkbutton(options, text="Create a folder for this download",
                        variable=self.subfolder_var).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options, text="Overwrite existing files",
                        variable=self.overwrite_var).grid(row=1, column=0, sticky="w", pady=(6, 0))

        spins = ttk.Frame(options)
        spins.grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(spins, text="Parallel", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(spins, from_=1, to=16, width=4,
                    textvariable=self.jobs_var).pack(side="left", padx=(6, 16))
        ttk.Label(spins, text="Retries", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(spins, from_=1, to=10, width=4,
                    textvariable=self.retries_var).pack(side="left", padx=(6, 16))
        ttk.Label(spins, text="Recovery passes", style="Muted.TLabel").pack(side="left")
        ttk.Spinbox(spins, from_=0, to=10, width=4,
                    textvariable=self.recovery_var).pack(side="left", padx=(6, 0))
        ttk.Label(frame, text="Recovery passes re-check missing, incomplete or corrupt files "
                              "after the first attempt.",
                  style="Muted.TLabel").grid(row=6, column=0, sticky="w", pady=(6, 0))

        ttk.Label(frame, text="ONLY DOWNLOAD MATCHING FILES (OPTIONAL)",
                  style="FieldLabel.TLabel").grid(row=7, column=0, sticky="w", pady=(16, 0))
        ttk.Entry(frame, textvariable=self.filter_var).grid(row=8, column=0, sticky="ew", pady=(5, 0))
        ttk.Label(frame, text="Comma-separated globs, e.g. *.zip, *.jar, mods/*.",
                  style="Muted.TLabel").grid(row=9, column=0, sticky="w", pady=(4, 0))

        self.advanced = ttk.Frame(frame)
        self.advanced.grid(row=10, column=0, sticky="ew", pady=(14, 0))
        self.advanced.columnconfigure(0, weight=1)
        ttk.Label(self.advanced, text="BROWSER COOKIES (OPTIONAL)",
                  style="FieldLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(self.advanced, textvariable=self.cookie_var).grid(
            row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Label(self.advanced, text="For 'blocked or not available' files copied into your "
                                      "own MediaFire: ukey=…; user=…   Never saved.",
                  style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=11, column=0, sticky="e", pady=(20, 0))
        ttk.Button(buttons, text="Cancel", command=self._cancel).grid(row=0, column=0, padx=(0, 8))
        self.add_btn = ttk.Button(buttons, text="Add download", style="Accent.TButton",
                                  command=self._accept)
        self.add_btn.grid(row=0, column=1)

        self.win.bind("<Return>", lambda _e: self._accept())
        self.win.bind("<Escape>", lambda _e: self._cancel())
        self._prefill()
        self.url_entry.focus_set()
        self._centre()

    def _centre(self) -> None:
        """Put the dialog in the middle of the main window instead of the screen corner."""
        self.win.update_idletasks()
        width = max(self.win.winfo_reqwidth(), 520)
        height = self.win.winfo_reqheight()
        try:
            x = self.parent.winfo_rootx() + (self.parent.winfo_width() - width) // 2
            y = self.parent.winfo_rooty() + (self.parent.winfo_height() - height) // 3
        except tk.TclError:
            x = y = 100
        self.win.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")

    # -- helpers --------------------------------------------------------------
    def _prefill(self) -> None:
        try:
            text = self.parent.clipboard_get().strip()
        except tk.TclError:
            return
        if "mediafire.com" in text.lower():
            self.url_var.set(text)

    def _paste(self) -> None:
        try:
            text = self.parent.clipboard_get().strip()
        except tk.TclError:
            return
        if text:
            self.url_var.set(text)

    def _browse(self) -> None:
        initial = self.dest_var.get().strip() or str(Path.home())
        if not Path(initial).expanduser().is_dir():
            initial = str(Path.home())
        chosen = filedialog.askdirectory(parent=self.win, initialdir=initial,
                                         title="Choose where to save the files")
        if chosen:
            self.dest_var.set(chosen)

    def _cancel(self) -> None:
        self.result = None
        self.win.destroy()

    def _accept(self) -> None:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showinfo(APP, "Paste a MediaFire link or folder key first.",
                                parent=self.win)
            self.url_entry.focus_set()
            return
        destination = self.dest_var.get().strip() or default_destination()
        try:
            dest = Path(destination).expanduser()
            if dest.exists() and not dest.is_dir():
                raise NotADirectoryError(f"{dest} is a file")
            dest.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            messagebox.showerror(APP, f"Cannot use this destination:\n{destination}\n\n{e}",
                                 parent=self.win)
            return
        try:
            source = dl.parse_source(url)
        except Exception as e:  # noqa: BLE001 - tell the user what is wrong with the link
            messagebox.showerror(APP, f"That does not look like a MediaFire link:\n\n{e}",
                                 parent=self.win)
            return

        def number(var, default, low, high):
            try:
                return max(low, min(high, int(var.get())))
            except ValueError:
                return default

        self.result = {
            "url": url,
            "dest": str(dest),
            "source": source,
            "subfolder": bool(self.subfolder_var.get()),
            "overwrite": bool(self.overwrite_var.get()),
            "jobs": number(self.jobs_var, 4, 1, 16),
            "retries": number(self.retries_var, 3, 1, 10),
            "recovery_rounds": number(self.recovery_var, 3, 0, 10),
            "patterns": parse_patterns(self.filter_var.get()),
            "cookies": self.cookie_var.get().strip() or None,
        }
        self.win.destroy()

    def show(self) -> dict | None:
        self.win.wait_window()
        return self.result


# --------------------------------------------------------------------------- window

def rounded_rect_points(x0: float, y0: float, x1: float, y1: float, radius: float,
                        steps: int = 8) -> list[float]:
    """Points of a rounded rectangle, for the dashed drop zone on a canvas."""
    import math

    radius = max(1.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    points: list[float] = []

    def arc(cx: float, cy: float, start: float, end: float) -> None:
        for step in range(steps + 1):
            angle = math.radians(start + (end - start) * step / steps)
            points.extend((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))

    arc(x1 - radius, y0 + radius, -90, 0)
    arc(x1 - radius, y1 - radius, 0, 90)
    arc(x0 + radius, y1 - radius, 90, 180)
    arc(x0 + radius, y0 + radius, 180, 270)
    points.extend((points[0], points[1]))
    return points


class App:
    """The main window: toolbar, download list, drop zone and status bar."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.downloads: dict[str, Download] = {}
        self.order: list[str] = []
        self.settings = load_settings()
        self.alive = True
        self.online = True
        self.search_text = ""
        self.sort_mode = self.settings.get("sort") or "Newest first"
        self.filter_mode = self.settings.get("filter") or "All types"
        dark = self.settings.get("dark")
        self.dark = dark if isinstance(dark, bool) else system_prefers_dark()
        self.palette = PALETTES["dark" if self.dark else "light"]

        self.root.report_callback_exception = self._report_tk_error
        self._build()
        self._apply_theme(self.dark)
        self._restore_settings()
        self._restore_jobs()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(120, self._poll)
        self.root.after(500, self._speed_tick)
        self.root.after(450, self._auto_resume)

    # ------------------------------------------------------------------ layout

    def _build(self) -> None:
        root = self.root
        root.title(APP)
        root.minsize(940, 600)
        width, height = 1120, 720
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.configure(bg=self.palette["bg"])

        families = set(tkfont.families(root))
        family = "Segoe UI" if "Segoe UI" in families else tkfont.nametofont("TkDefaultFont").actual("family")
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
            try:
                tkfont.nametofont(name).configure(family=family, size=10)
            except tk.TclError:
                pass
        self.body_font = tkfont.Font(root, family=family, size=10)
        self.small_font = tkfont.Font(root, family=family, size=9)
        self.field_font = tkfont.Font(root, family=family, size=9, weight="bold")
        self.title_font = tkfont.Font(root, family=family, size=13, weight="bold")
        self.status_font = tkfont.Font(root, family=family, size=10, weight="bold")
        self.button_font = tkfont.Font(root, family=family, size=10, weight="bold")
        self.family = family
        self.auto_resume_var = tk.BooleanVar(value=True)
        self.theme_var = tk.StringVar(value="dark" if self.dark else "light")

        self.style = ttk.Style(root)
        self.style.theme_use("clam")

        # ---- toolbar --------------------------------------------------------
        toolbar = ttk.Frame(root, style="Toolbar.TFrame", padding=(16, 9))
        toolbar.grid(row=0, column=0, sticky="ew")
        root.rowconfigure(2, weight=1)
        root.columnconfigure(0, weight=1)
        self.toolbar = toolbar
        toolbar.columnconfigure(6, weight=1)

        self.add_btn = ttk.Button(toolbar, text="＋  Add download", style="Accent.TButton",
                                  command=self._add_download)
        self.add_btn.grid(row=0, column=0, sticky="w")
        ttk.Separator(toolbar, orient="vertical").grid(row=0, column=1, sticky="ns", padx=12)
        self.pause_btn = ttk.Button(toolbar, text="Pause", style="Tool.TButton",
                                    command=self._pause_selected, state="disabled")
        self.pause_btn.grid(row=0, column=2, sticky="w")
        self.resume_btn = ttk.Button(toolbar, text="Resume", style="Tool.TButton",
                                     command=self._resume_selected, state="disabled")
        self.resume_btn.grid(row=0, column=3, sticky="w", padx=(6, 0))
        self.cancel_btn = ttk.Button(toolbar, text="Cancel", style="Tool.TButton",
                                     command=self._cancel_selected, state="disabled")
        self.cancel_btn.grid(row=0, column=4, sticky="w", padx=(6, 0))
        self.retry_btn = ttk.Button(toolbar, text="Retry", style="Tool.TButton",
                                    command=self._retry_selected, state="disabled")
        self.retry_btn.grid(row=0, column=5, sticky="w", padx=(6, 0))

        self.sort_combo = ttk.Combobox(toolbar, values=["Newest first", "Oldest first", "Name A-Z",
                                                         "Largest first", "Status"],
                                       width=12, state="readonly")
        self.sort_combo.set(self.sort_mode)
        self.sort_combo.grid(row=0, column=7, sticky="e", padx=(0, 8))
        self.sort_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_sort())
        self.filter_combo = ttk.Combobox(toolbar, values=list(FILE_GROUPS), width=11,
                                         state="readonly")
        self.filter_combo.set(self.filter_mode if self.filter_mode in FILE_GROUPS else "All types")
        self.filter_combo.grid(row=0, column=8, sticky="e", padx=(0, 8))
        self.filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_filter())

        search_frame = ttk.Frame(toolbar, style="Toolbar.TFrame")
        search_frame.grid(row=0, column=9, sticky="e")
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=18)
        self.search_entry.pack(side="left")
        self.search_var.trace_add("write", lambda *_a: self._on_search())
        ttk.Label(search_frame, text="⌕", style="Tool.TLabel").pack(side="left", padx=(4, 0))
        self.menu_btn = ttk.Button(toolbar, text="☰", style="Tool.TButton", width=3,
                                   command=self._show_menu)
        self.menu_btn.grid(row=0, column=10, sticky="e", padx=(10, 0))

        ttk.Separator(root, orient="horizontal").grid(row=1, column=0, sticky="ew")

        # ---- content --------------------------------------------------------
        content = ttk.Frame(root)
        content.grid(row=2, column=0, sticky="nsew")
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        self.content = content

        tree_frame = ttk.Frame(content)
        tree_frame.grid(row=0, column=0, sticky="nsew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        self.tree_frame = tree_frame

        self.tree = ttk.Treeview(tree_frame, columns=("size", "progress", "speed", "status"),
                                  show="tree headings", selectmode="extended")
        self.tree.heading("#0", text="Name", anchor="w")
        self.tree.heading("size", text="Size", anchor="e")
        self.tree.heading("progress", text="Progress", anchor="w")
        self.tree.heading("speed", text="Speed", anchor="e")
        self.tree.heading("status", text="Status", anchor="w")
        self.tree.column("#0", width=430, minwidth=200, anchor="w", stretch=True)
        self.tree.column("size", width=100, minwidth=70, anchor="e", stretch=False)
        self.tree.column("progress", width=90, minwidth=70, anchor="w", stretch=False)
        self.tree.column("speed", width=100, minwidth=70, anchor="e", stretch=False)
        self.tree.column("status", width=140, minwidth=100, anchor="w", stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_buttons())
        self.tree.bind("<Double-1>", self._open_row)
        self.tree.bind("<Button-3>", self._show_context_menu)
        self.tree.bind("<Delete>", lambda _e: self._cancel_selected())

        self.drop_canvas = tk.Canvas(content, bg=self.palette["bg"], highlightthickness=0, bd=0)
        self.drop_canvas.bind("<Configure>", lambda _e: self._draw_drop_zone())
        self.drop_canvas.bind("<Button-1>", lambda _e: self._add_download())

        # ---- status bar -----------------------------------------------------
        status = ttk.Frame(root, style="Status.TFrame", padding=(16, 6))
        status.grid(row=3, column=0, sticky="ew")
        status.columnconfigure(1, weight=1)
        self.status = status
        self.stats_var = tk.StringVar(value="No downloads")
        ttk.Label(status, textvariable=self.stats_var, style="Status.TLabel").grid(
            row=0, column=0, sticky="w")
        self.message_var = tk.StringVar(value="Ready")
        ttk.Label(status, textvariable=self.message_var, style="StatusMuted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(18, 0))
        self.speed_var = tk.StringVar(value="—")
        ttk.Label(status, textvariable=self.speed_var, style="StatusAccent.TLabel").grid(
            row=0, column=2, sticky="e")
        self.connection_var = tk.StringVar(value="Idle")
        ttk.Label(status, textvariable=self.connection_var, style="StatusMuted.TLabel").grid(
            row=0, column=3, sticky="e", padx=(14, 0))

        root.bind("<Control-v>", lambda _e: self._paste_url())
        root.bind("<Control-f>", lambda _e: self.search_entry.focus_set())
        if HAVE_DND:
            for widget in (self.tree, self.drop_canvas):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:  # noqa: BLE001 - drag & drop is optional
                    pass

    # ------------------------------------------------------------------ theme

    def _apply_theme(self, dark: bool) -> None:
        self.dark = dark
        p = PALETTES["dark" if dark else "light"]
        self.palette = p
        s = self.style
        s.theme_use("clam")
        s.configure(".", background=p["bg"], foreground=p["text"],
                    fieldbackground=p["field"], bordercolor=p["border"],
                    lightcolor=p["surface"], darkcolor=p["surface"],
                    troughcolor=p["surface"], focuscolor=p["accent"], font=self.body_font)
        s.configure("TFrame", background=p["bg"])
        s.configure("Toolbar.TFrame", background=p["toolbar"])
        s.configure("Status.TFrame", background=p["toolbar"])
        s.configure("TLabel", background=p["bg"], foreground=p["text"], font=self.body_font)
        s.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"], font=self.body_font)
        s.configure("Tool.TLabel", background=p["toolbar"], foreground=p["muted"], font=self.body_font)
        s.configure("FieldLabel.TLabel", background=p["bg"], foreground=p["muted"],
                    font=self.field_font)
        s.configure("Status.TLabel", background=p["toolbar"], foreground=p["text"], font=self.body_font)
        s.configure("StatusMuted.TLabel", background=p["toolbar"], foreground=p["muted"],
                    font=self.small_font)
        s.configure("StatusAccent.TLabel", background=p["toolbar"], foreground=p["accent"],
                    font=self.status_font if hasattr(self, "status_font") else self.body_font)

        s.configure("TButton", background=p["surface"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["surface"], darkcolor=p["surface"],
                    focuscolor=p["accent"], font=self.body_font, padding=(12, 7), relief="flat")
        s.map("TButton",
              background=[("disabled", p["bg"]), ("pressed", p["accent_press"]),
                          ("active", p["border"])],
              foreground=[("disabled", p["muted"]), ("pressed", p["white"]),
                          ("active", p["text"])],
              bordercolor=[("focus", p["accent"]), ("disabled", p["border"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["white"],
                    bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
                    focuscolor=p["white"], font=self.button_font, padding=(14, 7), relief="flat")
        s.map("Accent.TButton",
              background=[("disabled", p["border"]), ("pressed", p["accent_press"]),
                          ("active", p["accent_hover"])],
              foreground=[("disabled", p["muted"]), ("pressed", p["white"]),
                          ("active", p["white"])],
              bordercolor=[("disabled", p["border"]), ("active", p["accent_hover"])])
        s.configure("Tool.TButton", background=p["toolbar"], foreground=p["text"],
                    bordercolor=p["border_soft"], lightcolor=p["toolbar"],
                    darkcolor=p["toolbar"], focuscolor=p["accent"], font=self.body_font,
                    padding=(10, 6), relief="flat")
        s.map("Tool.TButton",
              background=[("disabled", p["toolbar"]), ("pressed", p["accent_press"]),
                          ("active", p["surface"])],
              foreground=[("disabled", p["muted"]), ("pressed", p["white"]),
                          ("active", p["text"])],
              bordercolor=[("focus", p["accent"]), ("disabled", p["border_soft"])])
        s.configure("Ghost.TButton", background=p["bg"], foreground=p["muted"],
                    bordercolor=p["border"], lightcolor=p["bg"], darkcolor=p["bg"],
                    focuscolor=p["accent"], font=self.small_font, padding=(8, 5), relief="flat")
        s.map("Ghost.TButton",
              background=[("active", p["border"]), ("pressed", p["bg"]),
                          ("disabled", p["bg"])],
              foreground=[("active", p["text"]), ("pressed", p["text"]),
                          ("disabled", p["muted"])],
              bordercolor=[("active", p["border"]), ("disabled", p["border"])])

        s.configure("TEntry", fieldbackground=p["field"], foreground=p["text"],
                    background=p["field"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"],
                    insertcolor=p["text"], padding=(8, 7), relief="flat")
        s.map("TEntry",
              fieldbackground=[("disabled", p["bg"]), ("readonly", p["bg"])],
              foreground=[("disabled", p["muted"])],
              bordercolor=[("focus", p["accent"]), ("disabled", p["border"])])
        s.configure("TCombobox", fieldbackground=p["field"], foreground=p["text"],
                    background=p["field"], bordercolor=p["border"],
                    lightcolor=p["field"], darkcolor=p["field"], arrowcolor=p["text"],
                    selectbackground=p["field"], selectforeground=p["text"],
                    padding=(6, 5), relief="flat")
        s.map("TCombobox",
              fieldbackground=[("readonly", p["field"]), ("disabled", p["bg"])],
              foreground=[("disabled", p["muted"])],
              bordercolor=[("focus", p["accent"]), ("disabled", p["border"])])
        s.configure("TSpinbox", fieldbackground=p["field"], foreground=p["text"],
                    background=p["field"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"], arrowcolor=p["text"],
                    insertcolor=p["text"], padding=(6, 5), arrowsize=12)
        s.configure("TCheckbutton", background=p["bg"], foreground=p["text"],
                    font=self.body_font, focuscolor=p["bg"],
                    indicatorbackground=p["field"], indicatorforeground=p["text"],
                    upperbordercolor=p["border"], lowerbordercolor=p["border"],
                    indicatormargin=(0, 0, 7, 0))
        s.map("TCheckbutton",
              background=[("active", p["bg"])],
              foreground=[("disabled", p["muted"])],
              indicatorbackground=[("selected", p["accent"]), ("disabled", p["bg"]),
                                   ("active", p["field"])],
              indicatorforeground=[("selected", p["white"]), ("disabled", p["text"])],
              upperbordercolor=[("selected", p["accent"])],
              lowerbordercolor=[("selected", p["accent"])])

        s.configure("Treeview", background=p["bg"], fieldbackground=p["bg"],
                    foreground=p["text"], bordercolor=p["border"], borderwidth=0,
                    rowheight=30, font=self.body_font, relief="flat")
        s.map("Treeview",
              background=[("selected", p["select"])],
              foreground=[("selected", p["select_fg"])])
        s.configure("Treeview.Heading", background=p["toolbar"], foreground=p["muted"],
                    bordercolor=p["border"], relief="flat", font=self.field_font,
                    padding=(10, 8))
        s.map("Treeview.Heading", background=[("active", p["surface"])],
              foreground=[("active", p["text"])])
        s.configure("Vertical.TScrollbar", background=p["border_soft"], troughcolor=p["bg"],
                    bordercolor=p["bg"], arrowcolor=p["muted"],
                    lightcolor=p["border_soft"], darkcolor=p["border_soft"], width=12)
        s.map("Vertical.TScrollbar", background=[("active", p["border"])])
        s.configure("TSeparator", background=p["border"])

        self.root.configure(bg=p["bg"])
        self.drop_canvas.configure(bg=p["bg"])
        self.root.after(40, lambda: set_titlebar_theme(self.root, dark))
        for tag, colour in (("waiting", p["muted"]), ("running", p["accent"]),
                            ("done", p["success"]), ("failed", p["danger"]),
                            ("cancelled", p["warning"]), ("hint", p["muted"])):
            self.tree.tag_configure(tag, foreground=colour)
        self._draw_drop_zone()
        self.rebuild_tree()
        self._update_buttons()

    def _draw_drop_zone(self) -> None:
        canvas = self.drop_canvas
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 10 or height < 10:
            return
        margin = 46
        points = rounded_rect_points(margin, margin, width - margin, height - margin, 14)
        canvas.create_line(points, fill=self.palette["border"], width=1, dash=(6, 6),
                           smooth=True)
        canvas.create_text(width / 2, height / 2 - 14,
                           text="Drag & drop a MediaFire link here",
                           fill=self.palette["text"], font=self.title_font)
        hint = ("Drop a folder link, a file link or a bare folder key"
                if HAVE_DND else
                "Press “Add download” and paste a folder link, file link or folder key")
        canvas.create_text(width / 2, height / 2 + 16, text=hint,
                           fill=self.palette["muted"], font=self.body_font)

    # ------------------------------------------------------------------ settings

    def _restore_settings(self) -> None:
        self.sort_mode = self.settings.get("sort") or "Newest first"
        self.filter_mode = self.settings.get("filter") or "All types"
        if self.sort_mode not in ("Newest first", "Oldest first", "Name A-Z", "Largest first",
                                  "Status"):
            self.sort_mode = "Newest first"
        if self.filter_mode not in FILE_GROUPS:
            self.filter_mode = "All types"
        self.sort_combo.set(self.sort_mode)
        self.filter_combo.set(self.filter_mode)
        self.auto_resume = bool(self.settings.get("auto_resume", True))
        self.auto_resume_var.set(self.auto_resume)
        self.theme_var.set("dark" if self.dark else "light")

    def _save_settings(self) -> None:
        save_settings({
            "destination": self._last_destination(),
            "jobs": 4,
            "retries": 3,
            "recovery_rounds": 3,
            "subfolder": True,
            "overwrite": False,
            "dark": bool(self.dark),
            "sort": self.sort_mode,
            "filter": self.filter_mode,
            "auto_resume": bool(self.auto_resume),
        })

    def _last_destination(self) -> str:
        for job_id in reversed(self.order):
            destination = self.downloads[job_id].destination
            if destination:
                return destination
        return self.settings.get("destination") or default_destination()

    # ------------------------------------------------------------------ model

    def _restore_jobs(self) -> None:
        """Reload the manifests: unfinished downloads come back as rows in the list."""
        for job in rs.list_jobs():
            if not job.job_id:
                continue
            download = Download(job.job_id, job.source_url, job.destination,
                                job.title or job.source_key, job.source_kind, job.source_key)
            download.files = job.files
            download.root = Path(job.root) if job.root else None
            download.status = job.status
            download.options = dict(job.options or {})
            download.error = job.last_error
            try:
                download.created = datetime.fromisoformat(job.created).timestamp()
            except (TypeError, ValueError):
                pass
            self.downloads[download.job_id] = download
            self.order.append(download.job_id)
        self.rebuild_tree()
        self._update_statusbar()

    def _auto_resume(self) -> None:
        """Pick up the downloads that were running when the window was closed."""
        if not getattr(self, "auto_resume", True):
            return
        for job_id in list(self.order):
            download = self.downloads.get(job_id)
            if download is None or download.running:
                continue
            if download.status not in rs.RESUMABLE_STATES:
                continue
            if (download.options or {}).get("cookies_used"):
                download.error = "This download used browser cookies – add them again to resume."
                self._update_job_row(download)
                continue
            self.start_download(download)
            self.root.after(400, self._auto_resume)

    def start_download(self, download: Download, mode: str = "download") -> None:
        """Start (or continue) a download in a background session."""
        if download.running:
            return
        options = dict(download.options or {})
        options.setdefault("subfolder", True)
        options.setdefault("overwrite", False)
        options.setdefault("jobs", 4)
        options.setdefault("retries", 3)
        options.setdefault("recovery_rounds", 3)
        options.setdefault("patterns", [])
        spec = {
            "job_id": download.job_id,
            "url": download.url,
            "dest": download.destination,
            "subfolder": bool(options.get("subfolder", True)),
            "overwrite": bool(options.get("overwrite", False)),
            "jobs": int(options.get("jobs", 4)),
            "retries": int(options.get("retries", 3)),
            "recovery_rounds": int(options.get("recovery_rounds", 3)),
            "patterns": list(options.get("patterns") or []),
            "cookies": options.get("cookies"),
            "timeout": 60.0,
            "mode": mode,
            "options": options,
        }
        download.status = rs.JOB_QUEUED if mode == "verify" else rs.JOB_RUNNING
        download.verifying = False
        download.paused = False
        download.progress_by_index.clear()
        download.network_bytes = 0
        download.done_bytes = 0
        download.speed_meter.reset()
        download.failures = []
        session = DownloadSession(spec, self.events)
        download.session = session
        session.start()
        self._update_job_row(download)
        self._update_buttons()

    # ------------------------------------------------------------------ actions

    def _add_download(self, url: str | None = None) -> None:
        dialog = AddDownloadDialog(self.root, self.settings, self.palette)
        if url:
            dialog.url_var.set(url)
        result = dialog.show()
        if not result:
            return
        source = result["source"]
        options = {
            "subfolder": result["subfolder"],
            "overwrite": result["overwrite"],
            "jobs": result["jobs"],
            "retries": result["retries"],
            "recovery_rounds": result["recovery_rounds"],
            "patterns": result["patterns"],
            "cookies_used": bool(result["cookies"]),
        }
        job = rs.create_job(result["url"], source.kind, source.key, result["dest"], options,
                            title=source.key)
        download = Download(job.job_id, result["url"], result["dest"], source.key,
                            source.kind, source.key)
        download.options = dict(options)
        download.options["cookies"] = result["cookies"]
        download.destination = result["dest"]
        self.downloads[download.job_id] = download
        if download.job_id not in self.order:
            self.order.append(download.job_id)
        self.rebuild_tree()
        self.start_download(download)

    def _selected(self) -> list[Download]:
        found: list[Download] = []
        for iid in self.tree.selection():
            job_id = iid.split(":")[1] if ":" in iid else ""
            download = self.downloads.get(job_id)
            if download is not None and download not in found:
                found.append(download)
        return found

    def _pause_selected(self) -> None:
        for download in self._selected():
            if download.session is not None and download.session.is_alive():
                download.session.set_paused(True)
                download.paused = True
                download.status = rs.JOB_PAUSED
                self._update_job_row(download)
        self.message_var.set("Paused. Progress is saved.")

    def _resume_selected(self) -> None:
        for download in self._selected():
            if download.session is not None and download.session.is_alive():
                download.session.set_paused(False)
                download.paused = False
                download.status = rs.JOB_RUNNING
                self._update_job_row(download)
            else:
                self.start_download(download)
        self.message_var.set("Resuming.")

    def _cancel_selected(self) -> None:
        for download in self._selected():
            if download.session is not None and download.session.is_alive():
                download.session.request_stop("cancelled")
                download.status = rs.JOB_CANCELLED
                self._update_job_row(download)
        self.message_var.set("Cancelling – finished files are kept.")

    def _retry_selected(self) -> None:
        for download in self._selected():
            self.start_download(download)
        self.message_var.set("Retrying the files that are missing or unverified.")

    def _verify_selected(self) -> None:
        for download in self._selected():
            self.start_download(download, mode="verify")
        self.message_var.set("Verifying the files on disk…")

    def _open_selected(self) -> None:
        for download in self._selected():
            path = download.root or Path(download.destination)
            try:
                if path.is_dir():
                    open_path(path)
            except OSError as e:
                messagebox.showerror(APP, f"Cannot open {path}:\n{e}", parent=self.root)

    def _show_errors(self) -> None:
        """A window with the errors of the selected downloads."""
        pairs: list[tuple[str, str]] = []
        for download in self._selected():
            for name, error in download.failures:
                pairs.append((f"{download.title}/{name}", error))
        if not pairs:
            messagebox.showinfo(APP, "No errors in the selected downloads.", parent=self.root)
            return
        p = self.palette
        win = tk.Toplevel(self.root)
        win.title("Download errors")
        win.configure(bg=p["bg"])
        win.minsize(620, 320)
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", win.destroy)
        frame = ttk.Frame(win, padding=16)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text = tk.Text(frame, wrap="word", bg=p["field"], fg=p["text"],
                       insertbackground=p["text"], relief="flat", bd=0,
                       highlightthickness=1, highlightbackground=p["border"],
                       highlightcolor=p["accent"], font=self.body_font, padx=12, pady=10)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        for name, error in pairs:
            text.insert("end", f"{name}\n    {error}\n\n")
        text.configure(state="disabled")
        ttk.Button(frame, text="Close", command=win.destroy).grid(row=1, column=0,
                                                                  columnspan=2, sticky="e",
                                                                  pady=(12, 0))
        win.update_idletasks()
        x = max(0, self.root.winfo_rootx() + (self.root.winfo_width() - 820) // 2)
        y = max(0, self.root.winfo_rooty() + 80)
        win.geometry(f"820x460+{x}+{y}")
        win.grab_set()
        win.focus_set()

    def _open_row(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        parts = row.split(":")
        download = self.downloads.get(parts[1] if len(parts) > 1 else "")
        if download is None:
            return
        if parts[0] == "f" and len(parts) > 2:
            index = int(parts[2])
            record = download.files[index - 1] if index <= len(download.files) else None
            if record is not None and download.root is not None:
                path = rs.record_path(download.root, record)
                if path.exists():
                    try:
                        open_path(path)
                    except OSError as e:
                        messagebox.showerror(APP, f"Cannot open {path}:\n{e}", parent=self.root)
                    return
        self._open_selected()

    def _remove_selected(self) -> None:
        for download in self._selected():
            if download.running:
                continue
            self.downloads.pop(download.job_id, None)
            if download.job_id in self.order:
                self.order.remove(download.job_id)
            try:
                rs.state_path(download.job_id).unlink(missing_ok=True)
            except OSError:
                pass
        self.rebuild_tree()
        self._update_statusbar()

    def _clear_finished(self) -> None:
        for job_id in list(self.order):
            download = self.downloads.get(job_id)
            if download is None or download.running:
                continue
            if download.status in (rs.JOB_COMPLETE, rs.JOB_CANCELLED):
                self.downloads.pop(job_id, None)
                self.order.remove(job_id)
                try:
                    rs.state_path(job_id).unlink(missing_ok=True)
                except OSError:
                    pass
        self.rebuild_tree()
        self._update_statusbar()

    def _paste_url(self) -> None:
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if not text:
            return
        if "mediafire.com" not in text.lower() and not re.fullmatch(r"[A-Za-z0-9_-]+", text):
            self.message_var.set("The clipboard does not hold a MediaFire link.")
            return
        self._add_download(text)

    def _on_drop(self, event) -> None:
        data = getattr(event, "data", "") or ""
        text = self.tk.splitlist(data)[0] if data.startswith("{") else data.strip()
        if not text:
            return
        if "mediafire.com" in text.lower() or re.fullmatch(r"[A-Za-z0-9_-]+", text):
            self._add_download(text)
        else:
            self.message_var.set("Only MediaFire links can be downloaded here.")

    def _show_menu(self) -> None:
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Open downloads folder", command=self._open_selected)
        menu.add_command(label="Show errors", command=self._show_errors)
        menu.add_command(label="Verify selected", command=self._verify_selected)
        menu.add_command(label="Retry selected", command=self._retry_selected)
        menu.add_separator()
        menu.add_command(label="Clear finished downloads", command=self._clear_finished)
        menu.add_separator()
        menu.add_checkbutton(label="Resume unfinished downloads on startup",
                             variable=self.auto_resume_var,
                             command=self._toggle_auto_resume)
        menu.add_separator()
        menu.add_radiobutton(label="Dark theme", variable=self.theme_var, value="dark",
                             command=lambda: self._set_theme(True))
        menu.add_radiobutton(label="Light theme", variable=self.theme_var, value="light",
                             command=lambda: self._set_theme(False))
        menu.add_separator()
        menu.add_command(label="About", command=self._about)
        try:
            menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def _show_context_menu(self, event) -> None:
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Pause", command=self._pause_selected)
        menu.add_command(label="Resume", command=self._resume_selected)
        menu.add_command(label="Cancel", command=self._cancel_selected)
        menu.add_command(label="Retry", command=self._retry_selected)
        menu.add_separator()
        menu.add_command(label="Verify", command=self._verify_selected)
        menu.add_command(label="Show errors", command=self._show_errors)
        menu.add_command(label="Open folder", command=self._open_selected)
        menu.add_separator()
        menu.add_command(label="Remove from list", command=self._remove_selected)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _toggle_auto_resume(self) -> None:
        self.auto_resume = bool(self.auto_resume_var.get())
        self._save_settings()

    def _set_theme(self, dark: bool) -> None:
        self._apply_theme(dark)
        self._save_settings()

    def _about(self) -> None:
        messagebox.showinfo(
            APP,
            "ArkLoad\n\n"
            "Downloads folders and files from MediaFire, verifies every file against "
            "MediaFire's own SHA-256 and resumes interrupted downloads where they stopped.\n\n"
            "State is stored in %s" % rs.STATE_DIR,
            parent=self.root)

    # ------------------------------------------------------------------ list

    def _on_sort(self) -> None:
        self.sort_mode = self.sort_combo.get()
        self._save_settings()
        self.rebuild_tree()

    def _on_filter(self) -> None:
        self.filter_mode = self.filter_combo.get()
        self._save_settings()
        self.rebuild_tree()

    def _on_search(self) -> None:
        self.search_text = self.search_var.get().strip().lower()
        self.rebuild_tree()

    def _sorted_ids(self) -> list[str]:
        downloads = [self.downloads[job_id] for job_id in self.order if job_id in self.downloads]
        if self.sort_mode == "Oldest first":
            downloads.sort(key=lambda d: d.created)
        elif self.sort_mode == "Name A-Z":
            downloads.sort(key=lambda d: d.title.lower())
        elif self.sort_mode == "Largest first":
            downloads.sort(key=lambda d: -d.bytes_total)
        elif self.sort_mode == "Status":
            downloads.sort(key=lambda d: (d.status_label(), d.title.lower()))
        else:
            downloads.sort(key=lambda d: -d.created)
        return [download.job_id for download in downloads]

    def _file_matches(self, record: rs.FileRecord) -> bool:
        group = FILE_GROUPS.get(self.filter_mode)
        if group and file_group(record.path) != self.filter_mode:
            return False
        if self.search_text and self.search_text not in record.path.lower():
            return False
        return True

    def rebuild_tree(self) -> None:
        """Rebuild the list from the model (used when the filter, sort or search changes)."""
        children = self.tree.get_children("")
        if children:
            self.tree.delete(*children)
        any_visible = False
        for job_id in self._sorted_ids():
            download = self.downloads[job_id]
            children = [record for record in download.files if self._file_matches(record)]
            title_hit = self.search_text in download.title.lower()
            if download.files:
                if not children and not title_hit:
                    continue
            elif not title_hit and self.search_text:
                continue
            any_visible = True
            parent = f"j:{download.job_id}"
            self.tree.insert("", "end", iid=parent, text=download.title,
                             values=self._job_values(download),
                             tags=(download.status_tag(),))
            download.iid = parent
            download.file_iids = []
            for index, record in enumerate(download.files, 1):
                child = f"f:{download.job_id}:{index}"
                if record not in children:
                    continue
                download.file_iids.append(child)
                self.tree.insert(parent, "end", iid=child, text=record.path,
                                 values=self._file_values(record),
                                 tags=(self._file_tag(record),))
            self.tree.item(parent, open=True)
        if not any_visible:
            self.tree.insert("", "end", iid="hint",
                             values=("", "", "", "Nothing here yet"), tags=("hint",))
        if self.downloads:
            self.tree_frame.grid()
            self.drop_canvas.grid_remove()
        else:
            self.drop_canvas.grid(row=0, column=0, sticky="nsew")
            self.tree_frame.grid_remove()
        self._update_statusbar()

    def _job_values(self, download: Download) -> tuple:
        return (fmt_bytes(download.bytes_total),
                f"{download.percent()}%" if download.files else "",
                fmt_speed(download.speed) if download.running else "",
                download.status_label())

    def _file_values(self, record: rs.FileRecord) -> tuple:
        return (fmt_bytes(record.size),
                "100%" if record.status == rs.FILE_COMPLETE else "",
                "",
                self._file_label(record))

    def _file_label(self, record: rs.FileRecord) -> str:
        return {
            rs.FILE_COMPLETE: "Verified",
            rs.FILE_DOWNLOADING: "Downloading",
            rs.FILE_INCOMPLETE: "Incomplete",
            rs.FILE_MISSING: "Missing",
            rs.FILE_VERIFY_FAILED: "Verification failed",
            rs.FILE_FAILED: "Failed",
            rs.FILE_PENDING: "Queued",
        }.get(record.status, "Queued")

    def _file_tag(self, record: rs.FileRecord) -> str:
        if record.status == rs.FILE_COMPLETE:
            return "done"
        if record.status in (rs.FILE_FAILED, rs.FILE_VERIFY_FAILED):
            return "failed"
        if record.status in (rs.FILE_INCOMPLETE, rs.FILE_DOWNLOADING):
            return "running" if record.status == rs.FILE_DOWNLOADING else "cancelled"
        return "waiting"

    def _update_job_row(self, download: Download) -> None:
        if not download.iid or not self.tree.exists(download.iid):
            return
        self.tree.item(download.iid, values=self._job_values(download),
                       tags=(download.status_tag(),))

    def _update_file_row(self, download: Download, index: int, record: rs.FileRecord) -> None:
        iid = f"f:{download.job_id}:{index}"
        if not self.tree.exists(iid):
            return
        self.tree.item(iid, values=self._file_values(record), tags=(self._file_tag(record),))

    def _update_buttons(self) -> None:
        selected = self._selected()
        running = any(download.session is not None and download.session.is_alive()
                      for download in selected)
        paused = any(download.paused for download in selected)
        self.pause_btn.configure(state="normal" if running and not paused else "disabled")
        self.resume_btn.configure(state="normal" if selected else "disabled")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        self.retry_btn.configure(state="normal" if selected and not running else "disabled")

    # ------------------------------------------------------------------ events

    def _poll(self) -> None:
        if not self.alive:
            return
        try:
            while True:
                job_id, kind, *payload = self.events.get_nowait()
                self._handle(job_id, kind, *payload)
        except queue.Empty:
            pass
        except Exception:  # noqa: BLE001 - never let one bad event kill the loop
            self._report_tk_error(*sys.exc_info())
        try:
            self.root.after(120, self._poll)
        except tk.TclError:
            pass

    def _handle(self, job_id: str, kind: str, *payload) -> None:
        download = self.downloads.get(job_id)
        if download is None:
            return
        if kind == "planning":
            download.status = rs.JOB_RUNNING
            download.verifying = False
            self.message_var.set(f"Reading {download.title} from MediaFire…")
            self._update_job_row(download)
        elif kind == "plan":
            plan, root, source_kind = payload
            download.plan = plan
            download.root = root
            if source_kind == "folder" and plan.root_name:
                download.title = plan.root_name
            download.bytes_total = plan.total_bytes
            self.rebuild_tree()
        elif kind == "job":
            job = payload[0]
            download.files = job.files
            download.bytes_total = job.total_bytes
            self.rebuild_tree()
        elif kind == "scan_start":
            download.verifying = True
            download.status = rs.JOB_RUNNING
            download.done_bytes = 0
            download.progress_by_index.clear()
            self.message_var.set(f"Verifying the files already in {download.title}…")
            self._update_job_row(download)
        elif kind == "file_state":
            index, record = payload
            self._set_file_bytes(download, index, record.bytes or 0, network=False)
            self._update_file_row(download, index, record)
            self._update_job_row(download)
        elif kind == "scan_done":
            stats = payload[0]
            download.verifying = False
            download.files = list(download.files)
            self.message_var.set(
                f"{stats.get('verified', 0)} of {stats.get('total', 0)} files verified – "
                "only the rest is downloaded again.")
            self.rebuild_tree()
        elif kind == "file_start":
            index = payload[0]
            record = download.files[index - 1] if index <= len(download.files) else None
            if record is not None:
                record.status = rs.FILE_DOWNLOADING
                self._update_file_row(download, index, record)
        elif kind == "progress":
            index, done, total = payload
            self._set_file_bytes(download, index, done, network=True)
            self._update_job_row(download)
        elif kind == "file_done":
            index, result, record = payload
            if record is not None:
                self._set_file_bytes(download, index, record.bytes or 0, network=False)
                self._update_file_row(download, index, record)
            self._update_job_row(download)
        elif kind == "recovery":
            round_no, remaining = payload
            download.status = rs.JOB_RUNNING
            self.message_var.set(
                f"Recovery pass {round_no}: retrying {remaining} missing or unverified file(s)…")
            self._update_job_row(download)
        elif kind == "done":
            stats, elapsed, cancelled, root, failures, total_files, processed, complete = payload
            self._finish(download, stats, elapsed, cancelled, root, failures, total_files,
                         processed, complete)
        elif kind == "cancelled":
            download.status = rs.JOB_CANCELLED
            self._update_job_row(download)
        elif kind == "fatal":
            message = str(payload[0]) if payload else "Unknown error"
            download.status = rs.JOB_FAILED
            download.error = message
            self.online = False
            self._update_job_row(download)
            self.message_var.set(f"Could not read {download.title}: {message}")
            self._update_statusbar()

    def _set_file_bytes(self, download: Download, index: int, done: int, network: bool) -> None:
        previous = download.progress_by_index.get(index, 0)
        delta = done - previous
        download.done_bytes = max(0, download.done_bytes + delta)
        if network:
            download.network_bytes = max(0, download.network_bytes + delta)
        download.progress_by_index[index] = done

    def _finish(self, download: Download, stats: dict, elapsed: float, cancelled: bool,
                root, failures: list, total_files: int, processed: int, complete: bool) -> None:
        download.root = root or download.root
        download.failures = failures
        download.verifying = False
        download.session = None
        if cancelled:
            download.status = rs.JOB_CANCELLED
            self.message_var.set(f"Cancelled – {processed} of {total_files} files verified.")
        elif complete:
            download.status = rs.JOB_COMPLETE
            self.message_var.set(f"All {total_files} files downloaded and verified.")
        else:
            download.status = rs.JOB_INCOMPLETE
            self.message_var.set(
                f"{processed} of {total_files} files verified, {len(failures)} still missing "
                "or unverified – press Retry to continue, ☰ → Show errors for details.")
        self.rebuild_tree()
        self._update_statusbar()
        self._update_buttons()

    def _speed_tick(self) -> None:
        if not self.alive:
            return
        total_speed = 0.0
        for download in self.downloads.values():
            if download.session is not None and download.session.is_alive():
                speed = download.speed_meter.update(download.network_bytes)
                download.speed = speed
                total_speed += speed
                self._update_job_row(download)
        self.speed_var.set(fmt_speed(total_speed) if total_speed else "—")
        if any(download.session is not None and download.session.is_alive()
               for download in self.downloads.values()):
            self.connection_var.set("Connected")
        else:
            self.connection_var.set("Idle")
        try:
            self.root.after(500, self._speed_tick)
        except tk.TclError:
            pass

    def _update_statusbar(self) -> None:
        downloads = list(self.downloads.values())
        files = sum(download.total for download in downloads)
        verified = sum(download.verified for download in downloads)
        if not downloads:
            self.stats_var.set("No downloads")
            return
        noun = "download" if len(downloads) == 1 else "downloads"
        self.stats_var.set(f"{len(downloads)} {noun} · {files} files · {verified} verified")

    # ------------------------------------------------------------------ closing

    def _close(self) -> None:
        running = [download for download in self.downloads.values() if download.running]
        if running:
            if not messagebox.askyesno(
                    APP, f"{len(running)} download(s) are still running.\n\n"
                         "Stop them and close? Finished files are kept and the next start "
                         "continues where they stopped.", parent=self.root):
                return
            for download in running:
                download.session.request_stop("interrupted")
        self._save_settings()
        self.alive = False
        self.root.destroy()

    def _report_tk_error(self, exc, val, tb) -> None:
        detail = "".join(traceback.format_exception(exc, val, tb))
        try:
            LOG.write_text(detail, encoding="utf-8")
        except OSError:
            pass
        try:
            messagebox.showerror(APP, f"Something went wrong:\n\n{exc}\n\nDetails: {LOG}",
                                 parent=self.root)
        except Exception:  # noqa: BLE001 - the window may already be gone
            pass


def main() -> int:
    if HAVE_DND:
        root = TkinterDnD.Tk()  # type: ignore[attr-defined]
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
