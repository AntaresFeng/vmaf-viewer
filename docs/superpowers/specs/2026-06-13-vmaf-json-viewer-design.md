# VMAF JSON Viewer Design

## Goal

Build a local web application for comparing multiple libvmaf JSON outputs. The primary question is not "where is one distorted video worse than the reference", but "among these distorted encodes, which one is better overall and under what conditions".

The first version targets 4-6 JSON files at once, with each JSON containing up to roughly 100,000 frame rows.

## Project Shape

Use a Python local service managed by `uv`. All Python project setup, dependency installation, running, and test commands should go through `uv`; the project should not document or require a bare `pip` workflow.

The service will scan local data directories, parse VMAF JSON files, cache derived data, serve API responses, and host the static frontend.

The frontend will be a single local web app using ECharts for charts. It should open directly to the comparison workspace, not to a marketing or landing page.

## Data Source

The default data source is the project-local `videos/` directory. This directory is ignored by Git but is the runtime location for local VMAF JSON outputs.

The app scans for `*_vmaf.json` files and shows them in a selectable list. The initial design does not require browser file upload or arbitrary filesystem browsing.

## Comparison Semantics

When selected JSON files have different frame counts, comparisons use the shortest common frame range by default. Summary statistics, ranking, histogram data, CDF data, and default line-chart data all use this common range. This keeps the comparison fair when answering which distorted encode is better.

Default ranking uses mean VMAF in descending order.

The UI should still show enough metadata to reveal when files have different total frame counts, so the user can understand that the common range is being used.

## Metrics

The app must support variable metric names because libvmaf output depends on the model and enabled features.

The primary score metric should be selected in this order when available:

1. `vmaf`
2. `vmaf_hd`
3. first metric name containing `vmaf`

Per-video summary statistics include:

- mean
- min
- max
- p1
- p5
- p10
- threshold counts and ratios

The default threshold set is `95, 90, 80, 60`. The UI should support adding or editing a custom threshold, and the summary table plus chart reference lines should update from the active threshold list.

Sub-metrics are supported but off by default. Users can toggle them on when needed. Video visibility and metric visibility are independent controls.

## Backend API

Keep the API small and explicit.

`GET /api/files`

Returns scanned VMAF JSON files with stable IDs, display names, paths relative to the project root, size, modification time, total frame count if known, and detected primary metric if already cached.

`POST /api/compare`

Input:

- selected file IDs
- primary metric name or auto-selection
- threshold list

Output:

- selected file metadata
- common frame range
- sorted summary table
- default VMAF line-series data
- histogram data
- CDF data
- warnings such as missing metrics or mismatched frame counts

`GET /api/file/{id}/metrics`

Returns available metric names for one file. This powers the sub-metric toggles without forcing every comparison request to return all metric data.

`POST /api/series`

Input:

- selected file IDs
- metric names
- frame range
- desired resolution or max points

Output:

- line-series data for the requested range and metrics

This endpoint is used when the user zooms in or enables sub-metrics.

## Backend Data Processing

Parsing should convert frame-level metric values into compact numeric arrays and avoid keeping the entire decoded JSON object tree longer than needed.

Cache parsed and derived data using file path, modification time, and file size as the cache key. If any of these change, the file should be reparsed.

For each parsed file, cache:

- frame numbers
- available metric names
- primary metric candidate
- numeric arrays for commonly used metrics
- total frame count

For comparison requests, derive:

- common frame length
- sorted summary rows
- threshold counts and ratios
- histogram buckets
- CDF points
- chart series for the active view

Percentiles should be computed from sorted values over the common range. Histogram and CDF data should be pre-aggregated on the backend.

## Frontend Layout

The first screen is the working comparison interface.

Top bar:

- current scan directory
- refresh button
- selected file count
- threshold editor

Left panel:

- scanned JSON list
- search/filter by filename
- file metadata and selection state

Main panel:

- comparison summary table
- per-frame line chart
- local zoom chart
- distribution area with histogram and CDF tabs

The visual style should be dense, calm, and work-focused. This is an analysis tool, so avoid decorative marketing sections.

## Summary Table

Each row represents one VMAF JSON file. Rows are sorted by mean VMAF descending by default.

Columns include:

- video label
- mean
- min
- max
- p1
- p5
- p10
- below-threshold counts and ratios
- total frames and common frames

The table should make cross-video comparison easy. It may highlight best values per column and visually flag values that are materially worse than the best row.

## Charts

### Per-Frame VMAF Line Chart

Shows frame number on the x-axis and score on the y-axis. It includes horizontal reference lines for the active thresholds, defaulting to 95, 90, 80, and 60.

By default, it shows only the primary VMAF score for each selected video. The user can hide individual videos and can enable sub-metrics separately.

### Local Zoom Chart

Provides a draggable axis and mini overview similar to `design/Large Area Chart.png`. Use ECharts `dataZoom` for the first version.

This chart shares selected videos and active metric settings with the main line chart. When the user zooms into a small range, the frontend should request higher-resolution series data for that range.

### Histogram

The histogram x-axis is VMAF score buckets and the y-axis is frame count. It should compare all selected videos in the same bucket definitions.

### CDF

The CDF x-axis is VMAF score and the y-axis is the proportion of frames at or below that score. This helps compare low-score tails when mean VMAF is close.

## Performance Requirements

The app should remain responsive when comparing 4-6 files with up to around 100,000 frames each.

Backend responsibilities:

- cache parsed numeric arrays
- crop comparisons to the shortest common frame range
- precompute summary, histogram, and CDF data
- downsample line data for broad views
- return detailed line data only for focused zoom ranges

Frontend responsibilities:

- render primary VMAF by default
- lazy-load sub-metric series
- avoid rendering every metric for every selected video at once
- use chart visibility controls instead of deleting selection state

## Error Handling

The app should report:

- no `*_vmaf.json` files found
- invalid JSON
- JSON missing `frames`
- selected files with no common score metric
- metric missing in one or more selected files
- frame-count mismatch and the common range used

Errors should be visible in the UI without crashing the app.

## Testing

Use small fixture JSON files for tests. Do not commit large files from `videos/`.

Backend tests:

- file scanning
- JSON parsing
- primary metric detection
- common range alignment
- mean/min/max/p1/p5/p10 calculations
- threshold counts and ratios
- histogram generation
- CDF generation
- API behavior for missing metrics and invalid JSON

Frontend/browser verification:

- start the app through `uv`
- load the local page
- confirm the file list renders
- select multiple JSON files
- confirm the summary table renders
- confirm the line chart, zoom chart, histogram, and CDF render
- confirm video visibility and metric visibility controls work

## Out of Scope For First Version

- uploading JSON files through the browser
- editing or generating VMAF JSON
- extracting video frames
- saving named comparison sessions
- arbitrary filesystem browsing outside the configured scan directory
- custom weighted ranking beyond mean VMAF

## Implementation Notes

Use `uv` from the start, likely with a small Python web framework and ECharts on the frontend. The exact framework choice belongs in the implementation plan, but the design assumes a backend-served static app plus JSON APIs.

The implementation should keep parsing/statistics code separate from web routing so it can be tested without running the server.
