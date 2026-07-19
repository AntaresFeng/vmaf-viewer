from __future__ import annotations

import json
import math
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import Any, Literal, Sequence

import cv2
import numpy as np


DETECTOR_NAME = "reference-assisted-positive-residual-v1"
DEFAULT_SAMPLES = 9
DEFAULT_ANALYSIS_WIDTH = 960
DEFAULT_EDGE_RATIO = 0.24
DEFAULT_MINIMUM_FREQUENCY = 0.56
DEFAULT_MINIMUM_MEDIAN_Z = 3.0
DEFAULT_SYNC_RADIUS_FRAMES = 1
MAX_SAMPLE_WORKERS = 4


class WatermarkDetectionError(RuntimeError):
    pass


class WatermarkGeometryError(ValueError):
    pass


@dataclass(frozen=True)
class DetectionSettings:
    samples: int = DEFAULT_SAMPLES
    analysis_width: int = DEFAULT_ANALYSIS_WIDTH
    edge_ratio: float = DEFAULT_EDGE_RATIO
    minimum_frequency: float = DEFAULT_MINIMUM_FREQUENCY
    minimum_median_z: float = DEFAULT_MINIMUM_MEDIAN_Z
    sync_radius_frames: int = DEFAULT_SYNC_RADIUS_FRAMES
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"


@dataclass(frozen=True)
class MediaInfo:
    path: str
    width: int
    height: int
    fps: float
    duration: float


@dataclass(frozen=True)
class Candidate:
    x: int
    y: int
    width: int
    height: int
    x_norm: float
    y_norm: float
    width_norm: float
    height_norm: float
    corner: str
    score: float
    median_z: float
    frequency: float
    pixels: int

    def normalized_edges(self) -> dict[str, float]:
        return {
            "left": round(self.x_norm, 12),
            "top": round(self.y_norm, 12),
            "right": round(self.x_norm + self.width_norm, 12),
            "bottom": round(self.y_norm + self.height_norm, 12),
        }


@dataclass(frozen=True)
class DetectionResult:
    state: Literal["present", "absent", "uncertain"]
    distorted: MediaInfo
    reference: MediaInfo
    analysis_width: int
    analysis_height: int
    candidates: tuple[Candidate, ...]
    alignments: tuple[dict[str, float], ...]
    settings: DetectionSettings
    output_dir: str

    @property
    def analysis_edges(self) -> dict[str, int] | None:
        if self.state != "present":
            return None
        candidate = self.candidates[0]
        return {
            "left": candidate.x,
            "top": candidate.y,
            "right": candidate.x + candidate.width,
            "bottom": candidate.y + candidate.height,
        }

    @property
    def normalized_edges(self) -> dict[str, float] | None:
        if self.state != "present":
            return None
        return self.candidates[0].normalized_edges()

    def to_summary(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "method": DETECTOR_NAME,
            "state": self.state,
            "distorted": asdict(self.distorted),
            "reference": asdict(self.reference),
            "analysis_size": {
                "width": self.analysis_width,
                "height": self.analysis_height,
            },
            "analysis_edges": self.analysis_edges,
            "normalized_edges": self.normalized_edges,
            "settings": {
                "samples": self.settings.samples,
                "analysis_width": self.settings.analysis_width,
                "edge_ratio": self.settings.edge_ratio,
                "minimum_frequency": self.settings.minimum_frequency,
                "minimum_median_z": self.settings.minimum_median_z,
                "sync_radius_frames": self.settings.sync_radius_frames,
                "sample_workers": min(MAX_SAMPLE_WORKERS, self.settings.samples),
            },
            "alignments": list(self.alignments),
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "diagnostics": {
                name: name
                for name in (
                    "contact-sheet.png",
                    "candidate-overlay.png",
                    "positive-z.png",
                    "frequency.png",
                    "candidate-mask.png",
                )
            },
        }


