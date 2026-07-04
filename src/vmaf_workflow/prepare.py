from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.project import WorkflowProject


MEDIA_SUFFIXES = {".mkv", ".mov", ".mp4", ".webm"}
EXCLUDED_DIR_NAMES = {".workflow", ".yt-dlp-temp"}


class PrepareError(ValueError):
    pass


def prepare_project(project: WorkflowProject, reference_path: Path) -> dict[str, Any]:
    reference = _register_reference(project, Path(reference_path))
    reference_rel = _relative_posix(reference, project.video_dir)
    inventory = build_media_inventory(project, reference_rel)
    if not any(entry["path"] == reference_rel for entry in inventory["files"]):
        raise PrepareError(f"reference is not a supported media file: {reference}")

    write_manifest(project.media_inventory_path, inventory)
    update_manifest_pointers(project, reference_rel)
    return inventory


def build_media_inventory(
    project: WorkflowProject, reference_rel: str
) -> dict[str, Any]:
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "project_dir": str(project.video_dir),
        "workflow_dir": str(project.workflow_dir),
        "reference": reference_rel,
        "files": [
            _media_entry(path, project.video_dir, reference_rel)
            for path in _iter_media_files(project.video_dir)
        ],
    }


def update_manifest_pointers(project: WorkflowProject, reference_rel: str) -> None:
    manifest = _load_existing_manifest(project.manifest_path)
    manifest["project_dir"] = str(project.video_dir)
    manifest["workflow_dir"] = str(project.workflow_dir)
    manifest["reference"] = {"path": reference_rel}
    manifest["media_inventory"] = str(project.media_inventory_path)
    write_manifest(project.manifest_path, manifest)


def _register_reference(project: WorkflowProject, reference: Path) -> Path:
    if not reference.is_file():
        raise PrepareError(f"reference file does not exist: {reference}")

    if _is_inside(reference, project.video_dir):
        return reference

    destination = project.video_dir / reference.name
    if destination.exists():
        raise PrepareError(f"reference destination already exists: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(reference, destination)
    return destination


def _iter_media_files(root: Path) -> list[Path]:
    files = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in MEDIA_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
            continue
        files.append(path)
    return sorted(files, key=lambda item: _relative_posix(item, root).lower())


def _media_entry(path: Path, root: Path, reference_rel: str) -> dict[str, Any]:
    relative = _relative_posix(path, root)
    entry: dict[str, Any] = {
        "path": relative,
        "role": "reference" if relative == reference_rel else "distorted",
        "size_bytes": path.stat().st_size,
        "suffix": path.suffix.lower(),
    }
    entry.update(_probe_media(path))
    return entry


def _probe_media(path: Path) -> dict[str, Any]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,avg_frame_rate",
                "-show_entries",
                "format=format_name",
                "-of",
                "json",
                str(path),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return {}

    if result.returncode != 0:
        return {}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    stream = _first_video_stream(data)
    if stream is None:
        return {}

    metadata: dict[str, Any] = {}
    width = _positive_int(stream.get("width"))
    height = _positive_int(stream.get("height"))
    if width is not None:
        metadata["width"] = width
    if height is not None:
        metadata["height"] = height
    if width is not None and height is not None:
        metadata["resolution"] = f"{width}x{height}"

    fps = _parse_fraction(stream.get("avg_frame_rate"))
    if fps is not None:
        metadata["fps"] = fps

    codec = stream.get("codec_name")
    if isinstance(codec, str) and codec:
        metadata["codec"] = codec

    format_name = data.get("format", {}).get("format_name")
    if isinstance(format_name, str) and format_name:
        metadata["container"] = format_name

    return metadata


def _first_video_stream(data: dict[str, Any]) -> dict[str, Any] | None:
    streams = data.get("streams")
    if not isinstance(streams, list) or not streams:
        return None
    stream = streams[0]
    return stream if isinstance(stream, dict) else None


def _positive_int(value: Any) -> int | None:
    if not isinstance(value, int) or value <= 0:
        return None
    return value


def _parse_fraction(value: Any) -> float | None:
    if not isinstance(value, str) or value in {"", "0/0"}:
        return None
    numerator_text, separator, denominator_text = value.partition("/")
    try:
        numerator = float(numerator_text)
        denominator = float(denominator_text) if separator else 1.0
    except ValueError:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def _load_existing_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PrepareError(f"manifest is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise PrepareError(f"manifest must be a JSON object: {path}")
    return data


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()
