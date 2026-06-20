# Distribution Boxplot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a left-side per-video VMAF boxplot to the Distribution section and focus Histogram/CDF x-axis display on scores 50-100.

**Architecture:** Compute quartiles once in the existing backend summary path, then have the frontend derive boxplot data from `summary[].stats`. Keep the boxplot as its own ECharts instance beside the existing Histogram/CDF tab panel, so the current distribution chart code stays readable and no extra per-frame API fetch is needed.

**Tech Stack:** Python 3.11, FastAPI test client, pytest, plain browser JavaScript, vendored ECharts 6.1, static HTML/CSS.

---

## Source Spec

- `docs/superpowers/specs/2026-06-19-distribution-boxplot-design.md`

## File Structure

- Modify: `src/vmaf_viewer/stats.py`
  - Add `q1`, `median`, and `q3` to `summarize_values()`.
  - Continue using the existing `percentile()` helper.
- Modify: `tests/test_stats.py`
  - Assert quartile values for finite and empty summaries.
- Modify: `tests/test_api.py`
  - Assert `/api/compare` exposes quartile fields in `summary[].stats`.
- Modify: `src/vmaf_viewer/static/index.html`
  - Add `#boxplotChart` to the Distribution section.
  - Wrap the boxplot and current tabbed charts in a responsive layout container.
- Modify: `src/vmaf_viewer/static/styles.css`
  - Add the two-column desktop layout and stacked mobile layout.
- Modify: `src/vmaf_viewer/static/app.js`
  - Initialize the boxplot chart.
  - Render empty states for all distribution charts consistently.
  - Build and render boxplot data from visible summary rows.
  - Filter Histogram display buckets to 50-100.
  - Set CDF x-axis display range to 50-100.
  - Resize the boxplot with the other charts.

No new dependency or endpoint is needed. `src/vmaf_viewer/static/vendor/echarts.min.js` already includes the `boxplot` series type.

---

### Task 1: Add Quartiles To Backend Summary

**Files:**
- Modify: `tests/test_stats.py`
- Modify: `tests/test_api.py`
- Modify: `src/vmaf_viewer/stats.py`

- [ ] **Step 1: Write failing stats assertions**

In `tests/test_stats.py`, update `test_summarize_values_computes_core_stats_and_thresholds()` to include the quartile assertions:

```python
def test_summarize_values_computes_core_stats_and_thresholds():
    summary = summarize_values([97.0, 96.0, 90.0, 80.0, 70.0], [95.0, 90.0, 80.0, 60.0])

    assert summary["mean"] == 86.6
    assert summary["min"] == 70.0
    assert summary["max"] == 97.0
    assert summary["q1"] == 80.0
    assert summary["median"] == 90.0
    assert summary["q3"] == 96.0
    assert summary["p1"] == 70.4
    assert summary["p5"] == 72.0
    assert summary["p10"] == 74.0
    assert summary["thresholds"][95.0]["count"] == 3
    assert summary["thresholds"][95.0]["ratio"] == 0.6
    assert summary["thresholds"][60.0]["count"] == 0
```

In `test_summarize_values_handles_empty_or_all_nan_values()`, add quartile `nan` assertions:

```python
        assert math.isnan(summary["q1"])
        assert math.isnan(summary["median"])
        assert math.isnan(summary["q3"])
```

- [ ] **Step 2: Write failing API assertions**

In `tests/test_api.py`, update `test_api_compare_returns_summary_and_charts()` after the existing summary-name assertion:

```python
    alpha = next(row for row in body["summary"] if row["name"] == "alpha_vmaf.json")
    beta = next(row for row in body["summary"] if row["name"] == "beta_vmaf.json")
    assert alpha["stats"]["q1"] == 87.5
    assert alpha["stats"]["median"] == 93.0
    assert alpha["stats"]["q3"] == 96.25
    assert beta["stats"]["q1"] == 88.75
    assert beta["stats"]["median"] == 90.0
    assert beta["stats"]["q3"] == 91.25
```

These values use the first 4 common frames because `alpha_vmaf.json` has 5 frames and `beta_vmaf.json` has 4 frames.

- [ ] **Step 3: Run targeted tests and verify failure**

Run:

```bash
uv run pytest tests/test_stats.py::test_summarize_values_computes_core_stats_and_thresholds tests/test_stats.py::test_summarize_values_handles_empty_or_all_nan_values tests/test_api.py::test_api_compare_returns_summary_and_charts -q
```

Expected: FAIL with `KeyError: 'q1'` or equivalent missing-key assertions.

- [ ] **Step 4: Add quartiles to `summarize_values()`**

In `src/vmaf_viewer/stats.py`, update the return dict in `summarize_values()` to include:

