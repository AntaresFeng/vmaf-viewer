# Distribution Boxplot Design

## Goal

Improve the Distribution section in `vmaf-viewer` by:

- Showing Histogram and CDF with a focused x-axis range of 50-100.
- Adding a compact per-video boxplot on the left side of the Distribution area.
- Preserving the current comparison flow: initial `/api/compare` should remain lightweight and should not fetch extra per-frame series.

## Chosen Approach

Use backend-computed quartiles and a separate frontend ECharts instance for the left-side boxplot.

This keeps statistics accurate, keeps the API shape simple, and avoids a fragile mixed-grid ECharts option that combines boxplot, bar, and line series in one chart.

## Backend Data

Extend `src/vmaf_viewer/stats.py` so `summarize_values()` returns:

- `q1`: 25th percentile
- `median`: 50th percentile
- `q3`: 75th percentile

Use the existing `percentile()` interpolation helper, matching the current behavior of `p1`, `p5`, and `p10`.

`/api/compare` should continue returning these values inside `summary[].stats`. No new endpoint is needed.

## Frontend Layout

Update the Distribution section to use a two-column layout:

- Left column: new `boxplotChart`, approximately 260px wide on desktop.
- Right column: existing Histogram/CDF tab content.

On narrow screens, stack the boxplot above the Histogram/CDF chart so labels and charts do not become cramped.

The Histogram/CDF tabs should control only the right-side chart. The boxplot remains visible regardless of the active tab.

## Chart Behavior

### Boxplot

Build one boxplot item per visible video from `summary[].stats`:

```text
[min, q1, median, q3, max]
```

The boxplot y-axis should list video names or short labels. The score axis should have `max: 100` while leaving the minimum adaptive/default so very low-scoring frames remain visible.

Tooltips should show the true `min`, `q1`, `median`, `q3`, and `max` values.

If a video has incomplete or non-finite boxplot stats, skip only that video's boxplot item.

### Histogram

Keep using the backend histogram data, but display only the 50-100 score range in the chart. The chart should omit buckets outside the focused range while preserving the existing per-video counts for the included buckets.

### CDF

Set the CDF x-axis display range to 50-100. CDF values should still reflect the full distribution, so the CDF line at score 50 may already be above zero when frames scored below 50.

### Visibility Sync

The existing hidden-video state should apply to all three distribution views:

- Boxplot
- Histogram
- CDF

If no videos are visible, all distribution charts should show empty states.

## Error Handling

Keep error handling local and non-disruptive:

- No comparison loaded: show empty states.
- No visible videos: show empty states.
- Missing or invalid boxplot stats for one video: skip that video in the boxplot only.
- Existing API warnings for invalid JSON, missing metrics, and non-finite values remain unchanged.

## Testing

Add or update backend/API tests to verify:

- `summary[].stats` includes `q1`, `median`, and `q3`.
- The quartile values are correct for existing fixtures or a focused fixture.

Run the standard checks after implementation:

```bash
node --check src/vmaf_viewer/static/app.js
uv run pytest -q
```

Also verify manually in the browser:

- Distribution shows a left-side boxplot and right-side Histogram/CDF chart on desktop.
- The layout stacks cleanly on narrow screens.
- Hiding videos updates boxplot, Histogram, and CDF together.
- Histogram/CDF x-axis display is focused on 50-100.
- Boxplot score axis has `max: 100` and an adaptive/default minimum.
