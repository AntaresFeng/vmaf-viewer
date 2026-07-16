from __future__ import annotations

import re
import shlex
import uuid
from pathlib import Path, PurePosixPath

from vmaf_workflow.config import RemoteSettings
from vmaf_workflow.models import CommandResult


class RemoteTargetError(ValueError):
    pass


class RemoteTransportError(RuntimeError):
    pass


class RemoteTransport:
    def __init__(self, settings: RemoteSettings, runner) -> None:
        _validate_target(settings)
        self.settings = settings
        self.runner = runner

    @property
    def work_dir(self) -> PurePosixPath:
        return self.settings.work_dir

    def ssh_argv(self, remote_command: str) -> list[str]:
        return [
            str(self.settings.ssh_executable),
            *self._connection_options(),
            self.settings.host,
            remote_command,
        ]

    def scp_upload_argv(
        self,
        local_path: Path,
        remote_path: PurePosixPath,
    ) -> list[str]:
        return [
            str(self.settings.scp_executable),
            *self._connection_options(),
            str(local_path),
            f"{self.settings.host}:{remote_path.as_posix()}",
        ]

    def scp_download_argv(
        self,
        remote_path: PurePosixPath,
        local_path: Path,
    ) -> list[str]:
        return [
            str(self.settings.scp_executable),
            *self._connection_options(),
            f"{self.settings.host}:{remote_path.as_posix()}",
            str(local_path),
        ]

    def ensure_work_dir(self, log_path: Path) -> None:
        result = self.run_remote(
            f"mkdir -p -- {_quote(self.work_dir.as_posix())}",
            log_path,
        )
        if result.returncode != 0:
            raise RemoteTransportError("failed to create remote work directory")

    def remote_sha256(
        self,
        remote_path: PurePosixPath,
        log_path: Path,
    ) -> str | None:
        quoted = _quote(remote_path.as_posix())
        result = self.run_remote(
            f"if [ -f {quoted} ]; then sha256sum -- {quoted}; else exit 44; fi",
            log_path,
        )
        if result.returncode == 44:
            return None
        if result.returncode != 0:
            raise RemoteTransportError(
                f"failed to read remote SHA-256: {remote_path}"
            )
        match = re.match(r"^([0-9a-fA-F]{64})(?:\s|$)", result.stdout.strip())
        if match is None:
            raise RemoteTransportError(
                f"remote SHA-256 output is invalid: {remote_path}"
            )
        return match.group(1).lower()

    def upload_atomic(
        self,
        local_path: Path,
        remote_path: PurePosixPath,
        expected_sha256: str,
        log_path: Path,
    ) -> bool:
        if self.remote_sha256(remote_path, log_path) == expected_sha256:
            return False

        temp_path = remote_path.with_name(
            f".{remote_path.name}.uploading-{uuid.uuid4().hex}"
        )
        returncode = self.runner.stream(
            self.scp_upload_argv(local_path, temp_path),
            log_path,
            append=True,
        )
        if returncode != 0:
            raise RemoteTransportError(f"failed to upload: {local_path}")

        temp_sha256 = self.remote_sha256(temp_path, log_path)
        if temp_sha256 != expected_sha256:
            self.run_remote(f"rm -f -- {_quote(temp_path.as_posix())}", log_path)
            raise RemoteTransportError(
                f"uploaded SHA-256 mismatch: {remote_path}"
            )

        result = self.run_remote(
            f"mv -f -- {_quote(temp_path.as_posix())} "
            f"{_quote(remote_path.as_posix())}",
            log_path,
        )
        if result.returncode != 0:
            raise RemoteTransportError(f"failed to install remote file: {remote_path}")
        return True

    def stream_script(
        self,
        script_path: PurePosixPath,
        argument: str | None,
        log_path: Path,
        append: bool = True,
    ) -> int:
        argv = ["bash", script_path.name]
        if argument is not None:
            argv.append(argument)
        remote_command = (
            f"cd {_quote(script_path.parent.as_posix())} && "
            + " ".join(_quote(value) for value in argv)
        )
        return self.runner.stream(
            self.ssh_argv(remote_command),
            log_path,
            append=append,
        )

    def stream_run(
        self,
        script_path: PurePosixPath,
        log_path: Path,
        append: bool = True,
    ) -> int:
        return self.stream_script(script_path, None, log_path, append=append)

    def download(
        self,
        remote_path: PurePosixPath,
        local_path: Path,
        log_path: Path,
    ) -> None:
        returncode = self.runner.stream(
            self.scp_download_argv(remote_path, local_path),
            log_path,
            append=True,
        )
        if returncode != 0:
            raise RemoteTransportError(f"failed to download: {remote_path}")

    def run_remote(self, remote_command: str, log_path: Path) -> CommandResult:
        result = self.runner.run(self.ssh_argv(remote_command))
        _append_result(log_path, result)
        return result

    def _connection_options(self) -> list[str]:
        return [
            "-o",
            "BatchMode=yes",
            "-o",
            f"ConnectTimeout={self.settings.connect_timeout_seconds}",
            "-o",
            (
                "ServerAliveInterval="
                f"{self.settings.server_alive_interval_seconds}"
            ),
        ]


def _validate_target(settings: RemoteSettings) -> None:
    host = settings.host
    if (
        not host
        or host.startswith("-")
        or any(character.isspace() or ord(character) < 32 for character in host)
    ):
        raise RemoteTargetError(f"invalid SSH host: {host!r}")
    work_dir = settings.work_dir
    raw_work_dir = work_dir.as_posix()
    if (
        not work_dir.is_absolute()
        or ".." in work_dir.parts
        or "\\" in raw_work_dir
        or any(ord(character) < 32 for character in raw_work_dir)
    ):
        raise RemoteTargetError(f"invalid remote work directory: {work_dir}")
    if settings.connect_timeout_seconds < 1:
        raise RemoteTargetError("connect timeout must be greater than 0")
    if settings.server_alive_interval_seconds < 1:
        raise RemoteTargetError("server alive interval must be greater than 0")


def _append_result(log_path: Path, result: CommandResult) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", newline="") as log_file:
        if result.stdout:
            log_file.write(result.stdout)
        if result.stderr:
            log_file.write(result.stderr)


def _quote(value: str) -> str:
    return shlex.quote(value)
