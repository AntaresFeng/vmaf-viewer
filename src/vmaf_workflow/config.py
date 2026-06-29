from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


QUALITY_LABELS: tuple[str, ...] = (
    "8K 超高清",
    "杜比视界",
    "HDR 真彩",
    "4K 超清",
    "1080P 高帧率",
    "1080P 高码率",
    "1080P 高清",
)

HIGH_1080_LABELS: tuple[str, ...] = (
    "1080P 高帧率",
    "1080P 高码率",
)

FALLBACK_1080_LABEL = "1080P 高清"

# yt-dlp CLI format selector. `is_target_format` below must mirror this filter
# when post-processing yt-dlp JSON output, so the two are kept together.
YTDLP_FORMAT_SELECTOR = "all[height>=1080][vcodec!=none][acodec=none]"


def is_target_format(format_info: dict[str, Any]) -> bool:
    """Mirror the YTDLP_FORMAT_SELECTOR for parsed JSON format dicts."""
    height = _coerce_int(format_info.get("height"))
    vcodec = format_info.get("vcodec")
    return (
        height is not None
        and height >= 1080
        and vcodec is not None
        and vcodec != "none"
        and format_info.get("acodec") == "none"
    )


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class BBDownSettings:
    exe_path: Path = Path(r"D:\BiliDown\BBDown.exe")
    file_pattern: str = "<bvid>-<dfn>-<videoCodecs>"
    multi_file_pattern: str = (
        "<bvid>-P<pageNumberWithZero>-<pageTitle>-<dfn>-<videoCodecs>"
    )


@dataclass(frozen=True)
class YtDlpSettings:
    exe_path: Path = Path(r"D:\YTDown\yt-dlp.exe")
    format_selector: str = YTDLP_FORMAT_SELECTOR
    output_template: str = "%(id)s-%(format_note)s-%(vcodec)s.%(ext)s"


@dataclass(frozen=True)
class WorkflowSettings:
    videos_dir: Path = Path("videos")
    bbdown: BBDownSettings = field(default_factory=BBDownSettings)
    ytdlp: YtDlpSettings = field(default_factory=YtDlpSettings)


def default_settings() -> WorkflowSettings:
    return WorkflowSettings()
