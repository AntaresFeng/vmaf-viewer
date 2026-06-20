const DEFAULT_THRESHOLDS = [95, 90, 80, 60];
const COLORS = [
  "#24736f",
  "#b45f1a",
  "#4f6f31",
  "#8f4a62",
  "#476a8f",
  "#80633a",
  "#6f5b9c",
  "#b23b32",
];
const DISTRIBUTION_MIN_SCORE = 50;
const DISTRIBUTION_MAX_SCORE = 100;

const state = {
  files: [],
  selected: new Set(),
  comparison: null,
  hiddenFiles: new Set(),
  activeMetrics: new Set(["primary"]),
  metricsByFile: new Map(),
  extraSeries: new Map(),
  thresholds: [...DEFAULT_THRESHOLDS],
  distribution: "histogram",
  comparisonRequestId: 0,
  zoomSeriesRequestId: 0,
};

const elements = {
  scanPathForm: document.getElementById("scanPathForm"),
  scanPathInput: document.getElementById("scanPathInput"),
  scanPathButton: document.getElementById("scanPathButton"),
  thresholdInput: document.getElementById("thresholdInput"),
  refreshButton: document.getElementById("refreshButton"),
  selectedCount: document.getElementById("selectedCount"),
  fileFilter: document.getElementById("fileFilter"),
  fileList: document.getElementById("fileList"),
  messages: document.getElementById("messages"),
  summaryTable: document.getElementById("summaryTable"),
  videoLegend: document.getElementById("videoLegend"),
  metricToggles: document.getElementById("metricToggles"),
  histogramTab: document.getElementById("histogramTab"),
  cdfTab: document.getElementById("cdfTab"),
  boxplotChart: document.getElementById("boxplotChart"),
  histogramChart: document.getElementById("histogramChart"),
  cdfChart: document.getElementById("cdfChart"),
  lineChart: document.getElementById("lineChart"),
  zoomChart: document.getElementById("zoomChart"),
};

const charts = {
  line: echarts.init(elements.lineChart),
  zoom: echarts.init(elements.zoomChart),
  boxplot: echarts.init(elements.boxplotChart),
  histogram: echarts.init(elements.histogramChart),
  cdf: echarts.init(elements.cdfChart),
};

async function api(path, options = {}) {
  const init = {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
  };

  if (init.body && typeof init.body !== "string") {
    init.body = JSON.stringify(init.body);
  }

  const response = await fetch(path, init);
  const text = await response.text();
  let body = null;

  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = text;
    }
  }

  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? body.detail : response.statusText;
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg || String(item)).join("; ") : detail);
  }

  return body;
}

function parseThresholds() {
  const values = elements.thresholdInput.value
    .split(",")
    .map((value) => Number(value.trim()))
    .filter((value) => Number.isFinite(value));

  state.thresholds = values.length ? values : [...DEFAULT_THRESHOLDS];
  return state.thresholds;
}

function formatNumber(value, digits = 2) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "n/a";
}

function formatThreshold(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return "n/a";
  }
  if (Number.isInteger(number)) {
    return String(number);
  }
  return number.toFixed(2).replace(/\.?0+$/, "");
}

