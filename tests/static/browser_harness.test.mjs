import assert from "node:assert/strict";
import test from "node:test";

import { loadAppContext, loadBrowserScripts } from "./browser_harness.mjs";

test("loads static helpers through browser globals", () => {
  const { context } = loadBrowserScripts([
    "src/vmaf_viewer/static/message_state.js",
    "src/vmaf_viewer/static/metric_metadata.js",
  ]);

  assert.equal(context.module, undefined);
  assert.equal(typeof context.VmafMessageState.pickMessageState, "function");
  assert.equal(typeof context.VmafMetricMetadata.metricMeta, "function");
});

test("loads app context with canonical frontend dependencies", () => {
  const { exports } = loadAppContext(["state", "baseChartOptions"]);

  assert.equal(exports.state.fps, 0);
  assert.equal(typeof exports.baseChartOptions, "function");
});