def detect_watermark(
    distorted_path: Path,
    reference_path: Path,
    output_dir: Path,
    settings: DetectionSettings | None = None,
) -> DetectionResult:
    active_settings = settings or DetectionSettings()
    distorted_path = Path(distorted_path).resolve()
    reference_path = Path(reference_path).resolve()
    output_dir = Path(output_dir).resolve()
    if not distorted_path.is_file():
        raise WatermarkDetectionError(
            f"distorted video does not exist: {distorted_path}"
        )
    if not reference_path.is_file():
        raise WatermarkDetectionError(
            f"reference video does not exist: {reference_path}"
        )
    if shutil.which(active_settings.ffmpeg) is None:
        raise WatermarkDetectionError(
            f"ffmpeg executable was not found: {active_settings.ffmpeg}"
        )
    if shutil.which(active_settings.ffprobe) is None:
        raise WatermarkDetectionError(
            f"ffprobe executable was not found: {active_settings.ffprobe}"
        )

    try:
        distorted_info = probe_media(distorted_path, active_settings.ffprobe)
        reference_info = probe_media(reference_path, active_settings.ffprobe)
        size = analysis_size(distorted_info, active_settings.analysis_width)
        timestamps = sample_times(
            min(distorted_info.duration, reference_info.duration),
            active_settings.samples,
        )
        sample_results = analyze_samples(
            timestamps,
            distorted_path=distorted_path,
            reference_path=reference_path,
            reference_info=reference_info,
            size=size,
            radius_frames=active_settings.sync_radius_frames,
            ffmpeg=active_settings.ffmpeg,
        )
        positive_stack = np.stack([item[0] for item in sample_results])
        absolute_stack = np.stack([item[1] for item in sample_results])
        preview_frames = [item[2] for item in sample_results]
        alignments = tuple(item[3] for item in sample_results)
        median_z = np.median(positive_stack, axis=0)
        median_absolute = np.median(absolute_stack, axis=0)
        frequency = np.mean(
            positive_stack >= active_settings.minimum_median_z,
            axis=0,
        )
        candidates, active_mask = find_candidates(
            median_z,
            frequency,
            edge_search_mask(median_z.shape, active_settings.edge_ratio),
            active_settings.minimum_median_z,
            active_settings.minimum_frequency,
        )
        write_outputs(
            output_dir,
            preview_frames[len(preview_frames) // 2],
            median_z,
            median_absolute,
            frequency,
            active_mask,
            candidates,
        )
    except WatermarkDetectionError:
        raise
    except (
        OSError,
        ValueError,
        KeyError,
        IndexError,
        subprocess.SubprocessError,
    ) as exc:
        raise WatermarkDetectionError(f"watermark detection failed: {exc}") from exc

    state: Literal["present", "absent", "uncertain"]
    if not candidates:
        state = "absent"
    elif len(candidates) == 1:
        state = "present"
    else:
        state = "uncertain"
    result = DetectionResult(
        state=state,
        distorted=distorted_info,
        reference=reference_info,
        analysis_width=size[0],
        analysis_height=size[1],
        candidates=tuple(candidates[:20]),
        alignments=alignments,
        settings=active_settings,
        output_dir=str(output_dir),
    )
    write_summary(output_dir / "summary.json", result.to_summary())
    return result


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, allow_nan=False, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def probe_media(path: Path, ffprobe: str) -> MediaInfo:
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height,avg_frame_rate,r_frame_rate",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        "--",
        str(path),
    ]
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        fps = parse_fraction(stream.get("avg_frame_rate"))
        if fps <= 0:
            fps = parse_fraction(stream.get("r_frame_rate"))
        width = int(stream["width"])
        height = int(stream["height"])
        duration = float(payload["format"]["duration"])
    except (
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:
        raise WatermarkDetectionError(
            f"failed to probe watermark input {path}: {exc}"
        ) from exc
    if width <= 0 or height <= 0 or duration <= 0:
        raise WatermarkDetectionError(f"invalid watermark input geometry: {path}")
    return MediaInfo(str(path.resolve()), width, height, fps, duration)


def parse_fraction(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    numerator, separator, denominator = value.partition("/")
    try:
        denominator_value = float(denominator) if separator else 1.0
        return float(numerator) / denominator_value if denominator_value else 0.0
    except ValueError:
        return 0.0


def analysis_size(info: MediaInfo, width: int) -> tuple[int, int]:
    if width < 64:
        raise ValueError("analysis width must be at least 64 pixels")
    height = max(2, round(width * info.height / info.width))
    if height % 2:
        height += 1
    return width, height


def sample_times(duration: float, count: int) -> list[float]:
    if duration <= 0:
        raise ValueError("duration must be positive")
    if count < 3:
        raise ValueError("at least three samples are required")
    return np.linspace(duration * 0.08, duration * 0.92, count).tolist()


def extract_gray_frames(
    path: Path,
    timestamp: float,
    count: int,
    size: tuple[int, int],
    ffmpeg: str,
) -> np.ndarray:
    if count < 1:
        raise ValueError("frame count must be positive")
    width, height = size
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp:.6f}",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-frames:v",
        str(count),
        "-vf",
        f"scale={width}:{height}:flags=lanczos,format=gray",
        "-pix_fmt",
        "gray",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise WatermarkDetectionError(
            f"failed to decode watermark sample from {path}: {exc}"
        ) from exc
    expected = count * width * height
    if len(result.stdout) != expected:
        raise WatermarkDetectionError(
            f"ffmpeg returned {len(result.stdout)} frame bytes for {count} frames, "
            f"expected {expected}"
        )
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(count, height, width)


def extract_gray_frame(
    path: Path,
    timestamp: float,
    size: tuple[int, int],
    ffmpeg: str,
) -> np.ndarray:
    return extract_gray_frames(path, timestamp, 1, size, ffmpeg)[0]


def robust_scale(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return max(1.0, 1.4826 * mad)


def normalize_reference(reference: np.ndarray, distorted: np.ndarray) -> np.ndarray:
    height, width = reference.shape
    y0, y1 = round(height * 0.2), round(height * 0.8)
    x0, x1 = round(width * 0.2), round(width * 0.8)
    reference_center = reference[y0:y1, x0:x1].astype(np.float32)
    distorted_center = distorted[y0:y1, x0:x1].astype(np.float32)
    reference_mean = float(np.median(reference_center))
    distorted_mean = float(np.median(distorted_center))
    reference_scale = robust_scale(reference_center)
    distorted_scale = robust_scale(distorted_center)
    gain = distorted_scale / reference_scale if reference_scale > 0 else 1.0
    gain = min(1.25, max(0.8, gain))
    normalized = (reference.astype(np.float32) - reference_mean) * gain
    normalized += distorted_mean
    return np.clip(normalized, 0, 255)


def alignment_score(reference: np.ndarray, distorted: np.ndarray) -> float:
    normalized = normalize_reference(reference, distorted)
    blurred_reference = cv2.GaussianBlur(normalized, (5, 5), 0)
    blurred_distorted = cv2.GaussianBlur(distorted, (5, 5), 0).astype(np.float32)
    height, width = reference.shape
    y0, y1 = round(height * 0.2), round(height * 0.8)
    x0, x1 = round(width * 0.2), round(width * 0.8)
    residual = np.abs(blurred_distorted[y0:y1, x0:x1] - blurred_reference[y0:y1, x0:x1])
    return float(np.median(residual))


def choose_reference_frame(
    reference_path: Path,
    timestamp: float,
    reference_info: MediaInfo,
    distorted_frame: np.ndarray,
    size: tuple[int, int],
    radius_frames: int,
    ffmpeg: str,
) -> tuple[np.ndarray, float, float]:
    if radius_frames < 0:
        raise ValueError("sync radius must not be negative")
    frame_duration = 1.0 / reference_info.fps if reference_info.fps > 0 else 0.0
    if frame_duration == 0.0:
        candidate = extract_gray_frame(reference_path, timestamp, size, ffmpeg)
        return candidate, 0.0, alignment_score(candidate, distorted_frame)
    candidate_times = [
        min(
            max(0.0, timestamp + offset * frame_duration),
            max(0.0, reference_info.duration - 0.001),
        )
        for offset in range(-radius_frames, radius_frames + 1)
    ]
    contiguous = all(
        math.isclose(later - earlier, frame_duration, rel_tol=0.0, abs_tol=1e-9)
        for earlier, later in zip(candidate_times, candidate_times[1:])
    )
    if contiguous:
        candidates = extract_gray_frames(
            reference_path, candidate_times[0], len(candidate_times), size, ffmpeg
        )
    else:
        candidates = np.stack(
            [
                extract_gray_frame(reference_path, candidate_time, size, ffmpeg)
                for candidate_time in candidate_times
            ]
        )
    scored = [
        (
            candidate,
            candidate_time - timestamp,
            alignment_score(candidate, distorted_frame),
        )
        for candidate_time, candidate in zip(candidate_times, candidates, strict=True)
    ]
    return min(scored, key=lambda item: item[2])


def standardized_positive_residual(
    reference: np.ndarray, distorted: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    normalized = normalize_reference(reference, distorted)
    residual = distorted.astype(np.float32) - normalized
    height, width = residual.shape
    y0, y1 = round(height * 0.2), round(height * 0.8)
    x0, x1 = round(width * 0.2), round(width * 0.8)
    center = residual[y0:y1, x0:x1]
    location = float(np.median(center))
    scale = robust_scale(center - location)
    z_score = (residual - location) / scale
    return np.maximum(z_score, 0), np.abs(residual - location)


def analyze_sample(
    timestamp: float,
    *,
    distorted_path: Path,
    reference_path: Path,
    reference_info: MediaInfo,
    size: tuple[int, int],
    radius_frames: int,
    ffmpeg: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float]]:
    distorted_frame = extract_gray_frame(distorted_path, timestamp, size, ffmpeg)
    reference_frame, offset, score = choose_reference_frame(
        reference_path,
        timestamp,
        reference_info,
        distorted_frame,
        size,
        radius_frames,
        ffmpeg,
    )
    positive, absolute = standardized_positive_residual(
        reference_frame, distorted_frame
    )
    return (
        positive,
        absolute,
        distorted_frame,
        {
            "timestamp": round(timestamp, 6),
            "reference_offset_seconds": round(offset, 6),
            "alignment_score": round(score, 6),
        },
    )


def analyze_samples(
    timestamps: Sequence[float],
    *,
    distorted_path: Path,
    reference_path: Path,
    reference_info: MediaInfo,
    size: tuple[int, int],
    radius_frames: int,
    ffmpeg: str,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, float]]]:
    if not timestamps:
        return []
    worker = partial(
        analyze_sample,
        distorted_path=distorted_path,
        reference_path=reference_path,
        reference_info=reference_info,
        size=size,
        radius_frames=radius_frames,
        ffmpeg=ffmpeg,
    )
    with ThreadPoolExecutor(
        max_workers=min(MAX_SAMPLE_WORKERS, len(timestamps)),
        thread_name_prefix="watermark-sample",
    ) as executor:
        return list(executor.map(worker, timestamps))


