from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import CleanerConfig
from .models import MediaFile, TrackInfo
from .process import run_tool


def _run_json(mkvmerge: Path, media_path: Path) -> dict[str, Any]:
    result = run_tool([str(mkvmerge), "-J", str(media_path)])
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "mkvmerge -J failed")
    return json.loads(result.stdout)


def inspect_for_cleaning(
    media: MediaFile,
    mkvmerge: Path,
    config: CleanerConfig,
) -> MediaFile:
    data = _run_json(mkvmerge, media.path)
    media.metadata = data
    media.original_size = media.path.stat().st_size

    container = data.get("container") or {}
    properties = container.get("properties") or {}
    media.segment_title = str(properties.get("title") or "")

    media.tracks.clear()
    media.clean_reasons.clear()

    for raw in data.get("tracks") or []:
        props = raw.get("properties") or {}
        track = TrackInfo(
            id=int(raw.get("id", 0)),
            type=str(raw.get("type") or ""),
            codec=str(raw.get("codec") or ""),
            codec_id=str(props.get("codec_id") or ""),
            language=str(props.get("language") or ""),
            name=str(props.get("track_name") or ""),
            default_track=bool(props.get("default_track", False)),
            forced_track=bool(props.get("forced_track", False)),
            enabled_track=bool(props.get("enabled_track", True)),
            properties=props,
        )
        media.tracks.append(track)

    tags = data.get("global_tags") or data.get("tags") or []
    media.has_global_tags = bool(tags)

    media.has_track_tags = any(
        bool((track.get("properties") or {}).get("tag_track_uid"))
        or bool(track.get("tags"))
        for track in (data.get("tracks") or [])
    )

    chapters = data.get("chapters") or []
    media.chapter_count = len(chapters)
    media.chapters_present = media.chapter_count > 0

    attachments = data.get("attachments") or []
    media.attachment_count = len(attachments)
    media.attachment_ids = [int(item.get("id", index)) for index, item in enumerate(attachments)]
    media.attachments_present = media.attachment_count > 0

    if config.remove_title and media.segment_title:
        media.add_reason("Segment title")
    if config.remove_track_names:
        named_count = sum(1 for track in media.tracks if track.name)
        if named_count:
            media.add_reason(f"Track names ({named_count})")
    if config.remove_global_tags and media.has_global_tags:
        media.add_reason("Global tags")
    if config.remove_track_tags and media.has_track_tags:
        media.add_reason("Track tags")
    if config.remove_chapters and media.chapters_present:
        media.add_reason(f"Chapters ({media.chapter_count})")
    if config.remove_attachments and media.attachments_present:
        media.add_reason(f"Attachments ({media.attachment_count})")

    return media
