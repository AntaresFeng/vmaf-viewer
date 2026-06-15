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

Run the viewer with a scan directory:

```bash
uv run vmaf-viewer /path/to/vmaf-jsons
uv run vmaf-viewer --data-dir /path/to/vmaf-jsons
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

The startup directory priority is `--data-dir`, then the positional directory, then `VMAF_VIEWER_DATA_DIR`, then `videos/`.
You can also change the scan directory from the top `Dir` field in the web UI and press `Scan`.

The app compares selected `*_vmaf.json` files over their shortest common frame range and ranks videos by mean VMAF by default.

## Tests

Run all tests through `uv`:

```bash
uv run pytest
```
