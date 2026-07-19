from __future__ import annotations

import argparse
from pathlib import Path

from vmaf_workflow.watermark_detection import (
    DetectionSettings,
    detect_watermark,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Explore fixed watermark detection by comparing a distorted video "
            "against a clean reference. This script is independent of "
            "vmaf-workflow state."
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


def analyze(args: argparse.Namespace) -> dict:
    settings = DetectionSettings(
        samples=args.samples,
        analysis_width=args.analysis_width,
        edge_ratio=args.edge_ratio,
        minimum_frequency=args.minimum_frequency,
        minimum_median_z=args.minimum_median_z,
        sync_radius_frames=args.sync_radius_frames,
        ffmpeg=args.ffmpeg,
        ffprobe=args.ffprobe,
    )
    return detect_watermark(
        args.distorted,
        args.reference,
        args.output_dir,
        settings,
    ).to_summary()


def main() -> None:
    args = build_parser().parse_args()
    summary = analyze(args)
    print(f"Wrote watermark research artifacts to {args.output_dir.resolve()}")
    candidates = summary["candidates"]
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
