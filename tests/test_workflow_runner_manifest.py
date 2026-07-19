from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import vmaf_workflow.runner as runner_module
from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.models import CommandResult
from vmaf_workflow.runner import ProcessInterrupted, SubprocessRunner


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


def test_subprocess_runner_can_stream_and_preserve_separate_output(
    monkeypatch,
) -> None:
    events = []

    class Pipe:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def read1(self, _size):
            return self.chunks.pop(0)

    class Stdin:
        def __init__(self):
            self.value = b""
            self.closed = False

        def write(self, value):
            self.value += value

        def close(self):
            self.closed = True

    class FakeProcess:
        stdout = Pipe([b"out-1", b"out-2", b""])
        stderr = Pipe([b"err", b""])
        stdin = Stdin()

        def wait(self, timeout=None):
            assert timeout is None
            return 9

    process = FakeProcess()
    calls = []

    def fake_popen(*_args, **kwargs):
        calls.append(kwargs)
        return process

    monkeypatch.setattr("vmaf_workflow.runner.subprocess.Popen", fake_popen)

    runner = SubprocessRunner(
        lambda stream, text: events.append((stream, text)),
        mirror_console=False,
        inherit_stdin=False,
    )
    result = runner.run(["tool", "arg"], stdin="选择\n")

    assert result == CommandResult(
        ("tool", "arg"),
        9,
        stdout="out-1out-2",
        stderr="err",
        stdin="选择\n",
    )
    assert process.stdin.value == "选择\n".encode()
    assert process.stdin.closed is True
    assert calls[0]["stdin"] == subprocess.PIPE
    assert sorted(events) == sorted(
        [("stdout", "out-1"), ("stdout", "out-2"), ("stderr", "err")]
    )


def test_subprocess_runner_detaches_terminal_input_for_callback_commands(
    monkeypatch,
) -> None:
    calls = []

    class Pipe:
        def read1(self, _size):
            return b""

    class FakeProcess:
        stdout = Pipe()
        stderr = Pipe()
        stdin = None

        def wait(self, timeout=None):
            assert timeout is None
            return 0

    def fake_popen(*_args, **kwargs):
        calls.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr("vmaf_workflow.runner.subprocess.Popen", fake_popen)

    result = SubprocessRunner(
        lambda *_args: None,
        mirror_console=False,
        inherit_stdin=False,
    ).run(["ssh", "3080", "command"])

    assert result.returncode == 0
    assert calls[0]["stdin"] == subprocess.DEVNULL


def test_subprocess_runner_decodes_callback_output_with_selected_encoding(
    monkeypatch,
) -> None:
    expected = "遇到问题，1080P 高码率\n"
    encoded = expected.encode("cp936")
    events = []

    class Pipe:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def read1(self, _size):
            return self.chunks.pop(0)

    class FakeProcess:
        stdout = Pipe([encoded[:1], encoded[1:7], encoded[7:], b""])
        stderr = Pipe([b""])
        stdin = None

        def wait(self, timeout=None):
            assert timeout is None
            return 0

    monkeypatch.setattr(
        "vmaf_workflow.runner.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    result = SubprocessRunner(
        lambda stream, text: events.append((stream, text)),
        mirror_console=False,
    ).run(["BBDown.exe", "-info"], output_encoding="cp936")

    assert result.stdout == expected
    assert "".join(text for stream, text in events if stream == "stdout") == expected


def test_console_output_encoding_uses_available_windows_code_page(monkeypatch) -> None:
    monkeypatch.setattr(
        runner_module,
        "_windows_console_output_code_page",
        lambda: 936,
    )

    assert runner_module.console_output_encoding() == "cp936"


def test_subprocess_runner_external_cancel_interrupts_active_process() -> None:
    ready = threading.Event()
    caught = []
    runner = SubprocessRunner(
        lambda _stream, text: ready.set() if "ready" in text else None,
        mirror_console=False,
    )

    def run_process() -> None:
        try:
            runner.run(
                [
                    sys.executable,
                    "-c",
                    "import time; print('ready', flush=True); time.sleep(30)",
                ]
            )
        except BaseException as exc:
            caught.append(exc)

    thread = threading.Thread(target=run_process)
    thread.start()
    assert ready.wait(timeout=5)
    runner.cancel_current()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert len(caught) == 1
    assert isinstance(caught[0], ProcessInterrupted)


