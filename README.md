# VMAF Compare

Local tools for comparing libvmaf JSON outputs from distorted video encodes.

## VMAF JSON Viewer

The viewer is a local web app managed with `uv`.

Install and sync dependencies:

```bash
uv sync
```

Fetch the local ECharts asset:

```bash
uv run python devscripts/fetch_echarts.py
```

Run the viewer:

```bash
uv run vmaf-viewer
```

Open:

```text
http://127.0.0.1:8765
```

By default, the app scans:

```text
videos/
```

Override the scan directory:

```bash
$env:VMAF_VIEWER_DATA_DIR = "/path/to/vmaf-jsons"
uv run vmaf-viewer
```

The app compares selected `*_vmaf.json` files over their shortest common frame range and ranks videos by mean VMAF by default.

## Tests

Run all tests through `uv`:

```bash
uv run pytest
```
