from pathlib import Path
import tomllib

from vmaf_workflow.config import is_target_format
from vmaf_workflow.models import StreamRecord


def test_pyproject_exposes_workflow_console_script_and_package():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    assert pyproject["project"]["scripts"]["vmaf-workflow"] == "vmaf_workflow.cli:main"
    assert pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/vmaf_viewer",
        "src/vmaf_workflow",
    ]


def test_is_target_format_mirrors_selector_including_protocol():
    target = {
        "height": 1080,
        "vcodec": "avc1.640028",
        "acodec": "none",
    }

    assert is_target_format({**target, "protocol": "https"})
    assert is_target_format({**target, "protocol": "http"})
    # `[protocol^=http]` also matches DASH segments whose protocol starts with http
    assert is_target_format({**target, "protocol": "http_dash_segments"})
    # m3u8/HLS streams do not start with http and must be excluded
    assert not is_target_format({**target, "protocol": "m3u8"})
    assert not is_target_format({**target, "protocol": "m3u8_native"})
    assert not is_target_format({**target, "protocol": "rtmp"})
    assert not is_target_format({**target, "protocol": None})
    assert not is_target_format({**target, "protocol": "https", "height": 720})
    assert not is_target_format(
        {**target, "protocol": "https", "acodec": "mp4a"}
    )


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
