(function (root, factory) {
  const metadata = factory();

  if (typeof module === "object" && module.exports) {
    module.exports = metadata;
  }

  root.VmafMetricMetadata = metadata;
})(typeof globalThis !== "undefined" ? globalThis : window, function () {
  "use strict";

  const DEFAULT_DETAIL_METRICS = {
    adm: ["integer_adm2", "integer_adm_scale0", "integer_adm_scale1", "integer_adm_scale2", "integer_adm_scale3"],
    vif: ["integer_vif_scale0", "integer_vif_scale1", "integer_vif_scale2", "integer_vif_scale3"],
    motion: ["integer_motion2", "integer_motion"],
  };

  const NORMALIZED_AXIS = {
    axisGroup: "normalized",
    yAxisIndex: 0,
    axisTag: "0-1",
    axisName: "ADM / VIF / AIM",
    unit: "",
  };

  const RAW_AXES = {
    motion: {
      axisGroup: "raw",
      yAxisIndex: 1,
      axisTag: "motion",
      axisName: "Motion",
      unit: "",
    },
    psnr: {
      axisGroup: "raw",
      yAxisIndex: 1,
      axisTag: "dB",
      axisName: "PSNR (dB)",
      unit: "dB",
    },
  };

  function metricMeta(metric) {
    if (typeof metric !== "string") {
      return null;
    }

    const family = normalizedFamily(metric);
    if (family) {
      return { family, ...NORMALIZED_AXIS };
    }

    if (/^integer_motion2?$/.test(metric)) {
      return { family: "motion", ...RAW_AXES.motion };
    }

    if (/^psnr(?:_[a-z0-9]+)?$/.test(metric)) {
      return { family: "psnr", ...RAW_AXES.psnr };
    }

    return null;
  }

  function normalizedFamily(metric) {
    if (/^integer_adm(?:2|_scale[0-9])(?:_egl_[0-9]+)?$/.test(metric)) {
      return "adm";
    }
    if (/^integer_vif_scale[0-9](?:_egl_[0-9]+)?$/.test(metric)) {
      return "vif";
    }
    if (/^integer_aim(?:_egl_[0-9]+)?$/.test(metric)) {
      return "aim";
    }
    return null;
  }

  function detailMetrics(metrics) {
    return metrics.filter((metric) => metricMeta(metric) !== null);
  }

  function defaultDetailMetrics(metrics) {
    const available = new Set(detailMetrics(metrics));
    const defaults = [];

    for (const family of ["adm", "vif", "motion"]) {
      const metric = DEFAULT_DETAIL_METRICS[family].find((candidate) => available.has(candidate));
      if (metric) {
        defaults.push(metric);
      }
    }

    return defaults;
  }

  function rawFamilyForMetrics(metrics) {
    for (const metric of metrics) {
      const meta = metricMeta(metric);
      if (meta && meta.axisGroup === "raw") {
        return meta.family;
      }
    }
    return null;
  }

  function toggleDetailMetric(activeMetrics, metric) {
    if (activeMetrics.includes(metric)) {
      return activeMetrics.filter((activeMetric) => activeMetric !== metric);
    }

    const meta = metricMeta(metric);
    if (!meta) {
      return activeMetrics.slice();
    }

    const nextMetrics = activeMetrics.filter((activeMetric) => {
      const activeMeta = metricMeta(activeMetric);
      return !(meta.axisGroup === "raw" && activeMeta && activeMeta.axisGroup === "raw" && activeMeta.family !== meta.family);
    });

    nextMetrics.push(metric);
    return nextMetrics;
  }

  function normalizeDetailSelection(activeMetrics, sharedMetrics) {
    const shared = new Set(detailMetrics(sharedMetrics));
    const normalized = [];
    let rawFamily = null;

    for (const metric of activeMetrics) {
      const meta = metricMeta(metric);
      if (!meta || !shared.has(metric)) {
        continue;
      }

      if (meta.axisGroup === "raw") {
        if (rawFamily && rawFamily !== meta.family) {
          continue;
        }
        rawFamily = meta.family;
      }

      if (!normalized.includes(metric)) {
        normalized.push(metric);
      }
    }

    return normalized;
  }

  return {
    DEFAULT_DETAIL_METRICS,
    metricMeta,
    detailMetrics,
    defaultDetailMetrics,
    rawFamilyForMetrics,
    toggleDetailMetric,
    normalizeDetailSelection,
  };
});
