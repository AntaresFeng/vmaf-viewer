# Persistent Messages Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the viewer `#messages` area permanently visible, render exactly one prioritized message, and show the loaded frame count only as a success fallback.

**Architecture:** Extract message priority and success-text formatting into a tiny browser-global helper so it can be tested with Node without booting the full ECharts app. `app.js` will call one DOM renderer that always creates one `.message` element. CSS will give the message region a stable one-line minimum height so loading-to-success transitions do not pull the summary or charts upward.

**Tech Stack:** Plain browser JavaScript, Node built-in `node:test` for pure helper tests, static HTML/CSS, ECharts viewer runtime, pytest for existing backend coverage.

---

## Source Spec

- `docs/superpowers/specs/2026-06-16-messages-status-design.md`

## File Structure

- Create: `src/vmaf_viewer/static/message_state.js`
  - Pure helper for selecting the one message to display.
  - Exposes `window.VmafMessageState` / `globalThis.VmafMessageState`.
  - No DOM, network, or ECharts dependency.
- Create: `tests/static/message_state.test.mjs`
  - Node unit tests for priority order, first-warning behavior, fallback behavior, and loaded-frame wording.
- Modify: `src/vmaf_viewer/static/index.html`
  - Load `message_state.js` after ECharts and before `app.js`.
- Modify: `src/vmaf_viewer/static/app.js`
  - Replace array-based `renderMessages` with a single-message renderer.
  - Update all current `renderMessages(...)` call sites to pass error, warning, status, or success fallback state.
- Modify: `src/vmaf_viewer/static/styles.css`
  - Add stable sizing to `.messages` and `.message`.

---

### Task 1: Add Pure Message-State Helper

**Files:**
- Create: `src/vmaf_viewer/static/message_state.js`
- Create: `tests/static/message_state.test.mjs`

- [ ] **Step 1: Write the failing Node unit tests**

Create `tests/static/message_state.test.mjs`:

```javascript
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

test("picks the highest priority message", () => {
  const helpers = loadHelpers();

  assert.deepEqual(
    helpers.pickMessageState({
      error: "Unable to compare selected files.",
      warnings: ["Common frame range was shortened."],
      status: "Loading comparison data...",
      success: "Loaded 2 files, 4 common frames.",
    }),
    { text: "Unable to compare selected files.", type: "error" },
  );

  assert.deepEqual(
    helpers.pickMessageState({
      warnings: ["Common frame range was shortened.", "Ignored another warning."],
      status: "Loading comparison data...",
      success: "Loaded 2 files, 4 common frames.",
    }),
    { text: "Common frame range was shortened.", type: "warning" },
  );

  assert.deepEqual(
    helpers.pickMessageState({
      status: "Loading comparison data...",
      success: "Loaded 2 files, 4 common frames.",
    }),
    { text: "Loading comparison data...", type: "status" },
  );

  assert.deepEqual(
    helpers.pickMessageState({
      success: "Loaded 2 files, 4 common frames.",
    }),
    { text: "Loaded 2 files, 4 common frames.", type: "status" },
  );
});

test("falls back to the default selection prompt", () => {
  const helpers = loadHelpers();

  assert.deepEqual(helpers.pickMessageState({}), {
    text: "Select 1-6 VMAF JSON files to compare.",
    type: "status",
  });
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
node --test tests/static/message_state.test.mjs
```

Expected: FAIL with `ENOENT` for `src/vmaf_viewer/static/message_state.js`.

- [ ] **Step 3: Add the helper implementation**

Create `src/vmaf_viewer/static/message_state.js`:

