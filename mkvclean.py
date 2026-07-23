from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from colorama import init
from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table
from rich.text import Text

from core.cli import parse_args
from core.cleaner import can_use_mkvpropedit
from core.config import load_config
from core.logger import RunLogger
from core.paths import application_directory, locate_mkvmerge
from core.scanner import scan_path
from core.workers import process_files
from version import __version__

try:
    import msvcrt
except ImportError:
    msvcrt = None


console = Console()


@dataclass
class FileDisplayState:
    number: int
    path: Path
    status: str = "Waiting"
    changes: list[str] = field(default_factory=list)
    result: str = ""
    error: str = ""
    printed: bool = False


def _resolve_config_path(cli_path: Path | None) -> Path:
    return cli_path.expanduser().resolve() if cli_path else application_directory() / "config.json"


def _apply_cli_overrides(config, args) -> None:
    if getattr(args, "dry_run", False):
        config.dry_run = True
    if getattr(args, "threads", None) is not None:
        config.threads = args.threads
    if getattr(args, "recursive", False):
        config.recursive = True
    if getattr(args, "keep_backup", False):
        config.keep_backup = True
    if getattr(args, "remove_chapters", False):
        config.remove_chapters = True
    if getattr(args, "remove_attachments", False):
        config.remove_attachments = True
    if getattr(args, "show_skipped", False):
        config.show_skipped = True
    if getattr(args, "mkvmerge", None):
        config.mkvmerge_path = str(args.mkvmerge)
    if getattr(args, "inspection_mode", None):
        config.inspection_mode = args.inspection_mode
    config.validate()


def _resolve_input(args, config) -> tuple[Path | None, str]:
    cli_input = getattr(args, "input_path", None)
    legacy_input = getattr(args, "legacy_path", None)
    if cli_input:
        return Path(cli_input).expanduser(), "command line (-i/--input)"
    if legacy_input:
        return Path(legacy_input).expanduser(), "legacy positional argument"
    if getattr(config, "default_input_directory", ""):
        return Path(config.default_input_directory).expanduser(), "config.json"
    return None, ""


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def _print_effective_config(config, config_path: Path) -> None:
    table = Table(title="Effective configuration", show_header=False)
    table.add_column("Setting", style="bold")
    table.add_column("Value")
    rows = [
        ("Config file", str(config_path)),
        ("Input directory", config.default_input_directory or "(not set)"),
        ("Inspection mode", config.inspection_mode),
        ("Threads", str(config.threads)),
        ("Recursive", _yes_no(config.recursive)),
        ("Keep backup", _yes_no(config.keep_backup)),
        ("Verify output", _yes_no(config.verify_output)),
        ("Preserve timestamps", _yes_no(config.preserve_timestamps)),
        ("Dry run", _yes_no(config.dry_run)),
        ("Remove segment title", _yes_no(config.remove_title)),
        ("Remove track names", _yes_no(config.remove_track_names)),
        ("Tag cleanup", config.tag_cleanup),
        ("Remove chapters", _yes_no(config.remove_chapters)),
        ("Remove attachments", _yes_no(config.remove_attachments)),
        ("Show skipped summary", _yes_no(config.show_skipped)),
        ("JSON logging", _yes_no(config.json_logging)),
        ("Log directory", str(config.log_directory)),
        ("MKVToolNix path", config.mkvmerge_path or "(auto-detect)"),
    ]
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)


def _unique_display_names(paths: list[Path]) -> dict[Path, str]:
    counts: dict[str, int] = {}
    for path in paths:
        counts[path.name.lower()] = counts.get(path.name.lower(), 0) + 1

    names: dict[Path, str] = {}
    for path in paths:
        if counts[path.name.lower()] > 1:
            names[path] = f"{path.parent.name}\\{path.name}"
        else:
            names[path] = path.name
    return names


def _status_style(status: str) -> str:
    return {
        "Waiting": "dim",
        "Inspecting": "cyan",
        "Will clean": "bright_cyan",
        "Will remux": "yellow",
        "Cleaning": "blue",
        "Waiting (paused)": "yellow",
        "Creating backup": "cyan",
        "Editing metadata": "blue",
        "Remuxing": "blue",
        "Replacing original": "cyan",
        "Verifying": "magenta",
        "Done": "green",
        "Would clean": "bright_cyan",
        "Dry run complete": "cyan",
        "Skipped": "yellow",
        "Failed": "red",
    }.get(status, "white")


