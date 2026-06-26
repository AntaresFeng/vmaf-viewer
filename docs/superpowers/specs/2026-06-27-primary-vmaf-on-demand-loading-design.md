# Per-frame VMAF Chart On-Demand Loading Design

Status: ready for user review
Date: 2026-06-27

## Context

The main Per-frame VMAF chart currently renders from `/api/compare`. That response includes a downsampled full-video primary score series, using `max_points: 2000`, which is good for initial comparison load but too sparse after the user zooms into a short frame range.

The Local Zoom detail chart already has a successful on-demand loading pattern:

- detect the chart's current zoom range from ECharts `dataZoom`;
- only fetch when the zoom window is small enough, currently `<= 5000` frames;
- wait for a trailing `400ms` debounce so slider dragging does not repeatedly fetch;
- call `/api/series` with the current file ids, metric names, frame range, and `max_points: 5000`;
- merge the returned points into a chart-local cache;
- update only the affected chart data and preserve the slider position.

The main Per-frame VMAF chart should use the same interaction rule, but it must keep its state separate from the Local Zoom detail metric state.

## Goal

When the user zooms into the main Per-frame VMAF chart, the frontend should load denser primary-score points for the visible frame window and update the main line chart without resetting the slider.

The expected user-visible behavior is:

- initial comparison still loads quickly from `/api/compare`;
- after zooming into a window of `<= 5000` frames, the main chart fills in denser points;
- requests are trailing-debounced by `400ms`;
- no loading indicator is shown;
- the Local Zoom detail chart continues to work independently.

## Non-Goals

- Do not change the initial `/api/compare` payload shape.
- Do not change histogram, CDF, boxplot, summary, ranking, or threshold behavior.
- Do not change Local Zoom detail metric selection or axis behavior.
- Do not add a new backend endpoint unless `/api/series` proves insufficient.
- Do not display a loading state for this refinement.

## Chosen Approach

Use a main-chart-specific range loading cache, separate from `state.extraSeries`.

This is the chosen version of option A from the discussion:

- keep `/api/compare` as the source for initial full-video downsampled main chart data;
- introduce dedicated frontend state for denser primary-series points loaded by range;
- reuse `/api/series` for the range fetch;
- merge range results into the main chart cache;
- update the main chart series in ECharts merge mode so the current `dataZoom` state is preserved.

This avoids polluting `state.comparison.series`, which should remain the original comparison result, and avoids coupling main VMAF loading to the Local Zoom detail metric cache.

## Data Model

Add frontend state dedicated to the main chart, conceptually:

```js
primarySeriesCache: Map<fileId, { metric: string, points: Array<[number, number]> }>
primaryRangeLoadTimer: number | null
pendingPrimaryRangeLoad: object | null
lastPrimaryRangeLoadKey: string
```

`primarySeriesCache` is initialized from `state.comparison.series` after a successful `/api/compare` response. Range-loaded points are merged into this cache. The original `state.comparison.series` remains unchanged.

The cache key is `fileId` because each displayed video row has exactly one primary score metric in the current comparison. The value shape intentionally matches `state.comparison.series[fileId]`: `{ metric, points }`. This differs from `state.extraSeries`, whose detail-metric cache is nested as `{ fileId: { metric: { points } } }`.

Stale response protection should use the existing `comparisonRequestId` captured in each pending range load. A separate primary-series request id is not needed because a response from an old comparison is already rejected by `comparisonRequestId`, and same-comparison range responses merge by frame number into the same source-of-truth metric data.

## Metric Grouping

The main chart must not assume every compared file uses the literal metric name `vmaf`.

`/api/compare` stores the selected primary metric per file under `state.comparison.series[fileId].metric`. Different files can theoretically use different primary metrics, for example `vmaf`, `vmaf_hd`, or another VMAF-like metric chosen by the parser.

`/api/series` validates every requested metric against every requested file. Therefore the frontend should group visible rows by their current primary metric before requesting range data:

```text
metric "vmaf"    -> file ids that currently use vmaf
metric "vmaf_hd" -> file ids that currently use vmaf_hd
```

For the common case where all rows use `vmaf`, this produces one request. For mixed primary metrics, it produces one request per metric group and avoids false 404 errors from asking a file for a metric it does not contain.

## Zoom Trigger

The main chart listens to its own `datazoom` event. This handler is independent from the Local Zoom detail chart handler. Before registering it, `renderLineCharts()` must call `charts.line.off("datazoom")`, mirroring the existing `charts.zoom.off("datazoom")`, so repeated renders do not stack multiple main-chart handlers.

On each main-chart `datazoom` event:

