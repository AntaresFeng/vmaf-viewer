from __future__ import annotations

import json
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.remote_state import (
    RemoteStateError,
    load_remote_state,
    sha256_file,
    utc_now,
    write_remote_state,
)


class CleanupStateError(ValueError):
    pass


class CleanupExecutionError(RuntimeError):
    pass


def cleanup_project(project: WorkflowProject) -> dict[str, Any]:
    state = _load_state(project)
    manifest = _load_json_object(project.manifest_path, "manifest")
    _validate_project_state(project, state)

    cleanup = state.get("cleanup")
    if isinstance(cleanup, dict) and cleanup.get("status") == "pending":
        return _continue_pending_cleanup(project, state, manifest)

    upload = _required_mapping(state, "upload", "remote state")
    if upload.get("status") != "completed":
        raise CleanupStateError("upload must be completed before cleanup")
    fetch = _required_mapping(state, "fetch", "remote state")
    if fetch.get("status") != "completed":
        raise CleanupStateError("fetch-results must be completed before cleanup")

    previous_cleanup = cleanup if isinstance(cleanup, dict) else None
    package_state = _required_mapping(
        upload,
        "package",
        "remote state upload",
    )
    _require_default_package_path(project, package_state)

    package_present = project.default_package_path.is_file()
    result_present = project.default_result_archive_path.is_file()
    package = _current_or_cleaned_artifact(
        project.default_package_path,
        package_state,
        "input",
        package_present,
        previous_cleanup,
        "package",
    )
    result = _current_or_cleaned_artifact(
        project.default_result_archive_path,
        _required_mapping(fetch, "archive", "remote state fetch"),
        "result",
        result_present,
        previous_cleanup,
        "result",
    )

    package_manifest = _load_json_object(
        project.package_manifest_path,
        "package-manifest.json",
    )
    _require_default_package_manifest_path(project, package_manifest)
    if package_present:
        _validate_package_contents(project, package_manifest)

    installed_paths = _validate_installed_results(
        project,
        fetch,
        manifest,
        result_present=result_present,
    )
    if result_present:
        _validate_result_archive_contents(project, installed_paths)

    targets = {
        name: path
        for name, path, present in (
            ("package", project.default_package_path, package_present),
            ("result", project.default_result_archive_path, result_present),
        )
        if present
    }
    if not targets:
        completed = {
            "status": "completed",
            "completed_at": (
                previous_cleanup.get("completed_at")
                if isinstance(previous_cleanup, dict)
                else utc_now()
            ),
            "last_run_at": utc_now(),
            "last_reclaimed_bytes": 0,
            "archives": {
                "package": {**package, "deleted": True},
                "result": {**result, "deleted": True},
            },
        }
        state["cleanup"] = completed
        _write_state(project, state, "write-noop-state")
        return state

    pending = _new_pending_cleanup(
        project,
        package,
        result,
        targets,
    )
    state["cleanup"] = pending
    _write_state(project, state, "write-pending-state")
    _stage_cleanup_targets(project, state, targets)
    return _continue_pending_cleanup(project, state, manifest)


def _load_state(project: WorkflowProject) -> dict[str, Any]:
    try:
        return load_remote_state(project.remote_state_path)
    except RemoteStateError as exc:
        raise CleanupStateError(str(exc)) from exc


def _validate_project_state(
    project: WorkflowProject,
    state: dict[str, Any],
) -> None:
    if state.get("project") != project.video_dir.name:
        raise CleanupStateError(
            "remote state project does not match project directory"
        )


def _require_default_package_path(
    project: WorkflowProject,
    package_state: dict[str, Any],
) -> None:
    raw_path = package_state.get("local_path")
    if not isinstance(raw_path, str):
        raise CleanupStateError("input archive local path is invalid")
    if Path(raw_path).resolve() != project.default_package_path.resolve():
        raise CleanupStateError(
            "cleanup only supports the default input archive: "
            f"{project.default_package_path}"
        )


def _require_default_package_manifest_path(
    project: WorkflowProject,
    package_manifest: dict[str, Any],
) -> None:
    raw_path = package_manifest.get("archive_path")
    if not isinstance(raw_path, str):
        raise CleanupStateError("package manifest archive_path is invalid")
    if Path(raw_path).resolve() != project.default_package_path.resolve():
        raise CleanupStateError(
            "cleanup only supports the default input archive: "
            f"{project.default_package_path}"
        )