def edge_search_mask(shape: tuple[int, int], ratio: float) -> np.ndarray:
    if not 0.05 <= ratio <= 0.45:
        raise ValueError("edge ratio must be between 0.05 and 0.45")
    height, width = shape
    mask = np.zeros(shape, dtype=np.uint8)
    y_band = max(1, round(height * ratio))
    x_band = max(1, round(width * ratio))
    mask[:y_band, :] = 1
    mask[-y_band:, :] = 1
    mask[:, :x_band] = 1
    mask[:, -x_band:] = 1
    return mask


def find_candidates(
    median_z: np.ndarray,
    frequency: np.ndarray,
    search_mask: np.ndarray,
    minimum_median_z: float,
    minimum_frequency: float,
) -> tuple[list[Candidate], np.ndarray]:
    active = (
        (median_z >= minimum_median_z)
        & (frequency >= minimum_frequency)
        & search_mask.astype(bool)
    ).astype(np.uint8)
    active = cv2.morphologyEx(
        active,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)),
    )
    grouped = cv2.dilate(
        active, cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5)), iterations=1
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(grouped, 8)
    height, width = active.shape
    candidates: list[Candidate] = []
    for label in range(1, count):
        x, y, box_width, box_height, area = [int(value) for value in stats[label]]
        if area < 18 or box_width < 4 or box_height < 3:
            continue
        component = labels == label
        original_pixels = active.astype(bool) & component
        pixels = int(original_pixels.sum())
        if pixels < 4:
            continue
        component_z = median_z[original_pixels]
        component_frequency = frequency[original_pixels]
        component_median_z = float(np.median(component_z))
        component_mean_frequency = float(np.mean(component_frequency))
        score = component_median_z * component_mean_frequency * math.log1p(pixels)
        center_x = x + box_width / 2
        center_y = y + box_height / 2
        corner = (
            ("top" if center_y < height / 2 else "bottom")
            + "-"
            + ("left" if center_x < width / 2 else "right")
        )
        candidates.append(
            Candidate(
                x,
                y,
                box_width,
                box_height,
                x / width,
                y / height,
                box_width / width,
                box_height / height,
                corner,
                score,
                component_median_z,
                component_mean_frequency,
                pixels,
            )
        )
    candidates.sort(key=lambda candidate: candidate.score, reverse=True)
    return candidates, active * 255


