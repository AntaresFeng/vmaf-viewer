import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadBaseChartOptions() {
  const source = readFileSync("src/vmaf_viewer/static/app.js", "utf8");
  const initIndex = source.indexOf("\nsetupEvents();");
  assert.notEqual(initIndex, -1);

  const context = {
    document: {
      body: {},
      getElementById() {
        return {};
      },
    },
    echarts: {
      init() {
        return {};
      },
    },
    getComputedStyle() {
      return { fontFamily: "sans-serif" };
    },
  };
  vm.createContext(context);
  vm.runInContext(`${source.slice(0, initIndex)}\nglobalThis.__baseChartOptions = baseChartOptions;`, context);
  return context.__baseChartOptions();
}

test("formats frame axis pointer label as grouped integer frames", () => {
  const options = loadBaseChartOptions();
  const formatter = options.tooltip.axisPointer?.label?.formatter;

  assert.equal(options.xAxis.axisLabel?.formatter, undefined);
  assert.equal(typeof formatter, "function");
  assert.equal(formatter({ value: 256 }), "256");
  assert.equal(formatter({ value: "256.00" }), "256");
  assert.equal(formatter({ value: 1234567 }), "1,234,567");
  assert.equal(formatter({ value: "1234567.00" }), "1,234,567");
});
