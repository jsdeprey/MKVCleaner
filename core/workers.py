from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from queue import Queue
from threading import Event
from typing import Callable, Iterable

from .cleaner import can_use_mkvpropedit, clean_file, clean_in_place
from .config import CleanerConfig
from .metadata import inspect_for_cleaning
from .models import MediaFile
from .verifier import replace_original, verify_output

InspectCallback = Callable[[MediaFile], None]
WaitCallback = Callable[[MediaFile, int], None]
StartCallback = Callable[[MediaFile, int], None]
PhaseCallback = Callable[[MediaFile, int, str], None]
ResultCallback = Callable[[MediaFile, int], None]


def inspect_one(media: MediaFile, mkvmerge: Path, config: CleanerConfig) -> MediaFile:
    inspect_for_cleaning(media, mkvmerge, config)
    return media


def clean_inspected(
    media: MediaFile,
    mkvmerge: Path,
    config: CleanerConfig,
    phase_callback: Callable[[str], None] | None = None,
) -> MediaFile:
    started = time.perf_counter()
    try:
        if not media.clean_reasons:
            media.status = "Skipped"
            media.error = "No metadata matched the configured cleanup options"
            return media
        if config.dry_run:
            media.status = "Dry-run"
            return media

        if can_use_mkvpropedit(media, config):
            clean_in_place(media, mkvmerge, config, phase_callback)
        else:
            if phase_callback:
                phase_callback("Remuxing")
            clean_file(media, mkvmerge, config)
            media.cleaning_method = "mkvmerge"
            if config.verify_output:
                if phase_callback:
                    phase_callback("Verifying")
                verify_output(media, mkvmerge, config)
            if phase_callback:
                phase_callback("Replacing original")
            replace_original(media, config)

        media.status = "Cleaned"
        return media
    except Exception as exc:
        media.status = "Failed"
        media.error = str(exc)
        return media
    finally:
        media.processing_time = time.perf_counter() - started
        if media.status == "Failed" and media.temp_path:
            media.temp_path.unlink(missing_ok=True)


def process_files(
    files: Iterable[MediaFile],
    mkvmerge: Path,
    config: CleanerConfig,
    inspect_callback: InspectCallback | None = None,
    wait_callback: WaitCallback | None = None,
    start_callback: StartCallback | None = None,
    phase_callback: PhaseCallback | None = None,
    result_callback: ResultCallback | None = None,
    run_event: Event | None = None,
    stop_event: Event | None = None,
) -> list[MediaFile]:
    file_list = list(files)
    results: list[MediaFile] = []
    worker_ids: Queue[int] = Queue()
    for worker_id in range(1, config.threads + 1):
        worker_ids.put(worker_id)

    def run_clean(media: MediaFile) -> tuple[MediaFile, int]:
        worker_id = worker_ids.get()
        try:
            if stop_event is not None and stop_event.is_set():
                media.status = "Cancelled"
                media.error = "Cancelled before cleaning started"
                if result_callback:
                    result_callback(media, worker_id)
                return media, worker_id
            if run_event is not None and not run_event.is_set():
                if wait_callback:
                    wait_callback(media, worker_id)
                run_event.wait()

            if stop_event is not None and stop_event.is_set():
                media.status = "Cancelled"
                media.error = "Cancelled before cleaning started"
                if result_callback:
                    result_callback(media, worker_id)
                return media, worker_id

            if start_callback:
                start_callback(media, worker_id)

            def report_phase(phase: str) -> None:
                if phase_callback:
                    phase_callback(media, worker_id, phase)

            result = clean_inspected(media, mkvmerge, config, report_phase)
            if result_callback:
                result_callback(result, worker_id)
            return result, worker_id
        finally:
            worker_ids.put(worker_id)

    with ThreadPoolExecutor(max_workers=config.threads) as executor:
        futures = []
        if config.inspection_mode == "full":
            inspected = []
            for media in file_list:
                try:
                    inspect_one(media, mkvmerge, config)
                except Exception as exc:
                    media.status = "Failed"
                    media.error = f"Inspection failed: {exc}"
                if inspect_callback:
                    inspect_callback(media)
                inspected.append(media)
            for media in inspected:
                if media.status == "Failed" or not media.clean_reasons:
                    results.append(media)
                else:
                    futures.append(executor.submit(run_clean, media))
        else:
            for media in file_list:
                try:
                    inspect_one(media, mkvmerge, config)
                except Exception as exc:
                    media.status = "Failed"
                    media.error = f"Inspection failed: {exc}"
                if inspect_callback:
                    inspect_callback(media)
                if media.status == "Failed" or not media.clean_reasons:
                    results.append(media)
                else:
                    futures.append(executor.submit(run_clean, media))

        for future in as_completed(futures):
            result, _worker_id = future.result()
            results.append(result)
    return results
