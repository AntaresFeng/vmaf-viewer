# AGENTS.md

This file gives coding agents durable, repository-specific guidance. Keep it concise: put long investigations, issue details, and design notes in `docs/`, then link them here.

## Project Overview

VMAF Compare is a local toolkit for comparing video encodes with Netflix VMAF:

- `vmaf-viewer` is a local FastAPI + static ECharts web app for comparing multiple `*_vmaf.json` files.
- `vmaf-workflow` automates the local and remote lifecycle from Bilibili/YouTube download through prepare, package, remote execution, result fetch, cleanup, and status inspection.
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
node --test tests/static/*.test.mjs
node --check src/vmaf_viewer/static/app.js
uv run vmaf-viewer
uv run vmaf-viewer /path/to/vmaf-jsons
uv run vmaf-viewer --data-dir /path/to/vmaf-jsons
uv run vmaf-workflow download --bvid <BVID> --ytid <YTID>
uv run vmaf-workflow prepare --project-dir videos/videoN --reference /path/to/reference.mp4
uv run vmaf-workflow package --project-dir videos/videoN
uv run vmaf-workflow remote-plan --project-dir videos/videoN
uv run vmaf-workflow upload --project-dir videos/videoN
uv run vmaf-workflow run --project-dir videos/videoN
uv run vmaf-workflow fetch-results --project-dir videos/videoN
uv run vmaf-workflow cleanup --project-dir videos/videoN
uv run vmaf-workflow status --project-dir videos/videoN
uv run python devscripts/fetch_echarts.py
```

`vmaf-viewer` scans `videos/` by default. Startup scan-directory priority is `--data-dir`, then the positional directory, then `VMAF_VIEWER_DATA_DIR`, then `videos/`. The web UI can also switch the scan directory from the top `Dir` field.

## Key Scripts

- `devscripts/fetch_echarts.py`: download and verify the vendored ECharts asset for the static viewer.

## Dependencies

Check `pyproject.toml` for details. Available local tools:

- Python 3.11+ managed with `uv`
- `ffmpeg` with libvmaf support; verify with `ffmpeg -h filter=libvmaf`
- `ffprobe`
- `jq` for shell scripts

## Critical Constraints

- On Chinese Windows with PowerShell 5.1, explicitly pass `-Encoding UTF8` when reading or writing UTF-8 files.

## VMAF Workflow Notes

- Package entrypoint: `vmaf-workflow = "vmaf_workflow.cli:main"` in `pyproject.toml`.
- Core local stages: `src/vmaf_workflow/cli.py`, `download_state.py`, `prepare.py`, `packager.py`, and `status.py`.
- Remote stages: `remote_plan.py`, `remote_transport.py`, `remote_workflow.py`, `remote_state.py`, and `cleanup.py`.
- Download accepts either `--bvid`, `--ytid`, or both. An existing project binds at most one normalized identity per site: the same ID may rerun, a missing site may be added, and a different ID must be rejected before writes or runner calls.
- Accepted non-dry-run downloads into an existing project invalidate reproducible downstream workflow artifacts. They preserve media, installed `*_vmaf.json`, logs, custom package outputs, and remote files; restart processing at `prepare`.
- `status` must treat both missing inventory media and supported on-disk media absent from the inventory as a stale inventory.
- Workflow tests live in `tests/test_workflow_*.py`; changes to workflow behavior require focused tests plus `uv run pytest -q`.

## VMAF Viewer Notes

- Package entrypoint: `vmaf-viewer = "vmaf_viewer.app:main"` in `pyproject.toml`.
- Backend: `src/vmaf_viewer/app.py`, `compare.py`, `parser.py`, `scanner.py`, `stats.py`.
- Backend cache/data model helpers: `cache.py`, `models.py`.
- Frontend: `src/vmaf_viewer/static/index.html`, `app.js`, `message_state.js`, `metric_metadata.js`, `styles.css`, plus vendored `vendor/echarts.min.js`.
- API surface includes `/api/files`, `/api/data-dir`, `/api/compare`, `/api/file/{file_id}/metrics`, and `/api/series`.
- Large JSON files and 4-6 way comparisons are expected; preserve downsampling, cache parsed files through `VmafCache`, and avoid loading unnecessary per-frame/detail series in the initial comparison path.
- Static frontend tests live in `tests/static/` and use `browser_harness.mjs`; keep script loading order aligned with `index.html`.
- For frontend changes, run `node --check` on changed static JS files, `node --test tests/static/*.test.mjs`, and `uv run pytest -q`.

## Documentation Links

- Workflow usage and lifecycle: `src/vmaf_workflow/README.md`
- VMAF JSON schema: `docs/vmaf_schema.json`
- VMAF zero-score investigation: `docs/vmaf-zero-score-issue.md` Essentially, it's frame synchronization.
- fps filter / PTS normalization note: `docs/fps-filter-pts-normalization-side-effect.md`
- Captured local FFmpeg libvmaf help: `docs/ffmpeg_help_libvamf.txt`
