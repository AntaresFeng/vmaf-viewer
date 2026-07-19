from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from vmaf_workflow.prepare import EXCLUDED_DIR_NAMES, MEDIA_SUFFIXES
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_state import sha256_file
from vmaf_workflow.watermark_detection import (
    WatermarkGeometryError,
    map_normalized_edges,
)


class WorkflowStatusError(ValueError):
    pass


@dataclass(frozen=True)
class WorkflowStatus:
    project: Path
    stage: str
    state: str
    missing_artifacts: tuple[str, ...]
    next_command: str


def inspect_workflow_status(project: WorkflowProject) -> WorkflowStatus:
    if not project.video_dir.is_dir():
        raise WorkflowStatusError(f"project directory is required: {project.video_dir}")

    manifest = load_optional_json_object(project.manifest_path, "manifest.json")
    inventory = load_optional_json_object(
        project.media_inventory_path,
        "media-inventory.json",
    )
    package_manifest = load_optional_json_object(
        project.package_manifest_path,
        "package-manifest.json",
    )
    remote_plan = load_optional_json_object(
        project.remote_plan_path,
        "remote-plan.json",
    )
    remote_state = load_optional_json_object(
        project.remote_state_path,
        "remote-state.json",
    )
    if remote_state is not None:
        _validate_remote_state(project, remote_state)

    if not _has_media(project.video_dir):
        return _report(
            project,
            "new",
            "incomplete",
            ("<downloaded media>",),
            _workflow_command("download", project) + " --bvid <BVID>",
        )

    if inventory is None:
        return _report(
            project,
            "downloaded",
            "incomplete",
            (str(project.media_inventory_path),),
            _prepare_command(project),
        )

    _validate_watermark_inventory(project, inventory)
    missing_media = _inventory_missing_media(project, inventory)
    if missing_media:
        return _report(
            project,
            "downloaded",
            "incomplete",
            missing_media,
            _prepare_command(project),
        )

    cleanup_status = _cleanup_status(remote_state)
    package_archive = _package_archive_path(project, package_manifest)
    package_missing: list[str] = []
    if package_manifest is None:
        package_missing.append(str(project.package_manifest_path))
    elif not _package_hashes_match(project, inventory, package_manifest):
        package_missing.append(str(project.package_manifest_path))
    if package_archive is None:
        package_archive = project.default_package_path
    package_absence_expected = (
        cleanup_status in {"pending", "completed"} and not package_archive.exists()
    )
    if not package_archive.is_file() and not package_absence_expected:
        package_missing.append(str(package_archive))
    if package_missing:
        return _report(
            project,
            "prepared",
            "incomplete",
            tuple(package_missing),
            _workflow_command("package", project),
        )

    if cleanup_status == "completed" and package_archive.is_file():
        return _report(
            project,
            "packaged",
            "incomplete",
            (),
            _workflow_command("remote-plan", project),
        )

    plan_missing = tuple(
        str(path)
        for path, value in (
            (project.remote_plan_path, remote_plan),
            (project.remote_plan_script_path, project.remote_plan_script_path),
        )
        if value is None
        or not path.is_file()
        or (
            path == project.remote_plan_path
            and remote_plan is not None
            and package_manifest is not None
            and not _remote_plan_matches_inputs(
                remote_plan, inventory, package_manifest
            )
        )
    )
    if plan_missing:
        return _report(
            project,
            "packaged",
            "incomplete",
            plan_missing,
            _workflow_command("remote-plan", project),
        )

    if remote_state is None:
        return _report(
            project,
            "planned",
            "incomplete",
            (str(project.remote_state_path),),
            _workflow_command("upload", project),
        )
    upload_status = _stage_status(remote_state, "upload")
    if upload_status == "running":
        return _report(
            project,
            "planned",
            "running",
            (),
            _workflow_command("status", project),
        )
    if upload_status != "completed":
        return _report(
            project,
            "planned",
            upload_status,
            (),
            _workflow_command("upload", project),
        )

    run_status = _stage_status(remote_state, "run")
    if run_status == "running":
        return _report(
            project,
            "running",
            "running",
            (),
            _workflow_command("status", project),
        )
    if run_status != "completed":
        return _report(
            project,
            "uploaded",
            run_status,
            (),
            _workflow_command("run", project),
        )

    fetch_status = _stage_status(remote_state, "fetch")
    result_archive = _result_archive_path(project, remote_state)
    if fetch_status == "running":
        missing = () if result_archive.is_file() else (str(result_archive),)
        return _report(
            project,
            "computed",
            "running",
            missing,
            _workflow_command("status", project),
        )
    if fetch_status != "completed":
        missing = () if result_archive.is_file() else (str(result_archive),)
        return _report(
            project,
            "computed",
            fetch_status,
            missing,
            _workflow_command("fetch-results", project),
        )

    missing_results = _missing_installed_results(project, manifest, remote_state)
    if missing_results:
        return _report(
            project,
            "computed",
            "incomplete",
            missing_results,
            _workflow_command("fetch-results", project),
        )

    if cleanup_status in {"pending", "failed", "interrupted"}:
        return _report(
            project,
            "fetched",
            cleanup_status,
            (),
            _workflow_command("cleanup", project),
        )
    if cleanup_status == "completed" and not result_archive.exists():
        return _report(
            project,
            "cleaned",
            "completed",
            (),
            _viewer_command(project),
        )
    if not result_archive.is_file():
        return _report(
            project,
            "computed",
            "incomplete",
            (str(result_archive),),
            _workflow_command("fetch-results", project),
        )
    if package_archive.resolve() != project.default_package_path.resolve():
        return _report(
            project,
            "fetched",
            "completed",
            (),
            _viewer_command(project),
        )
    return _report(
        project,
        "fetched",
        "completed",
        (),
        _workflow_command("cleanup", project),
    )