```javascript
(function (global) {
  const DEFAULT_STATUS_MESSAGE = "Select 1-6 VMAF JSON files to compare.";

  function firstText(value) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (typeof item !== "string") {
        continue;
      }
      const text = item.trim();
      if (text) {
        return text;
      }
    }
    return "";
  }

  function commonFrameCount(summary) {
    if (!Array.isArray(summary) || !summary.length) {
      return 0;
    }
    const value = Number(summary[0].common_frames);
    return Number.isFinite(value) ? value : 0;
  }

  function formatLoadedMessage(summary) {
    const rows = Array.isArray(summary) ? summary : [];
    const fileCount = rows.length;
    const frameCount = commonFrameCount(rows);
    const fileWord = fileCount === 1 ? "file" : "files";
    const frameWord = frameCount === 1 ? "common frame" : "common frames";
    return `Loaded ${fileCount} ${fileWord}, ${frameCount} ${frameWord}.`;
  }

  function pickMessageState({ error = null, warnings = [], status = null, success = null } = {}) {
    const errorText = firstText(error);
    if (errorText) {
      return { text: errorText, type: "error" };
    }

    const warningText = firstText(warnings);
    if (warningText) {
      return { text: warningText, type: "warning" };
    }

    const statusText = firstText(status);
    if (statusText) {
      return { text: statusText, type: "status" };
    }

    const successText = firstText(success);
    if (successText) {
      return { text: successText, type: "status" };
    }

    return { text: DEFAULT_STATUS_MESSAGE, type: "status" };
  }

  global.VmafMessageState = {
    DEFAULT_STATUS_MESSAGE,
    formatLoadedMessage,
    pickMessageState,
  };
})(globalThis);
```

- [ ] **Step 4: Run the helper tests to verify they pass**

Run:

```bash
node --test tests/static/message_state.test.mjs
```

Expected: PASS, with all three subtests passing.

- [ ] **Step 5: Commit the pure helper and tests**

Run:

```bash
git add src/vmaf_viewer/static/message_state.js tests/static/message_state.test.mjs
git commit -m "test: add message status helper"
```

Expected: commit succeeds.

---

### Task 2: Wire Single-Message Rendering Into The Viewer

**Files:**
- Modify: `src/vmaf_viewer/static/index.html`
- Modify: `src/vmaf_viewer/static/app.js`

- [ ] **Step 1: Load the helper before `app.js`**

In `src/vmaf_viewer/static/index.html`, replace the final scripts with:

```html
    <script src="/static/vendor/echarts.min.js"></script>
    <script src="/static/message_state.js"></script>
    <script src="/static/app.js"></script>
```

- [ ] **Step 2: Replace `renderMessages` with a single-message renderer**

In `src/vmaf_viewer/static/app.js`, replace the existing `renderMessages` function with:

```javascript
function renderMessage(messageState = {}) {
  const selected = VmafMessageState.pickMessageState(messageState);
  elements.messages.innerHTML = "";

  const item = document.createElement("div");
  item.className = selected.type === "status" ? "message" : `message ${selected.type}`;
  item.textContent = selected.text;
  elements.messages.appendChild(item);
}
```

- [ ] **Step 3: Update file-scan status calls**

In `applyFilesResponse`, replace the current empty render behavior with:

```javascript
  if (!state.files.length) {
    renderMessage({ status: "No *_vmaf.json files found." });
  } else {
    renderMessage({ status: VmafMessageState.DEFAULT_STATUS_MESSAGE });
  }
```

In `changeScanDirectory`, replace:

```javascript
renderMessages(["Enter a scan directory."], "error");
```

with:

```javascript
renderMessage({ error: "Enter a scan directory." });
```

Replace:

```javascript
renderMessages([error.message || "Unable to scan that directory."], "error");
```

with:

```javascript
renderMessage({ error: error.message || "Unable to scan that directory." });
```

- [ ] **Step 4: Update comparison status calls**

In the no-selection branch of `requestComparison`, replace:

```javascript
renderMessages(state.files.length ? ["Select 1-6 VMAF JSON files to compare."] : ["No *_vmaf.json files found."]);
```

with:

```javascript
renderMessage({
  status: state.files.length ? VmafMessageState.DEFAULT_STATUS_MESSAGE : "No *_vmaf.json files found.",
});
```

Replace the loading call:

```javascript
renderMessages(["Loading comparison..."]);
```

with:

```javascript
renderMessage({ status: "Loading comparison data..." });
```

Replace the successful compare call:

```javascript
renderMessages(body.warnings || []);
```

with:

```javascript
renderMessage({
  warnings: body.warnings || [],
  success: VmafMessageState.formatLoadedMessage(body.summary),
});
```

Replace the compare error call:

