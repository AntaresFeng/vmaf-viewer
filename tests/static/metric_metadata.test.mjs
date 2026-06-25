import assert from "node:assert/strict";
import test from "node:test";

import { loadBrowserScripts, toHostValue } from "./browser_harness.mjs";

function loadMetadata() {
  const { context } = loadBrowserScripts(["src/vmaf_viewer/static/metric_metadata.js"]);
  return context.VmafMetricMetadata;
}

test("classifies normalized metrics and EGL variants", () => {
  const metadata = loadMetadata();

  assert.deepEqual(toHostValue(metadata.metricMeta("integer_adm2")), {
    family: "adm",
    axisGroup: "normalized",
    yAxisIndex: 0,
    axisTag: "0-1",
    axisName: "ADM / VIF / AIM",
    unit: "",
  });
  assert.deepEqual(toHostValue(metadata.metricMeta("integer_adm3")), {
    family: "adm",
    axisGroup: "normalized",
    yAxisIndex: 0,
    axisTag: "0-1",
    axisName: "ADM / VIF / AIM",
    unit: "",
  });
  assert.deepEqual(toHostValue(metadata.metricMeta("integer_adm3_egl_1")), {
    family: "adm",
    axisGroup: "normalized",
    yAxisIndex: 0,
    axisTag: "0-1",
    axisName: "ADM / VIF / AIM",
    unit: "",
  });
  assert.deepEqual(toHostValue(metadata.metricMeta("integer_vif_scale2_egl_1")), {
    family: "vif",
    axisGroup: "normalized",
    yAxisIndex: 0,
    axisTag: "0-1",
    axisName: "ADM / VIF / AIM",
    unit: "",
  });
  assert.deepEqual(toHostValue(metadata.metricMeta("integer_aim_egl_1")), {
    family: "aim",
    axisGroup: "normalized",
    yAxisIndex: 0,
    axisTag: "0-1",
    axisName: "ADM / VIF / AIM",
    unit: "",
  });
});

test("classifies raw metrics and rejects model scores or unknown metrics", () => {
  const metadata = loadMetadata();

  assert.deepEqual(toHostValue(metadata.metricMeta("integer_motion2")), {
    family: "motion",
    axisGroup: "raw",
    yAxisIndex: 1,
    axisTag: "motion",
    axisName: "Motion",
    unit: "",
  });
  assert.deepEqual(toHostValue(metadata.metricMeta("integer_motion3")), {
    family: "motion",
    axisGroup: "raw",
    yAxisIndex: 1,
    axisTag: "motion",
    axisName: "Motion",
    unit: "",
  });
  assert.deepEqual(toHostValue(metadata.metricMeta("psnr_y")), {
    family: "psnr",
    axisGroup: "raw",
    yAxisIndex: 1,
    axisTag: "dB",
    axisName: "PSNR (dB)",
    unit: "dB",
  });
  assert.equal(metadata.metricMeta("vmaf"), null);
  assert.equal(metadata.metricMeta("vmaf_hd"), null);
  assert.equal(metadata.metricMeta("psnr"), null);
  assert.equal(metadata.metricMeta("unknown_metric"), null);
});

test("filters recognized detail metrics only", () => {
  const metadata = loadMetadata();

  assert.deepEqual(
    toHostValue(
      metadata.detailMetrics(["vmaf", "integer_adm2", "integer_motion2", "integer_motion3", "psnr_y", "unknown_metric"]),
    ),
    ["integer_adm2", "integer_motion2", "integer_motion3", "psnr_y"],
  );
});

test("selects one default representative per ADM, VIF, and Motion family", () => {
  const metadata = loadMetadata();

  assert.deepEqual(
    toHostValue(
      metadata.defaultDetailMetrics([
        "vmaf",
        "integer_adm_scale1",
        "integer_adm_scale3",
        "integer_vif_scale2",
        "integer_vif_scale0",
        "integer_motion",
        "psnr_y",
      ]),
    ),
    ["integer_adm_scale1", "integer_vif_scale0", "integer_motion"],
  );
});

test("prefers integer_adm2 and integer_motion2 when available", () => {
  const metadata = loadMetadata();

  assert.deepEqual(
    toHostValue(
      metadata.defaultDetailMetrics([
        "integer_adm_scale0",
        "integer_adm2",
        "integer_vif_scale3",
        "integer_motion",
        "integer_motion2",
      ]),
    ),
    ["integer_adm2", "integer_vif_scale3", "integer_motion2"],
  );
});

test("toggleDetailMetric enforces Motion and PSNR mutual exclusion", () => {
  const metadata = loadMetadata();

  assert.deepEqual(
    toHostValue(metadata.toggleDetailMetric(["integer_adm2", "integer_motion2"], "psnr_y")),
    ["integer_adm2", "psnr_y"],
  );
  assert.deepEqual(
    toHostValue(metadata.toggleDetailMetric(["integer_adm2", "psnr_y", "psnr_cb"], "integer_motion")),
    ["integer_adm2", "integer_motion"],
  );
  assert.deepEqual(
    toHostValue(metadata.toggleDetailMetric(["integer_adm2", "psnr_y"], "integer_motion3")),
    ["integer_adm2", "integer_motion3"],
  );
  assert.deepEqual(
    toHostValue(metadata.toggleDetailMetric(["integer_adm2", "integer_vif_scale0"], "integer_vif_scale0")),
    ["integer_adm2"],
  );
});

test("normalizeDetailSelection removes unsupported metrics and keeps one raw family", () => {
  const metadata = loadMetadata();

  assert.deepEqual(
    toHostValue(
      metadata.normalizeDetailSelection(["vmaf", "integer_adm2", "integer_motion2", "psnr_y", "missing"], [
        "integer_adm2",
        "integer_motion2",
        "psnr_y",
      ]),
    ),
    ["integer_adm2", "integer_motion2"],
  );
});
