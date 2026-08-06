import assert from "node:assert/strict";
import test from "node:test";

import { loadBrowserScripts } from "./browser_harness.mjs";

function loadMessageState() {
  const { context } = loadBrowserScripts(["src/vmaf_viewer/static/message_state.js"]);
  return context.VmafMessageState;
}

function assertMessage(actual, expectedText, expectedType) {
  assert.equal(actual.text, expectedText);
  assert.equal(actual.type, expectedType);
}

test("picks the highest priority message", () => {
  const helpers = loadMessageState();

  assertMessage(
    helpers.pickMessageState({
      error: "Unable to compare selected files.",
      warnings: ["One file could not be parsed."],
      status: "Loading comparison data...",
      success: "Loaded 2 files.",
    }),
    "Unable to compare selected files.",
    "error",
  );

  assertMessage(
    helpers.pickMessageState({
      warnings: ["One file could not be parsed.", "Ignored another warning."],
      status: "Loading comparison data...",
      success: "Loaded 2 files.",
    }),
    "One file could not be parsed.",
    "warning",
  );

  assertMessage(
    helpers.pickMessageState({
      status: "Loading comparison data...",
      success: "Loaded 2 files.",
    }),
    "Loading comparison data...",
    "status",
  );

  assertMessage(
    helpers.pickMessageState({
      success: "Loaded 2 files.",
    }),
    "Loaded 2 files.",
    "status",
  );
});

test("falls back to the default selection prompt", () => {
  const helpers = loadMessageState();

  assertMessage(helpers.pickMessageState({}), "Select VMAF log files to compare.", "status");
  assertMessage(helpers.pickMessageState(), "Select VMAF log files to compare.", "status");
});

test("ignores caller-provided constructors", () => {
  const helpers = loadMessageState();

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
  const helpers = loadMessageState();

  assert.equal(helpers.formatLoadedMessage([{}, {}]), "Loaded 2 files.");
  assert.equal(helpers.formatLoadedMessage([{}]), "Loaded 1 file.");
  assert.equal(helpers.formatLoadedMessage([]), "Loaded 0 files.");
});
