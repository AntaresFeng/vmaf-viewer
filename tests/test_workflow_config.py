from pathlib import Path
import tomllib

import pytest

from vmaf_workflow.config import (
    FALLBACK_1080_LABEL,
    HIGH_1080_LABELS,
    QUALITY_LABELS,
    YTDLP_FORMAT_SELECTOR,
    default_settings,
)
from vmaf_workflow.models import StreamRecord


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
        "all[height>=1080][vcodec!=none][acodec=none]"
    )
    assert YTDLP_FORMAT_SELECTOR == "all[height>=1080][vcodec!=none][acodec=none]"


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


@pytest.mark.parametrize(
    ("size_text", "size_bytes", "expected_size"),
    [
        ("123.4 MiB", 987654, "123.4 MiB"),
        (None, 987654, "987654"),
    ],
)
def test_stream_record_signature_prefers_size_text_then_size_bytes(
    size_text, size_bytes, expected_size
):
    record = StreamRecord(
        quality_label="1080P 高码率",
        resolution="1920x1080",
        codec="avc",
        fps=60.0,
        bitrate_kbps=4500,
        size_text=size_text,
        size_bytes=size_bytes,
        raw={"volatile": True},
    )

    assert record.signature() == f"1080P 高码率|1920x1080|avc|60.0|4500|{expected_size}"
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
        "size_text": size_text,
        "size_bytes": size_bytes,
        "format_id": None,
        "ext": None,
        "protocol": None,
        "container": None,
    }