function formatPercent(value, digits = 1) {
  const number = Number(value);
  return Number.isFinite(number) ? `${(number * 100).toFixed(digits)}%` : "n/a";
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) {
    return "n/a";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMessage(messageState = {}) {
  const selected = VmafMessageState.pickMessageState(messageState);
  elements.messages.innerHTML = "";

  const item = document.createElement("div");
  item.className = selected.type === "status" ? "message" : `message ${selected.type}`;
  item.textContent = selected.text;
  elements.messages.appendChild(item);
}

function colorForId(id) {
  const rows = state.comparison ? state.comparison.summary || [] : [];
  const index = rows.findIndex((row) => row.id === id);
  return COLORS[(index >= 0 ? index : 0) % COLORS.length];
}

function colorForRow(row) {
  return colorForId(row.id);
}

function resetComparisonState() {
  state.selected.clear();
  state.comparison = null;
  state.hiddenFiles.clear();
  state.activeMetrics = new Set(["primary"]);
  state.metricsByFile.clear();
  state.extraSeries.clear();
  state.comparisonRequestId += 1;
  state.zoomSeriesRequestId += 1;
}

function applyFilesResponse(body, { preserveSelection = true, resetFilter = false } = {}) {
  state.files = body.files || [];
  elements.scanPathInput.value = body.data_dir || "";

  const ids = new Set(state.files.map((file) => file.id));
  if (preserveSelection) {
    state.selected = new Set([...state.selected].filter((id) => ids.has(id)));
    state.hiddenFiles = new Set([...state.hiddenFiles].filter((id) => ids.has(id)));
    for (const id of state.metricsByFile.keys()) {
      if (!ids.has(id)) {
        state.metricsByFile.delete(id);
      }
    }
  } else {
    resetComparisonState();
  }

  if (resetFilter) {
    elements.fileFilter.value = "";
  }

  renderFiles();
  updateSelectedCount();

  if (!state.files.length) {
    renderMessage({ status: "No *_vmaf.json files found." });
  } else {
    renderMessage({ status: VmafMessageState.DEFAULT_STATUS_MESSAGE });
  }
}

async function loadFiles() {
  const body = await api("/api/files");
  applyFilesResponse(body);
}

async function changeScanDirectory() {
  const dataDir = elements.scanPathInput.value.trim();
  if (!dataDir) {
    renderMessage({ error: "Enter a scan directory." });
    elements.scanPathInput.focus();
    return;
  }

  elements.scanPathButton.disabled = true;
  try {
    const body = await api("/api/data-dir", {
      method: "POST",
      body: { data_dir: dataDir },
    });
    applyFilesResponse(body, { preserveSelection: false, resetFilter: true });
    renderSummary();
    renderControls();
    renderCharts();
  } catch (error) {
    renderMessage({ error: error.message || "Unable to scan that directory." });
  } finally {
    elements.scanPathButton.disabled = false;
  }
}

function renderFiles() {
  const filter = elements.fileFilter.value.trim().toLowerCase();
  const files = state.files.filter((file) => {
    const haystack = `${file.name} ${file.relative_path}`.toLowerCase();
    return haystack.includes(filter);
  });

  elements.fileList.innerHTML = "";

  if (!files.length) {
    const empty = document.createElement("div");
    empty.className = "message";
    empty.textContent = state.files.length ? "No files match the filter." : "No *_vmaf.json files found.";
    elements.fileList.appendChild(empty);
    return;
  }

  for (const file of files) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `file-item${state.selected.has(file.id) ? " is-selected" : ""}`;
    button.title = file.relative_path;
    button.innerHTML = `
      <span class="file-name">${escapeHtml(file.name)}</span>
      <span class="file-path">${escapeHtml(file.relative_path)}</span>
      <span class="file-meta">
        <span>${escapeHtml(formatBytes(file.size))}</span>
      </span>
    `;
    button.setAttribute("aria-pressed", String(state.selected.has(file.id)));
    button.addEventListener("click", () => {
      if (state.selected.has(file.id)) {
        state.selected.delete(file.id);
        state.hiddenFiles.delete(file.id);
      } else {
        state.selected.add(file.id);
      }
      updateSelectedCount();
      renderFiles();
      requestComparison();
    });
    elements.fileList.appendChild(button);
  }
}

function updateSelectedCount() {
  const count = state.selected.size;
  elements.selectedCount.textContent = `${count} selected`;
}

async function requestComparison() {
  const thresholds = parseThresholds();
  const fileIds = [...state.selected];
  const requestId = ++state.comparisonRequestId;

  if (!fileIds.length) {
    state.comparison = null;
    state.extraSeries.clear();
    renderMessage({
      status: state.files.length ? VmafMessageState.DEFAULT_STATUS_MESSAGE : "No *_vmaf.json files found.",
    });
    renderSummary();
    renderControls();
    renderCharts();
    return;
  }

  renderMessage({ status: "Loading comparison data..." });

  try {
    const body = await api("/api/compare", {
      method: "POST",
      body: {
        file_ids: fileIds,
        thresholds,
        max_points: 2000,
      },
    });
    if (requestId !== state.comparisonRequestId) {
      return;
    }
    state.comparison = body;
    state.extraSeries.clear();
    const comparedFileIds = (body.summary || []).map((row) => row.id);
    await loadMetricsForSelected(comparedFileIds);
    if (requestId !== state.comparisonRequestId) {
      return;
    }

    const comparedIds = new Set(comparedFileIds);
    state.hiddenFiles = new Set([...state.hiddenFiles].filter((id) => comparedIds.has(id)));
    pruneActiveMetrics();

    renderMessage({
      warnings: body.warnings || [],
      success: VmafMessageState.formatLoadedMessage(body.summary),
    });
    renderSummary();
    renderControls();
    renderCharts();
  } catch (error) {
    if (requestId !== state.comparisonRequestId) {
      return;
    }
    state.comparison = null;
    renderMessage({ error: error.message || "Unable to compare selected files." });
    renderSummary();
    renderControls();
    renderCharts();
  }
}

