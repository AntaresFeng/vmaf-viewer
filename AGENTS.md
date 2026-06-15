# AGENTS.md

This file gives coding agents durable, repository-specific guidance. Keep it concise: put long investigations, issue details, and design notes in `docs/`, then link them here.

## Project Overview

VMAF Compare is a local toolkit for comparing video encodes with Netflix VMAF:

- Shell/Python dev scripts generate VMAF JSON and extract low-score frame bundles.
- `vmaf-viewer` is a local FastAPI + static ECharts web app for comparing multiple `*_vmaf.json` files.
- The comparison focus is degraded-video vs degraded-video ranking, not only "where one encode differs from the source."

Test media and generated VMAF output live under `videos/`. Treat `videos/` as local data only; never add it to git.

## Agent Guidance Style

- Follow the nearest applicable `AGENTS.md`; explicit user instructions override repo guidance.
- Keep this file for commands, conventions, constraints, and pointers an agent needs before editing.
- Move detailed issue descriptions, long investigations, and future feature notes into `docs/`.
- When adding new project behavior, update the relevant docs link instead of expanding this file with a long narrative.

## Commands

Always use `uv` for Python environment management.

```bash
uv sync
uv run pytest -q
uv run vmaf-viewer
uv run vmaf-viewer /path/to/vmaf-jsons
uv run vmaf-viewer --data-dir /path/to/vmaf-jsons
uv run python devscripts/fetch_echarts.py
```

`vmaf-viewer` scans `videos/` by default. Startup scan-directory priority is `--data-dir`, then the positional directory, then `VMAF_VIEWER_DATA_DIR`, then `videos/`. The web UI can also switch the scan directory from the top `Dir` field.

## Key Scripts

- `devscripts/vmaf_compare.sh`: compare one reference video against one or more distorted videos and write per-frame VMAF JSON.
- `devscripts/extract_vmaf_frame_bundle.py`: export reference/distorted PNG bundles around selected low-VMAF frames.
- `devscripts/fetch_echarts.py`: download and verify the vendored ECharts asset for the static viewer.

## Dependencies

- Python 3.11+ managed with `uv`
- `ffmpeg` with libvmaf support; verify with `ffmpeg -h filter=libvmaf`
- `ffprobe`
- `jq` for shell scripts

## Critical Constraints

- Always ignore the entire `videos/` tree.
- Always use `ts_sync_mode=nearest` in libvmaf commands unless a task explicitly investigates an alternative.
- Do not hand-edit generated VMAF JSON outputs.
- On Chinese Windows with PowerShell 5.1, explicitly pass `-Encoding UTF8` when reading or writing UTF-8 files.

## VMAF Viewer Notes

- Package entrypoint: `vmaf-viewer = "vmaf_viewer.app:main"` in `pyproject.toml`.
- Backend: `src/vmaf_viewer/app.py`, `compare.py`, `parser.py`, `scanner.py`, `stats.py`.
- Frontend: `src/vmaf_viewer/static/index.html`, `app.js`, `styles.css`, plus vendored `vendor/echarts.min.js`.
- API surface includes `/api/files`, `/api/data-dir`, `/api/compare`, `/api/file/{file_id}/metrics`, and `/api/series`.
- Large JSON files and 4-6 way comparisons are expected; preserve downsampling and avoid loading unnecessary per-frame series in the initial comparison path.
- For frontend changes, run `node --check src/vmaf_viewer/static/app.js` and `uv run pytest -q`.

## Documentation Links

- Current feature/issues list: `docs/issues.md`
- VMAF JSON schema: `docs/vmaf_schema.json`
- VMAF zero-score investigation: `docs/vmaf-zero-score-issue.md` Essentially, it's frame synchronization.
- fps filter / PTS normalization note: `docs/fps-filter-pts-normalization-side-effect.md`
