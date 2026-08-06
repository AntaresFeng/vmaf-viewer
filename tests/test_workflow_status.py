from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest

from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_state import sha256_file
from vmaf_workflow.status import (
    WorkflowStatusError,
    inspect_workflow_status,
)


def test_status_rejects_missing_project_directory(tmp_path: Path) -> None:
    project = _project(tmp_path)

    with pytest.raises(WorkflowStatusError, match="project directory"):
        inspect_workflow_status(project)


def test_status_reports_new_project_and_download_command(tmp_path: Path) -> None:
    project = _project(tmp_path)
    project.video_dir.mkdir()

    status = inspect_workflow_status(project)

    assert status.stage == "new"
    assert status.state == "incomplete"
    assert status.missing_artifacts == ("<downloaded media>",)
    assert status.next_command == (
        shlex.join(
            [
                "uv",
                "run",
                "vmaf-workflow",
                "download",
                "--project-dir",
                str(project.video_dir),
            ]
        )
        + " --bvid <BVID>"
    )


def test_status_reports_downloaded_when_inventory_is_missing(tmp_path: Path) -> None:
    project = _downloaded_project(tmp_path)

    status = inspect_workflow_status(project)

    assert status.stage == "downloaded"
    assert status.state == "incomplete"
    assert status.missing_artifacts == (str(project.media_inventory_path),)
    assert "prepare --project-dir" in status.next_command
    assert status.next_command.endswith("--reference <reference-path>")


def test_status_progresses_through_local_planning_stages(tmp_path: Path) -> None:
    project = _prepared_project(tmp_path)

    prepared = inspect_workflow_status(project)
    assert prepared.stage == "prepared"
    assert prepared.missing_artifacts == (
        str(project.package_manifest_path),
        str(project.default_package_path),
    )
    assert " package " in f" {prepared.next_command} "

    _write_packaged(project)
    packaged = inspect_workflow_status(project)
    assert packaged.stage == "packaged"
    assert packaged.missing_artifacts == (
        str(project.remote_plan_path),
        str(project.remote_plan_script_path),
    )
    assert "remote-plan" in packaged.next_command

    _write_planned(project)
    planned = inspect_workflow_status(project)
    assert planned.stage == "planned"
    assert planned.missing_artifacts == (str(project.remote_state_path),)
    assert " upload " in f" {planned.next_command} "


def test_status_does_not_restart_running_upload(tmp_path: Path) -> None:
    project = _planned_project(tmp_path)
    _write_remote_state(project, upload={"status": "running"})

    status = inspect_workflow_status(project)

    assert status.stage == "planned"
    assert status.state == "running"
    assert " status " in f" {status.next_command} "


def test_status_reports_cleaned_without_treating_archives_as_missing(
    tmp_path: Path,
) -> None:
    project = _fetched_project(tmp_path)
    project.default_package_path.unlink()
    project.default_result_archive_path.unlink()
    state = _read_json(project.remote_state_path)
    state["cleanup"] = {
        "status": "completed",
        "archives": {
            "package": {"deleted": True},
            "result": {"deleted": True},
        },
    }
    _write_json(project.remote_state_path, state)

    status = inspect_workflow_status(project)

    assert status.stage == "cleaned"
    assert status.state == "completed"
    assert status.missing_artifacts == ()
    assert status.next_command == shlex.join(
        ["uv", "run", "vmaf-viewer", str(project.video_dir)]
    )


def _project(tmp_path: Path) -> WorkflowProject:
    video_dir = tmp_path / "video0"
    return WorkflowProject(video_dir, video_dir / ".workflow")


def _downloaded_project(tmp_path: Path) -> WorkflowProject:
    project = _project(tmp_path)
    project.workflow_dir.mkdir(parents=True)
    (project.video_dir / "reference.mp4").write_bytes(b"reference")
    (project.video_dir / "distorted.mp4").write_bytes(b"distorted")
    _write_json(
        project.manifest_path,
        {
            "project_dir": str(project.video_dir),
            "workflow_dir": str(project.workflow_dir),
        },
    )
    return project


