from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from vmaf_workflow.config import YTDLP_FORMAT_SELECTOR, is_target_format
from vmaf_workflow.models import StreamRecord


def codec_family(vcodec: str | None) -> str | None:
    if vcodec is None:
        return None

    normalized = vcodec.lower()
    if normalized.startswith("avc1"):
        return "AVC"
    if normalized.startswith("av01"):
        return "AV1"
    if normalized.startswith("vp9") or normalized.startswith("vp09"):
        return "VP9"
    if normalized.startswith("hev1") or normalized.startswith("hvc1"):
        return "HEVC"
    return vcodec


def normalize_ytdlp_format(
    format_info: dict[str, Any], index: int | None
) -> StreamRecord:
    width = _optional_int(format_info.get("width"))
    height = _optional_int(format_info.get("height"))
    resolution = format_info.get("resolution")
    if resolution is None and width is not None and height is not None:
        resolution = f"{width}x{height}"

    bitrate_value = format_info.get("vbr")
    bitrate_source = "vbr"
    if bitrate_value is None:
        bitrate_value = format_info.get("tbr")
        bitrate_source = "tbr" if bitrate_value is not None else None

    size_bytes = format_info.get("filesize")
    if size_bytes is None:
        size_bytes = format_info.get("filesize_approx")

    vcodec = format_info.get("vcodec")

    return StreamRecord(
        source="youtube",
        index=index,
        quality_label=format_info.get("format_note"),
        resolution=resolution,
        width=width,
        height=height,
        codec=vcodec,
        codec_family=codec_family(vcodec),
        fps=_optional_float(format_info.get("fps")),
        bitrate_kbps=_optional_float(bitrate_value),
        bitrate_source=bitrate_source,
        size_bytes=_optional_int(size_bytes),
        size_text=None,
        format_id=_optional_str(format_info.get("format_id")),
        ext=format_info.get("ext"),
        protocol=format_info.get("protocol"),
        container=format_info.get("container"),
        raw=format_info,
    )


def _normalize_target_many(
    formats: Iterable[dict[str, Any]] | None, index: int | None = None
) -> list[StreamRecord]:
    return [
        normalize_ytdlp_format(format_info, index)
        for format_info in formats or []
        if is_target_format(format_info)
    ]


def parse_ytdlp_preflight(
    raw_info: dict[str, Any],
) -> tuple[list[StreamRecord], list[StreamRecord]]:
    selected = [
        normalize_ytdlp_format(format_info, index)
        for index, format_info in enumerate(raw_info.get("formats") or [])
        if is_target_format(format_info)
    ]
    requested = _normalize_target_many(raw_info.get("requested_downloads"))
    return selected, requested


def load_after_video_downloads(path: str | Path) -> list[StreamRecord]:
    if not Path(path).exists():
        return []
    streams: list[StreamRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            raw_info = _loads_json_object(stripped)
            if raw_info is None:
                continue
            streams.extend(_normalize_target_many(raw_info.get("requested_downloads")))
    return streams


def load_sidecar_downloads(infojson_dir: str | Path) -> list[StreamRecord]:
    streams: list[StreamRecord] = []
    for infojson_path in sorted(Path(infojson_dir).glob("*.info.json")):
        raw_info = _loads_json_object(infojson_path.read_text(encoding="utf-8"))
        if raw_info is None:
            continue
        if is_target_format(raw_info):
            streams.append(normalize_ytdlp_format(raw_info, None))
        streams.extend(_normalize_target_many(raw_info.get("requested_downloads")))
    return streams


def _loads_json_object(text: str) -> dict[str, Any] | None:
    try:
        raw_info = json.loads(text)
    except json.JSONDecodeError:
        return None
    return raw_info if isinstance(raw_info, dict) else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def ytdlp_preflight_argv(exe_path: Path, youtube_url: str) -> list[str]:
    return [
        str(exe_path),
        "--ignore-config",
        "-J",
        "--skip-download",
        "-f",
        YTDLP_FORMAT_SELECTOR,
        youtube_url,
    ]


def ytdlp_download_argv(
    exe_path: Path, config_path: Path, youtube_url: str
) -> list[str]:
    return [str(exe_path), "--config-locations", str(config_path), youtube_url]
