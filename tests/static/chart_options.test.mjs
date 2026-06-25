import assert from "node:assert/strict";
import test from "node:test";

import { loadAppContext as loadHarnessAppContext } from "./browser_harness.mjs";

const APP_EXPORTS = [
  "baseChartOptions",
  "elements",
  "formatFrameValue",
  "initializeDefaultDetailMetrics",
  "normalizeFpsValue",
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
