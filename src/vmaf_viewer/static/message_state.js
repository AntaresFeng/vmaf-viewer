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

  function pickMessageState(input = {}) {
    const { error = null, warnings = [], status = null, success = null } = input;
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
