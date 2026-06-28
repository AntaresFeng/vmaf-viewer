from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _human_size(path: Path) -> str:
    """Return file size as a human-readable string (like `du -h`)."""
    size = os.path.getsize(path)
    for unit in ("B", "K", "M", "G"):
        if size < 1024:
            return f"{size}{unit}"
        size //= 1024
    return f"{size}T"


def _check_tool(name: str) -> None:
    """Raise SystemExit if *name* is not found on PATH."""
    exe = f"{name}.exe" if sys.platform == "win32" else name
    if shutil.which(exe) is None and shutil.which(name) is None:
        raise SystemExit(f"ERROR: {name} not found on PATH — is it installed?")


def _ffprobe_json(video: Path, entries: str) -> dict:
    """Run ffprobe and return parsed JSON."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", entries,
        "-of", "json",
        str(video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: ffprobe failed on {video}\n{result.stderr.strip()}"
        )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# stream probing
# ---------------------------------------------------------------------------

def probe_stream(video: Path) -> dict:
    """Return stream metadata dict or raise on missing video stream."""
    data = _ffprobe_json(video, "stream=time_base,r_frame_rate,avg_frame_rate,nb_frames")
    streams = data.get("streams", [])
    if not streams:
        raise ValueError("no video stream found")
    return streams[0]


def parse_time_base(tb_str: str) -> int:
    """Parse '1/90000' → 90000 (the denominator)."""
    num, den = tb_str.split("/")
    return int(den)


def parse_framerate(fr_str: str) -> tuple[int, int]:
    """Parse '30000/1001' → (30000, 1001)."""
    num, den = fr_str.split("/")
    return int(num), int(den)


# ---------------------------------------------------------------------------
# interval / mode logic
# ---------------------------------------------------------------------------

def is_constant_interval(tbn: int, fps_num: int, fps_den: int) -> bool:
    """Return True when each frame occupies an integer number of time_base ticks."""
    return (tbn * fps_den) % fps_num == 0


def frame_interval_ticks(tbn: int, fps_num: int, fps_den: int) -> int:
    """Time_base ticks per frame when the interval is constant."""
    return (tbn * fps_den) // fps_num


def compute_fast_pts(frame_index: int, tbn: int, fps_num: int, fps_den: int) -> int:
    """PTS for frame *frame_index* under a constant-interval assumption."""
    return frame_index * frame_interval_ticks(tbn, fps_num, fps_den)


# ---------------------------------------------------------------------------
# precise PTS lookup
# ---------------------------------------------------------------------------

def find_precise_pts(video: Path, frame_index: int) -> tuple[int, float]:
    """Return (pts, pts_time) of the Nth display-order frame (0-indexed).

    Probes every packet, sorts by PTS, and picks the requested index.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "packet=pts,pts_time",
        "-of", "csv=p=0",
        str(video),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(
            f"ERROR: ffprobe packet dump failed on {video}\n{result.stderr.strip()}"
        )

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    packets: list[tuple[int, float]] = []
    for line in lines:
        parts = line.split(",")
        # ffprobe csv=p=0 without -print_optional still emits entries; N/A means no value
        if len(parts) < 2:
            continue
        pts_str, pts_time_str = parts[0], parts[1]
        if pts_str == "N/A":
            continue
        packets.append((int(pts_str), float(pts_time_str) if pts_time_str != "N/A" else 0.0))

    # Sort by PTS ascending (display order)
    packets.sort(key=lambda p: p[0])

    if frame_index >= len(packets):
        raise ValueError(
            f"frame {frame_index} not found — video has only {len(packets)} frames"
        )

    return packets[frame_index]


# ---------------------------------------------------------------------------
# frame extraction
# ---------------------------------------------------------------------------