async function loadMetricsForSelected(fileIds = [...state.selected]) {
  for (const id of fileIds) {
    if (state.metricsByFile.has(id)) {
      continue;
    }
    const body = await api(`/api/file/${encodeURIComponent(id)}/metrics`);
    state.metricsByFile.set(id, body.metrics || []);
  }
}

function comparedFileIds() {
  if (state.comparison && Array.isArray(state.comparison.summary)) {
    return state.comparison.summary.map((row) => row.id);
  }
  return [...state.selected];
}

function sharedMetrics() {
  const ids = comparedFileIds();
  if (!ids.length || ids.some((id) => !state.metricsByFile.has(id))) {
    return [];
  }

  const metricSets = ids.map((id) => new Set(state.metricsByFile.get(id) || []));
  const first = [...metricSets[0]];
  return first.filter((metric) => metricSets.every((set) => set.has(metric)));
}

function activeExtraMetrics() {
  const shared = new Set(sharedMetrics());
  return [...state.activeMetrics].filter((metric) => metric !== "primary" && shared.has(metric));
}

function pruneActiveMetrics() {
  const allowed = new Set(["primary", ...sharedMetrics()]);
  state.activeMetrics = new Set([...state.activeMetrics].filter((metric) => allowed.has(metric)));
  if (!state.activeMetrics.size) {
    state.activeMetrics.add("primary");
  }
}

async function requestExtraSeries(metric, range = null) {
  const fileIds = comparedFileIds();
  if (!state.comparison || !fileIds.length) {
    return;
  }
  if (!range && state.extraSeries.has(metric)) {
    return;
  }

  const requestId = ++state.zoomSeriesRequestId;
  const commonRange = state.comparison.common_range || {};
  const body = await api("/api/series", {
    method: "POST",
    body: {
      file_ids: fileIds,
      metrics: [metric],
      start: range ? range.start : commonRange.start || 0,
      end: range ? range.end : commonRange.end,
      max_points: range ? 5000 : 2000,
    },
  });

  if (range && requestId !== state.zoomSeriesRequestId) {
    return;
  }
  state.extraSeries.set(metric, body.series || {});
}

async function requestExtraSeriesForRange(metrics, range) {
  const fileIds = comparedFileIds();
  if (!state.comparison || !metrics.length || !fileIds.length) {
    return;
  }

  const requestId = ++state.zoomSeriesRequestId;
  const body = await api("/api/series", {
    method: "POST",
    body: {
      file_ids: fileIds,
      metrics,
      start: range.start,
      end: range.end,
      max_points: 5000,
    },
  });

  if (requestId !== state.zoomSeriesRequestId) {
    return;
  }
  for (const metric of metrics) {
    state.extraSeries.set(metric, body.series || {});
  }
}

function thresholdEntry(stats, threshold) {
  const entries = stats && stats.thresholds ? stats.thresholds : {};
  const key = Object.keys(entries).find((candidate) => Number(candidate) === Number(threshold));
  return key ? entries[key] : null;
}

function meanQualityClass(value) {
  const mean = Number(value);
  if (!Number.isFinite(mean)) {
    return "";
  }
  if (mean >= 95) {
    return "mean-excellent";
  }
  if (mean >= 90) {
    return "mean-good";
  }
  if (mean >= 80) {
    return "mean-medium";
  }
  if (mean >= 70) {
    return "mean-poor";
  }
  return "mean-bad";
}

