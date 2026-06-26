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
const DISTRIBUTION_MIN_FLOOR_SCORE = 60;
const DISTRIBUTION_GRID = { top: "5%", right: "5%", bottom: "5%", left: "5%" };
const INTEGER_GROUP_RE = /\B(?=(\d{3})+(?!\d))/g;
const RANGE_LOAD_DEBOUNCE_MS = 400;

const state = {
  files: [],
  selected: new Set(),
  comparison: null,
  hiddenFiles: new Set(),
  activeDetailMetrics: new Set(),
  metricsByFile: new Map(),
  extraSeries: new Map(),
  thresholds: [...DEFAULT_THRESHOLDS],
  fps: 0,
  distribution: "histogram",
  comparisonRequestId: 0,
  zoomSeriesRequestId: 0,
  renderedDetailSeriesIds: new Set(),
  detailRangeLoadTimer: null,
  pendingDetailRangeLoad: null,
  lastDetailRangeLoadKey: "",
  primarySeriesCache: new Map(),
  primaryRangeLoadTimer: null,
  pendingPrimaryRangeLoad: null,
  lastPrimaryRangeLoadKey: "",
};

const elements = {
  scanPathForm: document.getElementById("scanPathForm"),
  scanPathInput: document.getElementById("scanPathInput"),
  scanPathButton: document.getElementById("scanPathButton"),
  fpsInput: document.getElementById("fpsInput"),
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

function applyFpsInput() {
  state.fps = normalizeFpsValue(elements.fpsInput.value);
  elements.fpsInput.value = String(state.fps);
  renderCharts();
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

function formatInteger(value) {
  return String(value).replace(INTEGER_GROUP_RE, ",");
}

function normalizeFpsValue(value) {
  const text = String(value ?? "").trim();
  if (!/^\d+$/.test(text)) {
    return 0;
  }
  const fps = Number(text);
  return Number.isSafeInteger(fps) && fps > 0 ? fps : 0;
}

function padInteger(value, width) {
  return String(value).padStart(width, "0");
}

function formatFrameTime(frame, fps) {
  const totalSeconds = Math.floor(frame / fps);
  const frameInSecond = frame % fps;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const frameWidth = String(fps - 1).length;
  const frameSuffix = padInteger(frameInSecond, frameWidth);

  if (hours > 0) {
    return `${hours}:${padInteger(minutes, 2)}:${padInteger(seconds, 2)}.${frameSuffix}`;
  }
  return `${padInteger(minutes, 2)}:${padInteger(seconds, 2)}.${frameSuffix}`;
}

function formatFrameValue(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value ?? "");
  }
  if (!Number.isInteger(number)) {
    return formatThreshold(number);
  }

  const frameLabel = formatInteger(number);
  return state.fps > 0 ? `${frameLabel} ${formatFrameTime(number, state.fps)}` : frameLabel;
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
  state.activeDetailMetrics.clear();
  state.metricsByFile.clear();
  state.extraSeries.clear();
  state.primarySeriesCache.clear();
  clearPrimaryRangeLoadState();
  state.comparisonRequestId += 1;
  state.zoomSeriesRequestId += 1;
  clearDetailRangeLoadState();
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
  clearDetailRangeLoadState();
  clearPrimaryRangeLoadState();

  if (!fileIds.length) {
    state.comparison = null;
    state.extraSeries.clear();
    state.primarySeriesCache.clear();
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
    initializePrimarySeriesCache();
    state.extraSeries.clear();
    const comparedFileIds = (body.summary || []).map((row) => row.id);
    await loadMetricsForSelected(comparedFileIds);
    if (requestId !== state.comparisonRequestId) {
      return;
    }

    const comparedIds = new Set(comparedFileIds);
    state.hiddenFiles = new Set([...state.hiddenFiles].filter((id) => comparedIds.has(id)));
    const defaultMetrics = initializeDefaultDetailMetrics();
    await requestExtraSeriesForMetrics(defaultMetrics, null, requestId);
    if (requestId !== state.comparisonRequestId) {
      return;
    }

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

function activeDetailMetrics() {
  const shared = sharedMetrics();
  return VmafMetricMetadata.normalizeDetailSelection([...state.activeDetailMetrics], shared);
}

function initializeDefaultDetailMetrics() {
  const activeMetrics = activeDetailMetrics();
  if (activeMetrics.length) {
    state.activeDetailMetrics = new Set(activeMetrics);
    return activeMetrics;
  }

  const defaults = VmafMetricMetadata.defaultDetailMetrics(sharedMetrics());
  state.activeDetailMetrics = new Set(defaults);
  return defaults;
}

function pruneActiveDetailMetrics() {
  state.activeDetailMetrics = new Set(activeDetailMetrics());
}

function initializePrimarySeriesCache() {
  state.primarySeriesCache.clear();
  const comparisonSeries = state.comparison?.series || {};
  for (const [fileId, series] of Object.entries(comparisonSeries)) {
    state.primarySeriesCache.set(fileId, {
      metric: series.metric,
      points: Array.isArray(series.points) ? [...series.points] : [],
    });
  }
}

function primarySeriesForRow(row) {
  return state.primarySeriesCache.get(row.id) || state.comparison?.series?.[row.id] || {};
}

async function requestExtraSeries(metric, range = null) {
  if (!range && state.extraSeries.has(metric)) {
    return false;
  }
  return requestExtraSeriesForMetrics([metric], range);
}

async function requestExtraSeriesForMetrics(metrics, range = null, expectedComparisonRequestId = state.comparisonRequestId) {
  const fileIds = comparedFileIds();
  const requestedMetrics = metrics.filter((metric) => range || !state.extraSeries.has(metric));
  if (!state.comparison || !requestedMetrics.length || !fileIds.length) {
    return false;
  }

  const requestId = ++state.zoomSeriesRequestId;
  const commonRange = state.comparison.common_range || {};
  const body = await api("/api/series", {
    method: "POST",
    body: {
      file_ids: fileIds,
      metrics: requestedMetrics,
      start: range ? range.start : commonRange.start || 0,
      end: range ? range.end : commonRange.end,
      max_points: range ? 5000 : 2000,
    },
  });

  if (expectedComparisonRequestId !== state.comparisonRequestId) {
    return false;
  }
  if (range && requestId !== state.zoomSeriesRequestId) {
    return false;
  }
  for (const metric of requestedMetrics) {
    const series = body.series || {};
    state.extraSeries.set(metric, range ? mergeMetricSeries(state.extraSeries.get(metric), series, metric) : series);
  }
  return true;
}

async function requestExtraSeriesForRange(metrics, range) {
  return requestExtraSeriesForMetrics(metrics, range);
}

function clearDetailRangeLoadState() {
  cancelPendingDetailRangeLoad();
  state.lastDetailRangeLoadKey = "";
}

function cancelPendingDetailRangeLoad() {
  if (state.detailRangeLoadTimer !== null) {
    clearTimeout(state.detailRangeLoadTimer);
  }
  state.detailRangeLoadTimer = null;
  state.pendingDetailRangeLoad = null;
}

function detailRangeLoadKey(metrics, range) {
  return `${metrics.join(",")}:${range.start}:${range.end}`;
}

function clearPrimaryRangeLoadState() {
  cancelPendingPrimaryRangeLoad();
  state.lastPrimaryRangeLoadKey = "";
}

function cancelPendingPrimaryRangeLoad() {
  if (state.primaryRangeLoadTimer !== null) {
    clearTimeout(state.primaryRangeLoadTimer);
  }
  state.primaryRangeLoadTimer = null;
  state.pendingPrimaryRangeLoad = null;
}

function chartZoomRange(chart) {
  if (!state.comparison) {
    return null;
  }

  const option = chart.getOption();
  const zoom = (option.dataZoom || []).find((item) => Number.isFinite(item.start) && Number.isFinite(item.end));
  if (!zoom) {
    return null;
  }

  const commonRange = state.comparison.common_range || {};
  const rangeStart = Number(commonRange.start || 0);
  const rangeEnd = Number(commonRange.end || 0);
  const span = Math.max(0, rangeEnd - rangeStart);
  const start = Math.max(rangeStart, Math.floor(rangeStart + (zoom.start / 100) * span));
  const end = Math.min(rangeEnd, Math.ceil(rangeStart + (zoom.end / 100) * span));

  if (end - start > 5000) {
    return null;
  }

  return { start, end };
}

function currentDetailZoomRange() {
  return chartZoomRange(charts.zoom);
}

function currentPrimaryZoomRange() {
  return chartZoomRange(charts.line);
}

function mergePointSeries(existingPoints = [], incomingPoints = []) {
  const pointsByFrame = new Map();
  for (const point of existingPoints || []) {
    if (Array.isArray(point) && point.length >= 2) {
      pointsByFrame.set(Number(point[0]), point);
    }
  }
  for (const point of incomingPoints || []) {
    if (Array.isArray(point) && point.length >= 2) {
      pointsByFrame.set(Number(point[0]), point);
    }
  }
  return [...pointsByFrame.entries()]
    .sort((left, right) => left[0] - right[0])
    .map((entry) => entry[1]);
}

function mergePrimarySeries(incomingSeries = {}) {
  for (const [fileId, incomingMetrics] of Object.entries(incomingSeries || {})) {
    const current = state.primarySeriesCache.get(fileId) || state.comparison?.series?.[fileId];
    const metric = current?.metric;
    if (!metric || !incomingMetrics?.[metric]) {
      continue;
    }
    state.primarySeriesCache.set(fileId, {
      metric,
      points: mergePointSeries(current.points || [], incomingMetrics[metric].points || []),
    });
  }
}

function primaryRangeLoadKey(rows, range) {
  const rowKey = rows
    .map((row) => {
      const metric = primarySeriesForRow(row).metric || "";
      return `${row.id}:${metric}`;
    })
    .join("|");
  return `${rowKey}:${range.start}:${range.end}`;
}

function scheduleCurrentDetailRangeLoad(metrics) {
  const range = currentDetailZoomRange();
  if (!range) {
    cancelPendingDetailRangeLoad();
    return;
  }
  scheduleDetailRangeLoad(metrics, range);
}

function scheduleDetailRangeLoad(metrics, range) {
  const key = detailRangeLoadKey(metrics, range);
  if (key === state.lastDetailRangeLoadKey) {
    return;
  }

  if (state.detailRangeLoadTimer !== null) {
    clearTimeout(state.detailRangeLoadTimer);
  }

  state.pendingDetailRangeLoad = {
    key,
    metrics: [...metrics],
    range: { ...range },
    comparisonRequestId: state.comparisonRequestId,
  };
  state.detailRangeLoadTimer = setTimeout(loadPendingDetailRange, RANGE_LOAD_DEBOUNCE_MS);
}

async function loadPendingDetailRange() {
  const pending = state.pendingDetailRangeLoad;
  state.detailRangeLoadTimer = null;
  state.pendingDetailRangeLoad = null;

  if (!pending || pending.key === state.lastDetailRangeLoadKey) {
    return;
  }
  if (pending.comparisonRequestId !== state.comparisonRequestId) {
    return;
  }
  const activeMetrics = activeDetailMetrics();
  if (!pending.metrics.every((metric) => activeMetrics.includes(metric))) {
    return;
  }

  try {
    const applied = await requestExtraSeriesForRange(pending.metrics, pending.range);
    if (!applied || pending.comparisonRequestId !== state.comparisonRequestId) {
      return;
    }
    state.lastDetailRangeLoadKey = pending.key;
    renderLineCharts();
  } catch (error) {
    if (pending.comparisonRequestId === state.comparisonRequestId) {
      renderMessage({ error: error.message || "Unable to load zoomed metric series." });
    }
  }
}

function mergeMetricSeries(existingSeries = {}, incomingSeries = {}, metric) {
  const mergedSeries = { ...existingSeries };

  for (const [fileId, incomingMetrics] of Object.entries(incomingSeries || {})) {
    const incomingMetric = incomingMetrics && incomingMetrics[metric];
    if (!incomingMetric) {
      continue;
    }

    const existingMetrics = mergedSeries[fileId] || {};
    const existingMetric = existingMetrics[metric] || {};
    mergedSeries[fileId] = {
      ...existingMetrics,
      [metric]: {
        ...existingMetric,
        points: mergePointSeries(existingMetric.points || [], incomingMetric.points || []),
      },
    };
  }

  return mergedSeries;
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

  const metrics = VmafMetricMetadata.detailMetrics(sharedMetrics());
  for (const metric of metrics) {
    const meta = VmafMetricMetadata.metricMeta(metric);
    const isActive = state.activeDetailMetrics.has(metric);
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = `chip metric-chip${isActive ? " is-active" : " is-muted"}`;
    chip.setAttribute("aria-pressed", String(isActive));
    chip.title = `${metric} · ${meta.axisName}`;
    chip.innerHTML = `
      <span class="metric-name">${escapeHtml(metric)}</span>
      <span class="metric-axis-tag metric-axis-tag-${escapeHtml(meta.axisGroup)}">${escapeHtml(meta.axisTag)}</span>
    `;
    chip.addEventListener("click", async () => {
      const requestId = state.comparisonRequestId;
      const previous = new Set(state.activeDetailMetrics);
      const wasActive = state.activeDetailMetrics.has(metric);
      state.activeDetailMetrics = new Set(
        VmafMetricMetadata.toggleDetailMetric([...state.activeDetailMetrics], metric),
      );
      try {
        if (state.activeDetailMetrics.has(metric)) {
          await requestExtraSeries(metric);
          if (!wasActive) {
            scheduleCurrentDetailRangeLoad([metric]);
          }
        }
      } catch (error) {
        if (requestId !== state.comparisonRequestId) {
          return;
        }
        state.activeDetailMetrics = previous;
        renderMessage({ error: error.message || `Unable to load ${metric}.` });
      }
      if (requestId !== state.comparisonRequestId) {
        return;
      }
      pruneActiveDetailMetrics();
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
    const color = colorForRow(row);
    data.push({
      value: values,
      itemStyle: { color, borderColor: color },
    });
  }

  return { labels, data };
}

function distributionMinScore(rows) {
  const mins = rows.map((row) => Number(row.stats && row.stats.min)).filter((value) => Number.isFinite(value));
  if (!mins.length) {
    return DISTRIBUTION_MIN_FLOOR_SCORE;
  }
  return Math.max(DISTRIBUTION_MIN_FLOOR_SCORE, Math.floor(Math.min(...mins)));
}

function focusedHistogramBuckets(row, minScore) {
  return (state.comparison.histogram[row.id] || []).filter(
    (bucket) => Number(bucket.end) > minScore && Number(bucket.start) < 100,
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
      axisPointer: {
        label: {
          formatter: (params) => formatFrameValue(params.value),
        },
      },
      valueFormatter: (value) => formatNumber(value),
    },
    grid: { top: 10, right: 22, bottom: 60, left: 22 },
    xAxis: {
      type: "value",
      name: "Frame",
      nameGap: 6,
      axisLine: { lineStyle: { color: "#c6cabf" } },
      splitLine: { lineStyle: { color: "#eceee9" } },
    },
    yAxis: {
      type: "value",
      max: 100,
      scale: true,
      name: "VMAF",
      nameGap: 6,
      axisLine: { lineStyle: { color: "#c6cabf" } },
      splitLine: { lineStyle: { color: "#eceee9" } },
    },
  };
}

function detailYAxisOptions(metrics) {
  const hasNormalized = metrics.some((metric) => {
    const meta = VmafMetricMetadata.metricMeta(metric);
    return meta && meta.axisGroup === "normalized";
  });
  const rawFamily = VmafMetricMetadata.rawFamilyForMetrics(metrics);

  return [
    {
      type: "value",
      min: 0,
      max: 1.1,
      scale: true,
      show: hasNormalized,
      name: "ADM / VIF / AIM",
      nameGap: 8,
      axisLine: { lineStyle: { color: "#8dbdb6" } },
      axisLabel: { color: "#24736f" },
      splitLine: { lineStyle: { color: "#eceee9" } },
    },
    {
      type: "value",
      scale: true,
      show: rawFamily !== null,
      position: "right",
      name: rawFamily === "psnr" ? "PSNR (dB)" : "Motion",
      nameGap: 10,
      axisLine: { lineStyle: { color: "#dfb17e" } },
      axisLabel: { color: "#b45f1a" },
      splitLine: { show: false },
    },
  ];
}

function detailChartOptions() {
  const metrics = activeDetailMetrics();
  const rawFamily = VmafMetricMetadata.rawFamilyForMetrics(metrics);
  const options = baseChartOptions();

  return {
    ...options,
    title: { show: false, text: "" },
    tooltip: {
      ...options.tooltip,
      valueFormatter: (value) => formatNumber(value, 3),
    },
    grid: { ...options.grid, left: 46, right: rawFamily ? 58 : 22 },
    yAxis: detailYAxisOptions(metrics),
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
  return rows.map((row) => {
    const series = primarySeriesForRow(row);
    const color = colorForRow(row);
    return {
      id: `primary:${row.id}`,
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

function detailMetricLineSeries(rows) {
  const series = [];
  for (const metric of activeDetailMetrics()) {
    const meta = VmafMetricMetadata.metricMeta(metric);
    if (!meta) {
      continue;
    }
    const metricSeries = state.extraSeries.get(metric) || {};
    for (const row of rows) {
      const color = colorForRow(row);
      series.push({
        id: `${row.id}:${metric}`,
        name: `${row.name} ${metric} · ${meta.axisTag}`,
        type: "line",
        yAxisIndex: meta.yAxisIndex,
        showSymbol: false,
        smooth: false,
        data: metricSeries[row.id]?.[metric]?.points || [],
        lineStyle: { width: 1.4, color, type: meta.axisGroup === "normalized" ? "solid" : "dotted" },
        itemStyle: { color },
        emphasis: { focus: "series" },
      });
    }
  }
  return series;
}

function vmafOverviewSeries(rows) {
  const series = primaryLineSeries(rows);
  if (series.length) {
    series[0].markLine = { silent: true, symbol: "none", data: referenceLines() };
  }
  return series;
}

function detailViewSeries(rows) {
  return detailMetricLineSeries(rows);
}

function detailSeriesForMerge(detailSeries) {
  const nextIds = new Set(detailSeries.map((series) => series.id).filter(Boolean));
  const removedSeries = [...state.renderedDetailSeriesIds]
    .filter((id) => !nextIds.has(id))
    .map((id) => ({ id, type: "line", data: [] }));

  state.renderedDetailSeriesIds = nextIds;
  return [...detailSeries, ...removedSeries];
}

function primaryMetricGroups(rows) {
  const groups = new Map();
  for (const row of rows) {
    const metric = primarySeriesForRow(row).metric;
    if (!metric) {
      continue;
    }
    if (!groups.has(metric)) {
      groups.set(metric, []);
    }
    groups.get(metric).push(row.id);
  }
  return groups;
}

function schedulePrimaryRangeLoad(rows, range) {
  const key = primaryRangeLoadKey(rows, range);
  if (key === state.lastPrimaryRangeLoadKey) {
    return;
  }

  if (state.primaryRangeLoadTimer !== null) {
    clearTimeout(state.primaryRangeLoadTimer);
  }

  state.pendingPrimaryRangeLoad = {
    key,
    rows: rows.map((row) => ({ id: row.id, name: row.name })),
    range: { ...range },
    comparisonRequestId: state.comparisonRequestId,
  };
  state.primaryRangeLoadTimer = setTimeout(loadPendingPrimaryRange, RANGE_LOAD_DEBOUNCE_MS);
}

async function requestPrimarySeriesForRange(rows, range) {
  const groups = primaryMetricGroups(rows);
  if (!state.comparison || !groups.size) {
    return false;
  }

  for (const [metric, fileIds] of groups.entries()) {
    const body = await api("/api/series", {
      method: "POST",
      body: {
        file_ids: fileIds,
        metrics: [metric],
        start: range.start,
        end: range.end,
        max_points: 5000,
      },
    });
    mergePrimarySeries(body.series || {});
  }
  return true;
}

async function loadPendingPrimaryRange() {
  const pending = state.pendingPrimaryRangeLoad;
  state.primaryRangeLoadTimer = null;
  state.pendingPrimaryRangeLoad = null;

  if (!pending || pending.key === state.lastPrimaryRangeLoadKey) {
    return;
  }
  if (pending.comparisonRequestId !== state.comparisonRequestId) {
    return;
  }

  try {
    const applied = await requestPrimarySeriesForRange(pending.rows, pending.range);
    if (!applied || pending.comparisonRequestId !== state.comparisonRequestId) {
      return;
    }
    state.lastPrimaryRangeLoadKey = pending.key;
    refreshPrimaryChartSeries();
  } catch (error) {
    if (pending.comparisonRequestId === state.comparisonRequestId) {
      renderMessage({ error: error.message || "Unable to load zoomed VMAF series." });
    }
  }
}

function refreshPrimaryChartSeries() {
  if (!state.comparison) {
    return;
  }
  const rows = visibleRows();
  if (!rows.length) {
    cancelPendingPrimaryRangeLoad();
    return;
  }
  charts.line.setOption({ series: vmafOverviewSeries(rows) });
}

function renderLineCharts() {
  const rows = visibleRows();
  charts.line.off("datazoom");
  charts.zoom.off("datazoom");

  if (!state.comparison || !rows.length) {
    state.renderedDetailSeriesIds = new Set();
    emptyChart(charts.line, "No visible VMAF series.");
    emptyChart(charts.zoom, "No visible VMAF series.");
    return;
  }

  const commonRange = state.comparison.common_range || {};
  const overviewSeries = vmafOverviewSeries(rows);

  if (!overviewSeries.length) {
    emptyChart(charts.line, "No visible VMAF series.");
  } else {
    const overviewOptions = baseChartOptions();
    charts.line.setOption(
      {
        ...overviewOptions,
        dataZoom: [
          { type: "inside", filterMode: "none" },
          { type: "slider", height: 24, bottom: 12, filterMode: "none" },
        ],
        xAxis: {
          ...overviewOptions.xAxis,
          min: commonRange.start || 0,
          max: commonRange.end || undefined,
        },
        series: overviewSeries,
      },
      true,
    );
  }

  charts.line.on("datazoom", async () => {
    const currentRows = visibleRows();
    if (!state.comparison || !currentRows.length) {
      cancelPendingPrimaryRangeLoad();
      return;
    }

    const range = currentPrimaryZoomRange();
    if (!range) {
      cancelPendingPrimaryRangeLoad();
      return;
    }

    schedulePrimaryRangeLoad(currentRows, range);
  });

  const detailSeries = detailViewSeries(rows);
  if (!detailSeries.length) {
    state.renderedDetailSeriesIds = new Set();
    emptyChart(charts.zoom, "No active detail metrics.");
    return;
  }

  const detailOptions = detailChartOptions();
  charts.zoom.setOption({
    ...detailOptions,
    dataZoom: [
      { type: "inside", filterMode: "none" },
      { type: "slider", height: 24, bottom: 12, filterMode: "none", showDataShadow: false },
    ],
    xAxis: {
      ...detailOptions.xAxis,
      min: commonRange.start || 0,
      max: commonRange.end || undefined,
    },
    series: detailSeriesForMerge(detailSeries),
  });

  charts.zoom.on("datazoom", async () => {
    const metrics = activeDetailMetrics();
    if (!state.comparison || !metrics.length) {
      cancelPendingDetailRangeLoad();
      return;
    }

    const range = currentDetailZoomRange();
    if (!range) {
      cancelPendingDetailRangeLoad();
      return;
    }

    scheduleDetailRangeLoad(metrics, range);
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
      grid: DISTRIBUTION_GRID,
      xAxis: {
        type: "value",
        scale: true,
        max: 100,
        name: "VMAF",
        nameGap: 6,
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      yAxis: {
        type: "category",
        data: labels,
        inverse: true,
        axisLabel: { interval: 0, overflow: "truncate", width: 60, rotate: 90 },
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

  const minScore = distributionMinScore(rows);
  const rowBuckets = rows.map((row) => focusedHistogramBuckets(row, minScore));
  const labels = rowBuckets[0].map((bucket) => `${formatThreshold(bucket.start)}-${formatThreshold(bucket.end)}`);

  charts.histogram.setOption(
    {
      animation: false,
      color: COLORS,
      tooltip: { trigger: "axis", confine: true },
      grid: DISTRIBUTION_GRID,
      xAxis: {
        type: "category",
        data: labels,
        name: "VMAF",
        nameGap: 6,
        axisLabel: { interval: "auto", rotate: 0 },
        axisLine: { lineStyle: { color: "#c6cabf" } },
      },
      yAxis: {
        type: "value",
        name: "Frames",
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      series: rows.map((row, i) => ({
        name: row.name,
        type: "bar",
        barMaxWidth: 9,
        itemStyle: { color: colorForRow(row) },
        data: rowBuckets[i].map((bucket) => bucket.count),
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
      grid: DISTRIBUTION_GRID,
      xAxis: {
        type: "value",
        min: minScore,
        max: 100,
        name: "VMAF",
        nameGap: 6,
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

  elements.fpsInput.addEventListener("change", () => {
    applyFpsInput();
  });

  elements.fpsInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      elements.fpsInput.blur();
      applyFpsInput();
    }
  });

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
