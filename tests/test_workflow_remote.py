from __future__ import annotations

import json
import io
import shutil
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from vmaf_workflow.config import RemoteSettings
from vmaf_workflow.models import CommandResult
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_state import (
    load_remote_state,
    sha256_file,
    write_remote_state,
)
from vmaf_workflow.remote_transport import RemoteTargetError, RemoteTransport
from vmaf_workflow.remote_workflow import (
    RemoteCommandError,
    RemoteWorkflowError,
    fetch_results,
    run_remote_project,
    upload_project,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.run_calls: list[list[str]] = []
        self.stream_calls: list[tuple[list[str], Path, bool]] = []

    def run(self, argv, stdin=None):
        self.run_calls.append(list(argv))
        return CommandResult(tuple(argv), 0, "", "")

    def stream(self, argv, log_path, append=False):
        self.stream_calls.append((list(argv), log_path, append))
        return 0


class QueueRunner(RecordingRunner):
    def __init__(self, run_results: list[CommandResult]) -> None:
        super().__init__()
        self.run_results = list(run_results)

    def run(self, argv, stdin=None):
        self.run_calls.append(list(argv))
        return self.run_results.pop(0)


def test_remote_transport_builds_ssh_and_scp_argv_for_host_alias() -> None:
    runner = RecordingRunner()
    transport = RemoteTransport(
        RemoteSettings(
            host="3080",
            work_dir=PurePosixPath("/home/fzx/vmaf_compare"),
        ),
        runner,
    )

    assert transport.ssh_argv("pwd") == [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=30",
        "3080",
        "pwd",
    ]
    assert transport.scp_upload_argv(
        Path("video11-inputs.tar"),
        PurePosixPath("/home/fzx/vmaf_compare/video11-inputs.tar"),
    ) == [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=30",
        "video11-inputs.tar",
        "3080:/home/fzx/vmaf_compare/video11-inputs.tar",
    ]
    assert "3080" not in {"-p", "-P"}


@pytest.mark.parametrize(
    ("host", "work_dir"),
    [
        ("", PurePosixPath("/home/fzx/vmaf_compare")),
        ("-bad", PurePosixPath("/home/fzx/vmaf_compare")),
        ("bad host", PurePosixPath("/home/fzx/vmaf_compare")),
        ("3080", PurePosixPath("relative")),
        ("3080", PurePosixPath("/home/fzx/../escape")),
    ],
)
def test_remote_transport_rejects_unsafe_targets(
    host: str,
    work_dir: PurePosixPath,
) -> None:
    with pytest.raises(RemoteTargetError):
        RemoteTransport(
            RemoteSettings(host=host, work_dir=work_dir),
            RecordingRunner(),
        )


def test_remote_transport_skips_upload_when_sha256_matches(
    tmp_path: Path,
) -> None:
    expected = "a" * 64
    remote_path = PurePosixPath("/home/fzx/vmaf_compare/remote-plan.sh")
    result = CommandResult(
        ("ssh",),
        0,
        f"{expected}  {remote_path.as_posix()}\n",
        "",
    )
    runner = QueueRunner([result])
    transport = RemoteTransport(RemoteSettings(), runner)
    local_path = tmp_path / "remote-plan.sh"
    local_path.write_text("script", encoding="utf-8")

    transferred = transport.upload_atomic(
        local_path,
        remote_path,
        expected,
        tmp_path / "upload.log",
    )

    assert transferred is False
    assert runner.stream_calls == []


def test_remote_state_write_is_atomic_and_hashes_files(tmp_path: Path) -> None:
    state_path = tmp_path / ".workflow" / "remote-state.json"
    payload_path = tmp_path / "payload.bin"
    payload_path.write_bytes(b"payload")
    state = {
        "schema_version": 1,
        "project": "video0",
        "updated_at": "2026-07-16T00:00:00+00:00",
    }

    write_remote_state(state_path, state)

    assert load_remote_state(state_path)["project"] == "video0"
    assert not state_path.with_name("remote-state.json.tmp").exists()
    assert sha256_file(payload_path) == (
        "239f59ed55e737c77147cf55ad0c1b030b6d7ee748a7426952f9b852d5a935e5"
    )


class UploadFakeTransport:
    def __init__(
        self,
        environment_returncode: int = 0,
        run_returncode: int = 0,
    ) -> None:
        self.environment_returncode = environment_returncode
        self.run_returncode = run_returncode
        self.uploaded: list[str] = []
        self.script_arguments: list[str] = []
        self.hashes: dict[str, str] = {}
        self.download_source: Path | None = None

    def ensure_work_dir(self, log_path: Path) -> None:
        pass

    def upload_atomic(
        self,
        local_path: Path,
        remote_path: PurePosixPath,
        expected_sha256: str,
        log_path: Path,
    ) -> bool:
        self.uploaded.append(remote_path.name)
        self.hashes[remote_path.as_posix()] = expected_sha256
        return True

    def stream_script(
        self,
        script_path: PurePosixPath,
        argument: str | None,
        log_path: Path,
        append: bool = True,
    ) -> int:
        if argument is not None:
            self.script_arguments.append(argument)
        if argument == "--environment-only":
            return self.environment_returncode
        return 0

    def remote_sha256(
        self,
        remote_path: PurePosixPath,
        log_path: Path,
    ) -> str | None:
        return self.hashes.get(remote_path.as_posix())

    def stream_run(
        self,
        script_path: PurePosixPath,
        log_path: Path,
        append: bool = True,
    ) -> int:
        return self.run_returncode

    def download(
        self,
        remote_path: PurePosixPath,
        local_path: Path,
        log_path: Path,
    ) -> None:
        assert self.download_source is not None
        shutil.copyfile(self.download_source, local_path)


def test_upload_stops_before_package_when_environment_preflight_fails(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport(environment_returncode=9)

    with pytest.raises(RemoteCommandError, match="environment preflight"):
        upload_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    assert transport.uploaded == ["remote-plan.sh"]
    state = load_remote_state(project.remote_state_path)
    assert state["upload"]["status"] == "failed"
    assert state["upload"]["stage"] == "environment-preflight"


def test_upload_completes_and_records_remote_target_and_hashes(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()

    state = upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )

    assert transport.uploaded == ["remote-plan.sh", "video0-inputs.tar"]
    assert transport.script_arguments == [
        "--environment-only",
        "--preflight-only",
    ]
    assert state["upload"]["status"] == "completed"
    assert state["remote"] == {
        "host": "3080",
        "work_dir": "/home/fzx/vmaf_compare",
    }
    assert state["upload"]["package"]["transferred"] is True
    assert state["upload"]["script"]["transferred"] is True
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    assert manifest["remote_workflow"] == {
        "state": str(project.remote_state_path)
    }


def test_upload_rejects_remote_plan_that_does_not_match_inventory(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    plan = json.loads(project.remote_plan_path.read_text(encoding="utf-8"))
    plan["commands"][0]["distorted"]["path"] = "stale.mp4"
    project.remote_plan_path.write_text(json.dumps(plan), encoding="utf-8")

    with pytest.raises(RemoteWorkflowError, match="distorted"):
        upload_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=UploadFakeTransport(),
        )


def test_run_streams_preflight_and_script_then_records_result_hash(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    result_path = PurePosixPath("/home/fzx/vmaf_compare/video0-json.tar.gz")
    transport.hashes[result_path.as_posix()] = "a" * 64

    state = run_remote_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )

    assert transport.script_arguments[-1] == "--preflight-only"
    assert state["run"]["status"] == "completed"
    assert state["run"]["returncode"] == 0
    assert state["run"]["result"] == {
        "remote_path": result_path.as_posix(),
        "sha256": "a" * 64,
    }


def test_run_failure_is_recorded(tmp_path: Path) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport(run_returncode=17)
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )

    with pytest.raises(RemoteCommandError, match="exit code 17"):
        run_remote_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    state = load_remote_state(project.remote_state_path)
    assert state["run"]["status"] == "failed"
    assert state["run"]["returncode"] == 17


def test_run_rejects_plan_drift_after_upload(tmp_path: Path) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    project.remote_plan_path.write_text(
        project.remote_plan_path.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RemoteWorkflowError, match="changed"):
        run_remote_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )


def test_run_interrupt_is_recorded(tmp_path: Path) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    transport.stream_run = interrupt

    with pytest.raises(KeyboardInterrupt):
        run_remote_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    state = load_remote_state(project.remote_state_path)
    assert state["run"]["status"] == "interrupted"
    assert state["run"]["returncode"] == 130


def test_fetch_accepts_existing_remote_results_and_installs_json(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    remote_result = PurePosixPath(
        "/home/fzx/vmaf_compare/video0-json.tar.gz"
    )
    archive_path = tmp_path / "remote-result.tar.gz"
    _write_result_archive(
        archive_path,
        {"video0/dist_vmaf.json": {"pooled_metrics": {"vmaf": {"mean": 95}}}},
    )
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    state = fetch_results(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )

    installed = project.video_dir / "dist_vmaf.json"
    assert json.loads(installed.read_text(encoding="utf-8"))[
        "pooled_metrics"
    ]["vmaf"]["mean"] == 95
    assert project.default_result_archive_path.read_bytes() == (
        archive_path.read_bytes()
    )
    assert state["fetch"]["status"] == "completed"
    assert state["fetch"]["source"] == "existing-remote"
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    assert manifest["results"]["archive"] == str(
        project.default_result_archive_path
    )
    assert manifest["results"]["files"] == [str(installed)]


def test_fetch_rejects_archive_with_extra_member_without_replacing_files(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    existing_result = project.video_dir / "dist_vmaf.json"
    existing_result.write_text('{"old": true}', encoding="utf-8")
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    remote_result = PurePosixPath(
        "/home/fzx/vmaf_compare/video0-json.tar.gz"
    )
    archive_path = tmp_path / "bad-result.tar.gz"
    _write_result_archive(
        archive_path,
        {
            "video0/dist_vmaf.json": {"ok": True},
            "video0/extra_vmaf.json": {"extra": True},
        },
    )
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    with pytest.raises(RemoteWorkflowError, match="members"):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    assert json.loads(existing_result.read_text(encoding="utf-8")) == {
        "old": True
    }
    assert not project.default_result_archive_path.exists()
    state = load_remote_state(project.remote_state_path)
    assert state["fetch"]["status"] == "failed"


def test_fetch_rejects_invalid_json(tmp_path: Path) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    remote_result = PurePosixPath(
        "/home/fzx/vmaf_compare/video0-json.tar.gz"
    )
    archive_path = tmp_path / "invalid-json.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        content = b"not-json"
        info = tarfile.TarInfo("video0/dist_vmaf.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    with pytest.raises(RemoteWorkflowError):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )


def test_fetch_rejects_symbolic_link_member(tmp_path: Path) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    remote_result = PurePosixPath(
        "/home/fzx/vmaf_compare/video0-json.tar.gz"
    )
    archive_path = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("video0/dist_vmaf.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../escape.json"
        archive.addfile(info)
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    with pytest.raises(RemoteWorkflowError, match="regular file"):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )


def test_fetch_rejects_remote_hash_drift_after_completed_run(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    result_path = PurePosixPath("/home/fzx/vmaf_compare/video0-json.tar.gz")
    transport.hashes[result_path.as_posix()] = "a" * 64
    run_remote_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    transport.hashes[result_path.as_posix()] = "b" * 64

    with pytest.raises(RemoteCommandError, match="differs"):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    assert transport.download_source is None


def _write_remote_project(tmp_path: Path) -> WorkflowProject:
    video_dir = tmp_path / "video0"
    workflow_dir = video_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    project = WorkflowProject(video_dir=video_dir, workflow_dir=workflow_dir)
    package_path = workflow_dir / "video0-inputs.tar"
    package_path.write_bytes(b"package")
    project.remote_plan_script_path.write_text(
        "#!/usr/bin/env bash\n",
        encoding="utf-8",
        newline="\n",
    )
    project.media_inventory_path.write_text(
        json.dumps(
            {
                "files": [
                    {"path": "ref.mp4", "role": "reference", "size_bytes": 1},
                    {"path": "dist.mp4", "role": "distorted", "size_bytes": 1},
                ]
            }
        ),
        encoding="utf-8",
    )
    project.package_manifest_path.write_text(
        json.dumps(
            {
                "archive_path": str(package_path),
                "archive_root": "video0",
                "media_files": [
                    {"path": "ref.mp4", "role": "reference", "size_bytes": 1},
                    {"path": "dist.mp4", "role": "distorted", "size_bytes": 1},
                ],
            }
        ),
        encoding="utf-8",
    )
    project.remote_plan_path.write_text(
        json.dumps(
            {
                "created_at": "2026-07-16T00:00:00+00:00",
                "package_archive": "video0-inputs.tar",
                "result_archive": "video0-json.tar.gz",
                "environment_preflight_argument": "--environment-only",
                "preflight_argument": "--preflight-only",
                "reference": {"path": "ref.mp4"},
                "commands": [
                    {
                        "distorted": {"path": "dist.mp4"},
                        "reference": {"path": "ref.mp4"},
                        "expected_result": "video0/dist_vmaf.json",
                    }
                ],
                "expected_results": ["video0/dist_vmaf.json"],
            }
        ),
        encoding="utf-8",
    )
    project.manifest_path.write_text(
        json.dumps({"keep": "existing"}),
        encoding="utf-8",
    )
    return project


def _write_result_archive(
    path: Path,
    files: dict[str, object],
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in files.items():
            content = json.dumps(payload).encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