function columnExtrema(rows, getValue, higherIsBetter = true) {
  const values = rows.map(getValue).map(Number).filter(Number.isFinite);
  if (values.length < 2) {
    return null;
  }

  const high = Math.max(...values);
  const low = Math.min(...values);
  if (high === low) {
    return null;
  }

  return higherIsBetter ? { best: high, worst: low } : { best: low, worst: high };
}

function extremaClass(value, extrema) {
  const number = Number(value);
  if (!extrema || !Number.isFinite(number)) {
    return "";
  }
  if (number === extrema.best) {
    return "cell-best";
  }
  if (number === extrema.worst) {
    return "cell-worst";
  }
  return "";
}

function renderSummary() {
  const rows = state.comparison ? state.comparison.summary || [] : [];
  const thresholds = state.thresholds.length ? state.thresholds : DEFAULT_THRESHOLDS;
  const thresholdHeaders = thresholds.map((threshold) => `<th>≤${escapeHtml(formatThreshold(threshold))}</th>`).join("");

  elements.summaryTable.innerHTML = `
    <thead>
      <tr>
        <th>Video</th>
        <th>Mean</th>
        <th>Min</th>
        <th>Max</th>
        <th>P1</th>
        <th>P5</th>
        <th>P10</th>
        ${thresholdHeaders}
        <th>Frames</th>
      </tr>
    </thead>
    <tbody></tbody>
  `;

  const tbody = elements.summaryTable.querySelector("tbody");
  const colspan = 8 + thresholds.length;

  if (!rows.length) {
    tbody.innerHTML = `<tr><td class="empty-cell" colspan="${colspan}">No comparison loaded.</td></tr>`;
    return;
  }

  const metricExtrema = {
    min: columnExtrema(rows, (row) => row.stats && row.stats.min),
    max: columnExtrema(rows, (row) => row.stats && row.stats.max),
    p1: columnExtrema(rows, (row) => row.stats && row.stats.p1),
    p5: columnExtrema(rows, (row) => row.stats && row.stats.p5),
    p10: columnExtrema(rows, (row) => row.stats && row.stats.p10),
  };
  const thresholdExtrema = new Map(
    thresholds.map((threshold) => [
      Number(threshold),
      columnExtrema(
        rows,
        (row) => {
          const entry = thresholdEntry(row.stats || {}, threshold);
          return entry ? entry.count : NaN;
        },
        false,
      ),
    ]),
  );

  tbody.innerHTML = rows
    .map((row) => {
      const stats = row.stats || {};
      const mean = Number(stats.mean);
      const meanClass = ["mean-cell", meanQualityClass(mean)].filter(Boolean).join(" ");
      const thresholdCells = thresholds
        .map((threshold) => {
          const entry = thresholdEntry(stats, threshold);
          const count = entry ? entry.count : "n/a";
          const ratio = entry ? formatPercent(entry.ratio) : "n/a";
          const cellClass = ["threshold-cell", extremaClass(count, thresholdExtrema.get(Number(threshold)))]
            .filter(Boolean)
            .join(" ");
          return `<td class="${cellClass}"><strong>${escapeHtml(count)}</strong> ${escapeHtml(ratio)}</td>`;
        })
        .join("");
      const frames = `${row.common_frames || stats.count || 0}/${row.total_frames || 0}`;

      return `
        <tr>
          <td title="${escapeHtml(row.relative_path || row.name)}">${escapeHtml(row.name)}</td>
          <td class="${meanClass}">${escapeHtml(formatNumber(stats.mean))}</td>
          <td class="${extremaClass(stats.min, metricExtrema.min)}">${escapeHtml(formatNumber(stats.min))}</td>
          <td class="${extremaClass(stats.max, metricExtrema.max)}">${escapeHtml(formatNumber(stats.max))}</td>
          <td class="${extremaClass(stats.p1, metricExtrema.p1)}">${escapeHtml(formatNumber(stats.p1))}</td>
          <td class="${extremaClass(stats.p5, metricExtrema.p5)}">${escapeHtml(formatNumber(stats.p5))}</td>
          <td class="${extremaClass(stats.p10, metricExtrema.p10)}">${escapeHtml(formatNumber(stats.p10))}</td>
          ${thresholdCells}
          <td>${escapeHtml(frames)}</td>
        </tr>
      `;
    })
    .join("");
}

