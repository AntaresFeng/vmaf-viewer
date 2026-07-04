from __future__ import annotations

import json
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.project import WorkflowProject


class PackageError(ValueError):
    pass


@dataclass(frozen=True)
class PackageFile:
    source_path: Path
    archive_path: str
    role: str | None = None
    size_bytes: int | None = None


def package_project(
    project: WorkflowProject, output_path: Path | None = None
) -> dict[str, Any]:
    inventory = _load_inventory(project.media_inventory_path)
    package_path = output_path or project.default_package_path
    media_files = _media_files_from_inventory(project, inventory)

    package_manifest = _write_package_metadata(project, package_path, media_files)
    workflow_files = _workflow_files(project)
    _write_tar(project, package_path, [*media_files, *workflow_files])
    return package_manifest


def _load_inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PackageError(f"media-inventory.json is required: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"media-inventory.json is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"media-inventory.json must be a JSON object: {path}")
    files = data.get("files")
    if not isinstance(files, list):
        raise PackageError("media-inventory.json must contain a files list")
    return data


def _media_files_from_inventory(
    project: WorkflowProject, inventory: dict[str, Any]
) -> list[PackageFile]:
    media_files = []
    for raw_entry in inventory["files"]:
        if not isinstance(raw_entry, dict) or not isinstance(
            raw_entry.get("path"), str
        ):
            raise PackageError("media-inventory.json file entries must include path")
        relative_path = raw_entry["path"]
        source_path = _project_relative_path(project.video_dir, relative_path)
        if not source_path.is_file():
            raise PackageError(f"inventory media file is missing: {relative_path}")
        role = raw_entry.get("role")
        media_files.append(
            PackageFile(
                source_path=source_path,
                archive_path=relative_path,
                role=role if isinstance(role, str) else None,
                size_bytes=source_path.stat().st_size,
            )
        )
    return media_files


def _write_package_metadata(
    project: WorkflowProject, package_path: Path, media_files: list[PackageFile]
) -> dict[str, Any]:
    manifest = _load_optional_json_object(project.manifest_path)
    manifest["project_dir"] = str(project.video_dir)
    manifest["workflow_dir"] = str(project.workflow_dir)
    manifest["package"] = {
        "path": str(package_path),
        "manifest": str(project.package_manifest_path),
    }
    write_manifest(project.manifest_path, manifest)

    package_manifest = {
        "created_at": datetime.now(UTC).isoformat(),
        "project_dir": str(project.video_dir),
        "archive_path": str(package_path),
        "archive_root": project.video_dir.name,
        "inventory_path": str(project.media_inventory_path),
        "media_files": [
            {
                "path": file.archive_path,
                "role": file.role,
                "size_bytes": file.size_bytes,
            }
            for file in media_files
        ],
        "workflow_files": [
            ".workflow/manifest.json",
            ".workflow/media-inventory.json",
            ".workflow/package-manifest.json",
        ],
    }
    write_manifest(project.package_manifest_path, package_manifest)
    return package_manifest


def _workflow_files(project: WorkflowProject) -> list[PackageFile]:
    paths = [
        project.manifest_path,
        project.media_inventory_path,
        project.package_manifest_path,
    ]
    files = []
    for path in paths:
        if path.is_file():
            files.append(
                PackageFile(
                    source_path=path,
                    archive_path=_relative_posix(path, project.video_dir),
                    size_bytes=path.stat().st_size,
                )
            )
    return files


def _write_tar(
    project: WorkflowProject, package_path: Path, files: list[PackageFile]
) -> None:
    package_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = package_path.with_name(f"{package_path.name}.tmp")
    if temp_path.exists():
        temp_path.unlink()
    try:
        with tarfile.open(temp_path, "w") as archive:
            for file in files:
                archive.add(
                    file.source_path,
                    arcname=f"{project.video_dir.name}/{file.archive_path}",
                )
        temp_path.replace(package_path)
    except OSError as exc:
        if temp_path.exists():
            temp_path.unlink()
        raise PackageError(f"failed to create package: {exc}") from exc


def _load_optional_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PackageError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PackageError(f"manifest must be a JSON object: {path}")
    return data


def _project_relative_path(root: Path, relative_path: str) -> Path:
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or "\\" in relative_path
        or ".." in pure_path.parts
        or not pure_path.parts
    ):
        raise PackageError(f"inventory path is outside project: {relative_path}")
    return root.joinpath(*pure_path.parts)


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
