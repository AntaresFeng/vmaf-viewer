import assert from "node:assert/strict";
import test from "node:test";

import { loadAppContext as loadHarnessAppContext, toHostValue } from "./browser_harness.mjs";

const APP_EXPORTS = [
  "baseChartOptions",
  "charts",
  "elements",
  "formatFrameValue",
  "initializeDefaultDetailMetrics",
  "normalizeFpsValue",
  "renderLineCharts",
  "requestExtraSeriesForRange",
  "renderControls",
  "setupEvents",
  "state",
];

function loadAppContext(extraGlobals = {}) {
  return loadHarnessAppContext(APP_EXPORTS, extraGlobals).exports;
}

test("formats frame axis pointer label as grouped integer frames without fps", () => {
  const { baseChartOptions, state } = loadAppContext();
  const options = baseChartOptions();
  const formatter = options.tooltip.axisPointer?.label?.formatter;

  assert.equal(state.fps, 0);
  assert.equal(options.xAxis.axisLabel?.formatter, undefined);
  assert.equal(typeof formatter, "function");
  assert.equal(formatter({ value: 256 }), "256");
  assert.equal(formatter({ value: "256.00" }), "256");
  assert.equal(formatter({ value: 1234567 }), "1,234,567");
  assert.equal(formatter({ value: "1234567.00" }), "1,234,567");
});

test("formats frame axis pointer label with fps-derived time", () => {
  const { baseChartOptions, state } = loadAppContext();
  state.fps = 60;
  const formatter = baseChartOptions().tooltip.axisPointer.label.formatter;

  assert.equal(formatter({ value: 256 }), "256 00:04.16");
  assert.equal(formatter({ value: 216001 }), "216,001 1:00:00.01");
});

test("uses frame suffix width from fps digit count", () => {
  const { baseChartOptions, state } = loadAppContext();
  state.fps = 120;
  const formatter = baseChartOptions().tooltip.axisPointer.label.formatter;

  assert.equal(formatter({ value: 256 }), "256 00:02.016");
  assert.equal(formatter({ value: 119 }), "119 00:00.119");
});

test("normalizes invalid fps input to zero", () => {
  const { normalizeFpsValue } = loadAppContext();

  assert.equal(normalizeFpsValue(""), 0);
  assert.equal(normalizeFpsValue("0"), 0);
  assert.equal(normalizeFpsValue("-1"), 0);
  assert.equal(normalizeFpsValue("abc"), 0);
  assert.equal(normalizeFpsValue("59.94"), 0);
  assert.equal(normalizeFpsValue("120"), 120);
});

test("fps changes normalize the input and only refresh charts", () => {
  let renderCount = 0;
  let comparisonRequests = 0;
  const { elements, setupEvents, state } = loadAppContext({
    renderCharts: () => {
      renderCount += 1;
    },
    requestComparison: () => {
      comparisonRequests += 1;
    },
  });

  setupEvents();
  elements.fpsInput.value = "";
  elements.fpsInput.listeners.change();

  assert.equal(state.fps, 0);
  assert.equal(elements.fpsInput.value, "0");
  assert.equal(renderCount, 1);
  assert.equal(comparisonRequests, 0);

  elements.fpsInput.value = "120";
  elements.fpsInput.listeners.keydown({ key: "Enter" });

  assert.equal(state.fps, 120);
  assert.equal(elements.fpsInput.value, "120");
  assert.equal(elements.fpsInput.didBlur, true);
  assert.equal(renderCount, 2);
  assert.equal(comparisonRequests, 0);
});

test("default detail metrics preserve valid user selection", () => {
  const { initializeDefaultDetailMetrics, state } = loadAppContext();
  state.comparison = {
    summary: [
      { id: "a", name: "A" },
      { id: "b", name: "B" },
    ],
  };
  state.metricsByFile = new Map([
    ["a", ["vmaf", "integer_adm2", "integer_adm_scale1", "integer_vif_scale0", "integer_motion2", "psnr_y"]],
    ["b", ["vmaf", "integer_adm2", "integer_adm_scale1", "integer_vif_scale0", "integer_motion2", "psnr_y"]],
  ]);
  state.activeDetailMetrics = new Set(["integer_adm_scale1", "psnr_y"]);

  const metricsToLoad = initializeDefaultDetailMetrics();

  assert.deepEqual(Array.from(metricsToLoad), ["integer_adm_scale1", "psnr_y"]);
  assert.deepEqual(Array.from(state.activeDetailMetrics), ["integer_adm_scale1", "psnr_y"]);
});