def test_subprocess_runner_preserves_exit_code_after_broken_stdin_pipe(
    monkeypatch,
) -> None:
    class Pipe:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def read1(self, _size):
            return self.chunks.pop(0)

    class BrokenStdin:
        closed = False

        def write(self, _value):
            raise BrokenPipeError

        def close(self):
            self.closed = True

    class FakeProcess:
        stdout = Pipe([b"partial stdout", b""])
        stderr = Pipe([b"early exit", b""])
        stdin = BrokenStdin()

        def wait(self, timeout=None):
            assert timeout is None
            return 7

    monkeypatch.setattr(
        "vmaf_workflow.runner.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )

    result = SubprocessRunner(lambda *_args: None, mirror_console=False).run(
        ["early-exit"],
        stdin="selection\n",
    )

    assert result.returncode == 7
    assert result.stdout == "partial stdout"
    assert result.stderr == "early exit"
    assert FakeProcess.stdin.closed is True


def test_subprocess_runner_cancels_process_registered_after_request(
    monkeypatch,
) -> None:
    popen_started = threading.Event()
    allow_popen_return = threading.Event()
    terminated = threading.Event()
    caught = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            terminated.set()

        def wait(self, timeout=None):
            assert timeout == 5
            assert terminated.wait(timeout=1)
            return 130

    process = FakeProcess()

    def delayed_popen(*_args, **_kwargs):
        popen_started.set()
        assert allow_popen_return.wait(timeout=1)
        return process

    monkeypatch.setattr("vmaf_workflow.runner.subprocess.Popen", delayed_popen)
    runner = SubprocessRunner(lambda *_args: None, mirror_console=False)

    def invoke() -> None:
        try:
            runner.run(["delayed-registration"])
        except BaseException as exc:
            caught.append(exc)

    thread = threading.Thread(target=invoke)
    thread.start()
    assert popen_started.wait(timeout=1)
    runner.cancel_current()
    allow_popen_return.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert terminated.is_set()
    assert len(caught) == 1
    assert isinstance(caught[0], ProcessInterrupted)


def test_subprocess_runner_kill_fallback_has_second_timeout() -> None:
    waits = []

    class FakeProcess:
        killed = False

        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            waits.append(timeout)
            if len(waits) == 1:
                raise subprocess.TimeoutExpired("tool", timeout)
            raise subprocess.TimeoutExpired("tool", timeout)

    process = FakeProcess()
    SubprocessRunner._terminate_process(process)

    assert process.killed is True
    assert waits == [5, 5]


def test_subprocess_runner_closes_reader_pipe_after_join_timeout(monkeypatch) -> None:
    release = threading.Event()

    class BlockingPipe:
        closed = False

        def read1(self, _size):
            release.wait(timeout=1)
            return b""

        def close(self):
            self.closed = True
            release.set()

    class EmptyPipe:
        def read1(self, _size):
            return b""

        def close(self):
            pass

    class FakeProcess:
        stdout = BlockingPipe()
        stderr = EmptyPipe()
        stdin = None

        def wait(self, timeout=None):
            assert timeout is None
            return 0

    monkeypatch.setattr(
        "vmaf_workflow.runner.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "vmaf_workflow.runner._READER_JOIN_TIMEOUT_SECONDS",
        0.01,
    )

    result = SubprocessRunner(lambda *_args: None, mirror_console=False).run(["tool"])

    assert result.returncode == 0
    assert FakeProcess.stdout.closed is True


def test_subprocess_runner_callback_failure_does_not_change_command_result() -> None:
    def broken_callback(_stream: str, _text: str) -> None:
        raise RuntimeError("UI closed")

    result = SubprocessRunner(broken_callback, mirror_console=False).run(
        [sys.executable, "-c", "print('captured')"]
    )

    assert result.returncode == 0
    assert result.stdout == "captured\r\n" or result.stdout == "captured\n"


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


def test_subprocess_runner_detaches_terminal_input_for_streaming_commands(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []

    class EmptyStdout:
        def read1(self, _size):
            return b""

    class FakeProcess:
        stdout = EmptyStdout()

        def wait(self, timeout=None):
            assert timeout is None
            return 0

    def fake_popen(_argv, **kwargs):
        calls.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr("vmaf_workflow.runner.subprocess.Popen", fake_popen)

    returncode = SubprocessRunner(
        lambda *_args: None,
        mirror_console=False,
        inherit_stdin=False,
    ).stream(["scp", "input", "3080:output"], tmp_path / "upload.log")

    assert returncode == 0
    assert calls[0]["stdin"] == subprocess.DEVNULL


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