def _current_or_cleaned_artifact(
    expected_path: Path,
    artifact_state: dict[str, Any],
    label: str,
    present: bool,
    previous_cleanup: dict[str, Any] | None,
    artifact_name: str,
) -> dict[str, Any]:
    if expected_path.is_symlink():
        raise CleanupStateError(f"{label} archive must not be a symbolic link")
    if present:
        return _validate_archive(expected_path, artifact_state, label)
    if expected_path.exists():
        raise CleanupStateError(f"{label} archive is not a regular file: {expected_path}")

    previous = _previous_cleanup_artifact(previous_cleanup, artifact_name)
    expected_sha256 = artifact_state.get("sha256")
    expected_size = artifact_state.get("size_bytes")
    if (
        previous.get("deleted") is not True
        or previous.get("sha256") != expected_sha256
        or previous.get("size_bytes") != expected_size
        or Path(str(previous.get("path"))).resolve() != expected_path.resolve()
    ):
        raise CleanupStateError(f"{label} archive is required: {expected_path}")
    return {
        "path": str(expected_path),
        "sha256": expected_sha256,
        "size_bytes": expected_size,
    }


def _previous_cleanup_artifact(
    cleanup: dict[str, Any] | None,
    artifact_name: str,
) -> dict[str, Any]:
    if not isinstance(cleanup, dict) or cleanup.get("status") != "completed":
        return {}
    archives = cleanup.get("archives")
    if not isinstance(archives, dict):
        return {}
    artifact = archives.get(artifact_name)
    return artifact if isinstance(artifact, dict) else {}