function renderControls() {
  elements.videoLegend.innerHTML = "";
  elements.metricToggles.innerHTML = "";

  const rows = state.comparison ? state.comparison.summary || [] : [];

  rows.forEach((row) => {
    const isVisible = !state.hiddenFiles.has(row.id);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `chip${isVisible ? " is-active" : " is-muted"}`;
    chip.title = row.name;
    chip.setAttribute("aria-pressed", String(isVisible));
    chip.innerHTML = `
      <span class="swatch" style="background:${colorForRow(row)}"></span>
      <span>${escapeHtml(row.name)}</span>
    `;
    chip.addEventListener("click", () => {
      if (state.hiddenFiles.has(row.id)) {
        state.hiddenFiles.delete(row.id);
      } else {
        state.hiddenFiles.add(row.id);
      }
      renderControls();
      renderCharts();
    });
    elements.videoLegend.appendChild(chip);
  });

  const primaryChip = document.createElement("button");
  primaryChip.type = "button";
  primaryChip.className = `chip${state.activeMetrics.has("primary") ? " is-active" : " is-muted"}`;
  primaryChip.setAttribute("aria-pressed", String(state.activeMetrics.has("primary")));
  primaryChip.textContent = "Primary VMAF";
  primaryChip.addEventListener("click", () => {
    if (state.activeMetrics.has("primary")) {
      state.activeMetrics.delete("primary");
    } else {
      state.activeMetrics.add("primary");
    }
    renderControls();
    renderCharts();
  });
  elements.metricToggles.appendChild(primaryChip);

  const metrics = sharedMetrics().filter((metric) => metric !== "vmaf");
  for (const metric of metrics) {
    const isActive = state.activeMetrics.has(metric);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `chip${isActive ? " is-active" : " is-muted"}`;
    chip.setAttribute("aria-pressed", String(isActive));
    chip.textContent = metric;
    chip.addEventListener("click", async () => {
      if (state.activeMetrics.has(metric)) {
        state.activeMetrics.delete(metric);
      } else {
        state.activeMetrics.add(metric);
        try {
          await requestExtraSeries(metric);
        } catch (error) {
          state.activeMetrics.delete(metric);
          renderMessage({ error: error.message || `Unable to load ${metric}.` });
        }
      }
      renderControls();
      renderCharts();
    });
    elements.metricToggles.appendChild(chip);
  }
}

function visibleRows() {
  if (!state.comparison) {
    return [];
  }
  return (state.comparison.summary || []).filter((row) => !state.hiddenFiles.has(row.id));
}

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

function referenceLines() {
  return state.thresholds.map((threshold) => ({
    name: formatThreshold(threshold),
    yAxis: threshold,
    label: {
      formatter: formatThreshold(threshold),
      color: "#667064",
    },
    lineStyle: {
      color: "#aeb5aa",
      type: "dashed",
      width: 1,
    },
  }));
}

function baseChartOptions() {
  return {
    animation: false,
    color: COLORS,
    textStyle: {
      color: "#242821",
      fontFamily: getComputedStyle(document.body).fontFamily,
    },
    tooltip: {
      trigger: "axis",
      confine: true,
      valueFormatter: (value) => formatNumber(value),
    },
    grid: {
      top: 28,
      right: 22,
      bottom: 56,
      left: 52,
      containLabel: true,
    },
    xAxis: {
      type: "value",
      name: "Frame",
      nameGap: 24,
      axisLine: { lineStyle: { color: "#c6cabf" } },
      splitLine: { lineStyle: { color: "#eceee9" } },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: 100,
      name: "VMAF",
      axisLine: { lineStyle: { color: "#c6cabf" } },
      splitLine: { lineStyle: { color: "#eceee9" } },
    },
  };
}

function emptyChart(chart, text) {
  chart.clear();
  chart.setOption({
    title: {
      text,
      left: "center",
      top: "middle",
      textStyle: {
        color: "#667064",
        fontSize: 13,
        fontWeight: 500,
      },
    },
  });
}

function primaryLineSeries(rows) {
  if (!state.activeMetrics.has("primary")) {
    return [];
  }
  return rows.map((row) => {
    const series = state.comparison.series[row.id] || {};
    const color = colorForRow(row);
    return {
      name: row.name,
      type: "line",
      showSymbol: false,
      smooth: false,
      sampling: "lttb",
      data: series.points || [],
      lineStyle: { width: 1.6, color },
      itemStyle: { color },
      emphasis: { focus: "series" },
    };
  });
}

