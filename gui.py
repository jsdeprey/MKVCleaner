from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core.config import CleanerConfig, load_config, save_config
from core.cleaner import can_use_mkvpropedit
from core.logger import RunLogger
from core.paths import application_directory, locate_mkvmerge, locate_mkvpropedit
from core.process import run_tool
from core.scanner import scan_path
from core.workers import process_files
from version import __version__


COLORS = {
    "dark": {
        "bg": "#171a1f", "panel": "#20242b", "field": "#292e37",
        "text": "#e7eaf0", "muted": "#9da6b3", "border": "#353b46",
        "accent": "#27b3d6", "select": "#164e63",
    },
    "light": {
        "bg": "#f3f5f7", "panel": "#ffffff", "field": "#ffffff",
        "text": "#1d2430", "muted": "#667085", "border": "#cfd5dd",
        "accent": "#1689a8", "select": "#ccecf4",
    },
}

STATUS_COLORS = {
    "Waiting": "#9da6b3", "Inspecting": "#55c7e8", "Will clean": "#55c7e8",
    "Will remux": "#e6b450",
    "Cleaning": "#4da3ff", "Creating backup": "#55c7e8",
    "Editing metadata": "#4da3ff", "Remuxing": "#4da3ff",
    "Replacing original": "#55c7e8", "Verifying": "#c58af9",
    "Done": "#50c878", "Skipped": "#e6b450", "Failed": "#ff6b6b",
    "Cancelled": "#e6b450", "Dry run complete": "#55c7e8",
    "Waiting (paused)": "#e6b450",
}

REPOSITORY_URL = "https://github.com/jsdeprey/MKVCleaner"
TAG_CLEANUP_LABELS = {
    "all": "Remove all tags — fast",
    "global_only": "Remove global tags only — fast",
    "track_only": "Remove track tags only — may require remuxing",
    "none": "Keep all tags",
}
TAG_CLEANUP_VALUES = {label: value for value, label in TAG_CLEANUP_LABELS.items()}