test("stale detail metric toggle does not prune or render current comparison", async () => {
  let renderControlsCount = 0;
  let renderChartsCount = 0;
  let resolveSeries;
  const { elements, renderControls, state } = loadAppContext({
    renderCharts: () => {
      renderChartsCount += 1;
    },
    renderControls: () => {
      renderControlsCount += 1;
    },
    requestExtraSeries: () =>
      new Promise((resolve) => {
        resolveSeries = resolve;
      }),
  });
  state.comparisonRequestId = 1;
  state.comparison = { summary: [{ id: "a", name: "A" }] };
  state.metricsByFile = new Map([["a", ["integer_adm2", "psnr_y"]]]);
  state.activeDetailMetrics = new Set(["integer_adm2"]);
  renderControls();

  const psnrToggle = elements.metricToggles.children.find((chip) => chip.title.startsWith("psnr_y"));
  assert.ok(psnrToggle);
  const clickPromise = psnrToggle.listeners.click();

  state.comparisonRequestId = 2;
  state.comparison = { summary: [{ id: "b", name: "B" }] };
  state.metricsByFile = new Map();
  state.activeDetailMetrics = new Set(["integer_adm2"]);
  resolveSeries();
  await clickPromise;

  assert.deepEqual([...state.activeDetailMetrics], ["integer_adm2"]);
  assert.equal(renderControlsCount, 0);
  assert.equal(renderChartsCount, 0);
});

