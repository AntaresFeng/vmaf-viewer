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
from vmaf_workflow.remote_transport import (
    RemoteTargetError,
    RemoteTransport,
    RemoteTransportError,
)
from vmaf_workflow.remote_workflow import (
    RemoteCommandError,
    RemoteRunInterrupted,
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


def test_remote_transport_wraps_process_startup_errors(tmp_path: Path) -> None:
    class MissingExecutableRunner:
        def run(self, argv, stdin=None):
            raise FileNotFoundError(argv[0])

        def stream(self, argv, log_path, append=False):
            raise FileNotFoundError(argv[0])

    transport = RemoteTransport(RemoteSettings(), MissingExecutableRunner())
    log_path = tmp_path / "remote.log"

    with pytest.raises(RemoteTransportError, match="failed to start ssh"):
        transport.run_remote("pwd", log_path)
    with pytest.raises(RemoteTransportError, match="failed to start ssh"):
        transport.stream_script(
            PurePosixPath("/home/fzx/vmaf_compare/remote-plan.sh"),
            None,
            log_path,
        )
    with pytest.raises(RemoteTransportError, match="failed to start scp"):
        transport.download(
            PurePosixPath("/home/fzx/vmaf_compare/result.tar.gz"),
            tmp_path / "result.tar.gz",
            log_path,
        )


def test_remote_transport_cleans_partial_upload_on_interrupt(
    tmp_path: Path,
) -> None:
    class InterruptingRunner(RecordingRunner):
        def run(self, argv, stdin=None):
            self.run_calls.append(list(argv))
            if len(self.run_calls) == 1:
                return CommandResult(tuple(argv), 44, "", "")
            return CommandResult(tuple(argv), 0, "", "")

        def stream(self, argv, log_path, append=False):
            self.stream_calls.append((list(argv), log_path, append))
            raise KeyboardInterrupt

    runner = InterruptingRunner()
    transport = RemoteTransport(RemoteSettings(), runner)
    local_path = tmp_path / "video0-inputs.tar"
    local_path.write_bytes(b"package")

    with pytest.raises(KeyboardInterrupt):
        transport.upload_atomic(
            local_path,
            PurePosixPath("/home/fzx/vmaf_compare/video0-inputs.tar"),
            "a" * 64,
            tmp_path / "upload.log",
        )

    assert len(runner.run_calls) == 2
    assert "rm -f --" in runner.run_calls[-1][-1]
    assert ".uploading-" in runner.run_calls[-1][-1]


def test_remote_transport_cleans_partial_download_on_interrupt(
    tmp_path: Path,
) -> None:
    class InterruptingRunner(RecordingRunner):
        def stream(self, argv, log_path, append=False):
            self.stream_calls.append((list(argv), log_path, append))
            Path(argv[-1]).write_bytes(b"partial")
            raise KeyboardInterrupt

    transport = RemoteTransport(RemoteSettings(), InterruptingRunner())
    local_path = tmp_path / ".result.download-test"

    with pytest.raises(KeyboardInterrupt):
        transport.download(
            PurePosixPath("/home/fzx/vmaf_compare/result.tar.gz"),
            local_path,
            tmp_path / "fetch.log",
        )

    assert not local_path.exists()


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

    assert transport.uploaded == [
        "remote-plan.sh",
        "vmaf-workflow-provenance.json",
    ]
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

    assert transport.uploaded == [
        "remote-plan.sh",
        "vmaf-workflow-provenance.json",
        "video0-inputs.tar",
    ]
    assert transport.script_arguments == [
        "--environment-only",
        "--preflight-only",
    ]
    assert state["upload"]["status"] == "completed"
    assert state["remote"]["host"] == "3080"
    assert state["remote"]["base_work_dir"] == "/home/fzx/vmaf_compare"
    assert state["upload"]["package"]["transferred"] is True
    assert state["upload"]["script"]["transferred"] is True
    assert state["upload"]["provenance"]["transferred"] is True
    provenance = json.loads(
        project.remote_provenance_path.read_text(encoding="utf-8")
    )
    assert provenance == {
        "schema_version": 1,
        "project": "video0",
        "plan_sha256": state["plan"]["sha256"],
        "package_sha256": state["upload"]["package"]["sha256"],
        "script_sha256": state["upload"]["script"]["sha256"],
    }
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    assert manifest["remote_workflow"] == {
        "state": str(project.remote_state_path)
    }


def test_upload_uses_project_and_plan_hash_isolated_remote_directory(
    tmp_path: Path,
) -> None:
    project = _write_remote_project(tmp_path)
    plan_sha256 = sha256_file(project.remote_plan_path)
    transport = UploadFakeTransport()

    state = upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )

    expected_work_dir = (
        f"/home/fzx/vmaf_compare/video0/{plan_sha256}"
    )
    assert state["remote"] == {
        "host": "3080",
        "base_work_dir": "/home/fzx/vmaf_compare",
        "work_dir": expected_work_dir,
    }
    assert state["upload"]["script"]["remote_path"] == (
        f"{expected_work_dir}/remote-plan.sh"
    )
    assert state["upload"]["package"]["remote_path"] == (
        f"{expected_work_dir}/video0-inputs.tar"
    )


