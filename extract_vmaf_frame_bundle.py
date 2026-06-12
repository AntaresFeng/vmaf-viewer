#!/usr/bin/env python3

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Sequence


def format_time(frame_num: int, fps: float) -> str:
    if fps <= 0:
        return "n/a"
    seconds = frame_num / fps
    minutes = int(seconds // 60)
    remaining = seconds - minutes * 60
    return f"{minutes:02d}:{remaining:06.3f}"


def sanitize_name(name: str) -> str:
    safe = []
    for char in name:
        if char.isalnum() or char in {"-", "_", "."}:
            safe.append(char)
        else:
            safe.append("_")
    result = "".join(safe).strip("._")
    return result or "video"


def load_frames(json_path: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    fps = float(data.get("fps") or 0.0)
    frames = []
    for item in data.get("frames", []):
        metrics = item.get("metrics", {})
        score = metrics.get("vmaf")
        if score is None:
            continue
        frames.append(
            {
                "frameNum": int(item.get("frameNum", 0)),
                "vmaf": float(score),
                "metrics": metrics,
            }
        )
    return fps, frames


def extract_frame(input_video: Path, frame_num: int, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-nostats",
        "-i",
        str(input_video),
        "-vf",
        f"select=eq(n\\,{frame_num})",
        "-frames:v",
        "1",
        "-y",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def build_target_map(center_frames: Sequence[int], window: int, max_frame: int) -> Dict[int, List[int]]:
    target_map: Dict[int, set] = defaultdict(set)
    for center in center_frames:
        for offset in range(-window, window + 1):
            target = center + offset
            if 0 <= target <= max_frame:
                target_map[target].add(center)
    return {frame_num: sorted(centers) for frame_num, centers in target_map.items()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract frame bundles for low-VMAF frames from one reference video and multiple distorted videos."
    )
    parser.add_argument("json_file", help="Path to the libvmaf JSON log")
    parser.add_argument("--ref", required=True, help="Path to the reference video")
    parser.add_argument(
        "--distorted",
        required=True,
        nargs="+",
        help="Paths to distorted videos that should be exported together",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory to write extracted frame bundles into (default: <json stem>_frames)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.0,
        help="Select frames with VMAF at or below this value (default: 0)",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=1,
        help="Also export neighboring frames around each selected frame (default: 1)",
    )
    parser.add_argument(
        "--limit-centers",
        type=int,
        default=0,
        help="Only use the first N center frames before expanding the window (0 means no limit)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing extracted frames if the output directory already exists",
    )
    args = parser.parse_args()

    json_path = Path(args.json_file)
    ref_path = Path(args.ref)
    distorted_paths = [Path(path) for path in args.distorted]

    if not json_path.is_file():
        raise SystemExit(f"JSON file not found: {json_path}")
    if not ref_path.is_file():
        raise SystemExit(f"Reference video not found: {ref_path}")
    for path in distorted_paths:
        if not path.is_file():
            raise SystemExit(f"Distorted video not found: {path}")

    fps, frames = load_frames(json_path)
    if not frames:
        raise SystemExit("No frame-level VMAF data found.")

    frames_sorted = sorted(frames, key=lambda frame: frame["frameNum"])
    max_frame = max(frame["frameNum"] for frame in frames_sorted)
    center_frames = [frame["frameNum"] for frame in frames_sorted if frame["vmaf"] <= args.threshold]
    if args.limit_centers > 0:
        center_frames = center_frames[: args.limit_centers]

    if not center_frames:
        raise SystemExit(f"No frames are at or below {args.threshold:.2f}.")

    target_map = build_target_map(center_frames, args.window, max_frame)
    target_frames = sorted(target_map)

    output_dir = Path(args.output_dir) if args.output_dir else json_path.with_name(f"{json_path.stem}_frames")
    if output_dir.exists() and not args.overwrite:
        raise SystemExit(f"Output directory already exists: {output_dir} (use --overwrite)")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"JSON: {json_path}")
    print(f"FPS: {fps:.3f}" if fps else "FPS: n/a")
    print(f"Frames: {len(frames_sorted)}")
    print(f"Threshold: {args.threshold:.2f}")
    print(f"Window: {args.window}")
    print(f"Center frames: {len(center_frames)}")
    print(f"Target frames: {len(target_frames)}")
    print(f"Output: {output_dir}")
    print()

    index_rows = []
    video_entries = [("reference", ref_path)] + [(sanitize_name(path.stem), path) for path in distorted_paths]

    for frame_num in target_frames:
        frame_dir = output_dir / f"frame_{frame_num:06d}"
        frame_dir.mkdir(parents=True, exist_ok=True)

        frame_info = next(frame for frame in frames_sorted if frame["frameNum"] == frame_num)
        frame_manifest = {
            "frameNum": frame_num,
            "time": format_time(frame_num, fps),
            "vmaf": frame_info["vmaf"],
            "selected_by": target_map[frame_num],
            "window": args.window,
            "threshold": args.threshold,
            "videos": [],
        }

        for label, video_path in video_entries:
            output_path = frame_dir / f"{label}.png"
            extract_frame(video_path, frame_num, output_path)
            frame_manifest["videos"].append(
                {
                    "label": label,
                    "path": str(output_path.name),
                    "source_video": str(video_path),
                }
            )

        (frame_dir / "frame.json").write_text(
            json.dumps(frame_manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        index_rows.append(
            {
                "frameNum": frame_num,
                "time": format_time(frame_num, fps),
                "vmaf": frame_info["vmaf"],
                "selected_by": target_map[frame_num],
                "frame_dir": str(frame_dir.relative_to(output_dir)),
            }
        )

    (output_dir / "index.json").write_text(
        json.dumps(
            {
                "json": str(json_path),
                "fps": fps,
                "threshold": args.threshold,
                "window": args.window,
                "center_frames": center_frames,
                "targets": index_rows,
                "videos": {
                    "reference": str(ref_path),
                    "distorted": [str(path) for path in distorted_paths],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Done. Extracted {len(target_frames)} frame bundles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())