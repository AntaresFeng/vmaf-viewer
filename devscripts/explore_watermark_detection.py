from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Explore fixed watermark detection by comparing a distorted video "
            "against a clean reference. This script is independent of vmaf-workflow."
        )
    )
    parser.add_argument("--distorted", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=9)
    parser.add_argument("--analysis-width", type=int, default=960)
    parser.add_argument("--edge-ratio", type=float, default=0.24)
    parser.add_argument("--minimum-frequency", type=float, default=0.56)
    parser.add_argument("--minimum-median-z", type=float, default=3.0)
    parser.add_argument("--sync-radius-frames", type=int, default=1)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--ffprobe", default="ffprobe")
    return parser


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
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    stream = payload["streams"][0]
    fps = parse_fraction(stream.get("avg_frame_rate"))
    if fps <= 0:
        fps = parse_fraction(stream.get("r_frame_rate"))
    return MediaInfo(
        path=str(path.resolve()),
        width=int(stream["width"]),
        height=int(stream["height"]),
        fps=fps,
        duration=float(payload["format"]["duration"]),
    )


def parse_fraction(value: Any) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    numerator, separator, denominator = value.partition("/")
    try:
        if separator:
            denominator_value = float(denominator)
            return float(numerator) / denominator_value if denominator_value else 0.0
        return float(numerator)
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
    start = duration * 0.08
    end = duration * 0.92
    return np.linspace(start, end, count, dtype=np.float64).tolist()


def extract_gray_frame(
    path: Path,
    timestamp: float,
    size: tuple[int, int],
    ffmpeg: str,
) -> np.ndarray:
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
        "1",
        "-vf",
        f"scale={width}:{height}:flags=lanczos,format=gray",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    result = subprocess.run(command, check=True, capture_output=True)
    expected = width * height
    if len(result.stdout) != expected:
        raise RuntimeError(
            f"ffmpeg returned {len(result.stdout)} frame bytes, expected {expected}"
        )
    return np.frombuffer(result.stdout, dtype=np.uint8).reshape(height, width)


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