def _report(
    project: WorkflowProject,
    stage: str,
    state: str,
    missing_artifacts: tuple[str, ...],
    next_command: str,
) -> WorkflowStatus:
    return WorkflowStatus(
        project=project.video_dir,
        stage=stage,
        state=state,
        missing_artifacts=missing_artifacts,
        next_command=next_command,
    )


def load_optional_json_object(
    path: Path,
    label: str,
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    if not path.is_file():
        raise WorkflowStatusError(f"{label} is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WorkflowStatusError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise WorkflowStatusError(f"{label} must be a JSON object: {path}")
    return value


def _has_media(root: Path) -> bool:
    return bool(_media_paths(root))


def _media_paths(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
            continue
        files.append(path)
    return tuple(sorted(files, key=lambda path: str(path).lower()))


def _inventory_missing_media(
    project: WorkflowProject,
    inventory: dict[str, Any],
) -> tuple[str, ...]:
    files = inventory.get("files")
    if not isinstance(files, list) or not files:
        raise WorkflowStatusError(
            "media-inventory.json must contain a non-empty files list"
        )
    roles: list[str] = []
    missing: list[str] = []
    recorded_paths: set[Path] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise WorkflowStatusError("media-inventory.json file entry is invalid")
        raw_path = entry.get("path")
        role = entry.get("role")
        if not isinstance(raw_path, str) or not isinstance(role, str):
            raise WorkflowStatusError("media-inventory.json file entry is invalid")
        relative = _safe_relative_path(raw_path, "inventory media path")
        media_path = project.video_dir.joinpath(*relative.parts)
        recorded_paths.add(media_path)
        if not media_path.is_file():
            missing.append(str(media_path))
        roles.append(role)
    if roles.count("reference") != 1 or "distorted" not in roles:
        raise WorkflowStatusError(
            "media-inventory.json requires one reference and at least one "
            "distorted file"
        )
    missing.extend(
        str(path)
        for path in _media_paths(project.video_dir)
        if path not in recorded_paths
    )
    return tuple(missing)


def _safe_relative_path(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or "\\" in value or ".." in path.parts or not path.parts:
        raise WorkflowStatusError(f"{label} is invalid: {value}")
    return path


def _validate_watermark_inventory(
    project: WorkflowProject,
    inventory: dict[str, Any],
) -> None:
    detection = inventory.get("watermark_detection")
    if detection is None:
        return
    if not isinstance(detection, dict):
        raise WorkflowStatusError("watermark_detection must be an object")
    applicable = detection.get("applicable")
    if not isinstance(applicable, bool):
        raise WorkflowStatusError("watermark_detection applicable must be boolean")
    exclusions = inventory.get("content_exclusions")
    if not isinstance(exclusions, list):
        raise WorkflowStatusError("content_exclusions must be a list")
    state = detection.get("state")
    if not applicable:
        if state != "not_applicable" or exclusions:
            raise WorkflowStatusError(
                "non-applicable watermark detection must have state "
                "not_applicable and no exclusions"
            )
        return
    if state not in {"present", "absent"}:
        raise WorkflowStatusError(
            "applicable watermark detection state must be present or absent"
        )
    representative = detection.get("representative")
    if not isinstance(representative, dict):
        raise WorkflowStatusError(
            "applicable watermark detection must contain representative"
        )
    representative_path = representative.get("path")
    if not isinstance(representative_path, str):
        raise WorkflowStatusError("watermark representative path is invalid")
    relative = _safe_relative_path(representative_path, "watermark representative path")
    if not project.video_dir.joinpath(*relative.parts).is_file():
        raise WorkflowStatusError(
            f"watermark representative is missing: {representative_path}"
        )
    inventory_files = inventory.get("files")
    representative_entries = (
        [
            entry
            for entry in inventory_files
            if isinstance(entry, dict)
            and entry.get("path") == representative_path
            and entry.get("role") == "distorted"
        ]
        if isinstance(inventory_files, list)
        else []
    )
    if len(representative_entries) != 1:
        raise WorkflowStatusError(
            "watermark representative must be one distorted inventory file"
        )
    representative_entry = representative_entries[0]
    for dimension in ("width", "height"):
        value = representative.get(dimension)
        if (
            not isinstance(value, int)
            or value <= 0
            or value != representative_entry.get(dimension)
        ):
            raise WorkflowStatusError(
                f"watermark representative {dimension} is invalid"
            )
    analysis = detection.get("analysis")
    if not isinstance(analysis, dict):
        raise WorkflowStatusError(
            "applicable watermark detection must contain analysis"
        )
    for dimension in ("width", "height"):
        value = analysis.get(dimension)
        if not isinstance(value, int) or value <= 0:
            raise WorkflowStatusError(f"watermark analysis {dimension} is invalid")
    summary_path = analysis.get("summary_path")
    expected_summary = ".workflow/watermark-analysis/summary.json"
    if summary_path != expected_summary:
        raise WorkflowStatusError(f"watermark summary_path must be {expected_summary}")
    summary = load_optional_json_object(
        project.watermark_summary_path,
        "watermark-analysis/summary.json",
    )
    if summary is None:
        raise WorkflowStatusError(
            f"watermark analysis summary is required: {project.watermark_summary_path}"
        )
    if summary.get("state") != state:
        raise WorkflowStatusError(
            "watermark analysis summary state does not match inventory"
        )
    if state == "absent":
        if exclusions:
            raise WorkflowStatusError(
                "absent watermark detection must not contain exclusions"
            )
        return
    if len(exclusions) != 1 or not isinstance(exclusions[0], dict):
        raise WorkflowStatusError(
            "present watermark detection requires exactly one exclusion"
        )
    exclusion = exclusions[0]
    if exclusion.get("kind") != "bilibili_watermark":
        raise WorkflowStatusError("watermark exclusion kind is invalid")
    edges = exclusion.get("normalized_edges")
    if not isinstance(edges, dict):
        raise WorkflowStatusError("watermark normalized_edges must be an object")
    try:
        map_normalized_edges(edges, 1, 1)
    except WatermarkGeometryError as exc:
        raise WorkflowStatusError(str(exc)) from exc
    if summary.get("normalized_edges") != edges:
        raise WorkflowStatusError(
            "watermark analysis summary edges do not match inventory"
        )


def _package_hashes_match(
    project: WorkflowProject,
    inventory: dict[str, Any],
    package_manifest: dict[str, Any],
) -> bool:
    if package_manifest.get("inventory_sha256") != sha256_file(
        project.media_inventory_path
    ):
        return False
    detection = inventory.get("watermark_detection")
    applicable = isinstance(detection, dict) and detection.get("applicable") is True
    analysis_hash = package_manifest.get("watermark_analysis_sha256")
    if not applicable:
        return analysis_hash is None
    if not project.watermark_summary_path.is_file():
        return False
    return analysis_hash == sha256_file(project.watermark_summary_path)


def _remote_plan_matches_inputs(
    remote_plan: dict[str, Any],
    inventory: dict[str, Any],
    package_manifest: dict[str, Any],
) -> bool:
    if remote_plan.get("schema_version") != 2:
        return False
    if remote_plan.get("watermark_mapping_contract") != "normalized-real-easyvmaf-v1":
        return False
    if remote_plan.get("inventory_sha256") != package_manifest.get("inventory_sha256"):
        return False
    if remote_plan.get("watermark_analysis_sha256") != package_manifest.get(
        "watermark_analysis_sha256"
    ):
        return False
    exclusions = inventory.get("content_exclusions", [])
    if remote_plan.get("content_exclusions", []) != exclusions:
        return False
    expected_scope = "content_excluding_regions" if exclusions else "full_frame"
    return remote_plan.get("score_scope") == expected_scope


def _package_archive_path(
    project: WorkflowProject,
    package_manifest: dict[str, Any] | None,
) -> Path | None:
    if package_manifest is None:
        return None
    raw_path = package_manifest.get("archive_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise WorkflowStatusError("package-manifest.json archive_path is invalid")
    return Path(raw_path)


def _cleanup_status(remote_state: dict[str, Any] | None) -> str:
    if remote_state is None or "cleanup" not in remote_state:
        return "incomplete"
    cleanup = remote_state.get("cleanup")
    if not isinstance(cleanup, dict):
        raise WorkflowStatusError("remote-state.json cleanup state is invalid")
    status = cleanup.get("status")
    if not isinstance(status, str):
        raise WorkflowStatusError("remote-state.json cleanup status is invalid")
    return status


def _validate_remote_state(
    project: WorkflowProject,
    remote_state: dict[str, Any],
) -> None:
    if remote_state.get("schema_version") != 1:
        raise WorkflowStatusError("remote-state.json schema_version must be 1")
    if remote_state.get("project") != project.video_dir.name:
        raise WorkflowStatusError(
            "remote-state.json project does not match project directory"
        )


def _stage_status(remote_state: dict[str, Any], stage: str) -> str:
    value = remote_state.get(stage)
    if value is None:
        return "incomplete"
    if not isinstance(value, dict):
        raise WorkflowStatusError(f"remote-state.json {stage} state is invalid")
    status = value.get("status")
    if not isinstance(status, str):
        raise WorkflowStatusError(f"remote-state.json {stage} status is invalid")
    return status


def _result_archive_path(
    project: WorkflowProject,
    remote_state: dict[str, Any],
) -> Path:
    fetch = remote_state.get("fetch")
    if not isinstance(fetch, dict):
        return project.default_result_archive_path
    archive = fetch.get("archive")
    if not isinstance(archive, dict):
        return project.default_result_archive_path
    raw_path = archive.get("local_path")
    if isinstance(raw_path, str):
        return Path(raw_path)
    return project.default_result_archive_path


def _missing_installed_results(
    project: WorkflowProject,
    manifest: dict[str, Any] | None,
    remote_state: dict[str, Any],
) -> tuple[str, ...]:
    fetch = remote_state.get("fetch")
    if not isinstance(fetch, dict):
        raise WorkflowStatusError("remote-state.json fetch state is invalid")
    files = fetch.get("files")
    if not isinstance(files, list) or not files:
        raise WorkflowStatusError("remote-state.json fetch files are invalid")

    paths: list[Path] = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise WorkflowStatusError("remote-state.json fetch file entry is invalid")
        paths.append(Path(entry["path"]))

    if manifest is None:
        raise WorkflowStatusError("manifest.json is required after fetch-results")
    results = manifest.get("results")
    if not isinstance(results, dict):
        raise WorkflowStatusError(
            "manifest.json results are required after fetch-results"
        )
    manifest_files = results.get("files")
    if not isinstance(manifest_files, list) or not all(
        isinstance(value, str) for value in manifest_files
    ):
        raise WorkflowStatusError("manifest.json result files are invalid")
    if {Path(value).resolve() for value in manifest_files} != {
        path.resolve() for path in paths
    }:
        raise WorkflowStatusError(
            "manifest.json result files do not match remote-state.json"
        )
    return tuple(str(path) for path in paths if not path.is_file())


def _workflow_command(command: str, project: WorkflowProject) -> str:
    return shlex.join(
        [
            "uv",
            "run",
            "vmaf-workflow",
            command,
            "--project-dir",
            str(project.video_dir),
        ]
    )


def _prepare_command(project: WorkflowProject) -> str:
    return _workflow_command("prepare", project) + " --reference <reference-path>"


def _viewer_command(project: WorkflowProject) -> str:
    return shlex.join(["uv", "run", "vmaf-viewer", str(project.video_dir)])
