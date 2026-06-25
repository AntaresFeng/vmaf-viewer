# Static Frontend Tests

Use `browser_harness.mjs` for static frontend tests.

- Load browser helpers with `loadBrowserScripts()` instead of `require()`.
- Load `app.js` with `loadAppContext()` so dependencies match `index.html` order.
- Keep app initialization cut before `setupEvents()` unless a test explicitly needs startup side effects.
- Convert VM-returned arrays or objects with `toHostValue()` before deep equality assertions.
