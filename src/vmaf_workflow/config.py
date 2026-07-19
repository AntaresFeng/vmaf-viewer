from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
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
YTDLP_MIN_HEIGHT = 1000
YTDLP_FORMAT_SELECTOR = f"all[height>={YTDLP_MIN_HEIGHT}][vcodec!=none][acodec=none]"


def is_target_format(format_info: dict[str, Any]) -> bool:
    """Mirror the YTDLP_FORMAT_SELECTOR for parsed JSON format dicts."""
    height = _coerce_int(format_info.get("height"))
    vcodec = format_info.get("vcodec")
    return (
        height is not None
        and height >= YTDLP_MIN_HEIGHT
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
class EasyVmafSettings:
    repo_dir: Path = Path("/home/fzx/easyVmaf")
    model_4k_min_height: int = 1600
    output_fmt: str = "json"
    endsync: bool = True
    threads: int | None = 8
    ffmpeg_min_major: int = 5
    required_branch: str = "master"

    def executable_path(self) -> Path:
        if self._is_windows_repo_dir():
            return self.repo_dir / ".venv" / "Scripts" / "easyvmaf.exe"
        return self.repo_dir / ".venv" / "bin" / "easyvmaf"

    def with_repo_dir(self, repo_dir: Path) -> "EasyVmafSettings":
        return EasyVmafSettings(
            repo_dir=repo_dir,
            model_4k_min_height=self.model_4k_min_height,
            output_fmt=self.output_fmt,
            endsync=self.endsync,
            threads=self.threads,
            ffmpeg_min_major=self.ffmpeg_min_major,
            required_branch=self.required_branch,
        )

    def _is_windows_repo_dir(self) -> bool:
        raw_path = str(self.repo_dir)
        posix_path = self.repo_dir.as_posix()
        if posix_path.startswith("/") and ":" not in posix_path:
            return False
        return ":" in raw_path or "\\" in raw_path


@dataclass(frozen=True)
class RemoteSettings:
    host: str = "3080"
    work_dir: PurePosixPath = PurePosixPath("/home/fzx/vmaf_compare")
    ssh_executable: Path = Path("ssh")
    scp_executable: Path = Path("scp")
    connect_timeout_seconds: int = 10
    server_alive_interval_seconds: int = 30

    def with_target(
        self,
        host: str | None = None,
        work_dir: PurePosixPath | None = None,
    ) -> "RemoteSettings":
        return RemoteSettings(
            host=self.host if host is None else host,
            work_dir=self.work_dir if work_dir is None else work_dir,
            ssh_executable=self.ssh_executable,
            scp_executable=self.scp_executable,
            connect_timeout_seconds=self.connect_timeout_seconds,
            server_alive_interval_seconds=self.server_alive_interval_seconds,
        )


@dataclass(frozen=True)
class WorkflowSettings:
    videos_dir: Path = Path("videos")
    bbdown: BBDownSettings = field(default_factory=BBDownSettings)
    ytdlp: YtDlpSettings = field(default_factory=YtDlpSettings)
    easyvmaf: EasyVmafSettings = field(default_factory=EasyVmafSettings)
    remote: RemoteSettings = field(default_factory=RemoteSettings)


def default_settings() -> WorkflowSettings:
    return WorkflowSettings()
