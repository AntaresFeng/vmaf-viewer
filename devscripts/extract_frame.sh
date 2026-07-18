#!/usr/bin/env bash
# extract_frame.sh — Extract the Nth display-order frame from video(s) as PNG
#
# Two modes:
#   fast    – Frame interval is constant (integer ticks). PTS = N × interval.
#   precise – PTS-sort all packets. Handles variable frame duration / B-frames.
#   auto    – Use fast when safe, otherwise fall back to precise.
#
# Usage: extract_frame.sh -n <N> [-o <dir>] [-m fast|precise|auto] <video...>
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: extract_frame.sh -n <frame_index> [-o <output_dir>] [-m fast|precise|auto] <video...>

Extract the Nth frame (0-indexed, display/PTS order) from one or more videos as PNG.

Options:
  -n N        Frame index (0-based, in display order). Required.
  -o DIR      Output directory.  Default: same directory as the first video.
  -m MODE     Extraction mode:
                auto    – Use fast mode when frame interval is an integer (default)
                fast    – Assume constant interval, PTS = N * (time_base_den / fps_num)
                precise – ffprobe + PTS-sort all packets to locate exact frame PTS
  -h, --help  Show this help

Examples:
  extract_frame.sh -n 835 video.mp4
  extract_frame.sh -n 835 -o /tmp/frames "videos/video4/BV*.mp4"
  extract_frame.sh -n 8733 -m precise TougenRenka.mp4 zaGCPy8DgBo*.mp4
EOF
    exit 1
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
FRAME_INDEX=""
OUTPUT_DIR=""
MODE="auto"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -n)           FRAME_INDEX="$2"; shift 2 ;;
        -o)           OUTPUT_DIR="$2";   shift 2 ;;
        -m)           MODE="$2";         shift 2 ;;
        -f|--fast)    MODE="fast";       shift   ;;
        -p|--precise) MODE="precise";    shift   ;;
        -h|--help)    usage ;;
        --) shift; break ;;
        -*) echo "ERROR: Unknown option: $1" >&2; usage ;;
        *)  break ;;
    esac
done

if [[ -z "$FRAME_INDEX" ]]; then
    echo "ERROR: -n <frame_index> is required" >&2
    usage
fi

if [[ $# -eq 0 ]]; then
    echo "ERROR: At least one video file is required" >&2
    usage
fi

if ! [[ "$FRAME_INDEX" =~ ^[0-9]+$ ]]; then
    echo "ERROR: Frame index must be a non-negative integer, got: '$FRAME_INDEX'" >&2
    exit 1
fi

if [[ "$MODE" != "auto" && "$MODE" != "fast" && "$MODE" != "precise" ]]; then
    echo "ERROR: Mode must be 'auto', 'fast', or 'precise', got: '$MODE'" >&2
    exit 1
fi

VIDEOS=("$@")

# Output directory defaults to the first video's directory
if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="$(dirname "${VIDEOS[0]}")"
fi
mkdir -p "$OUTPUT_DIR"

# ---------------------------------------------------------------------------
# Per-video extraction
# ---------------------------------------------------------------------------
FAILED=0
TOTAL=${#VIDEOS[@]}
DONE=0

for video in "${VIDEOS[@]}"; do
    echo "─── $video ───"

    if [[ ! -f "$video" ]]; then
        echo "  WARNING: file not found, skipping" >&2
        continue
    fi

    # -- Probe stream timing metadata --
    info=$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=time_base,r_frame_rate,avg_frame_rate,nb_frames \
        -of json "$video" 2>/dev/null || true)

    if [[ -z "$info" ]] || [[ "$(echo "$info" | jq -r '.streams | length')" == "0" ]]; then
        echo "  ERROR: no video stream found" >&2
        ((FAILED++)) || true
        continue
    fi

    tbn=$(echo "$info"     | jq -r '.streams[0].time_base     | split("/")[1] | tonumber')
    fps_num=$(echo "$info" | jq -r '.streams[0].r_frame_rate   | split("/")[0] | tonumber')
    fps_den=$(echo "$info" | jq -r '.streams[0].r_frame_rate   | split("/")[1] | tonumber')
    nb_frames=$(echo "$info" | jq -r '.streams[0].nb_frames // "N/A"')

    echo "  time_base: 1/$tbn   r_frame_rate: $fps_num/$fps_den   nb_frames: $nb_frames"

    # -- Determine effective mode --
    effective_mode="$MODE"
    if (( (tbn * fps_den) % fps_num == 0 )); then
        is_constant=true
        if [[ "$MODE" == "auto" ]]; then
            effective_mode="fast"
        fi
    else
        is_constant=false
        if [[ "$MODE" == "fast" ]]; then
            echo "  ERROR: frame interval is not constant (tbn=$tbn fps=$fps_num/$fps_den). Use -m precise." >&2
            ((FAILED++)) || true
            continue
        fi
        effective_mode="precise"
    fi
    echo "  mode: $effective_mode"

    # -- Pre-check frame count (fast mode only; precise discovers at sort time) --
    if [[ "$nb_frames" != "N/A" ]] && (( FRAME_INDEX >= nb_frames )); then
        echo "  ERROR: requested frame $FRAME_INDEX but video only has $nb_frames frames" >&2
        ((FAILED++)) || true
        continue
    fi

    # -- Compute PTS --
    if [[ "$effective_mode" == "fast" ]]; then
        frame_interval=$(( tbn * fps_den / fps_num ))
        pts=$(( FRAME_INDEX * frame_interval ))
        echo "  frame_interval: $frame_interval ticks   PTS: $pts"
    else
        echo "  PTS-sorting all packets (this may take a while for large files)..."
        line_idx=$(( FRAME_INDEX + 1 ))
        pts_line=$(ffprobe -v error -select_streams v:0 \
            -show_entries packet=pts,pts_time \
            -of csv=p=0 "$video" 2>/dev/null \
            | sort -t',' -k1 -n \
            | sed -n "${line_idx}p" || true)

        if [[ -z "$pts_line" ]]; then
            echo "  ERROR: frame $FRAME_INDEX not found — video may have fewer frames" >&2
            ((FAILED++)) || true
            continue
        fi

        pts=$(echo "$pts_line"    | cut -d',' -f1)
        pts_time=$(echo "$pts_line" | cut -d',' -f2)
        echo "  PTS: $pts  ($pts_time s)"
    fi

    # -- Build output path & extract --
    base="${video##*/}"
    base="${base%.*}"
    output_path="$OUTPUT_DIR/${base}_frame${FRAME_INDEX}.png"

    echo "  extracting → $output_path"
    ffmpeg -y -v error -i "$video" \
        -vf "select=eq(pts\\,$pts)" \
        -vframes 1 -update 1 \
        "$output_path" 2>/dev/null || true

    if [[ -f "$output_path" ]]; then
        size=$(du -h "$output_path" | cut -f1)
        echo "  ✓  $size"
        ((DONE++)) || true
    else
        echo "  ✗  extraction failed"
        ((FAILED++)) || true
    fi
    echo ""
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "─── $TOTAL video(s) — $DONE extracted, $FAILED failed ───"
if (( FAILED > 0 )); then
    exit 1
fi