```python
        "q1": percentile(sorted_values, 25.0),
        "median": percentile(sorted_values, 50.0),
        "q3": percentile(sorted_values, 75.0),
```

The return block should contain these fields near `min` and `max`:

```python
    return {
        "count": count,
        "mean": total / count if count else math.nan,
        "min": sorted_values[0] if count else math.nan,
        "max": sorted_values[-1] if count else math.nan,
        "q1": percentile(sorted_values, 25.0),
        "median": percentile(sorted_values, 50.0),
        "q3": percentile(sorted_values, 75.0),
        "p1": percentile(sorted_values, 1.0),
        "p5": percentile(sorted_values, 5.0),
        "p10": percentile(sorted_values, 10.0),
        "thresholds": threshold_map,
    }
```

- [ ] **Step 5: Run targeted tests and verify pass**

Run:

```bash
uv run pytest tests/test_stats.py::test_summarize_values_computes_core_stats_and_thresholds tests/test_stats.py::test_summarize_values_handles_empty_or_all_nan_values tests/test_api.py::test_api_compare_returns_summary_and_charts -q
```

Expected: PASS for all three tests.

- [ ] **Step 6: Commit backend summary support**

Run:

```bash
git add src/vmaf_viewer/stats.py tests/test_stats.py tests/test_api.py
git commit -m "feat: expose VMAF quartile stats"
```

Expected: commit succeeds.

---

### Task 2: Add Distribution Boxplot Layout And Chart Instance

**Files:**
- Modify: `src/vmaf_viewer/static/index.html`
- Modify: `src/vmaf_viewer/static/styles.css`
- Modify: `src/vmaf_viewer/static/app.js`

- [ ] **Step 1: Add boxplot markup beside the tabbed distribution chart**

In `src/vmaf_viewer/static/index.html`, replace the two chart divs in the Distribution section:

```html
          <div id="histogramChart" class="chart" role="tabpanel" aria-labelledby="histogramTab"></div>
          <div id="cdfChart" class="chart is-hidden" role="tabpanel" aria-labelledby="cdfTab"></div>
```

with:

```html
          <div class="distribution-layout">
            <div id="boxplotChart" class="chart boxplot-chart" role="img" aria-label="Video metric boxplot"></div>
            <div class="distribution-main">
              <div id="histogramChart" class="chart" role="tabpanel" aria-labelledby="histogramTab"></div>
              <div id="cdfChart" class="chart is-hidden" role="tabpanel" aria-labelledby="cdfTab"></div>
            </div>
          </div>
```

- [ ] **Step 2: Add responsive distribution layout CSS**

In `src/vmaf_viewer/static/styles.css`, add this block after `.chart.is-hidden`:

```css
.distribution-layout {
  display: grid;
  grid-template-columns: minmax(220px, 260px) minmax(0, 1fr);
  gap: 14px;
  align-items: stretch;
}

.distribution-main {
  min-width: 0;
}

.boxplot-chart {
  height: 340px;
}
```

Inside the existing `@media (max-width: 900px)` block, add:

```css
  .distribution-layout {
    grid-template-columns: 1fr;
  }

  .boxplot-chart {
    height: 260px;
  }
```

- [ ] **Step 3: Register the boxplot DOM element and chart**

In `src/vmaf_viewer/static/app.js`, add the DOM element to `elements`:

```javascript
  boxplotChart: document.getElementById("boxplotChart"),
```

Place it near the existing Distribution elements:

```javascript
  histogramTab: document.getElementById("histogramTab"),
  cdfTab: document.getElementById("cdfTab"),
  boxplotChart: document.getElementById("boxplotChart"),
  histogramChart: document.getElementById("histogramChart"),
  cdfChart: document.getElementById("cdfChart"),
```

Add the chart to `charts`:

```javascript
  boxplot: echarts.init(elements.boxplotChart),
```

The chart block should look like:

```javascript
const charts = {
  line: echarts.init(elements.lineChart),
  zoom: echarts.init(elements.zoomChart),
  boxplot: echarts.init(elements.boxplotChart),
  histogram: echarts.init(elements.histogramChart),
  cdf: echarts.init(elements.cdfChart),
};
```

- [ ] **Step 4: Include boxplot in empty and resize paths**

In `renderDistributionCharts()`, update the no-data branch:

```javascript
  if (!state.comparison || !rows.length) {
    emptyChart(charts.boxplot, "No visible distribution.");
    emptyChart(charts.histogram, "No visible distribution.");
    emptyChart(charts.cdf, "No visible distribution.");
    updateDistributionVisibility();
    return;
  }
```

In `renderCharts()`, update the no-comparison branch:

```javascript
    emptyChart(charts.boxplot, "No comparison loaded.");
    emptyChart(charts.histogram, "No comparison loaded.");
    emptyChart(charts.cdf, "No comparison loaded.");
```

