from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from vmaf_workflow.config import YTDLP_FORMAT_SELECTOR
from vmaf_workflow.models import StreamRecord


@dataclass(frozen=True)
class YtDlpStreamRecord(StreamRecord):
    source: str = "youtube"
    index: int | None = None
    width: int | None = None
    height: int | None = None
    codec_family: str | None = None
    bitrate_source: str | None = None
    format_id: str | None = None
    ext: str | None = None
    protocol: str | None = None
    container: str | None = None


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
) -> YtDlpStreamRecord:
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

    return YtDlpStreamRecord(
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


def parse_ytdlp_preflight(
    raw_info: dict[str, Any],
) -> tuple[list[YtDlpStreamRecord], list[YtDlpStreamRecord]]:
    selected = [
        normalize_ytdlp_format(format_info, index)
        for index, format_info in enumerate(raw_info.get("formats") or [])
        if _is_target_format(format_info)
    ]
    requested = [
        normalize_ytdlp_format(format_info, None)
        for format_info in raw_info.get("requested_downloads") or []
        if _is_target_format(format_info)
    ]
    return selected, requested


def load_after_video_downloads(path: str | Path) -> list[YtDlpStreamRecord]:
    if not Path(path).exists():
        return []
    streams: list[YtDlpStreamRecord] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            raw_info = json.loads(stripped)
            streams.extend(
                normalize_ytdlp_format(format_info, None)
                for format_info in raw_info.get("requested_downloads") or []
                if _is_target_format(format_info)
            )
    return streams


def load_sidecar_downloads(infojson_dir: str | Path) -> list[YtDlpStreamRecord]:
    streams: list[YtDlpStreamRecord] = []
    for infojson_path in sorted(Path(infojson_dir).glob("*.info.json")):
        raw_info = json.loads(infojson_path.read_text(encoding="utf-8"))
        if _is_target_format(raw_info):
            streams.append(normalize_ytdlp_format(raw_info, None))
        streams.extend(
            normalize_ytdlp_format(format_info, None)
            for format_info in raw_info.get("requested_downloads") or []
            if _is_target_format(format_info)
        )
    return streams


def _is_target_format(format_info: dict[str, Any]) -> bool:
    height = _optional_int(format_info.get("height"))
    vcodec = format_info.get("vcodec")
    return (
        height is not None
        and height >= 1080
        and vcodec is not None
        and vcodec != "none"
        and format_info.get("acodec") == "none"
    )


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