def normalize_image(values: np.ndarray, percentile: float = 99.5) -> np.ndarray:
    high = float(np.percentile(values, percentile))
    if high <= 0:
        return np.zeros(values.shape, dtype=np.uint8)
    return np.clip(values * (255.0 / high), 0, 255).astype(np.uint8)


def write_outputs(
    output_dir: Path,
    preview_frame: np.ndarray,
    median_z: np.ndarray,
    median_absolute: np.ndarray,
    frequency: np.ndarray,
    active_mask: np.ndarray,
    candidates: Sequence[Candidate],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    z_image = normalize_image(median_z)
    absolute_image = normalize_image(median_absolute)
    frequency_image = np.clip(frequency * 255, 0, 255).astype(np.uint8)
    heatmap = cv2.applyColorMap(z_image, cv2.COLORMAP_TURBO)
    preview = cv2.cvtColor(preview_frame, cv2.COLOR_GRAY2BGR)
    overlay = preview.copy()
    for index, candidate in enumerate(candidates[:10], start=1):
        color = (0, 0, 255) if index == 1 else (0, 180, 255)
        cv2.rectangle(
            overlay,
            (candidate.x, candidate.y),
            (candidate.x + candidate.width, candidate.y + candidate.height),
            color,
            2,
        )
        cv2.putText(
            overlay,
            str(index),
            (candidate.x, max(14, candidate.y - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    blended = cv2.addWeighted(preview, 0.55, heatmap, 0.45, 0)
    contact_sheet = cv2.hconcat([preview, blended, overlay])
    outputs = {
        "preview.png": preview,
        "positive-z.png": z_image,
        "absolute-residual.png": absolute_image,
        "frequency.png": frequency_image,
        "candidate-mask.png": active_mask,
        "heatmap-overlay.png": blended,
        "candidate-overlay.png": overlay,
        "contact-sheet.png": contact_sheet,
    }
    for name, image in outputs.items():
        path = output_dir / name
        if not cv2.imwrite(str(path), image):
            raise WatermarkDetectionError(f"failed to write image: {path}")


def map_normalized_edges(
    edges: dict[str, float], width: int, height: int
) -> dict[str, float]:
    _validate_normalized_edges(edges)
    if width <= 0 or height <= 0:
        raise WatermarkGeometryError("media dimensions must be positive")
    return {
        "left": edges["left"] * width,
        "top": edges["top"] * height,
        "right": edges["right"] * width,
        "bottom": edges["bottom"] * height,
    }


def map_real_edges_to_target(
    edges: dict[str, float],
    real_width: int,
    real_height: int,
    target_width: int,
    target_height: int,
) -> dict[str, float]:
    if min(real_width, real_height, target_width, target_height) <= 0:
        raise WatermarkGeometryError("media and target dimensions must be positive")
    return {
        "left": edges["left"] * target_width / real_width,
        "top": edges["top"] * target_height / real_height,
        "right": edges["right"] * target_width / real_width,
        "bottom": edges["bottom"] * target_height / real_height,
    }


def outward_bbox(
    edges: dict[str, float],
    width: int,
    height: int,
    margin: float,
) -> dict[str, int]:
    if margin < 0:
        raise WatermarkGeometryError("watermark margin must not be negative")
    left = max(0, math.floor(edges["left"] - margin))
    top = max(0, math.floor(edges["top"] - margin))
    right = min(width, math.ceil(edges["right"] + margin))
    bottom = min(height, math.ceil(edges["bottom"] + margin))
    if right <= left or bottom <= top:
        raise WatermarkGeometryError("watermark bbox is empty after clamping")
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
    }


def _validate_normalized_edges(edges: dict[str, float]) -> None:
    values = [edges.get(name) for name in ("left", "top", "right", "bottom")]
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value) for value in values
    ):
        raise WatermarkGeometryError(
            "normalized watermark edges must be finite numbers"
        )
    left, top, right, bottom = [float(value) for value in values]
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise WatermarkGeometryError(
            "normalized watermark edges must form a box inside the frame"
        )