function extraMetricLineSeries(rows) {
  const series = [];
  for (const metric of activeExtraMetrics()) {
    const metricSeries = state.extraSeries.get(metric) || {};
    for (const row of rows) {
      const color = colorForRow(row);
      series.push({
        name: `${row.name} ${metric}`,
        type: "line",
        showSymbol: false,
        smooth: false,
        data: metricSeries[row.id]?.[metric]?.points || [],
        lineStyle: { width: 1.4, color, type: "dotted" },
        itemStyle: { color },
        emphasis: { focus: "series" },
      });
    }
  }
  return series;
}

function comparisonLineSeries(rows) {
  const series = [...primaryLineSeries(rows), ...extraMetricLineSeries(rows)];
  if (series.length) {
    series[0].markLine = { silent: true, symbol: "none", data: referenceLines() };
  }
  return series;
}

function renderLineCharts() {
  const rows = visibleRows();

  if (!state.comparison || !rows.length) {
    emptyChart(charts.line, "No visible VMAF series.");
    emptyChart(charts.zoom, "No visible VMAF series.");
    return;
  }

  const series = comparisonLineSeries(rows);
  if (!series.length) {
    emptyChart(charts.line, "No active metric series.");
    emptyChart(charts.zoom, "No active metric series.");
    return;
  }
  const commonRange = state.comparison.common_range || {};

  charts.line.setOption(
    {
      ...baseChartOptions(),
      dataZoom: [
        { type: "inside", filterMode: "none" },
        { type: "slider", height: 24, bottom: 16, filterMode: "none" },
      ],
      xAxis: {
        ...baseChartOptions().xAxis,
        min: commonRange.start || 0,
        max: commonRange.end || undefined,
      },
      series,
    },
    true,
  );

  charts.zoom.setOption(
    {
      ...baseChartOptions(),
      dataZoom: [
        { type: "inside", filterMode: "none" },
        { type: "slider", height: 26, bottom: 14, brushSelect: true, filterMode: "none" },
      ],
      xAxis: {
        ...baseChartOptions().xAxis,
        min: commonRange.start || 0,
        max: commonRange.end || undefined,
      },
      series,
    },
    true,
  );

  charts.zoom.off("datazoom");
  charts.zoom.on("datazoom", async () => {
    const metrics = activeExtraMetrics();
    if (!state.comparison || !metrics.length) {
      return;
    }

    const option = charts.zoom.getOption();
    const zoom = (option.dataZoom || []).find((item) => Number.isFinite(item.start) && Number.isFinite(item.end));
    if (!zoom) {
      return;
    }

    const rangeStart = Number(commonRange.start || 0);
    const rangeEnd = Number(commonRange.end || 0);
    const span = Math.max(0, rangeEnd - rangeStart);
    const start = Math.max(rangeStart, Math.floor(rangeStart + (zoom.start / 100) * span));
    const end = Math.min(rangeEnd, Math.ceil(rangeStart + (zoom.end / 100) * span));

    if (end - start > 5000) {
      return;
    }

    try {
      await requestExtraSeriesForRange(metrics, { start, end });
      renderCharts();
    } catch (error) {
      renderMessage({ error: error.message || "Unable to load zoomed metric series." });
    }
  });
}

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
      grid: { top: 24, right: 18, bottom: 42, left: 176, containLabel: true },
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
        axisLabel: { interval: 0, overflow: "truncate", width: 160 },
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

