from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from vmaf_workflow.cleanup import (
    CleanupExecutionError,
    CleanupStateError,
    cleanup_project,
)
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_state import (
    load_remote_state,
    sha256_file,
    write_remote_state,
)


def test_cleanup_requires_completed_fetch_and_preserves_archives(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    state = load_remote_state(project.remote_state_path)
    state["fetch"]["status"] = "failed"
    write_remote_state(project.remote_state_path, state)

    with pytest.raises(CleanupStateError, match="fetch-results must be completed"):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_requires_completed_upload_and_preserves_archives(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    state = load_remote_state(project.remote_state_path)
    state["upload"]["status"] = "failed"
    write_remote_state(project.remote_state_path, state)

    with pytest.raises(CleanupStateError, match="upload must be completed"):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


@pytest.mark.parametrize(
    ("artifact", "message"),
    [
        ("package", "input archive SHA-256 does not match"),
        ("result", "result archive SHA-256 does not match"),
    ],
)
def test_cleanup_rejects_archive_hash_drift_without_deleting(
    tmp_path: Path,
    artifact: str,
    message: str,
) -> None:
    project = _write_cleanup_project(tmp_path)
    path = (
        project.default_package_path
        if artifact == "package"
        else project.default_result_archive_path
    )
    path.write_bytes(b"changed")

    with pytest.raises(CleanupStateError, match=message):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_rejects_installed_result_size_drift_without_deleting(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    result_path = project.video_dir / "dist_vmaf.json"
    result_path.write_text('{"changed": true}', encoding="utf-8")

    with pytest.raises(CleanupStateError, match="installed result size changed"):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_rejects_same_size_installed_result_content_drift(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    result_path = project.video_dir / "dist_vmaf.json"
    result_path.write_bytes(b"x" * result_path.stat().st_size)

    with pytest.raises(CleanupStateError, match="content does not match archive"):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_rejects_same_size_input_media_content_drift(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    distorted = project.video_dir / "dist.mp4"
    distorted.write_bytes(b"x" * distorted.stat().st_size)

    with pytest.raises(CleanupStateError, match="media content does not match"):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_rejects_package_manifest_drift_from_input_archive(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    package_manifest = json.loads(
        project.package_manifest_path.read_text(encoding="utf-8")
    )
    package_manifest["media_files"].pop()
    project.package_manifest_path.write_text(
        json.dumps(package_manifest),
        encoding="utf-8",
    )

    with pytest.raises(
        CleanupStateError,
        match="package manifest does not match input archive snapshot",
    ):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_rejects_manifest_result_coverage_drift_without_deleting(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    manifest["results"]["files"] = []
    project.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CleanupStateError, match="manifest result files do not match"):
        cleanup_project(project)

    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_deletes_only_archives_and_records_history(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    result_path = project.video_dir / "dist_vmaf.json"
    package_sha256 = sha256_file(project.default_package_path)
    result_sha256 = sha256_file(project.default_result_archive_path)
    package_size = project.default_package_path.stat().st_size
    result_size = project.default_result_archive_path.stat().st_size

    state = cleanup_project(project)

    assert not project.default_package_path.exists()
    assert not project.default_result_archive_path.exists()
    assert result_path.is_file()
    assert project.remote_plan_path.is_file()
    assert state["cleanup"]["status"] == "completed"
    assert state["cleanup"]["last_reclaimed_bytes"] == package_size + result_size
    assert state["cleanup"]["archives"]["package"] == {
        "path": str(project.default_package_path),
        "sha256": package_sha256,
        "size_bytes": package_size,
        "deleted": True,
    }
    assert state["cleanup"]["archives"]["result"] == {
        "path": str(project.default_result_archive_path),
        "sha256": result_sha256,
        "size_bytes": result_size,
        "deleted": True,
    }

    manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    assert manifest["package"]["path"] is None
    assert manifest["package"]["archive_cleanup"]["sha256"] == package_sha256
    assert manifest["results"]["archive"] is None
    assert manifest["results"]["archive_cleanup"]["sha256"] == result_sha256
    assert manifest["results"]["files"] == [str(result_path)]


def test_cleanup_is_noop_after_completed_cleanup(tmp_path: Path) -> None:
    project = _write_cleanup_project(tmp_path)
    cleanup_project(project)

    second_state = cleanup_project(project)

    assert second_state["cleanup"]["status"] == "completed"
    assert second_state["cleanup"]["last_reclaimed_bytes"] == 0


def test_cleanup_deletes_result_recreated_after_completed_cleanup(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    result_archive = project.default_result_archive_path.read_bytes()
    cleanup_project(project)
    first_manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    package_cleanup = first_manifest["package"]["archive_cleanup"]
    project.default_result_archive_path.write_bytes(result_archive)
    manifest = first_manifest
    manifest["results"]["archive"] = str(project.default_result_archive_path)
    project.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    state = cleanup_project(project)

    assert not project.default_package_path.exists()
    assert not project.default_result_archive_path.exists()
    assert state["cleanup"]["last_reclaimed_bytes"] == len(result_archive)
    second_manifest = json.loads(project.manifest_path.read_text(encoding="utf-8"))
    assert second_manifest["package"]["archive_cleanup"] == package_cleanup


def test_cleanup_rejects_custom_package_output_without_deleting(
    tmp_path: Path,
) -> None:
    project = _write_cleanup_project(tmp_path)
    custom_package = tmp_path / "custom-inputs.tar"
    project.default_package_path.replace(custom_package)
    state = load_remote_state(project.remote_state_path)
    state["upload"]["package"]["local_path"] = str(custom_package)
    write_remote_state(project.remote_state_path, state)
    package_manifest = json.loads(
        project.package_manifest_path.read_text(encoding="utf-8")
    )
    package_manifest["archive_path"] = str(custom_package)
    project.package_manifest_path.write_text(
        json.dumps(package_manifest),
        encoding="utf-8",
    )

    with pytest.raises(
        CleanupStateError,
        match="cleanup only supports the default input archive",
    ):
        cleanup_project(project)

    assert custom_package.is_file()
    assert project.default_result_archive_path.is_file()


def test_cleanup_rolls_back_when_second_archive_cannot_be_staged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _write_cleanup_project(tmp_path)
    original_replace = Path.replace

    def fail_result_staging(path: Path, target: Path):
        if path == project.default_result_archive_path:
            raise PermissionError("file is locked")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_result_staging)

    with pytest.raises(CleanupExecutionError, match="stage-result failed"):
        cleanup_project(project)

    state = load_remote_state(project.remote_state_path)
    assert state["cleanup"]["status"] == "failed"
    assert project.default_package_path.is_file()
    assert project.default_result_archive_path.is_file()
    assert list(project.workflow_dir.glob("*.cleanup-*")) == []


def test_cleanup_resumes_pending_staged_deletion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _write_cleanup_project(tmp_path)
    original_unlink = Path.unlink
    failed_once = False

    def fail_one_staged_unlink(path: Path, *args, **kwargs) -> None:
        nonlocal failed_once
        if ".cleanup-" in path.name and not failed_once:
            failed_once = True
            raise PermissionError("staged file is locked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_one_staged_unlink)

    with pytest.raises(CleanupExecutionError, match="delete-staged"):
        cleanup_project(project)

    pending = load_remote_state(project.remote_state_path)
    assert pending["cleanup"]["status"] == "pending"
    assert not project.default_package_path.exists()
    assert not project.default_result_archive_path.exists()
    assert list(project.workflow_dir.glob("*.cleanup-*"))

    monkeypatch.setattr(Path, "unlink", original_unlink)
    completed = cleanup_project(project)

    assert completed["cleanup"]["status"] == "completed"
    assert list(project.workflow_dir.glob("*.cleanup-*")) == []


def test_cleanup_rejects_unsafe_pending_archive_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project = _write_cleanup_project(tmp_path)
    original_unlink = Path.unlink

    def fail_staged_unlink(path: Path, *args, **kwargs) -> None:
        if ".cleanup-" in path.name:
            raise PermissionError("staged file is locked")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_staged_unlink)
    with pytest.raises(CleanupExecutionError):
        cleanup_project(project)
    monkeypatch.setattr(Path, "unlink", original_unlink)

    important = tmp_path / "important.txt"
    important.write_text("keep", encoding="utf-8")
    state = load_remote_state(project.remote_state_path)
    state["cleanup"]["archives"]["package"]["path"] = str(important)
    write_remote_state(project.remote_state_path, state)

    with pytest.raises(
        CleanupStateError,
        match="pending package archive path is unsafe",
    ):
        cleanup_project(project)

    assert important.read_text(encoding="utf-8") == "keep"


def _write_cleanup_project(tmp_path: Path) -> WorkflowProject:
    video_dir = tmp_path / "video0"
    workflow_dir = video_dir / ".workflow"
    workflow_dir.mkdir(parents=True)
    project = WorkflowProject(video_dir=video_dir, workflow_dir=workflow_dir)

    package_path = project.default_package_path
    result_archive_path = project.default_result_archive_path
    installed_path = video_dir / "dist_vmaf.json"
    reference_path = video_dir / "ref.mp4"
    distorted_path = video_dir / "dist.mp4"
    reference_path.write_bytes(b"reference media")
    distorted_path.write_bytes(b"distorted media")
    installed_bytes = b'{"pooled_metrics": {}}'
    installed_path.write_bytes(installed_bytes)
    _write_result_archive(
        result_archive_path,
        f"{video_dir.name}/{installed_path.name}",
        installed_bytes,
    )
    project.remote_plan_path.write_text("{}", encoding="utf-8")
    package_manifest = {
        "archive_path": str(package_path),
        "archive_root": video_dir.name,
        "media_files": [
            {
                "path": reference_path.name,
                "role": "reference",
                "size_bytes": reference_path.stat().st_size,
            },
            {
                "path": distorted_path.name,
                "role": "distorted",
                "size_bytes": distorted_path.stat().st_size,
            },
        ],
    }
    project.package_manifest_path.write_text(
        json.dumps(package_manifest),
        encoding="utf-8",
    )
    _write_input_archive(
        package_path,
        video_dir.name,
        {
            reference_path.name: reference_path.read_bytes(),
            distorted_path.name: distorted_path.read_bytes(),
        },
        package_manifest,
    )

    write_remote_state(
        project.remote_state_path,
        {
            "schema_version": 1,
            "project": "video0",
            "upload": {
                "status": "completed",
                "package": {
                    "local_path": str(package_path),
                    "sha256": sha256_file(package_path),
                    "size_bytes": package_path.stat().st_size,
                },
            },
            "fetch": {
                "status": "completed",
                "archive": {
                    "local_path": str(result_archive_path),
                    "sha256": sha256_file(result_archive_path),
                    "size_bytes": result_archive_path.stat().st_size,
                },
                "files": [
                    {
                        "path": str(installed_path),
                        "size_bytes": installed_path.stat().st_size,
                    }
                ],
            },
        },
    )
    project.manifest_path.write_text(
        json.dumps(
            {
                "keep": "existing",
                "package": {
                    "path": str(package_path),
                    "manifest": str(project.package_manifest_path),
                },
                "results": {
                    "archive": str(result_archive_path),
                    "files": [str(installed_path)],
                    "validated_at": "2026-07-16T00:00:00+00:00",
                },
            }
        ),
        encoding="utf-8",
    )
    return project


def _write_result_archive(
    path: Path,
    member_name: str,
    content: bytes,
) -> None:
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(member_name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))


def _write_input_archive(
    path: Path,
    project_name: str,
    files: dict[str, bytes],
    package_manifest: dict[str, object],
) -> None:
    with tarfile.open(path, "w") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(f"{project_name}/{name}")
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        manifest_content = json.dumps(package_manifest).encode("utf-8")
        manifest_info = tarfile.TarInfo(
            f"{project_name}/.workflow/package-manifest.json"
        )
        manifest_info.size = len(manifest_content)
        archive.addfile(manifest_info, io.BytesIO(manifest_content))
