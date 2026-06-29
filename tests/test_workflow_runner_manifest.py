from __future__ import annotations

import json

import pytest

from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.models import CommandResult
from vmaf_workflow.runner import SubprocessRunner


def test_subprocess_runner_uses_safe_capture_options(monkeypatch) -> None:
    calls = []

    class CompletedProcess:
        returncode = 23
        stdout = "standard output"
        stderr = "standard error"

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return CompletedProcess()

    monkeypatch.setattr("vmaf_workflow.runner.subprocess.run", fake_run)

    result = SubprocessRunner().run(["yt-dlp", "--dump-json"], stdin="url")

    assert calls == [
        (
            ["yt-dlp", "--dump-json"],
            {
                "input": "url",
                "text": True,
                "capture_output": True,
                "encoding": "utf-8",
                "errors": "replace",
                "shell": False,
                "check": False,
            },
        )
    ]
    assert result == CommandResult(
        ("yt-dlp", "--dump-json"),
        23,
        "standard output",
        "standard error",
        "url",
    )


def test_write_manifest_writes_jq_friendly_nested_json(tmp_path) -> None:
    manifest_path = tmp_path / "nested" / "workflow.json"
    data = {
        "stream": {
            "quality_label": "1080P 高码率",
            "codec": "HEVC",
        },
        "command": CommandResult(
            ("ffmpeg", "-i", "input.mp4"),
            0,
            stdout="done",
            stdin="视频片段",
        ).to_manifest(),
    }

    write_manifest(manifest_path, data)

    text = manifest_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text) == {
        "command": {
            "command": ["ffmpeg", "-i", "input.mp4"],
            "returncode": 0,
            "stderr": "",
            "stdin": "视频片段",
            "stdout": "done",
        },
        "stream": {
            "codec": "HEVC",
            "quality_label": "1080P 高码率",
        },
    }


def test_write_manifest_rejects_non_standard_json_numbers(tmp_path) -> None:
    with pytest.raises(ValueError):
        write_manifest(tmp_path / "manifest.json", {"bad": float("nan")})
