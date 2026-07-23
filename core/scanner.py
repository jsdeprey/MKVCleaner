from __future__ import annotations

from pathlib import Path

from .models import MediaFile


IGNORED_MARKERS = (".cleaning.", ".mkvclean-temp.", ".video-optimizer-")


def _eligible(path: Path) -> bool:
    name = path.name.lower()
    return path.is_file() and path.suffix.lower() == ".mkv" and not any(
        marker in name for marker in IGNORED_MARKERS
    )


def scan_path(path: Path, recursive: bool) -> list[MediaFile]:
    path = path.expanduser().resolve()

    if path.is_file():
        return [MediaFile(path)] if _eligible(path) else []

    if not path.is_dir():
        raise FileNotFoundError(f"Path does not exist: {path}")

    pattern = "**/*.mkv" if recursive else "*.mkv"
    return [MediaFile(item) for item in sorted(path.glob(pattern)) if _eligible(item)]