def extract_frame(video: Path, pts: int, output: Path) -> None:
    """Use ffmpeg to select and write the frame at *pts* as a PNG."""
    # The select filter expects the escaped comma: eq(pts\,<value>)
    select_expr = f"eq(pts\\,{pts})"
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video),
        "-vf", f"select={select_expr}",
        "-vframes", "1",
        "-update", "1",
        str(output),
    ]
    subprocess.run(cmd, capture_output=True)
    # Don't check returncode — ffmpeg may exit non-zero on some edge cases
    # while still producing output.  We check the output file below.


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def _setup_console() -> None:
    """Ensure stdout/stderr use UTF-8 so Unicode symbols print correctly.

    On Windows the default console encoding is often GBK, which causes
    UnicodeEncodeError for box-drawing / checkmark characters.  Python 3.7+
    supports reconfigure(); if it fails (e.g. redirected pipe) we leave the
    stream as-is — the caller will see garbled output rather than a crash.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def main() -> None:
    _setup_console()

    parser = argparse.ArgumentParser(
        description="Extract the Nth display-order frame from video(s) as PNG.",
    )
    parser.add_argument(
        "-n", dest="frame_index", type=int, required=True,
        help="Frame index (0-based, in display order).",
    )
    parser.add_argument(
        "-o", dest="output_dir", type=str, default=None,
        help="Output directory.  Default: same directory as the first video.",
    )
    parser.add_argument(
        "-m", dest="mode", type=str, default="auto",
        choices=["auto", "fast", "precise"],
        help="Extraction mode: auto (default), fast, or precise.",
    )
    parser.add_argument(
        "videos", nargs="+",
        help="One or more video files.",
    )

    args = parser.parse_args()

    if args.frame_index < 0:
        print(f"ERROR: Frame index must be a non-negative integer, got: '{args.frame_index}'", file=sys.stderr)
        sys.exit(1)

    # Verify tools are available
    _check_tool("ffprobe")
    _check_tool("ffmpeg")

    # Output directory: default to first video's directory
    output_dir = Path(args.output_dir) if args.output_dir else Path(args.videos[0]).resolve().parent
    output_dir.mkdir(parents=True, exist_ok=True)

    total = len(args.videos)
    done = 0
    failed = 0

    for video_path_str in args.videos:
        video = Path(video_path_str)
        print(f"─── {video} ───")

        if not video.is_file():
            print(f"  WARNING: file not found, skipping", file=sys.stderr)
            continue

        # ── Probe stream ──
        try:
            stream = probe_stream(video)
        except (ValueError, SystemExit) as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            failed += 1
            continue

        tbn = parse_time_base(stream["time_base"])
        fps_num, fps_den = parse_framerate(stream["r_frame_rate"])
        nb_frames = stream.get("nb_frames", None)

        print(f"  time_base: 1/{tbn}   r_frame_rate: {fps_num}/{fps_den}   nb_frames: {nb_frames}")

        # ── Determine effective mode ──
        constant = is_constant_interval(tbn, fps_num, fps_den)
        if constant:
            if args.mode == "auto":
                effective_mode = "fast"
            else:
                effective_mode = args.mode
        else:
            if args.mode == "fast":
                print(
                    f"  ERROR: frame interval is not constant "
                    f"(tbn={tbn} fps={fps_num}/{fps_den}). Use -m precise.",
                    file=sys.stderr,
                )
                failed += 1
                continue
            effective_mode = "precise"

        print(f"  mode: {effective_mode}")

        # ── Pre-check frame count (fast mode) ──
        if effective_mode == "fast" and nb_frames is not None and nb_frames != "N/A":
            try:
                nbf = int(nb_frames)
                if args.frame_index >= nbf:
                    print(
                        f"  ERROR: requested frame {args.frame_index} "
                        f"but video only has {nbf} frames",
                        file=sys.stderr,
                    )
                    failed += 1
                    continue
            except (ValueError, TypeError):
                pass  # nb_frames was something unexpected — skip the check

        # ── Compute PTS ──
        if effective_mode == "fast":
            interval = frame_interval_ticks(tbn, fps_num, fps_den)
            pts = compute_fast_pts(args.frame_index, tbn, fps_num, fps_den)
            print(f"  frame_interval: {interval} ticks   PTS: {pts}")
        else:
            print("  PTS-sorting all packets (this may take a while for large files)...")
            try:
                pts, pts_time = find_precise_pts(video, args.frame_index)
            except ValueError as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)
                failed += 1
                continue
            print(f"  PTS: {pts}  ({pts_time} s)")

        # ── Extract ──
        output_path = output_dir / f"{video.stem}_frame{args.frame_index}.png"
        print(f"  extracting → {output_path}")
        extract_frame(video, pts, output_path)

        if output_path.is_file():
            size = _human_size(output_path)
            print(f"  ✓  {size}")
            done += 1
        else:
            print(f"  ✗  extraction failed")
            failed += 1
        print()

    # ── Summary ──
    print(f"─── {total} video(s) — {done} extracted, {failed} failed ───")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