@pytest.mark.parametrize("artifact", ["script", "package", "provenance"])
def test_run_rechecks_remote_input_hashes_before_preflight(
    tmp_path: Path,
    artifact: str,
) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    state = load_remote_state(project.remote_state_path)
    remote_path = state["upload"][artifact]["remote_path"]
    transport.hashes[remote_path] = "f" * 64
    previous_arguments = list(transport.script_arguments)

    with pytest.raises(RemoteCommandError, match="remote SHA-256 mismatch"):
        run_remote_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    assert transport.script_arguments == previous_arguments
    failed_state = load_remote_state(project.remote_state_path)
    assert failed_state["run"]["status"] == "failed"
    assert failed_state["run"]["stage"] == "verify-inputs"


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
    uploaded_state = load_remote_state(project.remote_state_path)
    result_path = (
        PurePosixPath(uploaded_state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
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


def test_upload_interrupt_is_recorded(tmp_path: Path) -> None:
    project = _write_remote_project(tmp_path)
    transport = UploadFakeTransport()
    original_upload = transport.upload_atomic

    def interrupt_package(
        local_path,
        remote_path,
        expected_sha256,
        log_path,
    ):
        if remote_path.name == "video0-inputs.tar":
            raise KeyboardInterrupt
        return original_upload(
            local_path,
            remote_path,
            expected_sha256,
            log_path,
        )

    transport.upload_atomic = interrupt_package

    with pytest.raises(RemoteRunInterrupted):
        upload_project(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    state = load_remote_state(project.remote_state_path)
    assert state["upload"]["status"] == "interrupted"
    assert state["upload"]["stage"] == "upload-package"
    assert state["upload"]["returncode"] == 130


def test_fetch_interrupt_is_recorded_and_temp_archive_is_removed(
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
    state = load_remote_state(project.remote_state_path)
    remote_result = (
        PurePosixPath(state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
    transport.hashes[remote_result.as_posix()] = "a" * 64

    def interrupt_download(
        remote_path,
        local_path,
        log_path,
    ):
        local_path.write_bytes(b"partial")
        raise KeyboardInterrupt

    transport.download = interrupt_download

    with pytest.raises(RemoteRunInterrupted):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    interrupted_state = load_remote_state(project.remote_state_path)
    assert interrupted_state["fetch"]["status"] == "interrupted"
    assert interrupted_state["fetch"]["stage"] == "download"
    assert interrupted_state["fetch"]["returncode"] == 130
    assert list(project.workflow_dir.glob("*.download-*")) == []


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
    uploaded_state = load_remote_state(project.remote_state_path)
    remote_result = (
        PurePosixPath(uploaded_state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
    archive_path = tmp_path / "remote-result.tar.gz"
    _write_result_archive(
        archive_path,
        _with_provenance(
            project,
            {
                "video0/dist_vmaf.json": {
                    "pooled_metrics": {"vmaf": {"mean": 95}}
                }
            },
        ),
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


def test_fetch_rejects_existing_remote_archive_without_provenance(
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
    state = load_remote_state(project.remote_state_path)
    remote_result = PurePosixPath(state["remote"]["work_dir"]) / (
        "video0-json.tar.gz"
    )
    archive_path = tmp_path / "legacy-result.tar.gz"
    _write_result_archive(
        archive_path,
        {"video0/dist_vmaf.json": {"pooled_metrics": {"vmaf": {"mean": 95}}}},
    )
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    with pytest.raises(RemoteWorkflowError, match="provenance"):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )


def test_fetch_rejects_result_provenance_for_different_plan(
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
    state = load_remote_state(project.remote_state_path)
    remote_result = PurePosixPath(state["remote"]["work_dir"]) / (
        "video0-json.tar.gz"
    )
    archive_path = tmp_path / "stale-result.tar.gz"
    _write_result_archive(
        archive_path,
        {
            "video0/dist_vmaf.json": {"ok": True},
            "vmaf-workflow-provenance.json": {
                "schema_version": 1,
                "project": "video0",
                "plan_sha256": "f" * 64,
                "package_sha256": state["upload"]["package"]["sha256"],
                "script_sha256": state["upload"]["script"]["sha256"],
            },
        },
    )
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    with pytest.raises(RemoteWorkflowError, match="plan SHA-256"):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )


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
    uploaded_state = load_remote_state(project.remote_state_path)
    remote_result = (
        PurePosixPath(uploaded_state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
    archive_path = tmp_path / "bad-result.tar.gz"
    _write_result_archive(
        archive_path,
        _with_provenance(
            project,
            {
                "video0/dist_vmaf.json": {"ok": True},
                "video0/extra_vmaf.json": {"extra": True},
            },
        ),
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
    uploaded_state = load_remote_state(project.remote_state_path)
    remote_result = (
        PurePosixPath(uploaded_state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
    archive_path = tmp_path / "invalid-json.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        content = b"not-json"
        info = tarfile.TarInfo("video0/dist_vmaf.json")
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
        provenance_content = json.dumps(
            _provenance_payload(project)
        ).encode("utf-8")
        provenance_info = tarfile.TarInfo(
            "vmaf-workflow-provenance.json"
        )
        provenance_info.size = len(provenance_content)
        archive.addfile(
            provenance_info,
            io.BytesIO(provenance_content),
        )
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
    uploaded_state = load_remote_state(project.remote_state_path)
    remote_result = (
        PurePosixPath(uploaded_state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
    archive_path = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("video0/dist_vmaf.json")
        info.type = tarfile.SYMTYPE
        info.linkname = "../../escape.json"
        archive.addfile(info)
        provenance_content = json.dumps(
            _provenance_payload(project)
        ).encode("utf-8")
        provenance_info = tarfile.TarInfo(
            "vmaf-workflow-provenance.json"
        )
        provenance_info.size = len(provenance_content)
        archive.addfile(
            provenance_info,
            io.BytesIO(provenance_content),
        )
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
    uploaded_state = load_remote_state(project.remote_state_path)
    result_path = (
        PurePosixPath(uploaded_state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
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


def test_fetch_rolls_back_all_results_when_install_fails_midway(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _write_remote_project(tmp_path)
    _add_second_distorted(project)
    first_result = project.video_dir / "dist_vmaf.json"
    second_result = project.video_dir / "dist2_vmaf.json"
    first_result.write_text('{"old": 1}', encoding="utf-8")
    second_result.write_text('{"old": 2}', encoding="utf-8")
    old_archive = b"old archive"
    project.default_result_archive_path.write_bytes(old_archive)
    transport = UploadFakeTransport()
    upload_project(
        project,
        RemoteSettings(),
        RecordingRunner(),
        transport=transport,
    )
    state = load_remote_state(project.remote_state_path)
    remote_result = (
        PurePosixPath(state["remote"]["work_dir"])
        / "video0-json.tar.gz"
    )
    archive_path = tmp_path / "new-results.tar.gz"
    _write_result_archive(
        archive_path,
        _with_provenance(
            project,
            {
                "video0/dist_vmaf.json": {"new": 1},
                "video0/dist2_vmaf.json": {"new": 2},
            },
        ),
    )
    transport.download_source = archive_path
    transport.hashes[remote_result.as_posix()] = sha256_file(archive_path)

    original_replace = Path.replace

    def fail_second_staged_result(source: Path, target):
        target_path = Path(target)
        if (
            source.parent.name.startswith(".results-staging-")
            and target_path.name == "dist2_vmaf.json"
        ):
            raise PermissionError("simulated file lock")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second_staged_result)

    with pytest.raises(RemoteWorkflowError, match="simulated file lock"):
        fetch_results(
            project,
            RemoteSettings(),
            RecordingRunner(),
            transport=transport,
        )

    assert json.loads(first_result.read_text(encoding="utf-8")) == {"old": 1}
    assert json.loads(second_result.read_text(encoding="utf-8")) == {"old": 2}
    assert project.default_result_archive_path.read_bytes() == old_archive


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
                "result_provenance": "vmaf-workflow-provenance.json",
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


def _add_second_distorted(project: WorkflowProject) -> None:
    inventory = json.loads(
        project.media_inventory_path.read_text(encoding="utf-8")
    )
    second = {
        "path": "dist2.mp4",
        "role": "distorted",
        "size_bytes": 1,
    }
    inventory["files"].append(second)
    project.media_inventory_path.write_text(
        json.dumps(inventory),
        encoding="utf-8",
    )

    package_manifest = json.loads(
        project.package_manifest_path.read_text(encoding="utf-8")
    )
    package_manifest["media_files"].append(second)
    project.package_manifest_path.write_text(
        json.dumps(package_manifest),
        encoding="utf-8",
    )

    plan = json.loads(project.remote_plan_path.read_text(encoding="utf-8"))
    plan["commands"].append(
        {
            "distorted": {"path": "dist2.mp4"},
            "reference": {"path": "ref.mp4"},
            "expected_result": "video0/dist2_vmaf.json",
        }
    )
    plan["expected_results"].append("video0/dist2_vmaf.json")
    project.remote_plan_path.write_text(
        json.dumps(plan),
        encoding="utf-8",
    )


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


def _provenance_payload(project: WorkflowProject) -> dict[str, object]:
    return json.loads(
        project.remote_provenance_path.read_text(encoding="utf-8")
    )


def _with_provenance(
    project: WorkflowProject,
    files: dict[str, object],
) -> dict[str, object]:
    return {
        **files,
        "vmaf-workflow-provenance.json": _provenance_payload(project),
    }
