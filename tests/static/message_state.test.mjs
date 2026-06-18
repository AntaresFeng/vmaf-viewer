import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function loadHelpers() {
  const source = readFileSync("src/vmaf_viewer/static/message_state.js", "utf8");
  const context = {};
  vm.createContext(context);
  vm.runInContext(source, context);
  return context.VmafMessageState;
}

function assertMessage(actual, expectedText, expectedType) {
  assert.equal(actual.text, expectedText);
  assert.equal(actual.type, expectedType);
}

test("picks the highest priority message", () => {
  const helpers = loadHelpers();

  assertMessage(
    helpers.pickMessageState({
      error: "Unable to compare selected files.",
      warnings: ["Common frame range was shortened."],
      status: "Loading comparison data...",
      success: "Loaded 2 files, 4 common frames.",
    }),
    "Unable to compare selected files.",
    "error",
  );

  assertMessage(
    helpers.pickMessageState({
      warnings: ["Common frame range was shortened.", "Ignored another warning."],
      status: "Loading comparison data...",
      success: "Loaded 2 files, 4 common frames.",
    }),
    "Common frame range was shortened.",
    "warning",
  );

  assertMessage(
    helpers.pickMessageState({
      status: "Loading comparison data...",
      success: "Loaded 2 files, 4 common frames.",
    }),
    "Loading comparison data...",
    "status",
  );

  assertMessage(
    helpers.pickMessageState({
      success: "Loaded 2 files, 4 common frames.",
    }),
    "Loaded 2 files, 4 common frames.",
    "status",
  );
});

test("falls back to the default selection prompt", () => {
  const helpers = loadHelpers();

  assertMessage(helpers.pickMessageState({}), "Select 1-6 VMAF JSON files to compare.", "status");
  assertMessage(helpers.pickMessageState(), "Select 1-6 VMAF JSON files to compare.", "status");
});

test("ignores caller-provided constructors", () => {
  const helpers = loadHelpers();

  assertMessage(
    helpers.pickMessageState({
      constructor: function Object() {
        throw new Error("caller constructor should not be used");
      },
      status: "Loading comparison data...",
    }),
    "Loading comparison data...",
    "status",
  );
});

test("formats loaded comparison summaries in English", () => {
  const helpers = loadHelpers();

  assert.equal(
    helpers.formatLoadedMessage([{ common_frames: 4 }, { common_frames: 4 }]),
    "Loaded 2 files, 4 common frames.",
  );
  assert.equal(helpers.formatLoadedMessage([{ common_frames: 1 }]), "Loaded 1 file, 1 common frame.");
  assert.equal(helpers.formatLoadedMessage([]), "Loaded 0 files, 0 common frames.");
  assert.equal(helpers.formatLoadedMessage([{ common_frames: null }]), "Loaded 1 file, 0 common frames.");
});