test("detail chart rerenders in merge mode so dataZoom slider state is preserved", () => {
  const createdCharts = [];
  const { renderLineCharts, state } = loadAppContext({
    echarts: {
      init() {
        const chart = {
          offCalls: [],
          onCalls: [],
          setOptionCalls: [],
          clearCalls: 0,
          clear() {
            this.clearCalls += 1;
          },
          off(eventName) {
            this.offCalls.push(eventName);
          },
          on(eventName, handler) {
            this.onCalls.push([eventName, handler]);
          },
          resize() {},
          setOption(...args) {
            this.setOptionCalls.push(args);
          },
          getOption() {
            return {};
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
  });
  const [overviewChart, detailChart] = createdCharts;
  state.comparison = {
    common_range: { start: 0, end: 100 },
    summary: [{ id: "a", name: "A" }],
    series: { a: { points: [[0, 95], [100, 90]] } },
  };
  state.metricsByFile = new Map([["a", ["vmaf", "integer_motion"]]]);
  state.activeDetailMetrics = new Set(["integer_motion"]);
  state.extraSeries = new Map([
    ["integer_motion", { a: { integer_motion: { points: [[0, 1], [100, 2]] } } }],
  ]);

  renderLineCharts();

  assert.equal(overviewChart.setOptionCalls.at(-1)[1], true);
  assert.equal(detailChart.setOptionCalls.at(-1)[1], undefined);
  assert.deepEqual(detailChart.onCalls.at(-1)[0], "datazoom");
  assert.equal(detailChart.setOptionCalls.at(-1)[0].dataZoom[1].showDataShadow, false);
});

test("range detail series requests merge into existing full-range cache", async () => {
  const { requestExtraSeriesForRange, state } = loadAppContext({
    fetch: async () => ({
      ok: true,
      async text() {
        return JSON.stringify({
          series: {
            a: {
              integer_motion: {
                points: [[50, 5], [51, 6]],
              },
            },
          },
        });
      },
    }),
  });
  state.comparisonRequestId = 1;
  state.comparison = {
    common_range: { start: 0, end: 100 },
    summary: [{ id: "a", name: "A" }],
  };
  state.selected = new Set(["a"]);
  state.extraSeries = new Map([
    [
      "integer_motion",
      {
        a: {
          integer_motion: {
            points: [[0, 1], [100, 2]],
          },
        },
      },
    ],
  ]);

  await requestExtraSeriesForRange(["integer_motion"], { start: 50, end: 51 });

  assert.deepEqual(toHostValue(state.extraSeries.get("integer_motion")), {
    a: {
      integer_motion: {
        points: [[0, 1], [50, 5], [51, 6], [100, 2]],
      },
    },
  });
});

test("detail chart clears removed metric series while using merge mode", () => {
  const createdCharts = [];
  const { renderLineCharts, state } = loadAppContext({
    echarts: {
      init() {
        const chart = {
          setOptionCalls: [],
          clear() {},
          off() {},
          on() {},
          resize() {},
          setOption(...args) {
            this.setOptionCalls.push(args);
          },
          getOption() {
            return {};
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
  });
  const detailChart = createdCharts[1];
  state.comparison = {
    common_range: { start: 0, end: 100 },
    summary: [{ id: "a", name: "A" }],
    series: { a: { points: [[0, 95], [100, 90]] } },
  };
  state.metricsByFile = new Map([["a", ["vmaf", "integer_adm2", "integer_motion"]]]);
  state.extraSeries = new Map([
    ["integer_adm2", { a: { integer_adm2: { points: [[0, 0.9], [100, 0.8]] } } }],
    ["integer_motion", { a: { integer_motion: { points: [[0, 1], [100, 2]] } } }],
  ]);

  state.activeDetailMetrics = new Set(["integer_adm2", "integer_motion"]);
  renderLineCharts();
  state.activeDetailMetrics = new Set(["integer_adm2"]);
  renderLineCharts();

  const series = detailChart.setOptionCalls.at(-1)[0].series;
  assert.equal(detailChart.setOptionCalls.at(-1)[1], undefined);
  assert.deepEqual(
    toHostValue(series.map((item) => [item.id, item.data])),
    [
      ["a:integer_adm2", [[0, 0.9], [100, 0.8]]],
      ["a:integer_motion", []],
    ],
  );
});

test("detail chart hides stale empty-state title when metrics become active", () => {
  const createdCharts = [];
  const { renderLineCharts, state } = loadAppContext({
    echarts: {
      init() {
        const chart = {
          setOptionCalls: [],
          clear() {},
          off() {},
          on() {},
          resize() {},
          setOption(...args) {
            this.setOptionCalls.push(args);
          },
          getOption() {
            return {};
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
  });
  const detailChart = createdCharts[1];
  state.comparison = {
    common_range: { start: 0, end: 100 },
    summary: [{ id: "a", name: "A" }],
    series: { a: { points: [[0, 95], [100, 90]] } },
  };
  state.metricsByFile = new Map([["a", ["vmaf", "integer_motion"]]]);
  state.extraSeries = new Map([
    ["integer_motion", { a: { integer_motion: { points: [[0, 1], [100, 2]] } } }],
  ]);

  state.activeDetailMetrics = new Set();
  renderLineCharts();
  state.activeDetailMetrics = new Set(["integer_motion"]);
  renderLineCharts();

  assert.equal(detailChart.setOptionCalls.at(-1)[1], undefined);
  assert.deepEqual(toHostValue(detailChart.setOptionCalls.at(-1)[0].title), { show: false, text: "" });
});

test("detail range loading waits 400ms and only requests the final zoom range", async () => {
  const createdCharts = [];
  const pendingTimers = new Map();
  const fetchBodies = [];
  let nextTimerId = 0;
  let currentZoom = { start: 10, end: 20 };
  let renderChartsCount = 0;
  const { renderLineCharts, state } = loadAppContext({
    clearTimeout(id) {
      pendingTimers.delete(id);
    },
    echarts: {
      init() {
        const chart = {
          onCalls: [],
          setOptionCalls: [],
          clear() {},
          off() {},
          on(eventName, handler) {
            this.onCalls.push([eventName, handler]);
          },
          resize() {},
          setOption(...args) {
            this.setOptionCalls.push(args);
          },
          getOption() {
            return { dataZoom: [currentZoom] };
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
    fetch: async (_path, init) => {
      fetchBodies.push(JSON.parse(init.body));
      return {
        ok: true,
        async text() {
          return JSON.stringify({ series: {} });
        },
      };
    },
    renderCharts() {
      renderChartsCount += 1;
    },
    setTimeout(callback, delay) {
      const id = ++nextTimerId;
      pendingTimers.set(id, { callback, delay });
      return id;
    },
  });
  const detailChart = createdCharts[1];
  state.comparison = {
    common_range: { start: 0, end: 1000 },
    summary: [{ id: "a", name: "A" }],
    series: { a: { points: [[0, 95], [1000, 90]] } },
  };
  state.selected = new Set(["a"]);
  state.metricsByFile = new Map([["a", ["vmaf", "integer_motion"]]]);
  state.activeDetailMetrics = new Set(["integer_motion"]);
  state.extraSeries = new Map([
    ["integer_motion", { a: { integer_motion: { points: [[0, 1], [1000, 2]] } } }],
  ]);
  renderLineCharts();

  const dataZoomHandler = detailChart.onCalls.at(-1)[1];
  const initialDetailSetOptionCount = detailChart.setOptionCalls.length;
  await dataZoomHandler();
  currentZoom = { start: 30, end: 40 };
  await dataZoomHandler();

  assert.equal(fetchBodies.length, 0);
  assert.equal(pendingTimers.size, 1);
  const timer = [...pendingTimers.values()][0];
  assert.equal(timer.delay, 400);

  await timer.callback();

  assert.deepEqual(fetchBodies, [
    {
      file_ids: ["a"],
      metrics: ["integer_motion"],
      start: 300,
      end: 400,
      max_points: 5000,
    },
  ]);
  assert.equal(renderChartsCount, 0);
  assert.equal(detailChart.setOptionCalls.length, initialDetailSetOptionCount + 1);
});

test("primary range loading waits 400ms, fetches the final zoom range, and refreshes only main series", async () => {
  const createdCharts = [];
  const pendingTimers = new Map();
  const fetchBodies = [];
  let nextTimerId = 0;
  let currentLineZoom = { start: 10, end: 20 };

  const { renderLineCharts, state } = loadAppContext({
    clearTimeout(id) {
      pendingTimers.delete(id);
    },
    echarts: {
      init() {
        const chart = {
          onCalls: [],
          offCalls: [],
          setOptionCalls: [],
          clear() {},
          off(eventName) {
            this.offCalls.push(eventName);
          },
          on(eventName, handler) {
            this.onCalls.push([eventName, handler]);
          },
          resize() {},
          setOption(...args) {
            this.setOptionCalls.push(args);
          },
          getOption() {
            return { dataZoom: [currentLineZoom] };
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
    fetch: async (_path, init) => {
      fetchBodies.push(JSON.parse(init.body));
      return {
        ok: true,
        async text() {
          return JSON.stringify({
            series: {
              a: {
                vmaf: {
                  points: [
                    [300, 91],
                    [301, 92],
                  ],
                },
              },
            },
          });
        },
      };
    },
    setTimeout(callback, delay) {
      const id = ++nextTimerId;
      pendingTimers.set(id, { callback, delay });
      return id;
    },
  });

  const mainChart = createdCharts[0];
  state.comparisonRequestId = 1;
  state.comparison = {
    common_range: { start: 0, end: 1000 },
    summary: [{ id: "a", name: "A" }],
    series: {
      a: {
        metric: "vmaf",
        points: [
          [0, 95],
          [1000, 90],
        ],
      },
    },
  };
  state.selected = new Set(["a"]);
  state.metricsByFile = new Map([["a", ["vmaf", "integer_motion"]]]);
  state.activeDetailMetrics = new Set();

  renderLineCharts();

  assert.deepEqual(mainChart.offCalls, ["datazoom"]);
  assert.equal(mainChart.setOptionCalls.at(-1)[1], true);

  const dataZoomHandler = mainChart.onCalls.at(-1)[1];
  await dataZoomHandler();
  currentLineZoom = { start: 30, end: 40 };
  await dataZoomHandler();

  assert.equal(fetchBodies.length, 0);
  assert.equal(pendingTimers.size, 1);
  const timer = [...pendingTimers.values()][0];
  assert.equal(timer.delay, 400);

  await timer.callback();

  assert.deepEqual(fetchBodies, [
    {
      file_ids: ["a"],
      metrics: ["vmaf"],
      start: 300,
      end: 400,
      max_points: 5000,
    },
  ]);
  assert.equal(mainChart.setOptionCalls.at(-1)[1], undefined);
  assert.deepEqual(toHostValue(mainChart.setOptionCalls.at(-1)[0].series[0].data), [
    [0, 95],
    [300, 91],
    [301, 92],
    [1000, 90],
  ]);
  assert.deepEqual(toHostValue(mainChart.setOptionCalls.at(-1)[0].series[0].markLine.data), [
    { name: "95", yAxis: 95, label: { formatter: "95", color: "#667064" }, lineStyle: { color: "#aeb5aa", type: "dashed", width: 1 } },
    { name: "90", yAxis: 90, label: { formatter: "90", color: "#667064" }, lineStyle: { color: "#aeb5aa", type: "dashed", width: 1 } },
    { name: "80", yAxis: 80, label: { formatter: "80", color: "#667064" }, lineStyle: { color: "#aeb5aa", type: "dashed", width: 1 } },
    { name: "60", yAxis: 60, label: { formatter: "60", color: "#667064" }, lineStyle: { color: "#aeb5aa", type: "dashed", width: 1 } },
  ]);
});

test("primary range loading skips zoom windows wider than 5000 frames", async () => {
  const createdCharts = [];
  const pendingTimers = new Map();
  const fetchBodies = [];
  let nextTimerId = 0;

  const { renderLineCharts, state } = loadAppContext({
    clearTimeout(id) {
      pendingTimers.delete(id);
    },
    echarts: {
      init() {
        const chart = {
          onCalls: [],
          clear() {},
          off() {},
          on(eventName, handler) {
            this.onCalls.push([eventName, handler]);
          },
          resize() {},
          setOption() {},
          getOption() {
            return { dataZoom: [{ start: 0, end: 100 }] };
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
    fetch: async (_path, init) => {
      fetchBodies.push(JSON.parse(init.body));
      return {
        ok: true,
        async text() {
          return JSON.stringify({ series: {} });
        },
      };
    },
    setTimeout(callback, delay) {
      const id = ++nextTimerId;
      pendingTimers.set(id, { callback, delay });
      return id;
    },
  });

  const mainChart = createdCharts[0];
  state.comparison = {
    common_range: { start: 0, end: 10000 },
    summary: [{ id: "a", name: "A" }],
    series: { a: { metric: "vmaf", points: [[0, 95], [10000, 90]] } },
  };
  state.selected = new Set(["a"]);

  renderLineCharts();
  await mainChart.onCalls.at(-1)[1]();

  assert.equal(pendingTimers.size, 0);
  assert.deepEqual(fetchBodies, []);
});

test("primary range loading groups visible files by their selected primary metric", async () => {
  const createdCharts = [];
  const pendingTimers = new Map();
  const fetchBodies = [];
  let nextTimerId = 0;

  const { renderLineCharts, state } = loadAppContext({
    clearTimeout(id) {
      pendingTimers.delete(id);
    },
    echarts: {
      init() {
        const chart = {
          onCalls: [],
          setOptionCalls: [],
          clear() {},
          off() {},
          on(eventName, handler) {
            this.onCalls.push([eventName, handler]);
          },
          resize() {},
          setOption(...args) {
            this.setOptionCalls.push(args);
          },
          getOption() {
            return { dataZoom: [{ start: 25, end: 35 }] };
          },
        };
        createdCharts.push(chart);
        return chart;
      },
    },
    fetch: async (_path, init) => {
      const requestBody = JSON.parse(init.body);
      fetchBodies.push(requestBody);
      return {
        ok: true,
        async text() {
          const metric = requestBody.metrics[0];
          return JSON.stringify({
            series: Object.fromEntries(
              requestBody.file_ids.map((fileId) => [
                fileId,
                {
                  [metric]: {
                    points: [[250, fileId === "a" ? 91 : 88]],
                  },
                },
              ]),
            ),
          });
        },
      };
    },
    setTimeout(callback, delay) {
      const id = ++nextTimerId;
      pendingTimers.set(id, { callback, delay });
      return id;
    },
  });

  const mainChart = createdCharts[0];
  state.comparisonRequestId = 7;
  state.comparison = {
    common_range: { start: 0, end: 1000 },
    summary: [
      { id: "a", name: "A" },
      { id: "b", name: "B" },
    ],
    series: {
      a: { metric: "vmaf", points: [[0, 95], [1000, 90]] },
      b: { metric: "vmaf_hd", points: [[0, 90], [1000, 85]] },
    },
  };
  state.selected = new Set(["a", "b"]);

  renderLineCharts();
  await mainChart.onCalls.at(-1)[1]();
  await [...pendingTimers.values()][0].callback();

  assert.deepEqual(fetchBodies, [
    { file_ids: ["a"], metrics: ["vmaf"], start: 250, end: 350, max_points: 5000 },
    { file_ids: ["b"], metrics: ["vmaf_hd"], start: 250, end: 350, max_points: 5000 },
  ]);
  assert.deepEqual(toHostValue(mainChart.setOptionCalls.at(-1)[0].series.map((series) => series.data)), [
    [[0, 95], [250, 91], [1000, 90]],
    [[0, 90], [250, 88], [1000, 85]],
  ]);
});

test("activating a detail metric while zoomed loads that metric for the current range", async () => {
  const pendingTimers = new Map();
  const fetchBodies = [];
  let nextTimerId = 0;
  let renderChartsCount = 0;
  const { elements, renderControls, state } = loadAppContext({
    clearTimeout(id) {
      pendingTimers.delete(id);
    },
    echarts: {
      init() {
        return {
          clear() {},
          off() {},
          on() {},
          resize() {},
          setOption() {},
          getOption() {
            return { dataZoom: [{ start: 30, end: 40 }] };
          },
        };
      },
    },
    fetch: async (_path, init) => {
      fetchBodies.push(JSON.parse(init.body));
      return {
        ok: true,
        async text() {
          const body = JSON.parse(init.body);
          return JSON.stringify({
            series: {
              a: Object.fromEntries(body.metrics.map((metric) => [metric, { points: [] }])),
            },
          });
        },
      };
    },
    renderCharts() {
      renderChartsCount += 1;
    },
    setTimeout(callback, delay) {
      const id = ++nextTimerId;
      pendingTimers.set(id, { callback, delay });
      return id;
    },
  });
  state.comparisonRequestId = 1;
  state.comparison = {
    common_range: { start: 0, end: 1000 },
    summary: [{ id: "a", name: "A" }],
    series: { a: { points: [[0, 95], [1000, 90]] } },
  };
  state.selected = new Set(["a"]);
  state.metricsByFile = new Map([["a", ["vmaf", "integer_motion", "integer_adm2"]]]);
  state.activeDetailMetrics = new Set(["integer_motion"]);
  state.extraSeries = new Map([
    ["integer_motion", { a: { integer_motion: { points: [[0, 1], [1000, 2]] } } }],
  ]);
  renderControls();

  const admToggle = elements.metricToggles.children.find((chip) => chip.title.startsWith("integer_adm2"));
  assert.ok(admToggle);
  await admToggle.listeners.click();

  assert.deepEqual(fetchBodies, [
    {
      file_ids: ["a"],
      metrics: ["integer_adm2"],
      start: 0,
      end: 1000,
      max_points: 2000,
    },
  ]);
  assert.equal(renderChartsCount, 1);
  assert.equal(pendingTimers.size, 1);
  const timer = [...pendingTimers.values()][0];
  assert.equal(timer.delay, 400);

  await timer.callback();

  assert.deepEqual(fetchBodies.at(-1), {
    file_ids: ["a"],
    metrics: ["integer_adm2"],
    start: 300,
    end: 400,
    max_points: 5000,
  });
});
