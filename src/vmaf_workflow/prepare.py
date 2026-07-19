from __future__ import annotations

import json
import math
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vmaf_workflow.config import FALLBACK_1080_LABEL, HIGH_1080_LABELS
from vmaf_workflow.download_state import DownloadStateError, invalidate_downstream
from vmaf_workflow.manifest import write_manifest
from vmaf_workflow.project import WorkflowProject
from vmaf_workflow.watermark_detection import (
    DETECTOR_NAME,
    WatermarkDetectionError,
    WatermarkGeometryError,
    detect_watermark,
    map_normalized_edges,
    outward_bbox,
    write_summary,
)


MEDIA_SUFFIXES = {".mkv", ".mov", ".mp4", ".webm"}
EXCLUDED_DIR_NAMES = {".workflow", ".yt-dlp-temp"}
MAX_WATERMARK_ASPECT_RATIO_RELATIVE_ERROR = 0.002


class PrepareError(ValueError):
    pass


def prepare_project(project: WorkflowProject, reference_path: Path) -> dict[str, Any]:
    reference = _register_reference(project, Path(reference_path))
    reference_rel = _relative_posix(reference, project.video_dir)
    inventory = build_media_inventory(project, reference_rel)
    if not any(entry["path"] == reference_rel for entry in inventory["files"]):
        raise PrepareError(f"reference is not a supported media file: {reference}")

    manifest = _load_existing_manifest(project.manifest_path)
    stale_result_paths = _recorded_local_vmaf_results(project, manifest)
    try:
        invalidate_downstream(project, manifest)
    except DownloadStateError as exc:
        raise PrepareError(str(exc)) from exc
    write_manifest(project.manifest_path, manifest)
    _remove_watermark_analysis(project)
    watermark_detection, exclusions = _prepare_watermark_detection(
        project,
        inventory,
        manifest,
        reference_rel,
        stale_result_paths,
    )
    inventory["watermark_detection"] = watermark_detection
    inventory["content_exclusions"] = exclusions
    write_manifest(project.media_inventory_path, inventory)
    update_manifest_pointers(project, reference_rel, manifest)
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


def update_manifest_pointers(
    project: WorkflowProject,
    reference_rel: str,
    manifest: dict[str, Any] | None = None,
) -> None:
    manifest = (
        _load_existing_manifest(project.manifest_path) if manifest is None else manifest
    )
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
                (
                    "stream=codec_name,width,height,avg_frame_rate,"
                    "sample_aspect_ratio,display_aspect_ratio:"
                    "stream_tags=rotate:stream_side_data=rotation"
                ),
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

    sample_aspect_ratio = _ratio_string(stream.get("sample_aspect_ratio"))
    display_aspect_ratio = _ratio_string(stream.get("display_aspect_ratio"))
    metadata["sample_aspect_ratio"] = sample_aspect_ratio
    metadata["display_aspect_ratio"] = display_aspect_ratio
    metadata["rotation"] = _rotation(stream)

    return metadata


