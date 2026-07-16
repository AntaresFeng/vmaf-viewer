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
    expected_result: str

    def to_manifest(self) -> dict[str, Any]:
        return {
            "distorted": self.distorted,
            "reference": self.reference,
            "model": self.model,
            "command": self.argv,
            "expected_result": self.expected_result,
        }


def write_remote_plan(
    project: WorkflowProject, settings: EasyVmafSettings
) -> dict[str, Any]:
    if settings.output_fmt != "json":
        raise RemotePlanError("remote-plan requires easyVmaf JSON output")
    if settings.ffmpeg_min_major < 1:
        raise RemotePlanError("FFmpeg minimum major version must be greater than 0")
    if not settings.required_branch.strip():
        raise RemotePlanError("easyVmaf required branch must not be empty")

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
    if not distorted_entries:
        raise RemotePlanError(
            "media-inventory.json must contain at least one distorted file"
        )
    package_archive = _validate_package(project, inventory, package_manifest)
    result_archive = f"{project.video_dir.name}-json.tar.gz"

    commands = [
        _planned_command(project, settings, reference, distorted)
        for distorted in distorted_entries
    ]
    _validate_unique_result_paths(commands)
    executable = settings.executable_path().as_posix()
    plan = {
        "created_at": datetime.now(UTC).isoformat(),
        "project_dir": str(project.video_dir),
        "workflow_dir": str(project.workflow_dir),
        "easyvmaf_repo": settings.repo_dir.as_posix(),
        "easyvmaf_executable": executable,
        "package_archive": package_archive,
        "result_archive": result_archive,
        "preflight_argument": "--preflight-only",
        "requirements": {
            "ffmpeg": {
                "minimum_major": settings.ffmpeg_min_major,
                "required_filter": "libvmaf",
            },
            "ffprobe": {"minimum_major": settings.ffmpeg_min_major},
            "easyvmaf": {
                "repo": settings.repo_dir.as_posix(),
                "executable": executable,
                "required_branch": settings.required_branch,
            },
        },
        "reference": reference,
        "commands": [command.to_manifest() for command in commands],
        "expected_results": [command.expected_result for command in commands],
        "warnings": _resolution_warnings(reference, distorted_entries),
    }

    write_manifest(project.remote_plan_path, plan)
    _write_script(
        project.remote_plan_script_path,
        project.video_dir.name,
        package_archive,
        result_archive,
        commands,
        settings,
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
    expected_result = _expected_result_path(
        project.video_dir.name,
        distorted["path"],
        settings.output_fmt,
    )
    return PlannedCommand(
        distorted=distorted,
        reference=reference,
        model=model,
        argv=argv,
        expected_result=expected_result,
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


def _validate_package(
    project: WorkflowProject,
    inventory: dict[str, Any],
    package_manifest: dict[str, Any],
) -> str:
    archive_path = package_manifest.get("archive_path")
    if not isinstance(archive_path, str) or not archive_path:
        raise RemotePlanError("package-manifest.json must contain archive_path")
    archive_root = package_manifest.get("archive_root")
    if archive_root != project.video_dir.name:
        raise RemotePlanError(
            "package manifest archive_root does not match project; rerun package"
        )

    inventory_files = _package_file_signatures(
        inventory.get("files"),
        "media-inventory.json",
    )
    packaged_files = _package_file_signatures(
        package_manifest.get("media_files"),
        "package-manifest.json",
    )
    if inventory_files != packaged_files:
        raise RemotePlanError(
            "package manifest does not match media inventory; rerun package"
        )

    package_path = Path(archive_path)
    fallback_path = project.workflow_dir / package_path.name
    if not package_path.is_file() and not fallback_path.is_file():
        raise RemotePlanError(f"package archive is required: {archive_path}")
    return Path(archive_path).name


def _package_file_signatures(
    raw_entries: Any,
    source_name: str,
) -> list[tuple[str, str | None, int | None]]:
    if not isinstance(raw_entries, list):
        raise RemotePlanError(f"{source_name} must contain a media files list")

    signatures = []
    for entry in raw_entries:
        if not isinstance(entry, dict):
            raise RemotePlanError(f"{source_name} media entries must be objects")
        path = entry.get("path")
        if not isinstance(path, str):
            raise RemotePlanError(f"{source_name} media entries must include path")
        _validate_project_relative_path(path)
        role = entry.get("role")
        size_bytes = entry.get("size_bytes")
        signatures.append(
            (
                path,
                role if isinstance(role, str) else None,
                size_bytes if isinstance(size_bytes, int) else None,
            )
        )
    return sorted(
        signatures,
        key=lambda item: (
            item[0],
            item[1] or "",
            item[2] if item[2] is not None else -1,
        ),
    )


def _expected_result_path(
    archive_root: str,
    distorted_path: str,
    output_fmt: str,
) -> str:
    input_path = PurePosixPath(archive_root) / PurePosixPath(distorted_path)
    without_suffix = input_path.with_suffix("")
    return f"{without_suffix.as_posix()}_vmaf.{output_fmt}"


def _validate_unique_result_paths(commands: list[PlannedCommand]) -> None:
    seen = set()
    for command in commands:
        if command.expected_result in seen:
            raise RemotePlanError(
                f"result path collision: {command.expected_result}"
            )
        seen.add(command.expected_result)


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
    settings: EasyVmafSettings,
) -> None:
    reference_path = (
        f"{archive_root}/{commands[0].reference['path']}" if commands else ""
    )
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "die() {",
        "  printf 'error: %s\\n' \"$*\" >&2",
        "  exit 1",
        "}",
        "",
        "info() {",
        "  printf 'info: %s\\n' \"$*\"",
        "}",
        "",
        "require_command() {",
        "  command -v \"$1\" >/dev/null 2>&1 || die \"$1 is required but was not found in PATH\"",
        "}",
        "",
        "check_version() {",
        "  local tool=$1",
        "  local minimum=$2",
        "  local output",
        "  local line",
        "  local major",
        "  output=$(\"$tool\" -version 2>&1) || die \"failed to run $tool -version\"",
        "  line=${output%%$'\\n'*}",
        "  info \"$line\"",
        "  if [[ $line =~ version[[:space:]]+[^0-9]*([0-9]+) ]]; then",
        "    major=${BASH_REMATCH[1]}",
        "  else",
        "    die \"could not parse $tool major version: $line\"",
        "  fi",
        "  (( major >= minimum )) || die \"$tool major version $major is below required $minimum\"",
        "}",
        "",
        f"EASYVMAF_REPO={_shell_quote(settings.repo_dir.as_posix())}",
        (
            "EASYVMAF_EXECUTABLE="
            f"{_shell_quote(settings.executable_path().as_posix())}"
        ),
        f"EASYVMAF_REQUIRED_BRANCH={_shell_quote(settings.required_branch)}",
        f"PACKAGE_ARCHIVE={_shell_quote(package_archive)}",
        f"RESULT_ARCHIVE={_shell_quote(result_archive)}",
        'MODE=${1:-run}',
        "",
        '[[ $# -le 1 ]] || die "usage: $0 [--preflight-only]"',
        'case "$MODE" in',
        "  run|--preflight-only) ;;",
        '  *) die "usage: $0 [--preflight-only]" ;;',
        "esac",
        "",
        "require_command tar",
        "require_command ffmpeg",
        "require_command ffprobe",
        "require_command git",
        f"check_version ffmpeg {settings.ffmpeg_min_major}",
        f"check_version ffprobe {settings.ffmpeg_min_major}",
        (
            "ffmpeg -hide_banner -h filter=libvmaf >/dev/null 2>&1 "
            '|| die "ffmpeg does not provide the required libvmaf filter"'
        ),
        (
            'git -C "$EASYVMAF_REPO" rev-parse --is-inside-work-tree '
            '>/dev/null 2>&1 || die "easyVmaf repo is not a Git work tree: '
            '$EASYVMAF_REPO"'
        ),
        (
            'easyvmaf_branch=$(git -C "$EASYVMAF_REPO" '
            "symbolic-ref --quiet --short HEAD) "
            '|| die "easyVmaf repository is in detached HEAD state; '
            'expected branch: $EASYVMAF_REQUIRED_BRANCH"'
        ),
        (
            '[[ "$easyvmaf_branch" == "$EASYVMAF_REQUIRED_BRANCH" ]] '
            '|| die "easyVmaf branch mismatch: expected '
            '$EASYVMAF_REQUIRED_BRANCH, got $easyvmaf_branch"'
        ),
        'easyvmaf_revision=$(git -C "$EASYVMAF_REPO" rev-parse --short HEAD)',
        'info "easyVmaf branch: $easyvmaf_branch"',
        'info "easyVmaf revision: $easyvmaf_revision"',
        (
            '[[ -x "$EASYVMAF_EXECUTABLE" ]] '
            '|| die "easyVmaf executable is missing or not executable: '
            '$EASYVMAF_EXECUTABLE"'
        ),
        (
            '"$EASYVMAF_EXECUTABLE" --help >/dev/null 2>&1 '
            '|| die "easyVmaf executable failed its help check"'
        ),
        (
            '[[ -f "$PACKAGE_ARCHIVE" ]] '
            '|| die "package archive is missing: $PACKAGE_ARCHIVE"'
        ),
        (
            'tar -tf "$PACKAGE_ARCHIVE" >/dev/null '
            '|| die "package archive is not a readable tar file: $PACKAGE_ARCHIVE"'
        ),
        'if [[ $MODE == --preflight-only ]]; then',
        '  info "preflight complete"',
        "  exit 0",
        "fi",
        "",
        'tar -xf "$PACKAGE_ARCHIVE"',
        (
            f"[[ -f {_shell_quote(reference_path)} ]] "
            f"|| die {_shell_quote(f'reference input is missing: {reference_path}')}"
        ),
        "",
    ]
    for index, command in enumerate(commands, start=1):
        distorted_path = f"{archive_root}/{command.distorted['path']}"
        lines.extend(
            (
                (
                    f"[[ -f {_shell_quote(distorted_path)} ]] "
                    f"|| die {_shell_quote(f'distorted input is missing: {distorted_path}')}"
                ),
                (
                    "info "
                    + _shell_quote(
                        f"running {index}/{len(commands)}: "
                        f"{command.distorted['path']} (model {command.model})"
                    )
                ),
                f"rm -f -- {_shell_quote(command.expected_result)}",
                _script_command(command.argv),
                (
                    f"[[ -s {_shell_quote(command.expected_result)} ]] "
                    f"|| die {_shell_quote(f'easyVmaf result is missing or empty: {command.expected_result}')}"
                ),
                "",
            )
        )
    lines.extend(
        (
            (
                'tar -czf "$RESULT_ARCHIVE" -- '
                + " ".join(
                    _shell_quote(command.expected_result) for command in commands
                )
            ),
            (
                'tar -tzf "$RESULT_ARCHIVE" >/dev/null '
                '|| die "result archive verification failed: $RESULT_ARCHIVE"'
            ),
            'info "result archive: $RESULT_ARCHIVE"',
            "",
        )
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def _shell_join(argv: list[str]) -> str:
    return " ".join(_shell_quote(arg) for arg in argv)


def _script_command(argv: list[str]) -> str:
    return '"$EASYVMAF_EXECUTABLE" ' + _shell_join(argv[1:])


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
