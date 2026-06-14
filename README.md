# VMAF Compare

Local tools for comparing libvmaf JSON outputs from distorted video encodes.

## VMAF JSON Viewer

The viewer is a local web app managed with `uv`.

Install and sync dependencies:

```powershell
uv sync
```

Fetch the local ECharts asset:

```powershell
uv run python devscripts/fetch_echarts.py
```

Run the viewer:

```powershell
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

```powershell
$env:VMAF_VIEWER_DATA_DIR = "D:\path\to\vmaf-jsons"
uv run vmaf-viewer
```

The app compares selected `*_vmaf.json` files over their shortest common frame range and ranks videos by mean VMAF by default.

## Tests

Run all tests through `uv`:

```powershell
uv run pytest
```
