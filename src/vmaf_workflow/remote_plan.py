from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from vmaf_workflow.config import EasyVmafSettings
from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.project import WorkflowProject


class RemotePlanError(ValueError):
    pass


@dataclass(frozen=True)
class PlannedCommand:
    distorted: dict[str, Any]
    reference: dict[str, Any]
    model: str
    argv: list[str]

    def to_manifest(self) -> dict[str, Any]:
        return {
            "distorted": self.distorted,
            "reference": self.reference,
            "model": self.model,
            "command": self.argv,
        }


def write_remote_plan(
    project: WorkflowProject, settings: EasyVmafSettings
) -> dict[str, Any]:
    inventory = _load_json_object(
        project.media_inventory_path,
        "media-inventory.json",
    )
    package_manifest = _load_json_object(
        project.package_manifest_path,
        "package-manifest.json",
    )
    reference = _single_role_entry(inventory, "reference")
    distorted_entries = _role_entries(inventory, "distorted")
    package_archive = _package_archive_name(package_manifest)
    result_archive = f"{project.video_dir.name}-json.tar.gz"

    commands = [
        _planned_command(project, settings, reference, distorted)
        for distorted in distorted_entries
    ]
    plan = {
        "created_at": datetime.now(UTC).isoformat(),
        "project_dir": str(project.video_dir),
        "workflow_dir": str(project.workflow_dir),
        "easyvmaf_repo": settings.repo_dir.as_posix(),
        "easyvmaf_executable": settings.executable_path().as_posix(),
        "package_archive": package_archive,
        "result_archive": result_archive,
        "reference": reference,
        "commands": [command.to_manifest() for command in commands],
        "warnings": _resolution_warnings(reference, distorted_entries),
    }

    write_manifest(project.remote_plan_path, plan)
    _write_script(
        project.remote_plan_script_path,
        project.video_dir.name,
        package_archive,
        result_archive,
        commands,
    )
    _update_manifest_pointer(project)
    return plan


def _planned_command(
    project: WorkflowProject,
    settings: EasyVmafSettings,
    reference: dict[str, Any],
    distorted: dict[str, Any],
) -> PlannedCommand:
    model = _model_for_distorted(distorted, settings)
    argv = [
        settings.executable_path().as_posix(),
        "-d",
        f"{project.video_dir.name}/{distorted['path']}",
        "-r",
        f"{project.video_dir.name}/{reference['path']}",
    ]
    if settings.endsync:
        argv.append("-endsync")
    argv.extend(["-model", model, "-output_fmt", settings.output_fmt])
    if settings.threads is not None:
        if settings.threads < 1:
            raise RemotePlanError("easyVmaf threads must be greater than 0")
        argv.extend(["-threads", str(settings.threads)])
    return PlannedCommand(
        distorted=distorted,
        reference=reference,
        model=model,
        argv=argv,
    )


def _model_for_distorted(
    distorted: dict[str, Any], settings: EasyVmafSettings
) -> str:
    height = distorted.get("height")
    if isinstance(height, int):
        return "4K" if height >= settings.model_4k_min_height else "HD"

    path = distorted.get("path", "")
    if isinstance(path, str) and re.search(r"(4K|2160p)", path, re.IGNORECASE):
        return "4K"
    return "HD"


def _load_json_object(path: Path, name: str) -> dict[str, Any]:
    if not path.is_file():
        raise RemotePlanError(f"{name} is required: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RemotePlanError(f"{name} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise RemotePlanError(f"{name} must be a JSON object: {path}")
    return data


def _role_entries(inventory: dict[str, Any], role: str) -> list[dict[str, Any]]:
    files = inventory.get("files")
    if not isinstance(files, list):
        raise RemotePlanError("media-inventory.json must contain a files list")

    entries = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        if entry.get("role") != role:
            continue
        path = entry.get("path")
        if not isinstance(path, str):
            raise RemotePlanError("media-inventory.json entries must include path")
        _validate_project_relative_path(path)
        entries.append(entry)
    return entries


def _single_role_entry(inventory: dict[str, Any], role: str) -> dict[str, Any]:
    entries = _role_entries(inventory, role)
    if len(entries) != 1:
        raise RemotePlanError(f"media-inventory.json must contain one {role}")
    return entries[0]


def _validate_project_relative_path(relative_path: str) -> None:
    pure_path = PurePosixPath(relative_path)
    if (
        pure_path.is_absolute()
        or "\\" in relative_path
        or ".." in pure_path.parts
        or not pure_path.parts
    ):
        raise RemotePlanError(f"inventory path is outside project: {relative_path}")


def _package_archive_name(package_manifest: dict[str, Any]) -> str:
    archive_path = package_manifest.get("archive_path")
    if not isinstance(archive_path, str) or not archive_path:
        raise RemotePlanError("package-manifest.json must contain archive_path")
    return Path(archive_path).name


def _resolution_warnings(
    reference: dict[str, Any], distorted_entries: list[dict[str, Any]]
) -> list[str]:
    reference_resolution = reference.get("resolution")
    warnings = []
    for distorted in distorted_entries:
        distorted_resolution = distorted.get("resolution")
        if (
            isinstance(reference_resolution, str)
            and isinstance(distorted_resolution, str)
            and distorted_resolution != reference_resolution
        ):
            warnings.append(
                "resolution differs: "
                f"{distorted['path']} is {distorted_resolution}, "
                f"reference is {reference_resolution}"
            )
    return warnings


def _write_script(
    path: Path,
    archive_root: str,
    package_archive: str,
    result_archive: str,
    commands: list[PlannedCommand],
) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        f"tar -xf {_shell_quote(package_archive)}",
        "",
    ]
    lines.extend(_shell_join(command.argv) for command in commands)
    lines.extend(
        (
            "",
            f"tar -czf {_shell_quote(result_archive)} {archive_root}/*.json",
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _shell_join(argv: list[str]) -> str:
    return " ".join(_shell_quote(arg) for arg in argv)


def _shell_quote(value: str) -> str:
    quoted = shlex.quote(value)
    return value if quoted == value else quoted


def _update_manifest_pointer(project: WorkflowProject) -> None:
    manifest = _load_existing_manifest(project.manifest_path)
    manifest["project_dir"] = str(project.video_dir)
    manifest["workflow_dir"] = str(project.workflow_dir)
    manifest["remote_plan"] = {
        "manifest": str(project.remote_plan_path),
        "script": str(project.remote_plan_script_path),
    }
    write_manifest(project.manifest_path, manifest)


def _load_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RemotePlanError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise RemotePlanError(f"manifest must be a JSON object: {path}")
    return data
