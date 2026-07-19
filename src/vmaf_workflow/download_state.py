from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from vmaf_workflow.models import Manifest
from vmaf_workflow.project import WorkflowProject


DOWNSTREAM_MANIFEST_KEYS = (
    "reference",
    "media_inventory",
    "package",
    "remote_plan",
    "results",
)


class DownloadStateError(ValueError):
    pass


def load_download_manifest(path: Path) -> Manifest | None:
    if not path.exists():
        if path.is_symlink():
            raise DownloadStateError(f"manifest.json is not a regular file: {path}")
        return None
    if path.is_symlink() or not path.is_file():
        raise DownloadStateError(f"manifest.json is not a regular file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DownloadStateError(f"manifest.json is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise DownloadStateError(f"manifest.json must be a JSON object: {path}")
    return value


def validate_source_identity(
    existing: Manifest | None,
    bvid: str | None,
    youtube_url: str | None,
) -> None:
    if existing is None:
        return
    _validate_identity(existing, "bilibili", "bvid", bvid, "BVID")
    _validate_identity(existing, "youtube", "url", youtube_url, "YouTube URL")


def merge_download_manifest(
    existing: Manifest | None,
    current: Manifest,
    *,
    update_bilibili: bool,
    update_youtube: bool,
) -> Manifest:
    if existing is None:
        return deepcopy(current)

    merged = deepcopy(existing)
    for key in (
        "project_dir",
        "workflow_dir",
        "config_files",
        "dry_run",
    ):
        merged[key] = deepcopy(current[key])

    merged["created_at"] = deepcopy(existing.get("created_at", current["created_at"]))
    merged["updated_at"] = deepcopy(current["created_at"])

    for source, should_update in (
        ("bilibili", update_bilibili),
        ("youtube", update_youtube),
    ):
        if should_update or source not in merged:
            merged[source] = deepcopy(current[source])

    merged["commands"] = [
        *deepcopy(_command_history(existing, "existing")),
        *deepcopy(_command_history(current, "current")),
    ]
    return merged


def invalidate_downstream(project: WorkflowProject, manifest: Manifest) -> None:
    for path in (
        project.media_inventory_path,
        project.package_manifest_path,
        project.default_package_path,
        project.remote_plan_path,
        project.remote_plan_script_path,
        project.remote_state_path,
        project.remote_provenance_path,
        project.default_result_archive_path,
    ):
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise DownloadStateError(f"managed downstream path is not a file: {path}")
        if path.is_file():
            path.unlink()

    analysis_dir = project.watermark_analysis_dir
    if analysis_dir.is_symlink():
        raise DownloadStateError(
            f"managed watermark analysis path is a symlink: {analysis_dir}"
        )
    if analysis_dir.exists() and not analysis_dir.is_dir():
        raise DownloadStateError(
            f"managed watermark analysis path is not a directory: {analysis_dir}"
        )
    if analysis_dir.is_dir():
        shutil.rmtree(analysis_dir)

    for key in DOWNSTREAM_MANIFEST_KEYS:
        manifest.pop(key, None)


def _validate_identity(
    manifest: Manifest,
    source: str,
    identity_key: str,
    requested: str | None,
    label: str,
) -> None:
    if requested is None:
        return
    section = manifest.get(source)
    if section is None:
        return
    if not isinstance(section, dict):
        raise DownloadStateError(f"manifest.json {source} state is invalid")
    existing = section.get(identity_key)
    if existing is None:
        return
    if not isinstance(existing, str):
        raise DownloadStateError(f"manifest.json {label} is invalid")
    if existing != requested:
        raise DownloadStateError(
            f"{label} conflicts with existing project source: {existing}"
        )


def _command_history(manifest: Manifest, label: str) -> list[Any]:
    commands = manifest.get("commands", [])
    if not isinstance(commands, list):
        raise DownloadStateError(f"{label} manifest commands must be a list")
    return commands