def _format_seconds(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


class AboutDialog(tk.Toplevel):
    def __init__(self, parent: "CleanerGUI") -> None:
        super().__init__(parent)
        self.title("About MKVCleaner")
        self.geometry("520x380")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=24)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="MKVCleaner", style="AboutTitle.TLabel",
        ).pack(pady=(4, 2))
        ttk.Label(frame, text=f"Version {__version__}").pack()
        ttk.Label(
            frame,
            text="Batch queue to clean selected MKV metadata safely with MKVToolNix",
            justify="center", wraplength=430,
        ).pack(pady=(18, 20))

        ttk.Button(
            frame, text="GitHub Repository",
            command=lambda: webbrowser.open(REPOSITORY_URL),
        ).pack(fill="x", pady=3)
        ttk.Button(
            frame, text="View Releases",
            command=lambda: webbrowser.open(f"{REPOSITORY_URL}/releases"),
        ).pack(fill="x", pady=3)
        ttk.Button(
            frame, text="Report an Issue",
            command=lambda: webbrowser.open(f"{REPOSITORY_URL}/issues/new"),
        ).pack(fill="x", pady=3)
        ttk.Button(
            frame, text="MKVToolNix Website",
            command=lambda: webbrowser.open("https://mkvtoolnix.download/"),
        ).pack(fill="x", pady=3)
        ttk.Label(frame, text="License: MIT", style="Muted.TLabel").pack(
            pady=(15, 4),
        )
        ttk.Button(frame, text="Close", command=self.destroy).pack(pady=(8, 0))


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: "CleanerGUI", config: CleanerConfig) -> None:
        super().__init__(parent)
        self.parent = parent
        self.config = config
        self.vars: dict[str, tk.Variable] = {}
        self.title("MKVCleaner Settings")
        self.geometry("720x590")
        self.minsize(650, 520)
        self.transient(parent)
        self.grab_set()
        self._build()

    def _string(self, name: str, value: object) -> tk.StringVar:
        var = tk.StringVar(value="" if value is None else str(value))
        self.vars[name] = var
        return var

    def _boolean(self, name: str, value: bool) -> tk.BooleanVar:
        var = tk.BooleanVar(value=value)
        self.vars[name] = var
        return var

    def _tab(self, notebook: ttk.Notebook, title: str) -> ttk.Frame:
        frame = ttk.Frame(notebook, padding=14)
        frame.columnconfigure(1, weight=1)
        notebook.add(frame, text=title)
        return frame

    def _row(self, frame: ttk.Frame, row: int, label: str, name: str,
             value: object, choices: tuple[str, ...] | None = None,
             browse: str | None = None):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=6, pady=7)
        var = self._string(name, value)
        if choices:
            widget = ttk.Combobox(frame, textvariable=var, values=choices, state="readonly")
        else:
            widget = ttk.Entry(frame, textvariable=var)
        widget.grid(row=row, column=1, sticky="ew", padx=6, pady=7)
        if browse:
            ttk.Button(
                frame, text="Browse...",
                command=lambda: self._browse(var, browse),
            ).grid(row=row, column=2, padx=6, pady=7)
        return widget

    def _check(self, frame: ttk.Frame, row: int, label: str,
               name: str, value: bool) -> None:
        ttk.Checkbutton(
            frame, text=label, variable=self._boolean(name, value),
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=6, pady=7)

    def _build(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        c = self.config

        processing = self._tab(notebook, "Folders & processing")
        self._row(processing, 0, "Default input folder", "default_input_directory",
                  c.default_input_directory, browse="folder")
        self._row(processing, 1, "Inspection mode", "inspection_mode",
                  c.inspection_mode, ("stream", "full"))
        self._row(processing, 2, "Worker count", "threads", c.threads,
                  tuple(str(value) for value in range(1, 9)))
        self._check(processing, 3, "Scan subfolders recursively", "recursive", c.recursive)
        ttk.Label(
            processing,
            text="Stream mode queues each file after inspection. Full mode inspects all files first.",
            wraplength=540,
        ).grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 6))

        cleanup = self._tab(notebook, "Cleanup")
        cleanup_items = (
            ("Remove segment title", "remove_title", c.remove_title),
            ("Remove track names", "remove_track_names", c.remove_track_names),
            ("Remove chapters", "remove_chapters", c.remove_chapters),
            ("Remove attachments", "remove_attachments", c.remove_attachments),
        )
        for row, item in enumerate(cleanup_items):
            self._check(cleanup, row, *item)
        ttk.Separator(cleanup).grid(
            row=4, column=0, columnspan=3, sticky="ew", padx=6, pady=(12, 8),
        )
        tag_combo = self._row(
            cleanup, 5, "Tag cleanup", "tag_cleanup",
            TAG_CLEANUP_LABELS[c.tag_cleanup],
            tuple(TAG_CLEANUP_LABELS.values()),
        )
        self.tag_warning = ttk.Label(
            cleanup, text="", wraplength=550, style="Warning.TLabel",
        )
        self.tag_warning.grid(
            row=6, column=0, columnspan=3, sticky="w", padx=6, pady=(6, 0),
        )
        tag_combo.bind("<<ComboboxSelected>>", lambda _event: self._update_tag_warning())
        self._update_tag_warning()

        safety = self._tab(notebook, "Safety")
        self._check(safety, 0, "Keep a .bak copy of the original", "keep_backup", c.keep_backup)
        self._check(safety, 1, "Verify the cleaned file", "verify_output", c.verify_output)
        self._check(safety, 2, "Preserve original timestamps", "preserve_timestamps", c.preserve_timestamps)
        self._check(safety, 3, "Dry run (inspect without changing files)", "dry_run", c.dry_run)

        display = self._tab(notebook, "Display & logging")
        self._row(display, 0, "Log folder", "log_directory", c.log_directory, browse="folder")
        self._check(display, 1, "Create JSON logs", "json_logging", c.json_logging)
        self._check(display, 2, "Show skipped-file details", "show_skipped", c.show_skipped)
        self._row(display, 3, "Color mode", "theme", c.theme, ("dark", "light"))

        tools = self._tab(notebook, "Tools")
        self._row(tools, 0, "MKVToolNix folder", "mkvmerge_path",
                  c.mkvmerge_path or "", browse="folder")
        ttk.Label(
            tools,
            text=(
                "MKVCleaner uses mkvmerge.exe and mkvpropedit.exe from this folder. "
                "Both programs are included with MKVToolNix. Leave blank for automatic detection."
            ),
            wraplength=540,
        ).grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 6))
        tool_buttons = ttk.Frame(tools)
        tool_buttons.grid(row=2, column=0, columnspan=3, sticky="w", padx=6, pady=8)
        ttk.Button(tool_buttons, text="Test Detection", command=self._test_tools).pack(
            side="left",
        )
        ttk.Button(
            tool_buttons, text="Download MKVToolNix…",
            command=lambda: webbrowser.open("https://mkvtoolnix.download/downloads.html"),
        ).pack(side="left", padx=(8, 0))

        buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Open JSON (Advanced)", command=self._open_json).pack(side="left")
        ttk.Button(buttons, text="Restore Defaults", command=self._restore_defaults).pack(
            side="left", padx=8,
        )
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="Save", style="Accent.TButton", command=self._save).pack(side="right")

    def _browse(self, var: tk.StringVar, kind: str) -> None:
        if kind == "file":
            selected = filedialog.askopenfilename(
                parent=self, title="Choose mkvmerge",
                filetypes=(("mkvmerge", "mkvmerge.exe"), ("All files", "*.*")),
            )
        else:
            selected = filedialog.askdirectory(parent=self, title="Choose folder")
        if selected:
            var.set(selected)

    def _update_tag_warning(self) -> None:
        selected = str(self.vars["tag_cleanup"].get())
        if TAG_CLEANUP_VALUES.get(selected) == "track_only":
            self.tag_warning.configure(
                text=(
                    "⚠ Files containing both global and track tags must be completely "
                    "remuxed to preserve the global tags. This can take much longer. "
                    "Video, audio, and subtitles are not re-encoded."
                )
            )
        else:
            self.tag_warning.configure(
                text=(
                    "Most selected cleanup operations use fast in-place editing. "
                    "Video, audio, and subtitles are not re-encoded."
                )
            )

    def _test_tools(self) -> None:
        try:
            mkvmerge = locate_mkvmerge(str(self.vars["mkvmerge_path"].get()).strip() or None)
            mkvpropedit = locate_mkvpropedit(mkvmerge)
            merge_version = (run_tool([str(mkvmerge), "--version"]).stdout.splitlines() or ["Found"])[0]
            edit_version = (run_tool([str(mkvpropedit), "--version"]).stdout.splitlines() or ["Found"])[0]
            messagebox.showinfo(
                "MKVToolNix Found",
                f"mkvmerge:\n{mkvmerge}\n{merge_version}\n\n"
                f"mkvpropedit:\n{mkvpropedit}\n{edit_version}\n\nStatus: Both programs found",
                parent=self,
            )
        except Exception as exc:
            messagebox.showerror("MKVToolNix Not Found", str(exc), parent=self)

    def _open_json(self) -> None:
        try:
            os.startfile(self.parent.config_path)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Cannot open configuration", str(exc), parent=self)

    def _restore_defaults(self) -> None:
        if messagebox.askyesno(
            "Restore Defaults", "Replace every setting with its default value?",
            parent=self,
        ):
            self.destroy()
            SettingsDialog(self.parent, CleanerConfig())

    def _save(self) -> None:
        try:
            get = lambda name: self.vars[name].get()
            updated = replace(
                self.config,
                default_input_directory=str(get("default_input_directory")).strip(),
                inspection_mode=str(get("inspection_mode")),
                threads=int(get("threads")),
                recursive=bool(get("recursive")),
                remove_title=bool(get("remove_title")),
                remove_track_names=bool(get("remove_track_names")),
                tag_cleanup=TAG_CLEANUP_VALUES[str(get("tag_cleanup"))],
                remove_chapters=bool(get("remove_chapters")),
                remove_attachments=bool(get("remove_attachments")),
                keep_backup=bool(get("keep_backup")),
                verify_output=bool(get("verify_output")),
                preserve_timestamps=bool(get("preserve_timestamps")),
                dry_run=bool(get("dry_run")),
                log_directory=str(get("log_directory")).strip(),
                json_logging=bool(get("json_logging")),
                show_skipped=bool(get("show_skipped")),
                theme=str(get("theme")),
                mkvmerge_path=str(get("mkvmerge_path")).strip() or None,
            )
            save_config(updated, self.parent.config_path)
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid Setting", str(exc), parent=self)
            return
        self.parent.config = updated
        if not self.parent.input_var.get().strip():
            self.parent.input_var.set(updated.default_input_directory)
        self.parent.apply_theme(updated.theme)
        messagebox.showinfo(
            "Settings Saved",
            "Settings were saved to config.json.\nThey apply the next time Start is pressed.",
            parent=self,
        )
        self.destroy()


class CleanerGUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.config_path = application_directory() / "config.json"
        try:
            self.config = load_config(self.config_path)
        except ValueError as exc:
            messagebox.showerror("Invalid config.json", str(exc))
            self.config = CleanerConfig()

        self.title(f"MKVCleaner {__version__}")
        self.geometry("1120x780")
        self.minsize(900, 650)
        self.events: queue.Queue[tuple] = queue.Queue()
        self.run_event = threading.Event()
        self.stop_event = threading.Event()
        self.running = False
        self.paused = False
        self.started_at = 0.0
        self.total = 0
        self.inspected = 0
        self.completed = 0
        self.counts = {"Cleaned": 0, "Skipped": 0, "Failed": 0, "Dry-run": 0, "Cancelled": 0}
        self.workers: dict[int, tuple[str, str]] = {}
        self.row_ids: dict[Path, str] = {}
        self.input_var = tk.StringVar(value=self.config.default_input_directory)
        self.activity_file = tk.StringVar(value="Ready")
        self.activity_action = tk.StringVar(value="Choose an input folder and press Start.")
        self.progress_text = tk.StringVar(value="0.0%")
        self.counter_text = tk.StringVar(value="0/0 files")
        self.elapsed_text = tk.StringVar(value="Elapsed 0:00:00")
        self.summary_vars = {
            key: tk.StringVar(value="0")
            for key in ("Found", "Inspected", "Cleaned", "Skipped", "Failed")
        }
        self._build()
        self.apply_theme(self.config.theme)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._drain_events)
        self.after(500, self._tick)

    def _section(self, parent, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text=title, padding=9)

    def _build(self) -> None:
        menu = tk.Menu(self)
        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(label="About MKVCleaner", command=lambda: AboutDialog(self))
        menu.add_cascade(label="Help", menu=help_menu)
        self.configure(menu=menu)

        toolbar = ttk.Frame(self, padding=(12, 10))
        toolbar.pack(fill="x")
        ttk.Label(toolbar, text="Input folder").pack(side="left")
        ttk.Entry(toolbar, textvariable=self.input_var).pack(
            side="left", fill="x", expand=True, padx=8,
        )
        ttk.Button(toolbar, text="Browse...", command=self._browse).pack(side="left")
        self.settings_button = ttk.Button(toolbar, text="Settings", command=self._settings)
        self.settings_button.pack(side="left", padx=(8, 0))
        self.start_button = ttk.Button(
            toolbar, text="Start", style="Accent.TButton", command=self._start,
        )
        self.start_button.pack(side="left", padx=(8, 0))

        files_frame = self._section(self, "Found Files")
        files_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        columns = ("number", "file", "status", "details")
        self.file_tree = ttk.Treeview(files_frame, columns=columns, show="headings", height=12)
        self.file_tree.heading("number", text="#")
        self.file_tree.heading("file", text="File")
        self.file_tree.heading("status", text="Status")
        self.file_tree.heading("details", text="Changes / Result")
        self.file_tree.column("number", width=55, anchor="e", stretch=False)
        self.file_tree.column("file", width=360)
        self.file_tree.column("status", width=135, stretch=False)
        self.file_tree.column("details", width=430)
        scroll = ttk.Scrollbar(files_frame, orient="vertical", command=self.file_tree.yview)
        self.file_tree.configure(yscrollcommand=scroll.set)
        self.file_tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        activity = self._section(self, "Current Activity")
        activity.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(activity, textvariable=self.activity_file, style="Activity.TLabel").pack(
            anchor="w",
        )
        ttk.Label(activity, textvariable=self.activity_action, style="Muted.TLabel").pack(
            anchor="w", pady=(2, 7),
        )
        progress_holder = ttk.Frame(activity)
        progress_holder.pack(fill="x")
        self.progress = ttk.Progressbar(
            progress_holder, maximum=100, style="Main.Horizontal.TProgressbar",
        )
        self.progress.pack(fill="x")
        ttk.Label(
            progress_holder, textvariable=self.progress_text,
            style="ProgressText.TLabel",
        ).place(relx=.5, rely=.5, anchor="center")
        info = ttk.Frame(activity)
        info.pack(fill="x", pady=(6, 0))
        ttk.Label(info, textvariable=self.counter_text).pack(side="left")
        ttk.Label(info, textvariable=self.elapsed_text).pack(side="right")
        controls = ttk.Frame(activity)
        controls.pack(fill="x", pady=(8, 0))
        self.pause_button = ttk.Button(controls, text="Pause", command=self._toggle_pause, state="disabled")
        self.pause_button.pack(side="left")
        self.cancel_button = ttk.Button(controls, text="Cancel", command=self._cancel, state="disabled")
        self.cancel_button.pack(side="left", padx=8)

        lower = ttk.Frame(self)
        lower.pack(fill="both", padx=12, pady=(0, 12))
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)

        worker_frame = self._section(lower, "Worker Status")
        worker_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        self.worker_tree = ttk.Treeview(
            worker_frame, columns=("worker", "action", "file"), show="headings", height=5,
        )
        self.worker_tree.heading("worker", text="Worker")
        self.worker_tree.heading("action", text="Current action")
        self.worker_tree.heading("file", text="Current file")
        self.worker_tree.column("worker", width=65, stretch=False, anchor="center")
        self.worker_tree.column("action", width=150, stretch=False)
        self.worker_tree.column("file", width=340)
        self.worker_tree.pack(fill="both", expand=True)

        summary = self._section(lower, "Run Summary")
        summary.grid(row=0, column=1, sticky="nsew", padx=(4, 0))
        for row, key in enumerate(("Found", "Inspected", "Cleaned", "Skipped", "Failed")):
            ttk.Label(summary, text=key).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            ttk.Label(summary, textvariable=self.summary_vars[key], style="Summary.TLabel").grid(
                row=row, column=1, sticky="e", padx=8, pady=3,
            )
        summary.columnconfigure(0, weight=1)
        ttk.Label(
            self, text=f"MKVCleaner {__version__}", style="Footer.TLabel",
        ).pack(side="bottom", anchor="e", padx=14, pady=(0, 8))

    def apply_theme(self, mode: str) -> None:
        colors = COLORS[mode]
        self.configure(bg=colors["bg"])
        style = ttk.Style(self)
        style.theme_use("clam")
        base = {"background": colors["bg"], "foreground": colors["text"]}
        style.configure(".", font=("Segoe UI", 10), **base)
        style.configure("TFrame", background=colors["bg"])
        style.configure("TLabelframe", background=colors["panel"], bordercolor=colors["border"])
        style.configure("TLabelframe.Label", background=colors["panel"], foreground=colors["text"],
                        font=("Segoe UI", 10, "bold"))
        style.configure("TLabel", background=colors["bg"], foreground=colors["text"])
        style.configure("Activity.TLabel", background=colors["panel"], foreground=colors["text"],
                        font=("Segoe UI", 12, "bold"))
        style.configure("AboutTitle.TLabel", background=colors["bg"], foreground=colors["text"],
                        font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", background=colors["panel"], foreground=colors["muted"])
        style.configure("Warning.TLabel", background=colors["panel"], foreground="#e6b450")
        style.configure("Footer.TLabel", background=colors["bg"], foreground=colors["muted"],
                        font=("Segoe UI", 9))
        style.configure("Summary.TLabel", background=colors["panel"], foreground=colors["text"],
                        font=("Segoe UI", 10, "bold"))
        style.configure("TButton", background=colors["field"], foreground=colors["text"],
                        bordercolor=colors["border"], padding=(10, 6))
        style.map("TButton", background=[("active", colors["border"])])
        style.configure("Accent.TButton", background=colors["accent"], foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", colors["accent"])])
        style.configure("TEntry", fieldbackground=colors["field"], foreground=colors["text"],
                        insertcolor=colors["text"], bordercolor=colors["border"])
        style.configure("TCombobox", fieldbackground=colors["field"], foreground=colors["text"],
                        arrowcolor=colors["text"])
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", colors["field"]),
                ("disabled", colors["panel"]),
            ],
            foreground=[
                ("readonly", colors["text"]),
                ("disabled", colors["muted"]),
            ],
            selectbackground=[
                ("readonly", colors["field"]),
            ],
            selectforeground=[
                ("readonly", colors["text"]),
            ],
        )
        self.option_add("*TCombobox*Listbox.background", colors["field"])
        self.option_add("*TCombobox*Listbox.foreground", colors["text"])
        self.option_add("*TCombobox*Listbox.selectBackground", colors["select"])
        self.option_add("*TCombobox*Listbox.selectForeground", colors["text"])
        style.configure("Treeview", background=colors["panel"], fieldbackground=colors["panel"],
                        foreground=colors["text"], rowheight=25, bordercolor=colors["border"])
        style.configure("Treeview.Heading", background=colors["field"], foreground=colors["text"],
                        font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", colors["select"])])
        style.configure("Main.Horizontal.TProgressbar", background=colors["accent"],
                        troughcolor=colors["field"], bordercolor=colors["field"])
        style.configure("ProgressText.TLabel", background=colors["field"],
                        foreground="#ffffff", font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook", background=colors["bg"], bordercolor=colors["border"])
        style.configure("TNotebook.Tab", background=colors["field"], foreground=colors["text"],
                        padding=(12, 7))
        style.map("TNotebook.Tab", background=[("selected", colors["panel"])])
        for status, color in STATUS_COLORS.items():
            tag = status.lower().replace(" ", "_").replace("(", "").replace(")", "")
            self.file_tree.tag_configure(tag, foreground=color)

    def _browse(self) -> None:
        selected = filedialog.askdirectory(parent=self, title="Choose input folder")
        if selected:
            self.input_var.set(selected)

    def _settings(self) -> None:
        if self.running:
            messagebox.showinfo("Run in Progress", "Settings can be changed after this run finishes.")
            return
        try:
            self.config = load_config(self.config_path)
        except ValueError as exc:
            messagebox.showerror("Invalid config.json", str(exc))
            return
        SettingsDialog(self, self.config)

    def _reset_run(self) -> None:
        for item in self.file_tree.get_children():
            self.file_tree.delete(item)
        for item in self.worker_tree.get_children():
            self.worker_tree.delete(item)
        self.row_ids.clear()
        self.workers.clear()
        self.inspected = self.completed = 0
        self.counts = {"Cleaned": 0, "Skipped": 0, "Failed": 0, "Dry-run": 0, "Cancelled": 0}
        self.progress["value"] = 0
        self.progress_text.set("0.0%")
        for value in self.summary_vars.values():
            value.set("0")

    def _start(self) -> None:
        if self.running:
            return
        try:
            config = load_config(self.config_path)
            input_path = Path(self.input_var.get().strip()).expanduser()
            if not self.input_var.get().strip():
                raise ValueError("Choose an input folder first.")
            files = scan_path(input_path, config.recursive)
            if not files:
                raise ValueError("No eligible MKV files were found.")
            mkvmerge = locate_mkvmerge(config.mkvmerge_path)
        except Exception as exc:
            messagebox.showerror("Cannot Start", str(exc), parent=self)
            return

        self.config = config
        self._reset_run()
        self.total = len(files)
        self.summary_vars["Found"].set(str(self.total))
        self.counter_text.set(f"0/{self.total} files")
        names: dict[str, int] = {}
        for media in files:
            names[media.path.name.lower()] = names.get(media.path.name.lower(), 0) + 1
        for index, media in enumerate(files, 1):
            display = (
                f"{media.path.parent.name}\\{media.path.name}"
                if names[media.path.name.lower()] > 1 else media.path.name
            )
            row = self.file_tree.insert(
                "", "end", values=(index, display, "Waiting", "—"), tags=("waiting",),
            )
            self.row_ids[media.path] = row
        for worker_id in range(1, config.threads + 1):
            row = self.worker_tree.insert("", "end", values=(worker_id, "Idle", "—"))
            self.workers[worker_id] = (row, "")

        log_dir = Path(config.log_directory)
        if not log_dir.is_absolute():
            log_dir = application_directory() / log_dir
        logger = RunLogger(log_dir, config.json_logging)
        self.running = True
        self.paused = False
        self.run_event.set()
        self.stop_event.clear()
        self.started_at = time.monotonic()
        self.activity_file.set("Starting MKVCleaner")
        self.activity_action.set(f"Found {self.total} MKV file(s).")
        self.start_button.configure(state="disabled")
        self.settings_button.configure(state="disabled")
        self.pause_button.configure(state="normal", text="Pause")
        self.cancel_button.configure(state="normal")
        threading.Thread(
            target=self._run, args=(files, mkvmerge, config, logger),
            name="mkvcleaner-run", daemon=True,
        ).start()

    def _run(self, files, mkvmerge, config, logger) -> None:
        def emit(*event) -> None:
            self.events.put(event)

        def inspected(media) -> None:
            logger.add(media) if media.status == "Failed" or not media.clean_reasons else None
            emit("inspect", media)

        def waiting(media, worker_id) -> None:
            emit("worker", media, worker_id, "Waiting (paused)")

        def started(media, worker_id) -> None:
            emit("worker", media, worker_id, "Dry run" if config.dry_run else "Starting")

        def phase(media, worker_id, action) -> None:
            emit("worker", media, worker_id, action)

        def result(media, worker_id) -> None:
            logger.add(media)
            emit("result", media, worker_id)

        try:
            results = process_files(
                files, mkvmerge, config,
                inspect_callback=inspected,
                wait_callback=waiting,
                start_callback=started,
                phase_callback=phase,
                result_callback=result,
                run_event=self.run_event,
                stop_event=self.stop_event,
            )
            logger.finalize()
            emit("finished", results, logger)
        except Exception as exc:
            emit("fatal", str(exc))

    def _set_file(self, path: Path, status: str, details: str) -> None:
        row = self.row_ids[path]
        values = list(self.file_tree.item(row, "values"))
        values[2] = status
        values[3] = details or "—"
        tag = status.lower().replace(" ", "_").replace("(", "").replace(")", "")
        self.file_tree.item(row, values=values, tags=(tag,))
        self.file_tree.see(row)

    def _set_worker(self, worker_id: int, action: str, filename: str = "") -> None:
        row, _ = self.workers[worker_id]
        self.workers[worker_id] = (row, filename)
        self.worker_tree.item(row, values=(worker_id, action, filename or "—"))

    def _complete_one(self) -> None:
        self.completed += 1
        percent = self.completed * 100 / self.total if self.total else 0
        self.progress["value"] = percent
        self.progress_text.set(f"{percent:.1f}%")
        self.counter_text.set(f"{self.completed}/{self.total} files")

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "inspect":
            media = event[1]
            self.inspected += 1
            self.summary_vars["Inspected"].set(str(self.inspected))
            self.activity_file.set(media.path.name)
            if media.status == "Failed":
                self._set_file(media.path, "Failed", media.error)
                self.counts["Failed"] += 1
                self.summary_vars["Failed"].set(str(self.counts["Failed"]))
                self._complete_one()
            elif not media.clean_reasons:
                self._set_file(media.path, "Skipped", "Already clean — no configured cleanup needed")
                self.counts["Skipped"] += 1
                self.summary_vars["Skipped"].set(str(self.counts["Skipped"]))
                self._complete_one()
            else:
                in_place = can_use_mkvpropedit(media, self.config)
                status = "Will clean" if in_place else "Will remux"
                method = "fast in-place edit" if in_place else "full remux required"
                self._set_file(
                    media.path, status,
                    f"{' • '.join(media.clean_reasons)} — {method}",
                )
                self.activity_action.set(
                    "Inspection complete; queued for "
                    + ("fast in-place editing." if in_place else "full remuxing.")
                )
        elif kind == "worker":
            media, worker_id, action = event[1:]
            status = "Cleaning" if action == "Starting" else action
            self._set_worker(worker_id, action, media.path.name)
            self._set_file(media.path, status, " • ".join(media.clean_reasons))
            self.activity_file.set(media.path.name)
            self.activity_action.set(f"Worker {worker_id}: {action}")
        elif kind == "result":
            media, worker_id = event[1:]
            self._set_worker(worker_id, "Idle")
            if media.status == "Cleaned":
                detail = (
                    "Metadata cleaned in place with mkvpropedit"
                    if media.cleaning_method == "mkvpropedit" else "Cleaned with mkvmerge"
                )
                if media.verified:
                    detail += " and verified"
                self._set_file(media.path, "Done", detail)
                self.counts["Cleaned"] += 1
                self.summary_vars["Cleaned"].set(str(self.counts["Cleaned"]))
            elif media.status == "Dry-run":
                self._set_file(media.path, "Dry run complete", "No files were changed")
                self.counts["Dry-run"] += 1
            elif media.status == "Cancelled":
                self._set_file(media.path, "Cancelled", media.error)
                self.counts["Cancelled"] += 1
            elif media.status == "Failed":
                self._set_file(media.path, "Failed", media.error)
                self.counts["Failed"] += 1
                self.summary_vars["Failed"].set(str(self.counts["Failed"]))
            self._complete_one()
        elif kind == "finished":
            results, logger = event[1:]
            saved = sum(item.bytes_saved for item in results if item.status == "Cleaned")
            self.activity_file.set("Run complete" if not self.stop_event.is_set() else "Run cancelled")
            self.activity_action.set(
                f"Cleaned {self.counts['Cleaned']}, skipped {self.counts['Skipped']}, "
                f"failed {self.counts['Failed']} • size saved {_format_bytes(saved)}"
            )
            self._finish_controls()
        elif kind == "fatal":
            self.activity_file.set("Run failed")
            self.activity_action.set(event[1])
            self._finish_controls()
            messagebox.showerror("MKVCleaner Error", event[1], parent=self)

    def _drain_events(self) -> None:
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _tick(self) -> None:
        if self.running:
            self.elapsed_text.set(f"Elapsed {_format_seconds(time.monotonic() - self.started_at)}")
        self.after(500, self._tick)

    def _toggle_pause(self) -> None:
        if not self.running:
            return
        if self.paused:
            self.paused = False
            self.run_event.set()
            self.pause_button.configure(text="Pause")
            self.activity_action.set("Resuming queued work.")
        else:
            self.paused = True
            self.run_event.clear()
            self.pause_button.configure(text="Resume")
            self.activity_action.set("Pausing between files; active operations will finish safely.")

    def _cancel(self) -> None:
        if not self.running:
            return
        if not messagebox.askyesno(
            "Cancel Run",
            "Stop queued files from starting?\n\nAn active MKVToolNix operation will finish safely.",
            parent=self,
        ):
            return
        self.stop_event.set()
        self.run_event.set()
        self.paused = False
        self.pause_button.configure(state="disabled")
        self.cancel_button.configure(state="disabled")
        self.activity_action.set("Cancelling queued work; active operations are finishing.")

    def _finish_controls(self) -> None:
        self.running = False
        self.paused = False
        self.run_event.set()
        self.start_button.configure(state="normal")
        self.settings_button.configure(state="normal")
        self.pause_button.configure(state="disabled", text="Pause")
        self.cancel_button.configure(state="disabled")

    def _close(self) -> None:
        if self.running:
            messagebox.showinfo(
                "Run in Progress",
                "Cancel the run and wait for active operations to finish before closing.",
                parent=self,
            )
            return
        self.destroy()


def main() -> int:
    os.environ["MKVCLEANER_GUI"] = "1"
    app = CleanerGUI()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
