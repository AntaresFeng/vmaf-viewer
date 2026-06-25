import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const FRONTEND_HELPERS = [
  "src/vmaf_viewer/static/message_state.js",
  "src/vmaf_viewer/static/metric_metadata.js",
];
const APP_SCRIPT = "src/vmaf_viewer/static/app.js";
const APP_INIT_MARKER = "\nsetupEvents();";

function createElementStub(id) {
  const element = {
    id,
    attributes: {},
    children: [],
    didBlur: false,
    listeners: {},
    value: "",
    addEventListener(eventName, handler) {
      this.listeners[eventName] = handler;
    },
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    blur() {
      this.didBlur = true;
    },
    classList: {
      add() {},
      remove() {},
      toggle() {},
    },
    replaceChildren(...children) {
      this.children = children;
    },
    setAttribute(name, value) {
      this.attributes[name] = value;
    },
  };
  let innerHTML = "";
  Object.defineProperty(element, "innerHTML", {
    get() {
      return innerHTML;
    },
    set(value) {
      innerHTML = String(value);
      if (innerHTML === "") {
        this.children = [];
      }
    },
  });
  return element;
}

function createDocumentStub() {
  const elements = new Map();
  const document = {
    body: {},
    createElement(tagName) {
      return createElementStub(tagName);
    },
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, createElementStub(id));
      }
      return elements.get(id);
    },
  };
  return { document, elements };
}

function createChartStub() {
  return {
    clear() {},
    off() {},
    on() {},
    resize() {},
    setOption() {},
    getOption() {
      return {};
    },
  };
}

export function createBrowserContext(extraGlobals = {}) {
  const { document, elements } = createDocumentStub();
  const context = {
    document,
    echarts: {
      init() {
        return createChartStub();
      },
    },
    getComputedStyle() {
      return { fontFamily: "sans-serif" };
    },
    requestAnimationFrame(callback) {
      callback();
    },
    window: {
      addEventListener() {},
    },
    ...extraGlobals,
  };
  vm.createContext(context);
  return { context, elements };
}

export function runBrowserScript(context, scriptPath, { beforeMarker = null } = {}) {
  const source = readFileSync(scriptPath, "utf8");
  let script = source;
  if (beforeMarker !== null) {
    const markerIndex = source.indexOf(beforeMarker);
    assert.notEqual(markerIndex, -1);
    script = source.slice(0, markerIndex);
  }
  vm.runInContext(script, context);
}

export function loadBrowserScripts(scriptPaths, options = {}) {
  const { extraGlobals = {} } = options;
  const browser = createBrowserContext(extraGlobals);
  for (const scriptPath of scriptPaths) {
    runBrowserScript(browser.context, scriptPath);
  }
  return browser;
}

export function loadAppContext(exportNames, extraGlobals = {}) {
  const browser = createBrowserContext(extraGlobals);
  for (const scriptPath of FRONTEND_HELPERS) {
    runBrowserScript(browser.context, scriptPath);
  }
  runBrowserScript(browser.context, APP_SCRIPT, { beforeMarker: APP_INIT_MARKER });
  vm.runInContext(`globalThis.__exports = { ${exportNames.join(", ")} };`, browser.context);

  // App function declarations can overwrite test doubles; restore explicit overrides.
  for (const [key, value] of Object.entries(extraGlobals)) {
    browser.context[key] = value;
  }

  return { ...browser, exports: browser.context.__exports };
}

export function toHostValue(value) {
  if (value === null || typeof value !== "object") {
    return value;
  }
  return JSON.parse(JSON.stringify(value));
}
