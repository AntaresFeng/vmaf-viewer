from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


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

YTDLP_FORMAT_SELECTOR = "all[height>=1080][vcodec!=none][acodec=none]"


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