def _validate_archive(
    expected_path: Path,
    artifact_state: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    raw_path = artifact_state.get("local_path")
    if not isinstance(raw_path, str):
        raise CleanupStateError(f"{label} archive local path is invalid")
    if Path(raw_path).resolve() != expected_path.resolve():
        raise CleanupStateError(
            f"{label} archive path does not match current project: {raw_path}"
        )
    if expected_path.is_symlink() or not expected_path.is_file():
        raise CleanupStateError(f"{label} archive is required: {expected_path}")

    expected_sha256 = artifact_state.get("sha256")
    if not isinstance(expected_sha256, str) or not expected_sha256:
        raise CleanupStateError(f"{label} archive SHA-256 is invalid")
    actual_sha256 = sha256_file(expected_path)
    if actual_sha256 != expected_sha256:
        raise CleanupStateError(f"{label} archive SHA-256 does not match state")

    expected_size = artifact_state.get("size_bytes")
    actual_size = expected_path.stat().st_size
    if not isinstance(expected_size, int) or expected_size != actual_size:
        raise CleanupStateError(f"{label} archive size does not match state")

    return {
        "path": str(expected_path),
        "sha256": actual_sha256,
        "size_bytes": actual_size,
    }


def _validate_package_contents(
    project: WorkflowProject,
    package_manifest: dict[str, Any],
) -> None:
    try:
        with tarfile.open(project.default_package_path, "r:*") as archive:
            snapshot = _read_package_manifest_snapshot(project, archive)
            if snapshot != package_manifest:
                raise CleanupStateError(
                    "package manifest does not match input archive snapshot"
                )
            expected = _package_media_paths(project, snapshot)
            matching = [
                member for member in archive.getmembers() if member.name in expected
            ]
            if {member.name for member in matching} != set(expected):
                raise CleanupStateError(
                    "input archive does not contain all package media files"
                )
            if len(matching) != len(expected):
                raise CleanupStateError(
                    "input archive contains duplicate package media files"
                )
            for member in matching:
                if not member.isfile():
                    raise CleanupStateError(
                        f"input archive media is not a regular file: {member.name}"
                    )
                archived = archive.extractfile(member)
                if archived is None:
                    raise CleanupStateError(
                        f"input archive media cannot be read: {member.name}"
                    )
                with expected[member.name].open("rb") as source:
                    if not _streams_equal(archived, source):
                        raise CleanupStateError(
                            "package media content does not match input archive: "
                            f"{expected[member.name]}"
                        )
    except (OSError, tarfile.TarError) as exc:
        raise CleanupStateError(
            f"input archive cannot be validated: {exc}"
        ) from exc


def _read_package_manifest_snapshot(
    project: WorkflowProject,
    archive: tarfile.TarFile,
) -> dict[str, Any]:
    name = f"{project.video_dir.name}/.workflow/package-manifest.json"
    members = [member for member in archive.getmembers() if member.name == name]
    if len(members) != 1 or not members[0].isfile():
        raise CleanupStateError(
            "input archive must contain one regular package manifest snapshot"
        )
    manifest_file = archive.extractfile(members[0])
    if manifest_file is None:
        raise CleanupStateError("input archive package manifest cannot be read")
    try:
        snapshot = json.loads(manifest_file.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanupStateError(
            "input archive package manifest is not valid JSON"
        ) from exc
    if not isinstance(snapshot, dict):
        raise CleanupStateError(
            "input archive package manifest must be a JSON object"
        )
    return snapshot


def _package_media_paths(
    project: WorkflowProject,
    package_manifest: dict[str, Any],
) -> dict[str, Path]:
    media_files = package_manifest.get("media_files")
    if not isinstance(media_files, list) or not media_files:
        raise CleanupStateError("package manifest media_files are required")
    if package_manifest.get("archive_root") != project.video_dir.name:
        raise CleanupStateError("package manifest archive_root is invalid")

    expected: dict[str, Path] = {}
    for entry in media_files:
        if not isinstance(entry, dict):
            raise CleanupStateError("package manifest media file entry is invalid")
        raw_path = entry.get("path")
        expected_size = entry.get("size_bytes")
        if not isinstance(raw_path, str) or not isinstance(expected_size, int):
            raise CleanupStateError("package manifest media file entry is invalid")
        relative = _validated_relative_path(raw_path, "package media path")
        source = project.video_dir.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file():
            raise CleanupStateError(f"package media file is required: {source}")
        if source.stat().st_size != expected_size:
            raise CleanupStateError(f"package media file size changed: {source}")
        member_name = f"{project.video_dir.name}/{relative.as_posix()}"
        if member_name in expected:
            raise CleanupStateError(f"package media path is duplicated: {raw_path}")
        expected[member_name] = source
    return expected


def _streams_equal(first: BinaryIO, second: BinaryIO) -> bool:
    while True:
        first_chunk = first.read(1024 * 1024)
        second_chunk = second.read(1024 * 1024)
        if first_chunk != second_chunk:
            return False
        if not first_chunk:
            return True


def _validate_installed_results(
    project: WorkflowProject,
    fetch: dict[str, Any],
    manifest: dict[str, Any],
    *,
    result_present: bool,
) -> list[Path]:
    raw_files = fetch.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise CleanupStateError("fetch installed result files are required")

    video_root = project.video_dir.resolve()
    installed_paths: list[Path] = []
    installed_resolved: set[Path] = set()
    for entry in raw_files:
        if not isinstance(entry, dict):
            raise CleanupStateError("fetch installed result entry is invalid")
        raw_path = entry.get("path")
        expected_size = entry.get("size_bytes")
        if not isinstance(raw_path, str) or not isinstance(expected_size, int):
            raise CleanupStateError("fetch installed result entry is invalid")
        path = Path(raw_path)
        resolved = path.resolve()
        if resolved.parent != video_root:
            raise CleanupStateError(
                f"installed result is outside the project directory: {path}"
            )
        if path.is_symlink() or not path.is_file():
            raise CleanupStateError(f"installed result is required: {path}")
        if path.stat().st_size != expected_size:
            raise CleanupStateError(f"installed result size changed: {path}")
        if resolved in installed_resolved:
            raise CleanupStateError(f"installed result is duplicated: {path}")
        installed_paths.append(path)
        installed_resolved.add(resolved)

    results = _required_mapping(manifest, "results", "manifest")
    manifest_files = results.get("files")
    if not isinstance(manifest_files, list) or not all(
        isinstance(value, str) for value in manifest_files
    ):
        raise CleanupStateError("manifest result files are invalid")
    manifest_resolved = {Path(value).resolve() for value in manifest_files}
    if manifest_resolved != installed_resolved:
        raise CleanupStateError(
            "manifest result files do not match fetched result files"
        )

    raw_archive = results.get("archive")
    if result_present:
        if not isinstance(raw_archive, str):
            raise CleanupStateError("manifest result archive is invalid")
        if Path(raw_archive).resolve() != project.default_result_archive_path.resolve():
            raise CleanupStateError(
                "manifest result archive does not match current project"
            )
    elif raw_archive is not None:
        raise CleanupStateError("manifest result archive must be null after cleanup")
    return installed_paths


def _validate_result_archive_contents(
    project: WorkflowProject,
    installed_paths: list[Path],
) -> None:
    expected = {
        (
            f"{project.video_dir.name}/"
            f"{path.relative_to(project.video_dir).as_posix()}"
        ): path
        for path in installed_paths
    }
    try:
        with tarfile.open(project.default_result_archive_path, "r:gz") as archive:
            matching_members = [
                member for member in archive.getmembers() if member.name in expected
            ]
            if {member.name for member in matching_members} != set(expected):
                raise CleanupStateError(
                    "result archive does not contain all installed result files"
                )
            if len(matching_members) != len(expected):
                raise CleanupStateError(
                    "result archive contains duplicate installed result files"
                )
            for member in matching_members:
                if not member.isfile():
                    raise CleanupStateError(
                        f"result archive member is not a regular file: {member.name}"
                    )
                archived_file = archive.extractfile(member)
                if archived_file is None:
                    raise CleanupStateError(
                        f"result archive member cannot be read: {member.name}"
                    )
                with expected[member.name].open("rb") as installed_file:
                    if not _streams_equal(archived_file, installed_file):
                        raise CleanupStateError(
                            "installed result content does not match archive: "
                            f"{expected[member.name]}"
                        )
    except (OSError, tarfile.TarError) as exc:
        raise CleanupStateError(
            f"result archive cannot be validated: {exc}"
        ) from exc


def _new_pending_cleanup(
    project: WorkflowProject,
    package: dict[str, Any],
    result: dict[str, Any],
    targets: dict[str, Path],
) -> dict[str, Any]:
    token = uuid.uuid4().hex
    archives = {}
    for name, artifact in (("package", package), ("result", result)):
        entry = {
            **artifact,
            "deleted": name not in targets,
            "delete_requested": name in targets,
        }
        if name in targets:
            source = targets[name]
            entry["staging_path"] = str(
                project.workflow_dir / f".{source.name}.cleanup-{token}"
            )
            entry["staged"] = False
        archives[name] = entry
    return {
        "status": "pending",
        "started_at": utc_now(),
        "stage": "stage",
        "reclaimed_bytes": 0,
        "archives": archives,
    }


def _stage_cleanup_targets(
    project: WorkflowProject,
    state: dict[str, Any],
    targets: dict[str, Path],
) -> None:
    cleanup = _required_mapping(state, "cleanup", "remote state")
    archives = _required_mapping(cleanup, "archives", "cleanup state")
    staged_names: list[str] = []
    for name in ("package", "result"):
        if name not in targets:
            continue
        entry = _required_mapping(archives, name, "cleanup archives")
        staging_path = _validated_staging_path(
            project,
            entry,
            targets[name],
            name,
        )
        try:
            targets[name].replace(staging_path)
            entry["staged"] = True
            staged_names.append(name)
            cleanup["stage"] = f"staged-{name}"
            _write_state(project, state, f"record-staged-{name}")
        except OSError as exc:
            rollback_error = _rollback_staged_targets(
                project,
                archives,
                targets,
                staged_names,
            )
            if rollback_error is None:
                state["cleanup"] = {
                    "status": "failed",
                    "failed_at": utc_now(),
                    "stage": f"stage-{name}",
                    "error": str(exc),
                    "archives": {
                        artifact_name: {
                            key: value
                            for key, value in artifact.items()
                            if key not in {"staging_path", "staged"}
                        }
                        for artifact_name, artifact in archives.items()
                    },
                }
                _write_state(project, state, f"record-stage-{name}-failure")
            else:
                cleanup["stage"] = "rollback"
                cleanup["error"] = f"{exc}; rollback failed: {rollback_error}"
                _write_state(project, state, "record-rollback-failure")
            raise CleanupExecutionError(f"stage-{name} failed: {exc}") from exc


def _rollback_staged_targets(
    project: WorkflowProject,
    archives: dict[str, Any],
    targets: dict[str, Path],
    staged_names: list[str],
) -> OSError | None:
    for name in reversed(staged_names):
        entry = _required_mapping(archives, name, "cleanup archives")
        staging_path = _validated_staging_path(
            project,
            entry,
            targets[name],
            name,
        )
        try:
            staging_path.replace(targets[name])
            entry["staged"] = False
        except OSError as exc:
            return exc
    return None


def _continue_pending_cleanup(
    project: WorkflowProject,
    state: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    cleanup = _required_mapping(state, "cleanup", "remote state")
    archives = _required_mapping(cleanup, "archives", "cleanup state")
    expected_sources = {
        "package": project.default_package_path,
        "result": project.default_result_archive_path,
    }
    for name in ("package", "result"):
        entry = _required_mapping(archives, name, "cleanup archives")
        if entry.get("deleted") is True:
            continue
        source = Path(str(entry.get("path")))
        expected_source = expected_sources[name]
        if source.resolve() != expected_source.resolve():
            raise CleanupStateError(
                f"pending {name} archive path is unsafe: {source}"
            )
        staging_path = _validated_staging_path(
            project,
            entry,
            expected_source,
            name,
        )
        try:
            if staging_path.exists() and source.exists():
                raise CleanupStateError(
                    f"{name} archive was recreated while cleanup is pending"
                )
            if not staging_path.exists() and source.exists():
                source.replace(staging_path)
                entry["staged"] = True
            if staging_path.exists():
                staging_path.unlink()
            entry["deleted"] = True
            entry["staged"] = False
            cleanup["reclaimed_bytes"] = (
                int(cleanup.get("reclaimed_bytes", 0))
                + int(entry["size_bytes"])
            )
            cleanup["stage"] = f"deleted-{name}"
            _write_state(project, state, f"record-deleted-{name}")
        except CleanupStateError:
            raise
        except OSError as exc:
            cleanup["status"] = "pending"
            cleanup["stage"] = f"delete-staged-{name}"
            cleanup["error"] = str(exc)
            _write_state(project, state, f"record-delete-{name}-failure")
            raise CleanupExecutionError(
                f"delete-staged-{name} failed: {exc}"
            ) from exc

    completed_at = utc_now()
    try:
        _update_manifest_cleanup(
            project,
            manifest,
            archives,
            completed_at,
        )
    except OSError as exc:
        cleanup["status"] = "pending"
        cleanup["stage"] = "write-manifest"
        cleanup["error"] = str(exc)
        _write_state(project, state, "record-manifest-failure")
        raise CleanupExecutionError(f"write-manifest failed: {exc}") from exc

    final_archives = {
        name: {
            "path": entry["path"],
            "sha256": entry["sha256"],
            "size_bytes": entry["size_bytes"],
            "deleted": True,
        }
        for name, entry in archives.items()
    }
    state["cleanup"] = {
        "status": "completed",
        "completed_at": completed_at,
        "last_run_at": completed_at,
        "last_reclaimed_bytes": int(cleanup.get("reclaimed_bytes", 0)),
        "archives": final_archives,
    }
    _write_state(project, state, "write-completed-state")
    return state


def _validated_staging_path(
    project: WorkflowProject,
    entry: dict[str, Any],
    expected_source: Path,
    artifact_name: str,
) -> Path:
    raw_path = entry.get("staging_path")
    if not isinstance(raw_path, str):
        raise CleanupStateError("cleanup staging path is invalid")
    path = Path(raw_path)
    if (
        path.resolve().parent != project.workflow_dir.resolve()
        or not path.name.startswith(f".{expected_source.name}.cleanup-")
    ):
        raise CleanupStateError(
            f"pending {artifact_name} staging path is unsafe: {path}"
        )
    return path


def _update_manifest_cleanup(
    project: WorkflowProject,
    manifest: dict[str, Any],
    archives: dict[str, Any],
    completed_at: str,
) -> None:
    package_manifest = _required_mapping(manifest, "package", "manifest")
    package_manifest["path"] = None
    if (
        archives["package"].get("delete_requested") is True
        or not isinstance(package_manifest.get("archive_cleanup"), dict)
    ):
        package_manifest["archive_cleanup"] = _manifest_cleanup_entry(
            archives["package"],
            completed_at,
        )

    results = _required_mapping(manifest, "results", "manifest")
    results["archive"] = None
    if (
        archives["result"].get("delete_requested") is True
        or not isinstance(results.get("archive_cleanup"), dict)
    ):
        results["archive_cleanup"] = _manifest_cleanup_entry(
            archives["result"],
            completed_at,
        )
    write_manifest(project.manifest_path, manifest)


def _manifest_cleanup_entry(
    artifact: dict[str, Any],
    completed_at: str,
) -> dict[str, Any]:
    return {
        "path": artifact["path"],
        "sha256": artifact["sha256"],
        "size_bytes": artifact["size_bytes"],
        "cleaned_at": completed_at,
    }


def _validated_relative_path(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or "\\" in value
        or ".." in path.parts
        or not path.parts
    ):
        raise CleanupStateError(f"{label} is invalid: {value}")
    return path


def _load_json_object(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise CleanupStateError(f"{name} is required: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CleanupStateError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise CleanupStateError(f"{name} must be a JSON object: {path}")
    return data


def _required_mapping(
    value: dict[str, Any],
    key: str,
    context: str,
) -> dict[str, Any]:
    item = value.get(key)
    if not isinstance(item, dict):
        raise CleanupStateError(f"{context} {key} is required")
    return item


def _write_state(
    project: WorkflowProject,
    state: dict[str, Any],
    stage: str,
) -> None:
    try:
        write_remote_state(project.remote_state_path, state)
    except OSError as exc:
        raise CleanupExecutionError(f"{stage} failed: {exc}") from exc