```javascript
renderMessages([error.message || "Unable to compare selected files."], "error");
```

with:

```javascript
renderMessage({ error: error.message || "Unable to compare selected files." });
```

- [ ] **Step 5: Update remaining error call sites**

Replace each remaining `renderMessages(...)` call with the equivalent `renderMessage({ error: ... })` call:

```javascript
renderMessage({ error: error.message || `Unable to load ${metric}.` });
renderMessage({ error: error.message || "Unable to load zoomed metric series." });
renderMessage({ error: error.message || "Unable to refresh files." });
renderMessage({ error: error.message || "Unable to load files." });
```

- [ ] **Step 6: Verify no old plural renderer remains**

Run:

```bash
rg -n "renderMessages" src/vmaf_viewer/static/app.js
```

Expected: no matches.

- [ ] **Step 7: Run syntax and helper tests**

Run:

```bash
node --check src/vmaf_viewer/static/message_state.js
node --check src/vmaf_viewer/static/app.js
node --test tests/static/message_state.test.mjs
```

Expected: all commands exit 0.

- [ ] **Step 8: Commit the viewer wiring**

Run:

```bash
git add src/vmaf_viewer/static/index.html src/vmaf_viewer/static/app.js
git commit -m "feat: render persistent single status message"
```

Expected: commit succeeds.

---

### Task 3: Stabilize Layout And Run Full Verification

**Files:**
- Modify: `src/vmaf_viewer/static/styles.css`

- [ ] **Step 1: Add stable one-message sizing**

In `src/vmaf_viewer/static/styles.css`, replace the `.messages` and `.message` blocks with:

```css
.messages {
  display: grid;
  gap: 8px;
  min-height: 40px;
}

.message {
  box-sizing: border-box;
  min-height: 40px;
  display: flex;
  align-items: center;
  padding: 9px 11px;
  color: var(--muted);
  background: #ffffff;
  border: 1px solid var(--border);
  border-left: 4px solid var(--accent);
  border-radius: 6px;
  overflow-wrap: anywhere;
}
```

Keep the existing `.message.warning` and `.message.error` blocks unchanged.

- [ ] **Step 2: Run frontend checks**

Run:

```bash
node --check src/vmaf_viewer/static/message_state.js
node --check src/vmaf_viewer/static/app.js
node --test tests/static/message_state.test.mjs
```

Expected: all commands exit 0.

- [ ] **Step 3: Run repository tests**

Run:

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 4: Run a manual UI check**

Run:

```bash
uv run vmaf-viewer --data-dir tests/fixtures
```

Open the shown local URL and verify:

- Initial loaded file list shows exactly one `#messages .message`.
- With no selection, message text is `Select 1-6 VMAF JSON files to compare.`
- Selecting JSON files changes the message to `Loading comparison data...`.
- After loading without warnings, message text becomes `Loaded 2 files, 4 common frames.`
- If warnings are present in a comparison, only the first warning is shown and the loaded success message is not shown.
- Summary and charts do not move upward when loading finishes.

- [ ] **Step 5: Commit CSS and verification-ready state**

Run:

```bash
git add src/vmaf_viewer/static/styles.css
git commit -m "style: stabilize message status layout"
```

Expected: commit succeeds.

---

## Final Verification

Run these commands before marking implementation complete:

```bash
node --check src/vmaf_viewer/static/message_state.js
node --check src/vmaf_viewer/static/app.js
node --test tests/static/message_state.test.mjs
uv run pytest -q
```

Expected: every command exits 0.

Manual UI verification should confirm `#messages` always contains one visible message element and the summary/charts do not shift upward when loading finishes.

## Plan Self-Review

- Spec coverage: The plan covers persistent `#messages`, one rendered message, warning/error priority, success fallback with loaded frame count, English UI text, CSS stability, and unchanged API/data-loading behavior.
- Placeholder scan: No placeholder steps or deferred implementation notes remain.
- Type consistency: The helper API is consistently named `VmafMessageState.pickMessageState`, `VmafMessageState.formatLoadedMessage`, and `VmafMessageState.DEFAULT_STATUS_MESSAGE` across tests and `app.js` wiring.