In `updateDistributionVisibility()`, include the boxplot resize call:

```javascript
  requestAnimationFrame(() => {
    charts.boxplot.resize();
    charts.histogram.resize();
    charts.cdf.resize();
  });
```

The existing `window.resize` handler loops over `Object.values(charts)`, so it will pick up the new chart automatically.

- [ ] **Step 5: Run frontend syntax check**

Run:

```bash
node --check src/vmaf_viewer/static/app.js
```

Expected: command exits 0.

- [ ] **Step 6: Commit layout and chart registration**

Run:

```bash
git add src/vmaf_viewer/static/index.html src/vmaf_viewer/static/styles.css src/vmaf_viewer/static/app.js
git commit -m "feat: add distribution boxplot panel"
```

Expected: commit succeeds.

---

### Task 3: Render Boxplot And Focus Distribution Axes

**Files:**
- Modify: `src/vmaf_viewer/static/app.js`

- [ ] **Step 1: Add focused distribution constants and helpers**

Near the top of `src/vmaf_viewer/static/app.js`, after `COLORS`, add:

```javascript
const DISTRIBUTION_MIN_SCORE = 50;
const DISTRIBUTION_MAX_SCORE = 100;
```

Add these helper functions after `visibleRows()`:

```javascript
function finiteBoxplotValues(row) {
  const stats = row.stats || {};
  const values = [stats.min, stats.q1, stats.median, stats.q3, stats.max].map((value) => Number(value));
  return values.every((value) => Number.isFinite(value)) ? values : null;
}

function boxplotDataset(rows) {
  const labels = [];
  const data = [];

  for (const row of rows) {
    const values = finiteBoxplotValues(row);
    if (!values) {
      continue;
    }
    labels.push(row.name);
    data.push({
      value: values,
      itemStyle: { color: colorForRow(row), borderColor: colorForRow(row) },
    });
  }

  return { labels, data };
}

function focusedHistogramBuckets(row) {
  return (state.comparison.histogram[row.id] || []).filter(
    (bucket) => Number(bucket.end) > DISTRIBUTION_MIN_SCORE && Number(bucket.start) < DISTRIBUTION_MAX_SCORE,
  );
}
```

- [ ] **Step 2: Add a boxplot renderer**

Add this function immediately before `renderDistributionCharts()`:

```javascript
function renderBoxplotChart(rows) {
  const { labels, data } = boxplotDataset(rows);

  if (!data.length) {
    emptyChart(charts.boxplot, "No boxplot data.");
    return;
  }

  charts.boxplot.setOption(
    {
      animation: false,
      color: COLORS,
      tooltip: {
        trigger: "item",
        confine: true,
        formatter: (params) => {
          const [min, q1, median, q3, max] = params.value || [];
          return [
            params.name,
            `min: ${formatNumber(min)}`,
            `Q1: ${formatNumber(q1)}`,
            `median: ${formatNumber(median)}`,
            `Q3: ${formatNumber(q3)}`,
            `max: ${formatNumber(max)}`,
          ].join("<br>");
        },
      },
      grid: { top: 24, right: 14, bottom: 42, left: 72, containLabel: true },
      xAxis: {
        type: "value",
        max: DISTRIBUTION_MAX_SCORE,
        name: "VMAF",
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      yAxis: {
        type: "category",
        data: labels,
        inverse: true,
        axisLabel: { interval: 0, overflow: "truncate", width: 92 },
        axisLine: { lineStyle: { color: "#c6cabf" } },
      },
      series: [
        {
          name: "Boxplot",
          type: "boxplot",
          layout: "horizontal",
          data,
        },
      ],
    },
    true,
  );
}
```

This uses a horizontal boxplot layout so the video labels sit on the y-axis and the score axis can keep `max: 100` with an adaptive minimum.

- [ ] **Step 3: Focus Histogram to 50-100**

In `renderDistributionCharts()`, replace:

```javascript
  const firstHistogram = state.comparison.histogram[rows[0].id] || [];
  const labels = firstHistogram.map((bucket) => `${formatThreshold(bucket.start)}-${formatThreshold(bucket.end)}`);
```

with:

```javascript
  renderBoxplotChart(rows);

  const firstHistogram = focusedHistogramBuckets(rows[0]);
  const labels = firstHistogram.map((bucket) => `${formatThreshold(bucket.start)}-${formatThreshold(bucket.end)}`);
```

In the histogram `series` builder, replace:

```javascript
        data: (state.comparison.histogram[row.id] || []).map((bucket) => bucket.count),
```

with:

```javascript
        data: focusedHistogramBuckets(row).map((bucket) => bucket.count),
```

Keep the histogram x-axis as a category axis. The focused labels should now start at `50-51` and end at `99-100` when bucket size is 1.

