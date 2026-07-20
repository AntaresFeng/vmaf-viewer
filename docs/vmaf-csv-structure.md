# VMAF CSV log structure

This document describes the structure of libvmaf `log_fmt=csv` output, as observed in the CSV files under `videos/`. It is for encoding/reference purposes only — the viewer does **not** validate against this; `docs/vmaf_schema.json` / `docs/vmaf_schema.xsd` are similarly reference-only.

## Frame index column

CSV uses `Frame` as the frame index column (integer, 0-based, first column). This differs from the other two log formats:

| Format | Frame index field |
|--------|-------------------|
| CSV    | `Frame` (column)  |
| JSON   | `frames[].frameNum` |
| XML    | `<frame frameNum="...">` attribute |

`CsvVmafParser._FRAME_COL = "Frame"` already handles this; the JSON/XML parsers use `frameNum` / `frameNum`.

Frame indexes identify source-frame positions and are not required to be contiguous. For example, FFmpeg libvmaf with `n_subsample=30` records frame indexes `0, 30, 60, ...`; it does not renumber the sampled frames to `0, 1, 2, ...`.

The viewer therefore requires every frame index to be explicit, non-negative, unique, and strictly increasing. Series points are returned to the frontend as `[frameNum, value]`. When several files are selected, each file keeps all of its own samples and statistics; curves share the numeric frame axis but are not intersected, padded, interpolated, or truncated to a common prefix.

## Header row quirk

FFmpeg libvmaf writes a **trailing comma** in the header, e.g.:

```
Frame,integer_motion2,...,vmaf,
```

`csv.DictReader` therefore parses an extra *empty-string* field name at the end, with an empty value per row. Without filtering, the viewer treats that empty string as a real (all-NaN) metric column. The parser strips empty field names so it does not surface in the UI.

## Observed fields

The set of columns depends on which model / features the `libvmaf` run enabled. Across the sample CSVs in `videos/`, the columns fall into these groups:

### Core VMAF output

- `vmaf` — default model
- `vmaf_hd`, `vmaf_hd_neg`, `vmaf_hd_phone` — HD /NEG / phone variants (appear together when the HD model is used)

### Integer-valued base metrics

- `integer_motion2`, `integer_motion`, `integer_motion3`
- `integer_adm2`, `integer_aim`, `integer_adm3`
- `integer_adm_scale0` .. `integer_adm_scale3`
- `integer_vif_scale0` .. `integer_vif_scale3`

### PSNR (only when the PSNR feature is enabled)

- `psnr_y`, `psnr_cb`, `psnr_cr`

### EGL variants (`_egl_1` suffix, only in fuller runs)

- `integer_adm2_egl_1`, `integer_aim_egl_1`, `integer_adm3_egl_1`
- `integer_adm_scale0_egl_1` .. `integer_adm_scale3_egl_1`
- `integer_vif_scale0_egl_1` .. `integer_vif_scale3_egl_1`

## Column ordering

Column order is **not stable** across runs — e.g. one file may order columns as
`Frame, integer_motion2, integer_motion, ...` while another orders them as
`Frame, integer_adm2, integer_aim, ...`. The parser does not assume any order;
it reads field names from the header row directly.
