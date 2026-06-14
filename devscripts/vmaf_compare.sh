#!/usr/bin/env bash
# Compare one or more distorted videos against a reference video with VMAF.
# Usage: ./vmaf_compare.sh <reference.mp4> <distorted1.mp4> [distorted2.mp4] ...

set -euo pipefail

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <reference.mp4> <distorted1.mp4> [distorted2.mp4] ..."
    echo "Example: $0 ref.mp4 A.mp4 B.mp4 C.mp4"
    exit 1
fi

REF="$1"
shift

if [ ! -f "$REF" ]; then
    echo "Error: reference video not found: $REF"
    exit 1
fi

REF_W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=s=x:p=0 "$REF")
REF_H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=s=x:p=0 "$REF")
echo "Reference: $REF (${REF_W}x${REF_H})"
echo "================================"

for DISTORTED in "$@"; do
    if [ ! -f "$DISTORTED" ]; then
        echo "Warning: skipping missing file: $DISTORTED"
        continue
    fi

    DIST_W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of csv=s=x:p=0 "$DISTORTED" 2>/dev/null || echo "?")
    DIST_H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of csv=s=x:p=0 "$DISTORTED" 2>/dev/null || echo "?")

    BASENAME=$(basename "$DISTORTED")
    NAME_NOEXT="${BASENAME%.*}"
    OUTPUT="${NAME_NOEXT}_vmaf.json"

    echo ""
    echo "Comparing: $DISTORTED (${DIST_W}x${DIST_H}) -> $OUTPUT"

    ffmpeg -i "$DISTORTED" -i "$REF" \
        -lavfi "[0:v]setpts=PTS-STARTPTS[distorted];
                [1:v]setpts=PTS-STARTPTS[reference];
                [distorted][reference]libvmaf=log_fmt=json:log_path=${OUTPUT}:ts_sync_mode=nearest" \
        -f null - 2>&1 | tail -n 5

    if [ -f "$OUTPUT" ]; then
        MEAN=$(jq -r '.pooled_metrics.vmaf.mean // empty' "$OUTPUT")
        echo "  -> Mean VMAF: ${MEAN:-N/A}"
    fi
done

echo ""
echo "================================"
echo "Done. Generated VMAF JSON files:"
ls -lh *_vmaf.json 2>/dev/null || echo "(none)"
