from pathlib import Path, PurePosixPath
import tomllib

from vmaf_workflow.config import (
    FALLBACK_1080_LABEL,
    HIGH_1080_LABELS,
    QUALITY_LABELS,
    YTDLP_FORMAT_SELECTOR,
    YTDLP_MIN_HEIGHT,
    default_settings,
)
from vmaf_workflow.models import DownloadDecision, StreamRecord


def test_pyproject_exposes_workflow_console_script_and_package():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["vmaf-workflow"] == "vmaf_workflow.cli:main"
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/vmaf_viewer",
        "src/vmaf_workflow",
    ]


def test_default_settings_uses_local_tool_paths_and_output_patterns():
    settings = default_settings()

    assert str(settings.bbdown.exe_path) == r"D:\BiliDown\BBDown.exe"
    assert str(settings.ytdlp.exe_path) == r"D:\YTDown\yt-dlp.exe"
    assert settings.videos_dir == Path("videos")
    assert settings.bbdown.file_pattern == "<bvid>-<dfn>-<videoCodecs>"
    assert (
        settings.bbdown.multi_file_pattern
        == "<bvid>-P<pageNumberWithZero>-<pageTitle>-<dfn>-<videoCodecs>"
    )
    assert settings.ytdlp.format_selector == (
        "all[height>=1000][vcodec!=none][acodec=none]"
    )
    assert settings.ytdlp.output_template == (
        "%(id)s-%(format_note)s-%(vcodec)s.%(ext)s"
    )
    assert settings.easyvmaf.repo_dir == Path("/home/fzx/easyVmaf")
    assert settings.easyvmaf.model_4k_min_height == 1600
    assert settings.easyvmaf.output_fmt == "json"
    assert settings.easyvmaf.endsync is True
    assert settings.easyvmaf.threads == 8
    assert settings.easyvmaf.ffmpeg_min_major == 5
    assert settings.easyvmaf.required_branch == "master"
    assert settings.remote.host == "3080"
    assert settings.remote.work_dir == PurePosixPath("/home/fzx/vmaf_compare")
    assert settings.remote.ssh_executable == Path("ssh")
    assert settings.remote.scp_executable == Path("scp")
    assert settings.remote.connect_timeout_seconds == 10
    assert settings.remote.server_alive_interval_seconds == 30
    assert YTDLP_MIN_HEIGHT == 1000
    assert YTDLP_FORMAT_SELECTOR == "all[height>=1000][vcodec!=none][acodec=none]"


def test_easyvmaf_settings_infers_remote_executable_from_repo_shape():
    settings = default_settings().easyvmaf

    assert settings.executable_path().as_posix() == (
        "/home/fzx/easyVmaf/.venv/bin/easyvmaf"
    )
    windows_settings = settings.with_repo_dir(Path(r"C:\easyVmaf"))
    assert windows_settings.executable_path() == (
        Path(r"C:\easyVmaf\.venv\Scripts\easyvmaf.exe")
    )
    assert windows_settings.ffmpeg_min_major == settings.ffmpeg_min_major
    assert windows_settings.required_branch == settings.required_branch


def test_quality_labels_use_exact_order_and_define_1080_fallback():
    assert QUALITY_LABELS == (
        "8K 超高清",
        "杜比视界",
        "HDR 真彩",
        "4K 超清",
        "1080P 高帧率",
        "1080P 高码率",
        "1080P 高清",
    )
    assert "智能修复" not in QUALITY_LABELS
    assert QUALITY_LABELS[:4] == ("8K 超高清", "杜比视界", "HDR 真彩", "4K 超清")
    assert "1080P 高帧率" in QUALITY_LABELS
    assert "1080P 高码率" in QUALITY_LABELS
    assert "1080P 高清" in QUALITY_LABELS
    assert HIGH_1080_LABELS == ("1080P 高帧率", "1080P 高码率")
    assert FALLBACK_1080_LABEL in QUALITY_LABELS
    assert FALLBACK_1080_LABEL == "1080P 高清"


def test_stream_record_signature_ignores_volatile_size_estimates():
    record = StreamRecord(
        quality_label="1080P 高码率",
        resolution="1920x1080",
        codec="avc",
        fps=60.0,
        bitrate_kbps=4500,
        size_text="123.4 MiB",
        size_bytes=987654,
        raw={"volatile": True},
    )
    same_stream_new_estimate = StreamRecord(
        quality_label="1080P 高码率",
        resolution="1920x1080",
        codec="avc",
        fps=60.0,
        bitrate_kbps=4500,
        size_text="123.5 MiB",
        size_bytes=987655,
    )

    assert record.signature() == "1080P 高码率|1920x1080|avc|60.0|4500"
    assert same_stream_new_estimate.signature() == record.signature()
    assert record.to_manifest() == {
        "source": None,
        "index": None,
        "quality_label": "1080P 高码率",
        "resolution": "1920x1080",
        "width": None,
        "height": None,
        "codec": "avc",
        "codec_family": None,
        "fps": 60.0,
        "bitrate_kbps": 4500,
        "bitrate_source": None,
        "size_text": "123.4 MiB",
        "size_bytes": 987654,
        "format_id": None,
        "ext": None,
        "protocol": None,
        "container": None,
    }


def test_download_decision_manifest_omits_unused_null_fields():
    manifest = DownloadDecision(downloader="yt-dlp").to_manifest()

    assert manifest == {
        "downloader": "yt-dlp",
        "stream": None,
        "status": "planned",
        "reason": None,
        "command": None,
    }