def robust_scale(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return max(1.0, 1.4826 * mad)


def alignment_score(reference: np.ndarray, distorted: np.ndarray) -> float:
    normalized = normalize_reference(reference, distorted)
    blurred_reference = cv2.GaussianBlur(normalized, (5, 5), 0)
    blurred_distorted = cv2.GaussianBlur(distorted, (5, 5), 0).astype(np.float32)
    height, width = reference.shape
    y0, y1 = round(height * 0.2), round(height * 0.8)
    x0, x1 = round(width * 0.2), round(width * 0.8)
    residual = np.abs(
        blurred_distorted[y0:y1, x0:x1]
        - blurred_reference[y0:y1, x0:x1]
    )
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
    best: tuple[np.ndarray, float, float] | None = None
    for frame_offset in range(-radius_frames, radius_frames + 1):
        candidate_time = timestamp + frame_offset * frame_duration
        candidate_time = min(
            max(0.0, candidate_time), max(0.0, reference_info.duration - 0.001)
        )
        candidate = extract_gray_frame(
            reference_path, candidate_time, size, ffmpeg
        )
        score = alignment_score(candidate, distorted_frame)
        if best is None or score < best[2]:
            best = (candidate, candidate_time - timestamp, score)
    assert best is not None
    return best


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


def edge_search_mask(shape: tuple[int, int], ratio: float) -> np.ndarray:
    if not 0.05 <= ratio <= 0.45:
        raise ValueError("edge ratio must be between 0.05 and 0.45")
    height, width = shape
    y_band = max(1, round(height * ratio))
    x_band = max(1, round(width * ratio))
    mask = np.zeros(shape, dtype=np.uint8)
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
        active, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
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
        corner = ("top" if center_y < height / 2 else "bottom") + "-" + (
            "left" if center_x < width / 2 else "right"
        )
        candidates.append(
            Candidate(
                x=x,
                y=y,
                width=box_width,
                height=box_height,
                x_norm=x / width,
                y_norm=y / height,
                width_norm=box_width / width,
                height_norm=box_height / height,
                corner=corner,
                score=score,
                median_z=component_median_z,
                frequency=component_mean_frequency,
                pixels=pixels,
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
        if not cv2.imwrite(str(output_dir / name), image):
            raise RuntimeError(f"failed to write image: {output_dir / name}")


def analyze(args: argparse.Namespace) -> dict[str, Any]:
    distorted_path = args.distorted.resolve()
    reference_path = args.reference.resolve()
    if not distorted_path.is_file():
        raise SystemExit(f"distorted video does not exist: {distorted_path}")
    if not reference_path.is_file():
        raise SystemExit(f"reference video does not exist: {reference_path}")
    if shutil.which(args.ffmpeg) is None:
        raise SystemExit(f"ffmpeg executable was not found: {args.ffmpeg}")
    if shutil.which(args.ffprobe) is None:
        raise SystemExit(f"ffprobe executable was not found: {args.ffprobe}")

    distorted_info = probe_media(distorted_path, args.ffprobe)
    reference_info = probe_media(reference_path, args.ffprobe)
    size = analysis_size(distorted_info, args.analysis_width)
    duration = min(distorted_info.duration, reference_info.duration)
    timestamps = sample_times(duration, args.samples)

    positive_residuals: list[np.ndarray] = []
    absolute_residuals: list[np.ndarray] = []
    preview_frames: list[np.ndarray] = []
    alignments: list[dict[str, float]] = []
    for timestamp in timestamps:
        distorted_frame = extract_gray_frame(
            distorted_path, timestamp, size, args.ffmpeg
        )
        reference_frame, offset, score = choose_reference_frame(
            reference_path,
            timestamp,
            reference_info,
            distorted_frame,
            size,
            args.sync_radius_frames,
            args.ffmpeg,
        )
        positive, absolute = standardized_positive_residual(
            reference_frame, distorted_frame
        )
        positive_residuals.append(positive)
        absolute_residuals.append(absolute)
        preview_frames.append(distorted_frame)
        alignments.append(
            {
                "timestamp": round(timestamp, 6),
                "reference_offset_seconds": round(offset, 6),
                "alignment_score": round(score, 6),
            }
        )

    positive_stack = np.stack(positive_residuals)
    absolute_stack = np.stack(absolute_residuals)
    median_z = np.median(positive_stack, axis=0)
    median_absolute = np.median(absolute_stack, axis=0)
    frequency = np.mean(positive_stack >= args.minimum_median_z, axis=0)
    search_mask = edge_search_mask(median_z.shape, args.edge_ratio)
    candidates, active_mask = find_candidates(
        median_z,
        frequency,
        search_mask,
        args.minimum_median_z,
        args.minimum_frequency,
    )

    output_dir = args.output_dir.resolve()
    write_outputs(
        output_dir,
        preview_frames[len(preview_frames) // 2],
        median_z,
        median_absolute,
        frequency,
        active_mask,
        candidates,
    )
    summary = {
        "schema_version": 1,
        "method": "reference-assisted-positive-residual-v1",
        "distorted": asdict(distorted_info),
        "reference": asdict(reference_info),
        "analysis_size": {"width": size[0], "height": size[1]},
        "settings": {
            "samples": args.samples,
            "edge_ratio": args.edge_ratio,
            "minimum_frequency": args.minimum_frequency,
            "minimum_median_z": args.minimum_median_z,
            "sync_radius_frames": args.sync_radius_frames,
        },
        "alignments": alignments,
        "candidates": [asdict(candidate) for candidate in candidates[:20]],
    }
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    args = build_parser().parse_args()
    summary = analyze(args)
    candidates = summary["candidates"]
    print(f"Wrote watermark research artifacts to {args.output_dir.resolve()}")
    if candidates:
        candidate = candidates[0]
        print(
            "Top candidate: "
            f"{candidate['corner']} "
            f"bbox=({candidate['x']},{candidate['y']},"
            f"{candidate['width']},{candidate['height']}) "
            f"score={candidate['score']:.3f}"
        )
    else:
        print("No candidate passed the current thresholds")


if __name__ == "__main__":
    main()
