from __future__ import annotations

import json
from pathlib import Path

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


def test_subprocess_runner_streams_stdout_and_writes_log(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    calls = []

    class AvailableStdout:
        def __init__(self) -> None:
            self.chunks = [b"first\r", b"second\n", b""]
            self.read1_calls = 0

        def read1(self, _size):
            self.read1_calls += 1
            return self.chunks.pop(0)

        def read(self, _size):
            raise AssertionError("streaming must not wait on buffered text read")

    class FakeProcess:
        stdout = AvailableStdout()

        def wait(self, timeout=None):
            assert timeout is None
            return 7

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeProcess()

    monkeypatch.setattr("vmaf_workflow.runner.subprocess.Popen", fake_popen)
    log_path = tmp_path / "logs" / "remote.log"

    returncode = SubprocessRunner().stream(
        ["ssh", "3080", "command"],
        log_path,
    )

    captured = capsys.readouterr()
    assert returncode == 7
    assert captured.out == "first\rsecond\n"
    assert log_path.read_text(encoding="utf-8") == "first\nsecond\n"
    assert calls == [
        (
            ["ssh", "3080", "command"],
            {
                "stdout": -1,
                "stderr": -2,
                "text": False,
                "shell": False,
            },
        )
    ]
    assert FakeProcess.stdout.read1_calls == 3


def test_subprocess_runner_terminates_streaming_process_on_interrupt(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class InterruptingStdout:
        def read1(self, _size):
            raise KeyboardInterrupt

    class FakeProcess:
        stdout = InterruptingStdout()
        terminated = False

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            assert self.terminated
            assert timeout == 5
            return 130

    process = FakeProcess()
    monkeypatch.setattr(
        "vmaf_workflow.runner.subprocess.Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(KeyboardInterrupt):
        SubprocessRunner().stream(
            ["ssh", "3080", "command"],
            tmp_path / "remote.log",
        )

    assert process.terminated is True