def _prepared_project(tmp_path: Path) -> WorkflowProject:
    project = _downloaded_project(tmp_path)
    inventory = {
        "reference": "reference.mp4",
        "files": [
            {"path": "reference.mp4", "role": "reference", "size_bytes": 9},
            {"path": "distorted.mp4", "role": "distorted", "size_bytes": 9},
        ],
    }
    _write_json(project.media_inventory_path, inventory)
    manifest = _read_json(project.manifest_path)
    manifest.update(
        {
            "reference": {"path": "reference.mp4"},
            "media_inventory": str(project.media_inventory_path),
        }
    )
    _write_json(project.manifest_path, manifest)
    return project


def _write_packaged(project: WorkflowProject) -> None:
    _write_json(
        project.package_manifest_path,
        {
            "archive_path": str(project.default_package_path),
            "archive_root": project.video_dir.name,
            "inventory_path": str(project.media_inventory_path),
            "inventory_sha256": sha256_file(project.media_inventory_path),
            "media_files": [
                {"path": "reference.mp4", "role": "reference", "size_bytes": 9},
                {"path": "distorted.mp4", "role": "distorted", "size_bytes": 9},
            ],
        },
    )
    project.default_package_path.write_bytes(b"package")
    manifest = _read_json(project.manifest_path)
    manifest["package"] = {
        "path": str(project.default_package_path),
        "manifest": str(project.package_manifest_path),
    }
    _write_json(project.manifest_path, manifest)


def _write_planned(project: WorkflowProject) -> None:
    package_manifest = _read_json(project.package_manifest_path)
    _write_json(
        project.remote_plan_path,
        {
            "schema_version": 2,
            "watermark_mapping_contract": "normalized-real-easyvmaf-v1",
            "package_archive": project.default_package_path.name,
            "inventory_sha256": package_manifest["inventory_sha256"],
            "watermark_analysis_sha256": None,
            "result_archive": project.default_result_archive_path.name,
            "score_scope": "full_frame",
            "content_exclusions": [],
            "expected_results": [f"{project.video_dir.name}/distorted_vmaf.json"],
        },
    )
    project.remote_plan_script_path.write_text(
        "#!/usr/bin/env bash\n",
        encoding="utf-8",
    )
    manifest = _read_json(project.manifest_path)
    manifest["remote_plan"] = {
        "manifest": str(project.remote_plan_path),
        "script": str(project.remote_plan_script_path),
    }
    _write_json(project.manifest_path, manifest)


def _planned_project(tmp_path: Path) -> WorkflowProject:
    project = _prepared_project(tmp_path)
    _write_packaged(project)
    _write_planned(project)
    return project


def _fetched_project(tmp_path: Path) -> WorkflowProject:
    project = _planned_project(tmp_path)
    result = project.video_dir / "distorted_vmaf.json"
    result.write_text("{}\n", encoding="utf-8")
    project.default_result_archive_path.write_bytes(b"result-archive")
    _write_remote_state(
        project,
        upload={"status": "completed"},
        run={"status": "completed"},
        fetch={
            "status": "completed",
            "archive": {"local_path": str(project.default_result_archive_path)},
            "files": [{"path": str(result), "size_bytes": result.stat().st_size}],
        },
    )
    manifest = _read_json(project.manifest_path)
    manifest["results"] = {
        "archive": str(project.default_result_archive_path),
        "files": [str(result)],
    }
    _write_json(project.manifest_path, manifest)
    return project


def _write_remote_state(
    project: WorkflowProject,
    *,
    upload: dict[str, object],
    run: dict[str, object] | None = None,
    fetch: dict[str, object] | None = None,
) -> None:
    state: dict[str, object] = {
        "schema_version": 1,
        "project": project.video_dir.name,
        "upload": upload,
    }
    if run is not None:
        state["run"] = run
    if fetch is not None:
        state["fetch"] = fetch
    _write_json(project.remote_state_path, state)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