1. Read `charts.line.getOption().dataZoom` and convert the current percentage window into frame numbers using `state.comparison.common_range`.
2. If there is no active comparison, no visible rows, or no valid zoom state, cancel any pending main-chart range load.
3. If the computed frame window is wider than `5000` frames, cancel any pending main-chart range load.
4. Build a stable request key from the range, visible file ids, and their primary metrics.
5. If the key matches `lastPrimaryRangeLoadKey`, do nothing.
6. Otherwise, replace any pending timer with a new trailing `400ms` timer.

This keeps slider dragging responsive and only fetches after the user pauses or releases interaction.

## Range Request

When the debounce timer fires:

1. Capture the current `comparisonRequestId` in the pending load.
2. Re-check that the comparison is still current.
3. Group visible rows by primary metric.
4. Call `/api/series` once per metric group:

```json
{
  "file_ids": ["..."],
  "metrics": ["vmaf"],
  "start": 1200,
  "end": 1800,
  "max_points": 5000
}
```

5. Ignore all responses if the comparison changed while the requests were in flight.
6. Merge each returned point list into `primarySeriesCache`.
7. Set `lastPrimaryRangeLoadKey` only after the merge is applied.
8. Refresh the main chart series without rebuilding the whole chart.

If a request fails, show a concise message such as `Unable to load zoomed VMAF series.` and leave the existing chart data in place.

## Point Merge

Merging should be by frame number:

- keep existing cached points outside the loaded range;
- replace or insert points for frames returned by `/api/series`;
- sort the final point list by frame number;
- preserve finite numeric values as returned by the backend.

This mirrors the detail-series behavior: a range fetch enriches the current cache instead of replacing the full-video series with a local slice.

## Chart Update

The main chart needs stable series ids. `primaryLineSeries()` must set:

```js
id: `primary:${row.id}`
```

Without explicit ids, ECharts merge mode can match series by array index. Hiding or re-showing files changes the visible series order, so id-based matching is required for safe range refreshes.

`primaryLineSeries()` should read from `primarySeriesCache` when available and fall back to `state.comparison.series`.

The initial render path after a new comparison, empty-state recovery, or other broad reset should continue to use the existing full replacement behavior:

```js
charts.line.setOption(fullMainChartOptions, true);
```

That keeps stale axes, dataZoom components, and old series from leaking across comparisons.

After a range load, do not call the full chart replacement path that uses `setOption(..., true)`, because that would reset `dataZoom` to the full video range.

Instead, add a dedicated range-refresh helper, conceptually `refreshPrimaryChartSeries()`, that updates only the main chart's `series` in merge mode:

```js
charts.line.setOption({ series: vmafOverviewSeries(visibleRows()) });
```

The range-refresh helper must not pass `true` as the second argument. It should preserve the current `dataZoom` runtime state while still keeping the first visible primary series' reference `markLine` from `vmafOverviewSeries()`.

## Isolation From Local Zoom

Main-chart range loading and Local Zoom detail range loading should have separate state:

- separate pending timers;
- separate last-loaded keys;
- separate stale-response guards via captured `comparisonRequestId`;
- separate caches.

They should both be cleared when the comparison changes. A main-chart range request must not invalidate an in-flight detail-chart range request, and a detail-chart range request must not invalidate a main-chart range request.

`state.extraSeries` remains exclusively for Local Zoom sub-metrics. Main primary-score range data lives in the dedicated main-chart cache.

## Reset Rules

Clear the main range-loading state when:

- selected files change and a new comparison request starts;
- scan directory changes;
- the comparison becomes empty;
- a new comparison response is accepted;
- the chart is cleared because there are no visible VMAF rows.

The cache should be rebuilt from the accepted `/api/compare` response before any range data is merged.

## Testing Plan

Add frontend harness tests for:

- main-chart `datazoom` waits `400ms` and only requests the final zoom range;
- wide zoom ranges over `5000` frames do not fetch;
- `/api/series` is called with `max_points: 5000` for the main chart range load;
- mixed primary metrics are grouped into separate `/api/series` requests;
- returned range points merge into the main-chart cache instead of replacing the full-video points;
- range refresh updates the main chart series in merge mode and does not call the full replacement path that resets `dataZoom`;
- range refresh keeps the first visible primary series' reference `markLine`;
- hiding a file and then showing it again reuses cached dense points and does not re-request the same loaded range;
- Local Zoom detail range loading still uses its own state and is not invalidated by a main-chart range request.

Run the existing frontend and backend checks after implementation:

```powershell
node --check src/vmaf_viewer/static/app.js
node --test tests/static/*.test.mjs
uv run pytest -q
```

## Acceptance Criteria

- Zooming the main Per-frame VMAF chart into a small window loads denser points after `400ms`.
- The main chart slider remains at the user's zoomed range after data arrives.
- The loaded range does not create blank regions outside the zoom window.
- The Local Zoom detail chart still debounces and loads sub-metrics as before.
- Mixed primary-metric comparisons avoid false `/api/series` missing-metric errors by grouping requests.
- All required checks pass.