def _prepare_watermark_detection(
    project: WorkflowProject,
    inventory: dict[str, Any],
    manifest: dict[str, Any],
    reference_rel: str,
    stale_result_paths: list[Path],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    bvid = _manifest_bvid(manifest)
    if bvid is None:
        return (
            {
                "applicable": False,
                "state": "not_applicable",
                "detector": DETECTOR_NAME,
            },
            [],
        )

    files = inventory["files"]
    representative = select_bilibili_representative(files, bvid)
    _validate_watermark_geometry(files, representative)
    reference = next(entry for entry in files if entry["path"] == reference_rel)
    try:
        result = detect_watermark(
            project.video_dir / representative["path"],
            project.video_dir / reference_rel,
            project.watermark_analysis_dir,
        )
    except (WatermarkDetectionError, WatermarkGeometryError) as exc:
        raise PrepareError(str(exc)) from exc

    summary = result.to_summary()
    summary["distorted"]["path"] = representative["path"]
    summary["reference"]["path"] = reference["path"]
    normalized_edges = result.normalized_edges
    media_mappings: list[dict[str, Any]] = []
    if normalized_edges is not None:
        for entry in files:
            real_edges = map_normalized_edges(
                normalized_edges,
                entry["width"],
                entry["height"],
            )
            audit_margin = entry["width"] * 8 / 1920
            media_mappings.append(
                {
                    "path": entry["path"],
                    "width": entry["width"],
                    "height": entry["height"],
                    "real_pixel_edges": real_edges,
                    "audit_margin_pixels": audit_margin,
                    "audit_bbox_with_margin": outward_bbox(
                        real_edges,
                        entry["width"],
                        entry["height"],
                        audit_margin,
                    ),
                }
            )
    summary["workflow"] = {
        "representative_path": representative["path"],
        "reference_path": reference["path"],
        "normalized_edges": normalized_edges,
        "media_mappings": media_mappings,
    }
    write_summary(project.watermark_summary_path, summary)

    if result.state in {"present", "uncertain"}:
        _remove_stale_local_vmaf_results(stale_result_paths)
    if result.state == "uncertain":
        raise PrepareError(
            "watermark detection found multiple candidates; inspect "
            f"{project.watermark_summary_path}"
        )

    detection = {
        "applicable": True,
        "state": result.state,
        "detector": DETECTOR_NAME,
        "representative": {
            "path": representative["path"],
            "width": representative["width"],
            "height": representative["height"],
        },
        "reference": {
            "path": reference["path"],
            "width": reference["width"],
            "height": reference["height"],
        },
        "analysis": {
            "width": result.analysis_width,
            "height": result.analysis_height,
            "summary_path": _relative_posix(
                project.watermark_summary_path, project.video_dir
            ),
        },
    }
    exclusions = []
    if normalized_edges is not None:
        exclusions.append(
            {
                "kind": "bilibili_watermark",
                "normalized_edges": normalized_edges,
            }
        )
    return detection, exclusions


def select_bilibili_representative(
    files: list[dict[str, Any]], bvid: str
) -> dict[str, Any]:
    quality_labels = (*HIGH_1080_LABELS, FALLBACK_1080_LABEL)
    filename_prefixes = tuple(f"{bvid}-{label}-" for label in quality_labels)
    candidates = [
        entry
        for entry in files
        if entry.get("role") == "distorted"
        and Path(str(entry.get("path", ""))).name.startswith(filename_prefixes)
        and entry.get("codec") == "h264"
    ]
    if len(candidates) != 1:
        raise PrepareError(
            "Bilibili watermark detection requires exactly one distorted "
            f"platform-labeled 1080P AVC file for {bvid!r}; "
            f"found {len(candidates)}"
        )
    return candidates[0]


def _validate_watermark_geometry(
    files: list[dict[str, Any]], representative: dict[str, Any]
) -> None:
    representative_width = representative.get("width")
    representative_height = representative.get("height")
    if not isinstance(representative_width, int) or not isinstance(
        representative_height, int
    ):
        raise PrepareError("watermark representative is missing decoded dimensions")
    for entry in files:
        path = entry.get("path", "<unknown>")
        width = entry.get("width")
        height = entry.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            raise PrepareError(f"watermark geometry is missing dimensions: {path}")
        sample_aspect_ratio = entry.get("sample_aspect_ratio")
        if sample_aspect_ratio not in {None, "1:1"}:
            raise PrepareError(
                f"watermark geometry requires square pixels: {path} has "
                f"SAR {sample_aspect_ratio}"
            )
        rotation = entry.get("rotation", 0)
        if rotation != 0:
            raise PrepareError(
                f"watermark geometry does not support rotation: {path} has "
                f"rotation {rotation}"
            )
        aspect_ratio = width / height
        representative_aspect_ratio = representative_width / representative_height
        if not math.isclose(
            aspect_ratio,
            representative_aspect_ratio,
            rel_tol=MAX_WATERMARK_ASPECT_RATIO_RELATIVE_ERROR,
            abs_tol=0.0,
        ):
            raise PrepareError(
                "watermark geometry requires decoded aspect ratios within "
                f"{MAX_WATERMARK_ASPECT_RATIO_RELATIVE_ERROR:.1%}: "
                f"{path} is {width}x{height}, representative is "
                f"{representative_width}x{representative_height}"
            )


def _manifest_bvid(manifest: dict[str, Any]) -> str | None:
    bilibili = manifest.get("bilibili")
    if bilibili is None:
        return None
    if not isinstance(bilibili, dict):
        raise PrepareError("manifest.json bilibili state is invalid")
    bvid = bilibili.get("bvid")
    if bvid is None:
        return None
    if not isinstance(bvid, str) or not bvid:
        raise PrepareError("manifest.json BVID is invalid")
    return bvid


def _remove_watermark_analysis(project: WorkflowProject) -> None:
    path = project.watermark_analysis_dir
    if path.is_symlink():
        raise PrepareError(f"watermark analysis path must not be a symlink: {path}")
    if path.exists() and not path.is_dir():
        raise PrepareError(f"watermark analysis path must be a directory: {path}")
    if path.is_dir():
        shutil.rmtree(path)


def _recorded_local_vmaf_results(
    project: WorkflowProject,
    manifest: dict[str, Any],
) -> list[Path]:
    results = manifest.get("results")
    raw_files = results.get("files", []) if isinstance(results, dict) else []
    if not isinstance(raw_files, list):
        raise PrepareError("manifest.json results files are invalid")
    paths: list[Path] = []
    root = project.video_dir.resolve()
    for raw_path in raw_files:
        if not isinstance(raw_path, str):
            raise PrepareError("manifest.json result file path is invalid")
        candidate = Path(raw_path)
        resolved = candidate.resolve()
        if not candidate.is_absolute():
            try:
                resolved.relative_to(root)
            except ValueError:
                resolved = (project.video_dir / candidate.name).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise PrepareError(
                f"manifest.json result file is outside project: {raw_path}"
            ) from exc
        if not resolved.name.endswith("_vmaf.json"):
            raise PrepareError(
                f"manifest.json result file is not a VMAF JSON: {raw_path}"
            )
        paths.append(resolved)
    return paths


def _remove_stale_local_vmaf_results(paths: list[Path]) -> None:
    for path in paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise PrepareError(f"managed VMAF result is not a regular file: {path}")
        if path.is_file():
            path.unlink()


def _ratio_string(value: Any) -> str | None:
    if not isinstance(value, str) or value in {"", "N/A", "0:1", "0/1"}:
        return None
    normalized = value.replace("/", ":")
    numerator, separator, denominator = normalized.partition(":")
    try:
        numerator_value = int(numerator)
        denominator_value = int(denominator) if separator else 0
    except ValueError:
        return value
    if numerator_value <= 0 or denominator_value <= 0:
        return None
    divisor = math.gcd(numerator_value, denominator_value)
    return f"{numerator_value // divisor}:{denominator_value // divisor}"


def _rotation(stream: dict[str, Any]) -> Any:
    side_data = stream.get("side_data_list")
    if isinstance(side_data, list):
        for item in side_data:
            if not isinstance(item, dict) or "rotation" not in item:
                continue
            try:
                return int(item["rotation"])
            except (TypeError, ValueError):
                return item["rotation"]
    tags = stream.get("tags")
    if isinstance(tags, dict) and "rotate" in tags:
        try:
            return int(tags["rotate"])
        except (TypeError, ValueError):
            return tags["rotate"]
    return 0


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