function renderDistributionCharts() {
  const rows = visibleRows();

  if (!state.comparison || !rows.length) {
    emptyChart(charts.boxplot, "No visible distribution.");
    emptyChart(charts.histogram, "No visible distribution.");
    emptyChart(charts.cdf, "No visible distribution.");
    updateDistributionVisibility();
    return;
  }

  renderBoxplotChart(rows);

  const firstHistogram = focusedHistogramBuckets(rows[0]);
  const labels = firstHistogram.map((bucket) => `${formatThreshold(bucket.start)}-${formatThreshold(bucket.end)}`);

  charts.histogram.setOption(
    {
      animation: false,
      color: COLORS,
      tooltip: { trigger: "axis", confine: true },
      grid: { top: 36, right: 18, bottom: 64, left: 52, containLabel: true },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { interval: 9, rotate: 0 },
        axisLine: { lineStyle: { color: "#c6cabf" } },
      },
      yAxis: {
        type: "value",
        name: "Frames",
        nameGap: 12,
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      series: rows.map((row) => ({
        name: row.name,
        type: "bar",
        barMaxWidth: 9,
        itemStyle: { color: colorForRow(row) },
        data: focusedHistogramBuckets(row).map((bucket) => bucket.count),
      })),
    },
    true,
  );

  charts.cdf.setOption(
    {
      animation: false,
      color: COLORS,
      tooltip: {
        trigger: "axis",
        confine: true,
        valueFormatter: (value) => `${formatNumber(value)}%`,
      },
      grid: { top: 36, right: 18, bottom: 52, left: 52, containLabel: true },
      xAxis: {
        type: "value",
        min: DISTRIBUTION_MIN_SCORE,
        max: DISTRIBUTION_MAX_SCORE,
        name: "VMAF",
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        name: "CDF %",
        nameGap: 12,
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      series: rows.map((row) => ({
        name: row.name,
        type: "line",
        showSymbol: false,
        step: "end",
        data: (state.comparison.cdf[row.id] || []).map((point) => [point.score, point.ratio * 100]),
        lineStyle: { width: 1.8, color: colorForRow(row) },
        itemStyle: { color: colorForRow(row) },
      })),
    },
    true,
  );

  updateDistributionVisibility();
}

function renderCharts() {
  if (!state.comparison) {
    emptyChart(charts.line, "No comparison loaded.");
    emptyChart(charts.zoom, "No comparison loaded.");
    emptyChart(charts.boxplot, "No comparison loaded.");
    emptyChart(charts.histogram, "No comparison loaded.");
    emptyChart(charts.cdf, "No comparison loaded.");
    updateDistributionVisibility();
    return;
  }

  renderLineCharts();
  renderDistributionCharts();
}

function updateDistributionVisibility() {
  const showHistogram = state.distribution === "histogram";
  elements.histogramChart.classList.toggle("is-hidden", !showHistogram);
  elements.cdfChart.classList.toggle("is-hidden", showHistogram);
  elements.histogramTab.classList.toggle("is-active", showHistogram);
  elements.cdfTab.classList.toggle("is-active", !showHistogram);
  elements.histogramTab.setAttribute("aria-selected", String(showHistogram));
  elements.cdfTab.setAttribute("aria-selected", String(!showHistogram));

  requestAnimationFrame(() => {
    charts.boxplot.resize();
    charts.histogram.resize();
    charts.cdf.resize();
  });
}

function setupEvents() {
  elements.scanPathForm.addEventListener("submit", (event) => {
    event.preventDefault();
    changeScanDirectory();
  });

  elements.refreshButton.addEventListener("click", async () => {
    elements.refreshButton.disabled = true;
    try {
      await loadFiles();
      if (state.selected.size) {
        await requestComparison();
      } else {
        renderSummary();
        renderControls();
        renderCharts();
      }
    } catch (error) {
      renderMessage({ error: error.message || "Unable to refresh files." });
    } finally {
      elements.refreshButton.disabled = false;
    }
  });

  elements.fileFilter.addEventListener("input", renderFiles);

  elements.thresholdInput.addEventListener("change", () => {
    requestComparison();
  });

  elements.thresholdInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      elements.thresholdInput.blur();
      requestComparison();
    }
  });

  elements.histogramTab.addEventListener("click", () => {
    state.distribution = "histogram";
    updateDistributionVisibility();
  });

  elements.cdfTab.addEventListener("click", () => {
    state.distribution = "cdf";
    updateDistributionVisibility();
  });

  window.addEventListener("resize", () => {
    for (const chart of Object.values(charts)) {
      chart.resize();
    }
  });
}

setupEvents();
renderSummary();
renderControls();
renderCharts();
renderMessage({ status: VmafMessageState.DEFAULT_STATUS_MESSAGE });
loadFiles().catch((error) => {
  renderMessage({ error: error.message || "Unable to load files." });
});
