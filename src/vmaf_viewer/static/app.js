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

const state = {
  files: [],
  selected: new Set(),
  comparison: null,
  hiddenFiles: new Set(),
  activeMetrics: new Set(["primary_vmaf"]),
  thresholds: [...DEFAULT_THRESHOLDS],
  distribution: "histogram",
  comparisonRequestId: 0,
};

const elements = {
  scanPath: document.getElementById("scanPath"),
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
  histogramChart: document.getElementById("histogramChart"),
  cdfChart: document.getElementById("cdfChart"),
  lineChart: document.getElementById("lineChart"),
  zoomChart: document.getElementById("zoomChart"),
};

const charts = {
  line: echarts.init(elements.lineChart),
  zoom: echarts.init(elements.zoomChart),
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

function renderMessages(messages, type = "warning") {
  elements.messages.innerHTML = "";
  for (const message of messages || []) {
    const item = document.createElement("div");
    item.className = `message ${type}`;
    item.textContent = message;
    elements.messages.appendChild(item);
  }
}

function colorForId(id) {
  const rows = state.comparison ? state.comparison.summary || [] : [];
  const index = rows.findIndex((row) => row.id === id);
  return COLORS[(index >= 0 ? index : 0) % COLORS.length];
}

function colorForRow(row) {
  return colorForId(row.id);
}

async function loadFiles() {
  const body = await api("/api/files");
  state.files = body.files || [];
  elements.scanPath.textContent = body.data_dir || "";

  const ids = new Set(state.files.map((file) => file.id));
  state.selected = new Set([...state.selected].filter((id) => ids.has(id)));
  state.hiddenFiles = new Set([...state.hiddenFiles].filter((id) => ids.has(id)));

  renderFiles();
  updateSelectedCount();

  if (!state.files.length) {
    renderMessages(["No *_vmaf.json files found."]);
  } else {
    renderMessages([]);
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
    renderMessages(state.files.length ? ["Select one or more files."] : []);
    renderSummary();
    renderControls();
    renderCharts();
    return;
  }

  renderMessages(["Loading comparison..."]);

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

    const comparedIds = new Set((body.summary || []).map((row) => row.id));
    state.hiddenFiles = new Set([...state.hiddenFiles].filter((id) => comparedIds.has(id)));

    renderMessages(body.warnings || []);
    renderSummary();
    renderControls();
    renderCharts();
  } catch (error) {
    if (requestId !== state.comparisonRequestId) {
      return;
    }
    state.comparison = null;
    renderMessages([error.message || "Unable to compare selected files."], "error");
    renderSummary();
    renderControls();
    renderCharts();
  }
}

function thresholdEntry(stats, threshold) {
  const entries = stats && stats.thresholds ? stats.thresholds : {};
  const key = Object.keys(entries).find((candidate) => Number(candidate) === Number(threshold));
  return key ? entries[key] : null;
}

function renderSummary() {
  const rows = state.comparison ? state.comparison.summary || [] : [];
  const thresholds = state.thresholds.length ? state.thresholds : DEFAULT_THRESHOLDS;
  const thresholdHeaders = thresholds.map((threshold) => `<th>&lt;= ${escapeHtml(formatThreshold(threshold))}</th>`).join("");

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

  const bestMean = Math.max(...rows.map((row) => Number(row.stats.mean)).filter(Number.isFinite));
  tbody.innerHTML = rows
    .map((row) => {
      const stats = row.stats || {};
      const mean = Number(stats.mean);
      const rowClass = Number.isFinite(bestMean) && mean === bestMean ? "best-mean" : bestMean - mean >= 1 ? "weak-mean" : "";
      const thresholdCells = thresholds
        .map((threshold) => {
          const entry = thresholdEntry(stats, threshold);
          const count = entry ? entry.count : "n/a";
          const ratio = entry ? formatPercent(entry.ratio) : "n/a";
          return `<td class="threshold-cell"><strong>${escapeHtml(count)}</strong> ${escapeHtml(ratio)}</td>`;
        })
        .join("");
      const frames = `${row.common_frames || stats.count || 0}/${row.total_frames || 0}`;

      return `
        <tr class="${rowClass}">
          <td title="${escapeHtml(row.relative_path || row.name)}">${escapeHtml(row.name)}</td>
          <td class="mean-cell">${escapeHtml(formatNumber(stats.mean))}</td>
          <td>${escapeHtml(formatNumber(stats.min))}</td>
          <td>${escapeHtml(formatNumber(stats.max))}</td>
          <td>${escapeHtml(formatNumber(stats.p1))}</td>
          <td>${escapeHtml(formatNumber(stats.p5))}</td>
          <td>${escapeHtml(formatNumber(stats.p10))}</td>
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

  const metricChip = document.createElement("button");
  metricChip.type = "button";
  metricChip.className = "chip is-active";
  metricChip.disabled = true;
  metricChip.setAttribute("aria-disabled", "true");
  metricChip.textContent = "Primary VMAF";
  elements.metricToggles.appendChild(metricChip);
}

function visibleRows() {
  if (!state.comparison) {
    return [];
  }
  return (state.comparison.summary || []).filter((row) => !state.hiddenFiles.has(row.id));
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

function lineSeries(rows) {
  return rows.map((row, index) => {
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
      markLine: index === 0 ? { silent: true, symbol: "none", data: referenceLines() } : undefined,
    };
  });
}

function renderLineCharts() {
  const rows = visibleRows();

  if (!state.comparison || !rows.length) {
    emptyChart(charts.line, "No visible VMAF series.");
    emptyChart(charts.zoom, "No visible VMAF series.");
    return;
  }

  const series = lineSeries(rows);
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
}

function renderDistributionCharts() {
  const rows = visibleRows();

  if (!state.comparison || !rows.length) {
    emptyChart(charts.histogram, "No visible distribution.");
    emptyChart(charts.cdf, "No visible distribution.");
    updateDistributionVisibility();
    return;
  }

  const firstHistogram = state.comparison.histogram[rows[0].id] || [];
  const labels = firstHistogram.map((bucket) => `${formatThreshold(bucket.start)}-${formatThreshold(bucket.end)}`);

  charts.histogram.setOption(
    {
      animation: false,
      color: COLORS,
      tooltip: { trigger: "axis", confine: true },
      grid: { top: 24, right: 18, bottom: 64, left: 52, containLabel: true },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { interval: 9, rotate: 0 },
        axisLine: { lineStyle: { color: "#c6cabf" } },
      },
      yAxis: {
        type: "value",
        name: "Frames",
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      series: rows.map((row) => ({
        name: row.name,
        type: "bar",
        barMaxWidth: 9,
        itemStyle: { color: colorForRow(row) },
        data: (state.comparison.histogram[row.id] || []).map((bucket) => bucket.count),
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
      grid: { top: 24, right: 18, bottom: 52, left: 52, containLabel: true },
      xAxis: {
        type: "value",
        min: 0,
        max: 100,
        name: "VMAF",
        axisLine: { lineStyle: { color: "#c6cabf" } },
        splitLine: { lineStyle: { color: "#eceee9" } },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        name: "CDF %",
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
    charts.histogram.resize();
    charts.cdf.resize();
  });
}

function setupEvents() {
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
      renderMessages([error.message || "Unable to refresh files."], "error");
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
loadFiles().catch((error) => {
  renderMessages([error.message || "Unable to load files."], "error");
});