- [ ] **Step 4: Focus CDF x-axis to 50-100**

In the CDF `xAxis` option inside `renderDistributionCharts()`, replace:

```javascript
        min: 0,
        max: 100,
```

with:

```javascript
        min: DISTRIBUTION_MIN_SCORE,
        max: DISTRIBUTION_MAX_SCORE,
```

Keep the CDF data unchanged:

```javascript
        data: (state.comparison.cdf[row.id] || []).map((point) => [point.score, point.ratio * 100]),
```

This preserves the true cumulative ratio. If frames below 50 exist, the CDF value at 50 can already be above 0.

- [ ] **Step 5: Run frontend syntax check**

Run:

```bash
node --check src/vmaf_viewer/static/app.js
```

Expected: command exits 0.

- [ ] **Step 6: Commit distribution rendering**

Run:

```bash
git add src/vmaf_viewer/static/app.js
git commit -m "feat: render focused distribution charts"
```

Expected: commit succeeds.

---

### Task 4: Full Verification And Manual UI Check

**Files:**
- Verify: `src/vmaf_viewer/static/app.js`
- Verify: `src/vmaf_viewer/static/index.html`
- Verify: `src/vmaf_viewer/static/styles.css`
- Verify: `src/vmaf_viewer/stats.py`
- Verify: `tests/test_stats.py`
- Verify: `tests/test_api.py`

- [ ] **Step 1: Run frontend syntax check**

Run:

```bash
node --check src/vmaf_viewer/static/app.js
```

Expected: command exits 0.

- [ ] **Step 2: Run full backend test suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass. The existing FastAPI/Starlette test-client deprecation warning may still appear.

- [ ] **Step 3: Start the local viewer**

Run:

```bash
uv run vmaf-viewer --data-dir tests/fixtures
```

Expected: the server starts and prints a local URL.

- [ ] **Step 4: Verify desktop Distribution layout in the browser**

Open the local URL from Step 3 and select `alpha_vmaf.json` and `beta_vmaf.json`.

Verify:

- The Distribution panel has a boxplot chart on the left.
- Histogram is shown on the right by default.
- Histogram labels display only score buckets in the 50-100 range.
- Clicking `CDF` keeps the boxplot visible and switches only the right chart.
- CDF x-axis starts at 50 and ends at 100.
- Toggling one video chip hides that video from boxplot, Histogram, and CDF.
- Toggling all video chips off shows empty distribution states.

- [ ] **Step 5: Verify mobile/narrow layout in the browser**

Use browser responsive mode or a narrow window around 390px wide.

Verify:

- The boxplot stacks above the Histogram/CDF chart.
- Text and axis labels do not overlap badly.
- The Histogram/CDF tab buttons remain usable.

- [ ] **Step 6: Commit any verification fixes**

If Step 4 or Step 5 reveals a layout or rendering issue, make the smallest targeted fix and run:

```bash
node --check src/vmaf_viewer/static/app.js
uv run pytest -q
git add src/vmaf_viewer/static/app.js src/vmaf_viewer/static/index.html src/vmaf_viewer/static/styles.css
git commit -m "fix: polish distribution boxplot layout"
```

Expected: commit succeeds only if a fix was needed. If no fix was needed, skip this step.

---

## Final Verification

Run these commands before marking implementation complete:

```bash
node --check src/vmaf_viewer/static/app.js
uv run pytest -q
```

Expected: every command exits 0. The existing FastAPI/Starlette test-client deprecation warning may still appear.

Manual UI verification should confirm:

- Distribution shows a left-side boxplot and right-side Histogram/CDF chart on desktop.
- Distribution stacks cleanly on narrow screens.
- Hidden-video state applies to boxplot, Histogram, and CDF.
- Histogram and CDF x-axis display focuses on 50-100.
- Boxplot score axis uses `max: 100` and keeps its minimum adaptive/default.

## Plan Self-Review

- Spec coverage: Task 1 covers backend quartiles and API exposure. Task 2 covers the two-column responsive layout and chart instance. Task 3 covers boxplot rendering, visible-video sync through `visibleRows()`, Histogram 50-100 filtering, and CDF 50-100 display range. Task 4 covers syntax, pytest, desktop UI, and narrow UI verification.
- Placeholder scan: No placeholder tasks or vague deferred implementation steps remain. The optional verification-fix commit in Task 4 names the exact files, commands, and condition for use.
- Type consistency: The plan consistently uses `q1`, `median`, `q3`, `DISTRIBUTION_MIN_SCORE`, `DISTRIBUTION_MAX_SCORE`, `boxplotChart`, `charts.boxplot`, `finiteBoxplotValues()`, `boxplotDataset()`, `focusedHistogramBuckets()`, and `renderBoxplotChart()` across all steps.