def main() -> int:
    init(autoreset=True)
    args = parse_args()

    console.print(f"[bold]MKVCleaner {__version__}[/bold]")

    config_path = _resolve_config_path(getattr(args, "config", None))
    created_config = not config_path.exists()
    config = load_config(config_path)
    _apply_cli_overrides(config, args)

    if getattr(args, "show_config", False):
        _print_effective_config(config, config_path)
        return 0

    if created_config:
        console.print(f"Created default configuration: {config_path}")

    input_path, input_source = _resolve_input(args, config)
    if input_path is None:
        console.print("[red]No input directory specified.[/red]")
        console.print('Set "default_input_directory" in config.json or use -i/--input.')
        return 1

    console.print(f"Using input directory from {input_source}:")
    console.print(str(input_path))
    console.print(f"Inspection mode: [bold]{config.inspection_mode}[/bold]")

    mkvmerge = locate_mkvmerge(config.mkvmerge_path)
    files = scan_path(input_path, config.recursive)
    if not files:
        console.print("No eligible MKV files were found.")
        return 0

    log_dir = Path(config.log_directory)
    if not log_dir.is_absolute():
        log_dir = application_directory() / log_dir
    logger = RunLogger(log_dir, config.json_logging)

    total_files = len(files)
    display_names = _unique_display_names([m.path for m in files])
    file_states = {
        media.path: FileDisplayState(number=index, path=media.path)
        for index, media in enumerate(files, start=1)
    }

    console.print(f"Found {total_files} MKV file(s).")
    if config.inspection_mode == "stream":
        console.print("Files are inspected and queued immediately.")
    else:
        console.print("All files are inspected before cleaning begins.")

    progress = Progress(
        TextColumn("[bold cyan]Processing"),
        BarColumn(
            bar_width=40,
            style="bright_black",
            complete_style="cyan",
            finished_style="green",
            pulse_style="cyan",
        ),
        TaskProgressColumn(),
        TextColumn("[bold]{task.completed}/{task.total} files"),
        TextColumn("•"),
        TimeElapsedColumn(),
        expand=True,
    )
    progress_task = progress.add_task("Overall", total=total_files)

    state_lock = threading.RLock()
    run_event = threading.Event()
    run_event.set()
    finished = threading.Event()

    workers = {
        worker_id: {"action": "Idle", "file": ""}
        for worker_id in range(1, config.threads + 1)
    }
    paused = False
    inspected_count = 0
    completed_count = 0
    skipped_files: list[tuple[Path, str]] = []

    def _item_details(item: FileDisplayState) -> str:
        details: list[str] = []
        if item.changes:
            details.extend(f"• {change}" for change in item.changes)
        if item.result and item.result not in item.changes:
            details.append(item.result)
        if item.error:
            details.append(item.error)
        return "\n".join(details) if details else "—"

    def _permanent_result(item: FileDisplayState) -> Table:
        table = Table(
            show_header=False,
            box=None,
            expand=True,
            padding=(0, 1),
        )
        table.add_column(width=8, no_wrap=True)
        table.add_column(ratio=3, overflow="fold")
        table.add_column(width=18, no_wrap=True)
        table.add_column(ratio=2, overflow="fold")
        table.add_row(
            Text(f"{item.number}/{total_files}", style="bold"),
            display_names[item.path],
            Text(item.status, style=f"bold {_status_style(item.status)}"),
            _item_details(item),
        )
        return table

    def _print_completed(item: FileDisplayState) -> None:
        if item.printed:
            return
        item.printed = True
        live.console.print(_permanent_result(item))
        live.console.print(Text("─" * 72, style="dim"))

    def build_history_table() -> Table:
        history = Table(
            title="Current Files",
            show_header=True,
            header_style="bold",
            expand=True,
            padding=(0, 1),
        )
        history.add_column("#", width=5, justify="right", no_wrap=True)
        history.add_column("File", ratio=3, overflow="fold")
        history.add_column("Status", width=18, no_wrap=True)
        history.add_column("Changes / Result", ratio=2, overflow="fold")

        with state_lock:
            unfinished = [
                item for item in sorted(file_states.values(), key=lambda value: value.number)
                if not item.printed
            ]

            # Keep the live region shorter than the terminal. Active entries are
            # shown first, followed by the next waiting entries, with a hard cap.
            active = [item for item in unfinished if item.status != "Waiting"]
            waiting = [item for item in unfinished if item.status == "Waiting"]
            visible = (active + waiting)[:8]

            for item in visible:
                history.add_row(
                    f"{item.number}/{total_files}",
                    display_names[item.path],
                    Text(item.status, style=f"bold {_status_style(item.status)}"),
                    _item_details(item),
                )

            hidden = len(unfinished) - len(visible)
            if hidden > 0:
                history.add_row("…", f"{hidden} more file(s) queued", "Waiting", "—")
            elif not visible:
                history.add_row("—", "No active files", "Complete", "—")
        return history

    def build_worker_table() -> Table:
        with state_lock:
            active_actions = {
                "Creating backup", "Editing metadata", "Remuxing",
                "Verifying", "Replacing original", "Dry run",
            }
            active_count = sum(info["action"] in active_actions for info in workers.values())
            paused_waiting = sum(info["action"] == "Waiting (paused)" for info in workers.values())
            if paused:
                if active_count:
                    status = (
                        f"PAUSING — {active_count} active operation(s) finishing; "
                        f"{paused_waiting} file(s) waiting; press R to resume"
                    )
                else:
                    status = (
                        f"PAUSED — {paused_waiting} file(s) reserved by workers; "
                        "0 cleaning processes active; press R to resume"
                    )
                status_style = "yellow"
            else:
                status = "RUNNING — press P to pause between files"
                status_style = "green"

            table = Table(
                title=Text(f"Workers — {status}", style=f"bold {status_style}"),
                show_header=True,
                header_style="bold",
                expand=True,
                padding=(0, 1),
            )
            table.add_column("Worker", width=8, no_wrap=True)
            table.add_column("Action", width=13, no_wrap=True)
            table.add_column("Current file", ratio=1, overflow="fold")

            for worker_id in range(1, config.threads + 1):
                info = workers[worker_id]
                action = info["action"]
                filename = info["file"] or "—"
                table.add_row(
                    str(worker_id),
                    Text(action, style=_status_style(action)),
                    filename,
                )
            return table

    def build_screen() -> Group:
        with state_lock:
            counts = Text(
                f"Inspected: {inspected_count}/{total_files}    "
                f"Completed: {completed_count}/{total_files}",
                style="bold",
            )
        return Group(build_history_table(), progress, counts, build_worker_table())

    live = Live(
        build_screen(),
        console=console,
        refresh_per_second=4,
        transient=False,
        vertical_overflow="crop",
    )

    def refresh() -> None:
        live.update(build_screen(), refresh=True)

    def keyboard_loop() -> None:
        nonlocal paused
        if msvcrt is None:
            return
        while not finished.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getwch().lower()
                with state_lock:
                    if key == "p" and not paused:
                        paused = True
                        run_event.clear()
                    elif key == "r" and paused:
                        paused = False
                        run_event.set()
                refresh()
            time.sleep(0.05)

    keyboard_thread = threading.Thread(
        target=keyboard_loop,
        name="pause-listener",
        daemon=True,
    )

    def on_inspect(media) -> None:
        nonlocal inspected_count, completed_count
        with state_lock:
            item = file_states[media.path]
            inspected_count += 1

            if media.status == "Failed":
                item.status = "Failed"
                item.error = media.error
                completed_count += 1
                progress.update(progress_task, advance=1)
                _print_completed(item)
            elif media.clean_reasons:
                item.changes = list(media.clean_reasons)
                if config.dry_run:
                    item.status = "Would clean"
                else:
                    item.status = (
                        "Will clean"
                        if can_use_mkvpropedit(media, config)
                        else "Will remux"
                    )
            else:
                item.status = "Skipped"
                item.result = "Already clean — no configured cleanup needed"
                skipped_files.append((media.path, "No configured cleanup needed"))
                completed_count += 1
                progress.update(progress_task, advance=1)
                _print_completed(item)
        refresh()

    def on_wait(media, worker_id: int) -> None:
        with state_lock:
            item = file_states[media.path]
            item.status = "Waiting (paused)"
            workers[worker_id]["action"] = "Waiting (paused)"
            workers[worker_id]["file"] = display_names[media.path]
        refresh()

    def on_start(media, worker_id: int) -> None:
        with state_lock:
            item = file_states[media.path]
            if config.dry_run:
                item.status = "Would clean"
                action = "Dry run"
            else:
                item.status = "Cleaning"
                action = "Starting"
            workers[worker_id]["action"] = action
            workers[worker_id]["file"] = display_names[media.path]
        refresh()

    def on_phase(media, worker_id: int, phase: str) -> None:
        with state_lock:
            item = file_states[media.path]
            item.status = phase
            workers[worker_id]["action"] = phase
            workers[worker_id]["file"] = display_names[media.path]
        refresh()

    def on_result(media, worker_id: int) -> None:
        nonlocal completed_count
        logger.add(media)

        with state_lock:
            item = file_states[media.path]
            if media.status == "Cleaned":
                item.status = "Done"
                if media.cleaning_method == "mkvpropedit":
                    item.result = (
                        "Metadata cleaned in place with mkvpropedit and verified"
                        if media.verified else
                        "Metadata cleaned in place with mkvpropedit"
                    )
                else:
                    item.result = "Cleaned and verified" if media.verified else "Cleaned"
            elif media.status == "Dry-run":
                item.status = "Dry run complete"
                item.result = "No files were changed"
            elif media.status == "Skipped":
                item.status = "Skipped"
                item.result = "Already clean — no configured cleanup needed"
            elif media.status == "Failed":
                item.status = "Failed"
                item.error = media.error

            workers[worker_id]["action"] = "Idle"
            workers[worker_id]["file"] = ""
            completed_count += 1
            progress.update(progress_task, advance=1)
            _print_completed(item)
        refresh()

    keyboard_thread.start()
    results = []

    try:
        with live:
            # Mark each file as it becomes the next inspection candidate.
            # The workers module performs inspection synchronously, so the
            # history row changes immediately once inspection completes.
            results = process_files(
                files,
                mkvmerge,
                config,
                inspect_callback=on_inspect,
                wait_callback=on_wait,
                start_callback=on_start,
                phase_callback=on_phase,
                result_callback=on_result,
                run_event=run_event,
            )
    finally:
        finished.set()
        run_event.set()
        keyboard_thread.join(timeout=1.0)

    logger.finalize()

    counts = {
        status: sum(1 for item in results if item.status == status)
        for status in ("Cleaned", "Skipped", "Dry-run", "Failed")
    }
    # Inspection failures are returned directly and may not pass result_callback.
    failed_total = sum(1 for state in file_states.values() if state.status == "Failed")
    total_saved = sum(item.bytes_saved for item in results if item.status == "Cleaned")

    console.print()
    summary = Table(title="Run Summary", show_header=False)
    summary.add_column("Result", style="bold")
    summary.add_column("Count", justify="right")
    summary.add_row("Processed", str(total_files))
    if config.dry_run:
        summary.add_row("Would clean", str(counts["Dry-run"]))
    else:
        summary.add_row("Cleaned", str(counts["Cleaned"]))
        summary.add_row("Size saved", _format_bytes(total_saved))
    summary.add_row("Skipped", str(counts["Skipped"]))
    summary.add_row("Failed", str(failed_total))
    console.print(summary)

    if config.show_skipped and skipped_files:
        console.print()
        skipped_table = Table(title="Skipped Files", expand=True)
        skipped_table.add_column("File")
        skipped_table.add_column("Reason")
        for path, reason in skipped_files:
            skipped_table.add_row(str(path), reason)
        console.print(skipped_table)

    console.print(f"Text log: {logger.text_path}")
    if config.json_logging:
        console.print(f"JSON log: {logger.json_path}")

    return 1 if failed_total else 0


if __name__ == "__main__":
    raise SystemExit(main())
