# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

VMAF video quality comparison toolkit for analyzing encoding differences between Bilibili and YouTube video sources. Compares AVC, HEVC, and AV1 encodes against reference videos using Netflix's VMAF metric via ffmpeg's libvmaf filter.

Test datasets live in `videos/video0/` and `videos/video1/`, each containing a reference video and multiple distorted encodes. Source metadata (BV IDs, YouTube IDs, encoding specs) is in `metadata.txt`.

## Key Scripts

### vmaf_compare.sh

Runs VMAF comparison between a reference video and one or more distorted videos. Outputs per-frame VMAF scores as JSON.

```bash
./vmaf_compare.sh <reference.mp4> <distorted1.mp4> [distorted2.mp4] ...
```

Output: `<distorted_name>_vmaf.json` containing frame-level metrics (vmaf, adm, vif, motion).

### extract_vmaf_frame_bundle.py

Extracts PNG frame bundles for low-VMAF frames from reference and all distorted videos simultaneously. Used for visual inspection of quality degradation.

```bash
python extract_vmaf_frame_bundle.py <vmaf.json> --ref <reference.mp4> --distorted <video1.mp4> <video2.mp4> [--threshold 0.0] [--window 1] [--overwrite]
```

Key options:
- `--threshold`: Select frames with VMAF at or below this value (default: 0)
- `--window`: Export neighboring frames ±N around each selected frame (default: 1)
- `--limit-centers`: Only process first N low-scoring frames
- `--overwrite`: Replace existing output directory

Output structure:
```
<name>_vmaf_frames/
  index.json          # manifest with all frame metadata
  frame_NNNNNN/
    frame.json        # per-frame metadata (frameNum, vmaf, selected_by)
    reference.png
    <distorted1>.png
    <distorted2>.png
```

## Dependencies

- **ffmpeg** with libvmaf support (run `ffmpeg -h filter=libvmaf` to verify)
- **ffprobe** (bundled with ffmpeg)
- **jq** for JSON parsing in shell scripts
- **Python 3.6+** for frame extraction script

## VMAF Frame Sync Issue (Critical)

Different sources produce videos with different `time_base` values even at the same frame rate. Bilibili AVC uses `1/16000` with PTS quantized to integer milliseconds (alternating 16ms/17ms), while YouTube reference uses `1/15360` with precise 1/60s spacing. This causes ±0.333ms per-frame PTS oscillation.

libvmaf's default `ts_sync_mode=default` uses "nearest lower or equal timestamp" to pair frames. When a distorted frame's PTS is slightly less than the reference, framesync picks the *previous* reference frame — producing VMAF=0 for ~15% of frames. The `motion=0` sub-metric on affected frames confirms the mismatch (VMAF receives duplicate content).

**Fix:** Always use `ts_sync_mode=nearest` in libvmaf parameters:

```bash
ffmpeg -i distorted.mp4 -i reference.mp4 \
  -lavfi "[0:v]setpts=PTS-STARTPTS[distorted];
          [1:v]setpts=PTS-STARTPTS[reference];
          [distorted][reference]libvmaf=log_fmt=json:log_path=output.json:ts_sync_mode=nearest" \
  -f null -
```

**Alternative:** Force PTS to frame index with `setpts=N/(60*TB)` on both inputs — eliminates the quantization difference entirely.

See `docs/vmaf-zero-score-issue.md` for the full investigation, and `docs/fps-filter-pts-normalization-side-effect.md` for why easyVmaf's fps filter accidentally masks this issue.

## Data Format

VMAF JSON output (`vmaf_schema.json` for formal schema):
- Top-level: `version`, `fps`, `frames[]`, `pooled_metrics`, `aggregate_metrics`
- `frames[]`: per-frame array with `frameNum` and `metrics` — metric names vary by model/feature; at least one of `vmaf` or `vmaf_hd` is present
- `pooled_metrics`: per-metric `{min, max, mean, harmonic_mean}`

## Directory Structure

- `video0/`, `video1/`: Test datasets with reference and distorted encodes
- `video*_proxy.mp4`: 10-second proxy clips generated with `-c copy` for fast testing (time_base preserved, but duration may exceed 10s due to keyframe boundaries — use `-t 10` in ffmpeg to clamp)
- `metadata.txt`: Source video metadata (Bilibili BV IDs, YouTube IDs, encoding specs)
- `devscripts/`: Test and development scripts
- `docs/`: Investigation notes on PTS alignment and zero-score issues
- `mono`: Scratch notes
